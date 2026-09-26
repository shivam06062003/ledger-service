"""Test helpers that drive the public API, plus ledger invariant checks."""

import uuid
from typing import Any

from httpx import AsyncClient

from app.services.reconciliation import reconcile


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
    *,
    idempotency_key: str | None = None,
) -> Any:
    return await client.post(
        "/v1/transfers",
        headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
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
    """The three invariants of a double-entry ledger, checked by the same code
    the scheduled reconciliation job runs."""
    report = await reconcile()
    assert report.ok, report
