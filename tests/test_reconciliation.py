from httpx import AsyncClient
from sqlalchemy import text

from app.core.db import engine
from app.services.reconciliation import reconcile
from tests.helpers import create_account, create_funded_account, transfer


async def test_clean_ledger_passes(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    await transfer(client, alice["id"], bob["id"], 400)

    report = await reconcile()

    assert report.ok
    assert report.ledger_total == 0


async def test_detects_cached_balance_drift(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    # A bad manual "fix": someone edits a balance directly. The entries (the
    # source of truth) still say 1,000.
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE accounts SET balance = 1500 WHERE id = :id"), {"id": alice["id"]}
        )

    report = await reconcile()

    assert not report.ok
    assert [str(i) for i in report.drifted_account_ids] == [alice["id"]]
    assert report.ledger_total == 0  # entries still balance; only the cache drifted
