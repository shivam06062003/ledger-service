import uuid

from httpx import AsyncClient

from tests.helpers import create_account, create_funded_account, transfer


async def test_create_account_starts_at_zero(client: AsyncClient) -> None:
    account = await create_account(client, name="Alice", currency="INR")

    assert account["name"] == "Alice"
    assert account["currency"] == "INR"
    assert account["balance"] == 0
    assert account["allow_negative_balance"] is False


async def test_get_account(client: AsyncClient) -> None:
    account = await create_account(client)

    response = await client.get(f"/v1/accounts/{account['id']}")

    assert response.status_code == 200
    assert response.json() == account


async def test_get_unknown_account_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"/v1/accounts/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


async def test_rejects_invalid_currency(client: AsyncClient) -> None:
    response = await client.post("/v1/accounts", json={"name": "Bad", "currency": "rupees"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "currency"]


async def test_entries_are_listed_newest_first_with_running_balance(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    await transfer(client, alice["id"], bob["id"], 300)
    await transfer(client, alice["id"], bob["id"], 200)

    response = await client.get(f"/v1/accounts/{alice['id']}/entries")

    assert response.status_code == 200
    entries = response.json()["data"]
    assert [(e["amount"], e["balance_after"]) for e in entries] == [
        (-200, 500),
        (-300, 700),
        (1_000, 1_000),
    ]


async def test_entries_for_unknown_account_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"/v1/accounts/{uuid.uuid4()}/entries")

    assert response.status_code == 404
