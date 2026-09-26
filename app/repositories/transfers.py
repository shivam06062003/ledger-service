import uuid
from datetime import datetime

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entry, Transfer


def add(session: AsyncSession, transfer: Transfer) -> None:
    session.add(transfer)


async def get(session: AsyncSession, transfer_id: uuid.UUID) -> Transfer | None:
    return await session.get(Transfer, transfer_id)


async def list_entries_for_account(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    limit: int,
    before: tuple[datetime, uuid.UUID] | None = None,
) -> list[Entry]:
    """Newest first. `before` is a keyset cursor: return only entries strictly
    older than that (created_at, id) position.

    Unlike OFFSET, which makes Postgres read and discard every skipped row,
    the row-value comparison seeks straight to the position via the
    (account_id, created_at, id) index, so page 1000 is as fast as page 1.
    """
    stmt = select(Entry).where(Entry.account_id == account_id)
    if before is not None:
        created_at, entry_id = before
        stmt = stmt.where(
            tuple_(Entry.created_at, Entry.id) < tuple_(literal(created_at), literal(entry_id))
        )
    stmt = stmt.order_by(Entry.created_at.desc(), Entry.id.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())
