"""Periodic jobs run by the worker: reconciliation, retention, queue metrics.

Coordination across replicas uses the same idea as webhook delivery leases: a
job is claimed with one atomic UPDATE that only succeeds if the interval has
elapsed since its last start:

    UPDATE scheduled_jobs SET last_started_at = now()
    WHERE name = :job AND last_started_at <= now() - :interval
    RETURNING name

Exactly one replica gets the row back and runs the job; the others see a fresh
last_started_at and skip. No lock is held while the job runs, and if the
runner crashes the job simply runs again at the next interval. (Postgres
advisory locks are the other common tool; they would stop two runs happening
at the SAME time, but not stop every replica running it once per interval.)
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import structlog
from sqlalchemy import func, or_, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings
from app.core.db import SessionLocal
from app.core.metrics import (
    DELIVERIES_DEAD_LETTERED,
    DELIVERIES_PENDING,
    JOB_RUNS,
    OLDEST_PENDING_DELIVERY_AGE,
    OUTBOX_BACKLOG,
    RECONCILIATION_DISCREPANCIES,
    RECONCILIATION_LAST_RUN,
)
from app.models import ScheduledJob
from app.repositories import webhooks as webhooks_repo
from app.services import idempotency, reconciliation
from app.services import webhooks as webhook_service

logger = structlog.get_logger()


@dataclass(frozen=True)
class Job:
    name: str
    interval: timedelta
    run: Callable[[], Awaitable[None]]


async def claim(job_name: str, interval: timedelta) -> bool:
    """True if this caller won the right to run the job now."""
    async with SessionLocal() as session, session.begin():
        await session.execute(insert(ScheduledJob).values(name=job_name).on_conflict_do_nothing())
        claimed = await session.scalar(
            update(ScheduledJob)
            .where(
                ScheduledJob.name == job_name,
                or_(
                    ScheduledJob.last_started_at.is_(None),
                    ScheduledJob.last_started_at <= func.now() - interval,
                ),
            )
            .values(last_started_at=func.now())
            .returning(ScheduledJob.name)
        )
    return claimed is not None


async def _finish(job_name: str, status: str, error: str | None) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(ScheduledJob)
            .where(ScheduledJob.name == job_name)
            .values(last_finished_at=func.now(), last_status=status, last_error=error)
        )


async def run_if_due(job: Job) -> bool:
    """Run the job if this replica claims it. Returns True if it ran."""
    if not await claim(job.name, job.interval):
        return False
    log = logger.bind(job=job.name)
    start = time.perf_counter()
    try:
        await job.run()
    except Exception as exc:
        JOB_RUNS.labels(job.name, "failed").inc()
        log.exception("job_failed")
        await _finish(job.name, "failed", f"{type(exc).__name__}: {exc}"[:500])
    else:
        JOB_RUNS.labels(job.name, "succeeded").inc()
        log.info("job_succeeded", duration_ms=round((time.perf_counter() - start) * 1000))
        await _finish(job.name, "succeeded", None)
    return True


# --- Job bodies ----------------------------------------------------------------


async def reconcile_ledger() -> None:
    report = await reconciliation.reconcile()
    RECONCILIATION_DISCREPANCIES.labels("ledger_total").set(abs(report.ledger_total))
    RECONCILIATION_DISCREPANCIES.labels("unbalanced_transfers").set(
        len(report.unbalanced_transfer_ids)
    )
    RECONCILIATION_DISCREPANCIES.labels("drifted_accounts").set(len(report.drifted_account_ids))
    RECONCILIATION_LAST_RUN.set_to_current_time()
    if report.ok:
        logger.info("reconciliation_passed")
    else:
        # Page someone: money is wrong. The alert rule fires on the gauge.
        logger.error(
            "reconciliation_failed",
            ledger_total=report.ledger_total,
            unbalanced_transfer_ids=[str(i) for i in report.unbalanced_transfer_ids],
            drifted_account_ids=[str(i) for i in report.drifted_account_ids],
        )


def make_jobs(settings: Settings) -> list[Job]:
    async def purge_idempotency_keys() -> None:
        async with SessionLocal() as session:
            deleted = await idempotency.purge_expired(
                session, timedelta(hours=settings.idempotency_key_retention_hours)
            )
        logger.info("idempotency_keys_purged", deleted=deleted)

    async def purge_webhook_history() -> None:
        async with SessionLocal() as session:
            deliveries, events = await webhook_service.purge_history(
                session, timedelta(days=settings.webhook_history_retention_days)
            )
        logger.info("webhook_history_purged", deliveries=deliveries, events=events)

    retention_interval = timedelta(seconds=settings.retention_job_interval_seconds)
    return [
        Job(
            "reconcile_ledger",
            timedelta(seconds=settings.reconciliation_interval_seconds),
            reconcile_ledger,
        ),
        Job("purge_idempotency_keys", retention_interval, purge_idempotency_keys),
        Job("purge_webhook_history", retention_interval, purge_webhook_history),
    ]


async def refresh_queue_metrics() -> None:
    """Not a claimed job: every replica refreshes its own gauges (cheap counts
    served by the partial indexes)."""
    async with SessionLocal() as session:
        stats = await webhooks_repo.queue_stats(session)
    OUTBOX_BACKLOG.set(stats.outbox_unpublished)
    DELIVERIES_PENDING.set(stats.deliveries_pending)
    DELIVERIES_DEAD_LETTERED.set(stats.deliveries_dead_lettered)
    OLDEST_PENDING_DELIVERY_AGE.set(stats.oldest_pending_age_seconds)
