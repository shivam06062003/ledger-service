"""Moves events from the outbox to subscribers. Run by the worker process.

Two steps, each safe to run on any number of worker replicas at once:

1. relay_outbox: turn each unpublished event into one delivery row per
   subscribed endpoint (fan-out), and mark the event published. Atomic.
2. deliver_due: lease due deliveries, POST them (signed), then record success,
   schedule a retry with exponential backoff, or dead-letter after max attempts.

Guarantee: at-least-once. A worker can crash after the receiver got the event
but before recording success, and the event is then sent again. Receivers
deduplicate on the event ID (sent in the payload and the Ledger-Event-Id header).
"""

import asyncio
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import structlog
from opentelemetry import propagate
from opentelemetry.trace import SpanKind, Status, StatusCode
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import SessionLocal
from app.core.metrics import OUTBOX_RELAYED, WEBHOOK_ATTEMPTS, WEBHOOK_DURATION
from app.core.tracing import context_from, tracer
from app.models import WebhookDelivery
from app.repositories import outbox as outbox_repo
from app.repositories import webhooks as webhooks_repo
from app.repositories.webhooks import ClaimedDelivery
from app.webhooks.signing import SIGNATURE_HEADER, sign

logger = structlog.get_logger()


@dataclass(frozen=True)
class DispatchConfig:
    batch_size: int
    max_attempts: int
    lease: timedelta
    backoff_base_seconds: float
    backoff_cap_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "DispatchConfig":
        return cls(
            batch_size=settings.worker_batch_size,
            max_attempts=settings.webhook_max_attempts,
            lease=timedelta(seconds=settings.webhook_lease_seconds),
            backoff_base_seconds=settings.webhook_backoff_base_seconds,
            backoff_cap_seconds=settings.webhook_backoff_cap_seconds,
        )


@dataclass(frozen=True)
class DeliveryOutcome:
    succeeded: bool
    status_code: int | None
    error: str | None = None


def backoff_delay(
    attempts: int, *, base: float, cap: float, rand: Callable[[], float] = random.random
) -> float:
    """Exponential backoff with jitter: base, 2*base, 4*base, ... capped.

    The jitter (a random 50-100% of the delay) spreads retries out. Without it,
    every delivery that failed during a receiver's outage would retry at the
    same instants and hit it with synchronized waves of traffic.
    """
    delay = min(cap, base * 2.0 ** (attempts - 1))
    return delay / 2 + rand() * delay / 2


def build_body(delivery: ClaimedDelivery) -> bytes:
    event: dict[str, Any] = {
        "id": str(delivery.event_id),
        "type": delivery.event_type,
        "created_at": delivery.event_created_at.isoformat(),
        "data": delivery.payload,
    }
    return json.dumps(event, separators=(",", ":")).encode()


async def relay_outbox(config: DispatchConfig) -> int:
    """Fan out a batch of unpublished events. Returns the number relayed."""
    async with SessionLocal() as session, session.begin():
        events = await outbox_repo.claim_unpublished(session, config.batch_size)
        if not events:
            return 0
        endpoints = await webhooks_repo.list_endpoints(session, enabled_only=True)
        for event in events:
            for endpoint in endpoints:
                if event.event_type in endpoint.event_types:
                    webhooks_repo.add(
                        session, WebhookDelivery(event_id=event.id, endpoint_id=endpoint.id)
                    )
            event.published_at = func.now()
    OUTBOX_RELAYED.inc(len(events))
    logger.info("outbox_relayed", events=len(events))
    return len(events)


