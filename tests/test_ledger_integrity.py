"""The database itself enforces the ledger's rules, so a bug in application code
(or someone with a SQL console) cannot corrupt it. These tests bypass the API
and go straight to SQL to prove it."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import engine
from tests.helpers import create_account, create_funded_account, transfer


async def test_database_rejects_overdrawing_a_normal_account(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 100)

    with pytest.raises(DBAPIError, match="ck_accounts_non_negative_balance"):
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE accounts SET balance = -1 WHERE id = :id"), {"id": alice["id"]}
            )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE entries SET amount = amount * 2",
        "DELETE FROM entries",
        "UPDATE transfers SET amount = 1",
        "DELETE FROM transfers",
    ],
)
async def test_ledger_rows_are_append_only(client: AsyncClient, statement: str) -> None:
    alice = await create_funded_account(client, 100)
    bob = await create_account(client)
    await transfer(client, alice["id"], bob["id"], 50)

    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as conn:
            await conn.execute(text(statement))


async def test_database_rejects_unbalanced_transfer_at_commit(client: AsyncClient) -> None:
    alice = await create_account(client, allow_negative_balance=True)
    transfer_id = uuid.uuid4()

    with pytest.raises(DBAPIError, match="unbalanced"):
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO transfers (id, amount, currency) VALUES (:id, 100, 'INR')"),
                {"id": transfer_id},
            )
            # A debit with no matching credit: money would vanish.
            await conn.execute(
                text(
                    "INSERT INTO entries (id, transfer_id, account_id, amount, balance_after) "
                    "VALUES (:id, :transfer_id, :account_id, -100, -100)"
                ),
                {"id": uuid.uuid4(), "transfer_id": transfer_id, "account_id": alice["id"]},
            )
