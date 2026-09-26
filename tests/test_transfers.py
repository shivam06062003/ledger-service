import uuid

from httpx import AsyncClient

from tests.helpers import (
    assert_ledger_consistent,
    create_account,
    create_funded_account,
    get_balance,
    transfer,
)


async def test_transfer_moves_money_with_balanced_entries(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 10_000)
    bob = await create_account(client)

    response = await transfer(client, alice["id"], bob["id"], 2_500)

    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == 2_500
    debit, credit = body["entries"]
    assert (debit["account_id"], debit["amount"], debit["balance_after"]) == (
        alice["id"],
        -2_500,
        7_500,
    )
    assert (credit["account_id"], credit["amount"], credit["balance_after"]) == (
        bob["id"],
        2_500,
        2_500,
    )
    assert await get_balance(client, alice["id"]) == 7_500
    assert await get_balance(client, bob["id"]) == 2_500
    await assert_ledger_consistent()


async def test_can_spend_exact_balance(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 500)
    bob = await create_account(client)

    response = await transfer(client, alice["id"], bob["id"], 500)

    assert response.status_code == 201
    assert await get_balance(client, alice["id"]) == 0


async def test_insufficient_funds_changes_nothing(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 100)
    bob = await create_account(client)

    response = await transfer(client, alice["id"], bob["id"], 101)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_funds"
    assert await get_balance(client, alice["id"]) == 100
    assert await get_balance(client, bob["id"]) == 0
    entries = (await client.get(f"/v1/accounts/{bob['id']}/entries")).json()["data"]
    assert entries == []


async def test_system_account_may_go_negative(client: AsyncClient) -> None:
    funding = await create_account(client, allow_negative_balance=True)
    alice = await create_account(client)

    response = await transfer(client, funding["id"], alice["id"], 1_000)

    assert response.status_code == 201
    assert await get_balance(client, funding["id"]) == -1_000
    await assert_ledger_consistent()


async def test_rejects_currency_mismatch(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000, currency="INR")
    bob = await create_account(client, currency="USD")

    response = await transfer(client, alice["id"], bob["id"], 100, currency="INR")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "currency_mismatch"


async def test_rejects_transfer_to_same_account(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)

    response = await transfer(client, alice["id"], alice["id"], 100)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "same_account_transfer"


async def test_rejects_unknown_account(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)

    response = await transfer(client, alice["id"], str(uuid.uuid4()), 100)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


async def test_rejects_non_positive_amount(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)

    for amount in (0, -50):
        response = await transfer(client, alice["id"], bob["id"], amount)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


async def test_get_transfer(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    created = (await transfer(client, alice["id"], bob["id"], 400)).json()

    response = await client.get(f"/v1/transfers/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


async def test_get_unknown_transfer_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"/v1/transfers/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "transfer_not_found"
