import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Account(Base):
    """A place money lives. `balance` is a cached running total of the account's
    entries, kept in sync inside the same transaction that writes the entries."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        # Last line of defence: even buggy application code can't overdraw a
        # normal account, because Postgres will reject the UPDATE.
        CheckConstraint("allow_negative_balance OR balance >= 0", name="non_negative_balance"),
    )
    # Fetch server-generated columns (created_at) via INSERT ... RETURNING.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3))
    # Integer minor units (paise, cents). Never floats for money.
    balance: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    # True only for system accounts (e.g. an external funding source), which
    # represent money owed to/from the outside world and may go negative.
    allow_negative_balance: Mapped[bool] = mapped_column(
        default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
