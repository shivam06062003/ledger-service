"""The outbox guarantee: an event exists if and only if its change committed."""

from httpx import AsyncClient
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import OutboxEvent
from tests.helpers import create_account, create_funded_account, transfer


async def events_of_type(event_type: str) -> list[OutboxEvent]:
    async with SessionLocal() as session:
        stmt = select(OutboxEvent).where(OutboxEvent.event_type == event_type)
        return list((await session.scalars(stmt)).all())


async def test_transfer_writes_event_with_full_snapshot(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)

    body = (await transfer(client, alice["id"], bob["id"], 250)).json()

    payloads = [event.payload for event in await events_of_type("transfer.created")]
    assert body in payloads  # the event carries exactly what the API returned


async def test_failed_transfer_writes_no_event(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 100)
    bob = await create_account(client)
    before = len(await events_of_type("transfer.created"))

    response = await transfer(client, alice["id"], bob["id"], 999)

    assert response.status_code == 422
    assert len(await events_of_type("transfer.created")) == before


async def test_idempotent_replay_writes_no_second_event(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    before = len(await events_of_type("transfer.created"))

    await transfer(client, alice["id"], bob["id"], 1, idempotency_key="once")
    await transfer(client, alice["id"], bob["id"], 1, idempotency_key="once")

    assert len(await events_of_type("transfer.created")) == before + 1


async def test_account_creation_writes_event(client: AsyncClient) -> None:
    account = await create_account(client, name="Alice")

    payloads = [event.payload for event in await events_of_type("account.created")]
    assert account in payloads
