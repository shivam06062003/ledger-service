import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IdempotencyKey(Base):
    """The recorded outcome of a request made with an Idempotency-Key header.

    Keys are scoped per API key, so two clients choosing the same key string
    never collide. Rows are written in the same transaction as the operation
    they protect, so a row exists if and only if the operation committed.
    """

    __tablename__ = "idempotency_keys"

    api_key_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_keys.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    # SHA-256 of method + path + request body: detects a key reused for a
    # different request, which is a client bug we must not paper over.
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None]
    # JSON (not JSONB) preserves the exact response text, so replays are
    # byte-identical. We never query inside it, so JSONB's indexing buys nothing.
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
