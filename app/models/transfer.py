import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Transfer(Base):
    """One movement of money. Its entries always sum to zero (double-entry)."""

    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="positive_amount"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str | None] = mapped_column(String(255))
    # Audit trail: which calling service moved this money.
    initiated_by_api_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # selectin: entries are loaded with the transfer in one extra query. Lazy
    # loading on attribute access is not allowed in async SQLAlchemy.
    entries: Mapped[list["Entry"]] = relationship(
        back_populates="transfer", lazy="selectin", order_by="Entry.amount"
    )


class Entry(Base):
    """One line of the ledger: a signed change to one account's balance.

    Negative = debit (money leaves the account), positive = credit.
    Entries are append-only; the database rejects UPDATE and DELETE.
    """

    __tablename__ = "entries"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="non_zero_amount"),
        # Serves paginated account statements: "entries for account X, newest
        # first, after cursor (created_at, id)". id breaks ties between entries
        # written in the same transaction, which share a created_at.
        Index("ix_entries_account_id_created_at_id", "account_id", "created_at", "id"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    transfer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transfers.id"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    # The account's balance immediately after this entry: gives running-balance
    # statements for free and makes audits easy.
    balance_after: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    transfer: Mapped[Transfer] = relationship(back_populates="entries")
