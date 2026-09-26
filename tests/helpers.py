"""Test helpers that drive the public API, plus ledger invariant checks."""

from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

from app.core.db import engine


async def create_account(
    client: AsyncClient,
    *,
    name: str = "Test account",
    currency: str = "INR",
    allow_negative_balance: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        "/v1/accounts",
        json={"name": name, "currency": currency, "allow_negative_balance": allow_negative_balance},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def transfer(
    client: AsyncClient,
    source_id: str,
    destination_id: str,
    amount: int,
    currency: str = "INR",
) -> Any:
    return await client.post(
        "/v1/transfers",
        json={
            "source_account_id": source_id,
            "destination_account_id": destination_id,
            "amount": amount,
            "currency": currency,
        },
    )


async def create_funded_account(
    client: AsyncClient, amount: int, currency: str = "INR"
) -> dict[str, Any]:
    """A normal account holding `amount`, funded from a system account the way
    real money enters a ledger (e.g. a bank deposit settlement account)."""
    funding = await create_account(
        client, name="Funding source", currency=currency, allow_negative_balance=True
    )
    account = await create_account(client, currency=currency)
    response = await transfer(client, funding["id"], account["id"], amount, currency)
    assert response.status_code == 201, response.text
    return account


async def get_balance(client: AsyncClient, account_id: str) -> int:
    response = await client.get(f"/v1/accounts/{account_id}")
    assert response.status_code == 200
    balance: int = response.json()["balance"]
    return balance


async def assert_ledger_consistent() -> None:
    """The three invariants of a double-entry ledger. These are the same checks
    a production reconciliation job would run."""
    async with engine.connect() as conn:
        # 1. Money is never created or destroyed.
        total = await conn.scalar(text("SELECT COALESCE(SUM(amount), 0) FROM entries"))
        assert total == 0, f"ledger does not sum to zero: {total}"

        # 2. Every transfer balances on its own.
        unbalanced = await conn.execute(
            text("SELECT transfer_id FROM entries GROUP BY transfer_id HAVING SUM(amount) <> 0")
        )
        assert unbalanced.all() == []

        # 3. Cached balances equal the sum of each account's entries.
        drifted = await conn.execute(
            text(
                """
                SELECT a.id, a.balance, COALESCE(SUM(e.amount), 0) AS entries_total
                FROM accounts a LEFT JOIN entries e ON e.account_id = a.id
                GROUP BY a.id, a.balance
                HAVING a.balance <> COALESCE(SUM(e.amount), 0)
                """
            )
        )
        assert drifted.all() == []
