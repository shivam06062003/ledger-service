import asyncio
from datetime import timedelta

from httpx import AsyncClient
from prometheus_client import REGISTRY
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.jobs import Job, claim, reconcile_ledger, run_if_due
from app.services import webhooks as webhook_service
from app.webhooks.dispatcher import run_iteration
from tests.helpers import create_account, create_funded_account, transfer
from tests.webhook_fakes import FakeReceiver, deliveries, dispatch_config, subscribe

HOUR = timedelta(hours=1)


async def test_only_one_replica_claims_a_due_job() -> None:
    results = await asyncio.gather(*(claim("nightly", HOUR) for _ in range(10)))

    assert sum(results) == 1


async def test_job_runs_again_only_after_its_interval() -> None:
    assert await claim("hourly", HOUR)
    assert not await claim("hourly", HOUR)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE scheduled_jobs SET last_started_at = now() - interval '61 minutes' "
                "WHERE name = 'hourly'"
            )
        )

    assert await claim("hourly", HOUR)


async def test_run_if_due_records_status_and_survives_failures() -> None:
    async def explode() -> None:
        raise RuntimeError("boom")

    ran = await run_if_due(Job("flaky", HOUR, explode))

    assert ran
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT last_status, last_error FROM scheduled_jobs WHERE name = 'flaky'")
            )
        ).one()
    assert row.last_status == "failed"
    assert "boom" in row.last_error


async def test_reconcile_job_publishes_discrepancy_gauge(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 100)
    await reconcile_ledger()
    assert (
        REGISTRY.get_sample_value(
            "ledger_reconciliation_discrepancies", {"check": "drifted_accounts"}
        )
        == 0
    )

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE accounts SET balance = 7 WHERE id = :id"), {"id": alice["id"]}
        )
    await reconcile_ledger()

    assert (
        REGISTRY.get_sample_value(
            "ledger_reconciliation_discrepancies", {"check": "drifted_accounts"}
        )
        == 1
    )


async def test_webhook_history_purge_keeps_pending_and_dead_letters(client: AsyncClient) -> None:
    await subscribe(client)
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    for _ in range(3):
        await transfer(client, alice["id"], bob["id"], 1)
    receiver = FakeReceiver()
    async with receiver.client() as http:
        await run_iteration(http, dispatch_config())
    rows = await deliveries()
    succeeded_old, failed_old, succeeded_recent = (str(r.id) for r in rows[-3:])
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE webhook_deliveries SET delivered_at = now() - interval '40 days' "
                "WHERE id IN (:a, :b)"
            ),
            {"a": succeeded_old, "b": failed_old},
        )
        await conn.execute(
            text("UPDATE webhook_deliveries SET status = 'failed' WHERE id = :id"),
            {"id": failed_old},
        )
        await conn.execute(
            text("UPDATE outbox_events SET published_at = now() - interval '40 days'")
        )

    async with SessionLocal() as session:
        deleted = await webhook_service.purge_history(session, timedelta(days=30), batch_size=1)

    remaining = {str(r.id) for r in await deliveries()}
    assert succeeded_old not in remaining
    assert {failed_old, succeeded_recent} <= remaining
    # Its delivery is gone, so the old event goes too; events still referenced
    # by remaining deliveries are kept. batch_size=1 exercises the batch loop.
    assert deleted[0] == 1
    assert deleted[1] >= 1
