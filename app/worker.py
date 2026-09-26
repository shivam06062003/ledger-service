"""Background worker: relays the outbox, delivers webhooks, runs scheduled jobs.

Run with `python -m app.worker`. Safe to run several replicas: every step
claims its rows with FOR UPDATE SKIP LOCKED or an atomic UPDATE, so replicas
never do the same work.
"""

import asyncio
import signal
import time
from contextlib import suppress
from pathlib import Path

import httpx
import structlog
from prometheus_client import start_http_server

from app.core.config import get_settings
from app.core.db import engine
from app.core.logging import configure_logging
from app.core.tracing import configure_tracing
from app.jobs import Job, make_jobs, refresh_queue_metrics, run_if_due
from app.webhooks.dispatcher import DispatchConfig, run_iteration

logger = structlog.get_logger()

QUEUE_METRICS_INTERVAL_SECONDS = 15.0
JOB_CHECK_INTERVAL_SECONDS = 10.0


async def run(
    stop: asyncio.Event,
    *,
    config: DispatchConfig,
    poll_interval: float,
    heartbeat_path: Path,
    http_timeout: float,
    jobs: list[Job] | None = None,
) -> None:
    next_metrics_refresh = next_job_check = 0.0
    async with httpx.AsyncClient(timeout=http_timeout, follow_redirects=False) as http:
        while not stop.is_set():
            try:
                work_done = await run_iteration(http, config)

                now = time.monotonic()
                if now >= next_metrics_refresh:
                    next_metrics_refresh = now + QUEUE_METRICS_INTERVAL_SECONDS
                    await refresh_queue_metrics()
                if now >= next_job_check:
                    next_job_check = now + JOB_CHECK_INTERVAL_SECONDS
                    for job in jobs or []:
                        await run_if_due(job)
            except Exception:
                # A database blip must not kill the worker; log and try again.
                logger.exception("worker_iteration_failed")
                work_done = 0
            # Container health check: "is the loop still turning?"
            await asyncio.to_thread(heartbeat_path.touch)
            if work_done == 0:
                # Idle: sleep, but wake immediately on shutdown.
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    configure_tracing(settings)
    # Prometheus scrapes the worker on its own port (it has no web framework).
    start_http_server(settings.worker_metrics_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Graceful shutdown: finish the current batch, then exit.
        loop.add_signal_handler(sig, stop.set)

    logger.info("worker_started", metrics_port=settings.worker_metrics_port)
    try:
        await run(
            stop,
            config=DispatchConfig.from_settings(settings),
            poll_interval=settings.worker_poll_interval_seconds,
            heartbeat_path=Path(settings.worker_heartbeat_path),
            http_timeout=settings.webhook_timeout_seconds,
            jobs=make_jobs(settings),
        )
    finally:
        await engine.dispose()
        logger.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
