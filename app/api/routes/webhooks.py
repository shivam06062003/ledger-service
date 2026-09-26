import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Query, status

from app.api.auth import Admin
from app.core.db import SessionDep
from app.models import DeliveryStatus, WebhookDelivery, WebhookEndpoint
from app.schemas.webhook import (
    WebhookDeliveryRead,
    WebhookEndpointCreate,
    WebhookEndpointCreated,
    WebhookEndpointRead,
)
from app.services import webhooks as webhook_service

logger = structlog.get_logger()

endpoints_router = APIRouter(prefix="/v1/webhook-endpoints", tags=["webhooks"])
deliveries_router = APIRouter(prefix="/v1/webhook-deliveries", tags=["webhooks"])


@endpoints_router.post("", status_code=status.HTTP_201_CREATED)
async def create_webhook_endpoint(
    body: WebhookEndpointCreate, session: SessionDep, _: Admin
) -> WebhookEndpointCreated:
    """Subscribe a URL to events. The signing secret is returned only in this response."""
    created = await webhook_service.create_endpoint(session, body)
    logger.info("webhook_endpoint_created", endpoint_id=str(created.id), url=created.url)
    return created


@endpoints_router.get("", response_model=list[WebhookEndpointRead])
async def list_webhook_endpoints(session: SessionDep, _: Admin) -> list[WebhookEndpoint]:
    return await webhook_service.list_endpoints(session)


@endpoints_router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_webhook_endpoint(endpoint_id: uuid.UUID, session: SessionDep, _: Admin) -> None:
    """Stop sending to this endpoint. Delivery history is kept."""
    await webhook_service.disable_endpoint(session, endpoint_id)


@deliveries_router.get("", response_model=list[WebhookDeliveryRead])
async def list_webhook_deliveries(
    session: SessionDep,
    _: Admin,
    delivery_status: Annotated[
        DeliveryStatus | None,
        Query(alias="status", description="`failed` lists the dead-letter queue"),
    ] = None,
    endpoint_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[WebhookDelivery]:
    return await webhook_service.list_deliveries(
        session, status=delivery_status, endpoint_id=endpoint_id, limit=limit
    )


@deliveries_router.post("/{delivery_id}/retry", response_model=WebhookDeliveryRead)
async def retry_webhook_delivery(
    delivery_id: uuid.UUID, session: SessionDep, _: Admin
) -> WebhookDelivery:
    """Re-queue a dead-lettered delivery with a fresh retry budget."""
    delivery = await webhook_service.retry_delivery(session, delivery_id)
    logger.info("webhook_delivery_requeued", delivery_id=str(delivery_id))
    return delivery
