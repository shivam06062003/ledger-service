import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import DeliveryStatus, WebhookDelivery, WebhookEndpoint
from app.repositories import webhooks as webhooks_repo
from app.schemas.webhook import WebhookEndpointCreate, WebhookEndpointCreated, WebhookEndpointRead
from app.services.errors import (
    DeliveryNotRetryable,
    InsecureWebhookUrl,
    WebhookDeliveryNotFound,
    WebhookEndpointNotFound,
)

SECRET_PREFIX = "whsec_"


async def create_endpoint(
    session: AsyncSession, data: WebhookEndpointCreate
) -> WebhookEndpointCreated:
    # Signed payloads over plain http can still be read in transit, and would
    # leak transfer details. Plain http is allowed locally for development.
    if get_settings().environment == "production" and data.url.scheme != "https":
        raise InsecureWebhookUrl()

    endpoint = WebhookEndpoint(
        url=str(data.url),
        secret=SECRET_PREFIX + secrets.token_urlsafe(32),
        event_types=sorted({event_type.value for event_type in data.event_types}),
        description=data.description,
    )
    async with session.begin():
        webhooks_repo.add(session, endpoint)
    return WebhookEndpointCreated(
        **WebhookEndpointRead.model_validate(endpoint).model_dump(), secret=endpoint.secret
    )


async def list_endpoints(session: AsyncSession) -> list[WebhookEndpoint]:
    return await webhooks_repo.list_endpoints(session)


async def disable_endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> None:
    """Soft delete: stops new fan-out and delivery, keeps history for audit."""
    async with session.begin():
        endpoint = await webhooks_repo.get_endpoint(session, endpoint_id)
        if endpoint is None:
            raise WebhookEndpointNotFound(endpoint_id)
        endpoint.enabled = False


async def list_deliveries(
    session: AsyncSession,
    *,
    status: DeliveryStatus | None,
    endpoint_id: uuid.UUID | None,
    limit: int,
) -> list[WebhookDelivery]:
    return await webhooks_repo.list_deliveries(
        session, status=status, endpoint_id=endpoint_id, limit=limit
    )


async def retry_delivery(session: AsyncSession, delivery_id: uuid.UUID) -> WebhookDelivery:
    """Move a dead-lettered delivery back into the queue with a fresh retry budget.
    Typical use: the receiver was broken for hours, and has now been fixed."""
    async with session.begin():
        delivery = await webhooks_repo.get_delivery(session, delivery_id)
        if delivery is None:
            raise WebhookDeliveryNotFound(delivery_id)
        if delivery.status != DeliveryStatus.FAILED:
            raise DeliveryNotRetryable(delivery_id, delivery.status)
        delivery.status = DeliveryStatus.PENDING
        delivery.attempts = 0
        delivery.next_attempt_at = func.now()
        await session.flush()
        await session.refresh(delivery)
    return delivery


async def purge_history(
    session: AsyncSession, retention: timedelta, *, batch_size: int = 5_000
) -> tuple[int, int]:
    """Delete succeeded deliveries and fully-delivered events older than the
    retention window, in batches. Pending and dead-lettered data is kept.
    Returns (deliveries deleted, events deleted)."""
    cutoff = datetime.now(UTC) - retention
    deleted_deliveries = deleted_events = 0
    while True:
        async with session.begin():
            n = await webhooks_repo.delete_succeeded_deliveries_before(
                session, cutoff, batch_size=batch_size
            )
        deleted_deliveries += n
        if n < batch_size:
            break
    while True:
        async with session.begin():
            n = await webhooks_repo.delete_published_events_before(
                session, cutoff, batch_size=batch_size
            )
        deleted_events += n
        if n < batch_size:
            break
    return deleted_deliveries, deleted_events
