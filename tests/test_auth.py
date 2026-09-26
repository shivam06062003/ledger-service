import hashlib

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.db import engine
from app.schemas.api_key import Scope
from tests.conftest import ClientFactory
from tests.helpers import create_account, create_funded_account, transfer


async def test_missing_api_key_returns_401(make_client: ClientFactory) -> None:
    anonymous = await make_client()

    response = await anonymous.get("/v1/transfers/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_invalid_api_key_returns_401(make_client: ClientFactory) -> None:
    anonymous = await make_client()

    response = await anonymous.get(
        "/v1/accounts/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": "Bearer ldg_not-a-real-key"},
    )

    assert response.status_code == 401


async def test_health_checks_need_no_api_key(make_client: ClientFactory) -> None:
    anonymous = await make_client()

    assert (await anonymous.get("/health/live")).status_code == 200


@pytest.mark.parametrize(
    ("scope", "method", "path"),
    [
        (Scope.ACCOUNTS_READ, "POST", "/v1/accounts"),
        (Scope.TRANSFERS_READ, "POST", "/v1/transfers"),
        (Scope.TRANSFERS_WRITE, "GET", "/v1/accounts/00000000-0000-0000-0000-000000000000"),
        (Scope.ACCOUNTS_WRITE, "POST", "/v1/api-keys"),
    ],
)
async def test_missing_scope_returns_403(
    make_client: ClientFactory, scope: Scope, method: str, path: str
) -> None:
    limited = await make_client(scope)

    response = await limited.request(method, path, json={})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


async def test_only_admin_can_create_system_accounts(make_client: ClientFactory) -> None:
    writer = await make_client(Scope.ACCOUNTS_WRITE)

    system = await writer.post(
        "/v1/accounts",
        json={"name": "Mint", "currency": "INR", "allow_negative_balance": True},
    )
    normal = await writer.post("/v1/accounts", json={"name": "Wallet", "currency": "INR"})

    assert system.status_code == 403
    assert normal.status_code == 201


async def test_admin_issues_key_that_works_with_its_scopes(
    client: AsyncClient, make_client: ClientFactory
) -> None:
    response = await client.post(
        "/v1/api-keys", json={"name": "checkout-service", "scopes": ["accounts:read"]}
    )
    assert response.status_code == 201
    created = response.json()
    assert created["key"].startswith("ldg_")
    assert created["prefix"] == created["key"][:12]

    account = await create_account(client)
    new_client = await make_client()
    new_client.headers["Authorization"] = f"Bearer {created['key']}"

    assert (await new_client.get(f"/v1/accounts/{account['id']}")).status_code == 200
    assert (await new_client.post("/v1/accounts", json={})).status_code == 403


async def test_only_the_hash_of_a_key_is_stored(client: AsyncClient) -> None:
    created = (
        await client.post("/v1/api-keys", json={"name": "svc", "scopes": ["accounts:read"]})
    ).json()

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT key_hash, api_keys::text AS full_row FROM api_keys WHERE id = :id"),
                {"id": created["id"]},
            )
        ).one()

    assert row.key_hash == hashlib.sha256(created["key"].encode()).hexdigest()
    assert created["key"] not in row.full_row


async def test_revoked_key_is_rejected(client: AsyncClient, make_client: ClientFactory) -> None:
    created = (
        await client.post("/v1/api-keys", json={"name": "svc", "scopes": ["accounts:read"]})
    ).json()
    account = await create_account(client)
    service = await make_client()
    service.headers["Authorization"] = f"Bearer {created['key']}"
    assert (await service.get(f"/v1/accounts/{account['id']}")).status_code == 200

    revoke = await client.delete(f"/v1/api-keys/{created['id']}")

    assert revoke.status_code == 204
    assert (await service.get(f"/v1/accounts/{account['id']}")).status_code == 401
    # Revoking twice is harmless.
    assert (await client.delete(f"/v1/api-keys/{created['id']}")).status_code == 204


async def test_transfer_records_which_api_key_initiated_it(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 100)
    bob = await create_account(client)

    body = (await transfer(client, alice["id"], bob["id"], 10)).json()

    assert body["initiated_by_api_key_id"] is not None
