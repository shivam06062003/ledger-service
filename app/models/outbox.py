import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OutboxEvent(Base):
    """Transactional outbox: domain events written in the SAME transaction as
    the change they describe.

    Writing to the database and then calling an external system is a "dual
    write": a crash in between loses the event, and calling first then rolling
    back announces something that never happened. With an outbox, the event
    exists if and only if the transfer committed. A worker publishes it later.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        # Partial index: only unpublished rows, which is all the relay ever
        # scans. Stays tiny no matter how much history accumulates.
        Index(
            "ix_outbox_events_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
