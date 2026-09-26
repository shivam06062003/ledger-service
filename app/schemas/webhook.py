import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models import DeliveryStatus
from app.schemas.events import EventType


class WebhookEndpointCreate(BaseModel):
    url: HttpUrl = Field(examples=["https://payments.example.com/webhooks/ledger"])
    event_types: list[EventType] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=255)


class WebhookEndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    event_types: list[EventType]
    description: str | None
    enabled: bool
    created_at: datetime


class WebhookEndpointCreated(WebhookEndpointRead):
    secret: str = Field(
        description="Signing secret for verifying the Ledger-Signature header. Shown only once."
    )


class WebhookDeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_id: uuid.UUID
    endpoint_id: uuid.UUID
    status: DeliveryStatus
    attempts: int
    next_attempt_at: datetime
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