async def send(http: httpx.AsyncClient, delivery: ClaimedDelivery) -> DeliveryOutcome:
    # Resume the trace of the API request that created the event.
    with tracer.start_as_current_span(
        f"webhook deliver {delivery.event_type}",
        context=context_from(delivery.trace_context),
        kind=SpanKind.CONSUMER,
        attributes={
            "ledger.event_id": str(delivery.event_id),
            "ledger.delivery_id": str(delivery.id),
            "ledger.delivery_attempt": delivery.attempts,
            "url.full": delivery.url,
        },
    ) as span:
        outcome = await _post(http, delivery)
        if outcome.status_code is not None:
            span.set_attribute("http.response.status_code", outcome.status_code)
        if not outcome.succeeded:
            span.set_status(Status(StatusCode.ERROR, outcome.error))
        return outcome


async def _post(http: httpx.AsyncClient, delivery: ClaimedDelivery) -> DeliveryOutcome:
    body = build_body(delivery)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ledger-service-webhooks/1.0",
        "Ledger-Event-Id": str(delivery.event_id),
        "Ledger-Event-Type": delivery.event_type,
        "Ledger-Delivery-Attempt": str(delivery.attempts),
        SIGNATURE_HEADER: sign(delivery.secret, body),
    }
    # Forward traceparent so the subscriber can continue the trace too.
    propagate.inject(headers)
    start = time.perf_counter()
    try:
        # Redirects are not followed: a 3xx counts as a failure. Following them
        # could send signed payloads somewhere the subscriber never registered.
        response = await http.post(delivery.url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return DeliveryOutcome(False, None, f"{type(exc).__name__}: {exc}")
    finally:
        WEBHOOK_DURATION.observe(time.perf_counter() - start)
    if response.is_success:
        return DeliveryOutcome(True, response.status_code)
    return DeliveryOutcome(False, response.status_code, f"HTTP {response.status_code}")


async def deliver_due(http: httpx.AsyncClient, config: DispatchConfig) -> int:
    """Send a batch of due deliveries. Returns the number attempted."""
    async with SessionLocal() as session, session.begin():
        claimed = await webhooks_repo.claim_due_deliveries(
            session, limit=config.batch_size, lease=config.lease
        )
    if not claimed:
        return 0

    # The claim is committed, so no transaction is open during the HTTP calls.
    outcomes = await asyncio.gather(*(send(http, delivery) for delivery in claimed))

    async with SessionLocal() as session, session.begin():
        for delivery, outcome in zip(claimed, outcomes, strict=True):
            await _record(session, delivery, outcome, config)
    return len(claimed)


async def _record(
    session: AsyncSession,
    delivery: ClaimedDelivery,
    outcome: DeliveryOutcome,
    config: DispatchConfig,
) -> None:
    log = logger.bind(
        delivery_id=str(delivery.id),
        event_id=str(delivery.event_id),
        attempt=delivery.attempts,
        status_code=outcome.status_code,
    )
    if outcome.succeeded:
        assert outcome.status_code is not None
        await webhooks_repo.mark_succeeded(session, delivery.id, outcome.status_code)
        WEBHOOK_ATTEMPTS.labels("succeeded").inc()
        log.info("webhook_delivered")
        return

    error = outcome.error or "unknown error"
    if delivery.attempts >= config.max_attempts:
        await webhooks_repo.mark_attempt_failed(
            session, delivery.id, status_code=outcome.status_code, error=error, retry_in=None
        )
        WEBHOOK_ATTEMPTS.labels("dead_lettered").inc()
        log.warning("webhook_dead_lettered", error=error)
        return

    delay = backoff_delay(
        delivery.attempts, base=config.backoff_base_seconds, cap=config.backoff_cap_seconds
    )
    await webhooks_repo.mark_attempt_failed(
        session,
        delivery.id,
        status_code=outcome.status_code,
        error=error,
        retry_in=timedelta(seconds=delay),
    )
    WEBHOOK_ATTEMPTS.labels("retry_scheduled").inc()
    log.info("webhook_retry_scheduled", error=error, retry_in_seconds=round(delay, 1))


async def run_iteration(http: httpx.AsyncClient, config: DispatchConfig) -> int:
    """One pass of both steps. Returns the amount of work done (0 = idle)."""
    return await relay_outbox(config) + await deliver_due(http, config)
