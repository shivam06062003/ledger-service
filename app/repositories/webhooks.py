import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeliveryStatus, OutboxEvent, WebhookDelivery, WebhookEndpoint


def add(session: AsyncSession, obj: WebhookEndpoint | WebhookDelivery) -> None:
    session.add(obj)


async def get_endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> WebhookEndpoint | None:
    return await session.get(WebhookEndpoint, endpoint_id)


async def list_endpoints(
    session: AsyncSession, *, enabled_only: bool = False
) -> list[WebhookEndpoint]:
    stmt = select(WebhookEndpoint).order_by(WebhookEndpoint.created_at)
    if enabled_only:
        stmt = stmt.where(WebhookEndpoint.enabled.is_(True))
    return list((await session.scalars(stmt)).all())


async def get_delivery(session: AsyncSession, delivery_id: uuid.UUID) -> WebhookDelivery | None:
    return await session.get(WebhookDelivery, delivery_id)


async def list_deliveries(
    session: AsyncSession,
    *,
    status: DeliveryStatus | None,
    endpoint_id: uuid.UUID | None,
    limit: int,
) -> list[WebhookDelivery]:
    stmt = select(WebhookDelivery).order_by(
        WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc()
    )
    if status is not None:
        stmt = stmt.where(WebhookDelivery.status == status)
    if endpoint_id is not None:
        stmt = stmt.where(WebhookDelivery.endpoint_id == endpoint_id)
    return list((await session.scalars(stmt.limit(limit))).all())


@dataclass(frozen=True)
class ClaimedDelivery:
    """Everything needed to send one delivery, detached from any session so the
    HTTP call can happen after the claiming transaction has committed."""

    id: uuid.UUID
    attempts: int
    url: str
    secret: str
    event_id: uuid.UUID
    event_type: str
    event_created_at: datetime
    payload: dict[str, Any]


async def claim_due_deliveries(
    session: AsyncSession, *, limit: int, lease: timedelta
) -> list[ClaimedDelivery]:
    """Lease a batch of due deliveries, like an SQS visibility timeout.

    The claimed rows get next_attempt_at pushed `lease` into the future and
    their attempt counter bumped, then the transaction commits. The caller makes
    the HTTP requests with NO transaction open (holding row locks across network
    calls would tie up connections and block other workers). If the worker dies
    mid-delivery, the lease simply expires and another worker retries.
    """
    due = (
        select(WebhookDelivery.id)
        .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
        .where(
            WebhookDelivery.status == DeliveryStatus.PENDING,
            WebhookDelivery.next_attempt_at <= func.now(),
            WebhookEndpoint.enabled.is_(True),
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(limit)
        .with_for_update(of=WebhookDelivery, skip_locked=True)
    )
    claimed_ids = (
        await session.scalars(
            update(WebhookDelivery)
            .where(WebhookDelivery.id.in_(due.scalar_subquery()))
            .values(
                next_attempt_at=func.now() + lease,
                attempts=WebhookDelivery.attempts + 1,
            )
            .returning(WebhookDelivery.id)
        )
    ).all()
    if not claimed_ids:
        return []

    rows = await session.execute(
        select(
            WebhookDelivery.id,
            WebhookDelivery.attempts,
            WebhookEndpoint.url,
            WebhookEndpoint.secret,
            OutboxEvent.id,
            OutboxEvent.event_type,
            OutboxEvent.created_at,
            OutboxEvent.payload,
        )
        .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
        .join(OutboxEvent, OutboxEvent.id == WebhookDelivery.event_id)
        .where(WebhookDelivery.id.in_(claimed_ids))
    )
    return [ClaimedDelivery(*row) for row in rows]


async def mark_succeeded(session: AsyncSession, delivery_id: uuid.UUID, status_code: int) -> None:
    await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id)
        .values(
            status=DeliveryStatus.SUCCEEDED,
            last_status_code=status_code,
            last_error=None,
            delivered_at=func.now(),
        )
    )


async def mark_attempt_failed(
    session: AsyncSession,
    delivery_id: uuid.UUID,
    *,
    status_code: int | None,
    error: str,
    retry_in: timedelta | None,
) -> None:
    """Schedule a retry after `retry_in`, or dead-letter it if retry_in is None."""
    values: dict[str, Any] = {"last_status_code": status_code, "last_error": error[:500]}
    if retry_in is None:
        values["status"] = DeliveryStatus.FAILED
    else:
        values["next_attempt_at"] = func.now() + retry_in
    await session.execute(
        update(WebhookDelivery).where(WebhookDelivery.id == delivery_id).values(**values)
    )
