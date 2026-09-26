import asyncio
import uuid
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import func, select, text

from app.core.db import SessionLocal, engine
from app.models import IdempotencyKey, Transfer
from app.schemas.api_key import Scope
from app.services import idempotency
from tests.conftest import ClientFactory
from tests.helpers import (
    assert_ledger_consistent,
    create_account,
    create_funded_account,
    get_balance,
    transfer,
)


async def count_transfers() -> int:
    async with SessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(Transfer)) or 0


async def test_retry_with_same_key_replays_original_response(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    before = await count_transfers()

    first = await transfer(client, alice["id"], bob["id"], 300, idempotency_key="order-42")
    retry = await transfer(client, alice["id"], bob["id"], 300, idempotency_key="order-42")

    assert first.status_code == retry.status_code == 201
    assert retry.content == first.content  # byte-identical replay
    assert "Idempotent-Replayed" not in first.headers
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert await count_transfers() == before + 1
    assert await get_balance(client, alice["id"]) == 700


async def test_concurrent_duplicates_execute_exactly_once(client: AsyncClient) -> None:
    # A client's network flaps and it fires the same request 20 times at once.
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    before = await count_transfers()

    responses = await asyncio.gather(
        *(transfer(client, alice["id"], bob["id"], 100, idempotency_key="dup") for _ in range(20))
    )

    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert sum(r.headers.get("Idempotent-Replayed") == "true" for r in responses) == 19
    assert await count_transfers() == before + 1
    assert await get_balance(client, alice["id"]) == 900
    await assert_ledger_consistent()


async def test_reusing_key_for_different_request_is_rejected(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    await transfer(client, alice["id"], bob["id"], 100, idempotency_key="k1")

    response = await transfer(client, alice["id"], bob["id"], 999, idempotency_key="k1")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert await get_balance(client, alice["id"]) == 900


async def test_failed_request_is_not_recorded_so_retry_can_succeed(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 50)
    bob = await create_account(client)

    failed = await transfer(client, alice["id"], bob["id"], 100, idempotency_key="k")
    assert failed.status_code == 422

    # The client tops up the account, then retries with the same key.
    funding = await create_account(client, allow_negative_balance=True)
    await transfer(client, funding["id"], alice["id"], 50)
    retried = await transfer(client, alice["id"], bob["id"], 100, idempotency_key="k")

    assert retried.status_code == 201
    assert "Idempotent-Replayed" not in retried.headers
    assert await get_balance(client, bob["id"]) == 100


async def test_keys_are_scoped_per_api_key(client: AsyncClient, make_client: ClientFactory) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    other_service = await make_client(Scope.TRANSFERS_WRITE)

    first = await transfer(client, alice["id"], bob["id"], 100, idempotency_key="same")
    second = await transfer(other_service, alice["id"], bob["id"], 100, idempotency_key="same")

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert await get_balance(client, alice["id"]) == 800


async def test_transfers_require_an_idempotency_key(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)

    response = await client.post(
        "/v1/transfers",
        json={
            "source_account_id": alice["id"],
            "destination_account_id": bob["id"],
            "amount": 100,
            "currency": "INR",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["loc"] == ["header", "Idempotency-Key"]


async def test_account_creation_is_idempotent_when_key_given(client: AsyncClient) -> None:
    payload = {"name": "Alice", "currency": "INR"}
    headers = {"Idempotency-Key": str(uuid.uuid4())}

    first = await client.post("/v1/accounts", json=payload, headers=headers)
    retry = await client.post("/v1/accounts", json=payload, headers=headers)

    assert first.json()["id"] == retry.json()["id"]
    assert retry.headers["Idempotent-Replayed"] == "true"


async def test_purge_removes_only_expired_keys(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    await transfer(client, alice["id"], bob["id"], 1, idempotency_key="old")
    await transfer(client, alice["id"], bob["id"], 1, idempotency_key="fresh")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE idempotency_keys SET created_at = now() - interval '25 hours' "
                "WHERE key = 'old'"
            )
        )

    async with SessionLocal() as session:
        deleted = await idempotency.purge_expired(session, timedelta(hours=24))

    async with SessionLocal() as session:
        remaining = set((await session.scalars(select(IdempotencyKey.key))).all())
    assert deleted == 1
    assert "old" not in remaining
    assert "fresh" in remaining
