import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    # Exhausted all retries: the dead-letter queue. An operator can inspect and
    # re-queue these via the API.
    FAILED = "failed"


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String(2048))
    # Used to HMAC-sign payloads, so it must be stored recoverably (unlike API
    # keys, which are hashed). In production this column would be encrypted
    # with a KMS-managed key.
    secret: Mapped[str] = mapped_column(String(64))
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    description: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookDelivery(Base):
    """One event going to one endpoint, with its retry state."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        # Belt and braces: fan-out can never create two deliveries of the same
        # event to the same endpoint, even if the relay logic had a bug.
        UniqueConstraint("event_id", "endpoint_id"),
        CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="valid_status"),
        # Partial index for the worker's hot query: "pending and due".
        Index(
            "ix_webhook_deliveries_due",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outbox_events.id"))
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_endpoints.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=DeliveryStatus.PENDING, server_default=DeliveryStatus.PENDING
    )
    # Incremented when an attempt STARTS (at claim time), so a delivery that
    # crashes the worker still counts towards the limit and can't retry forever.
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_status_code: Mapped[int | None]
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
