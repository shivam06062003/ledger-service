import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entry, Transfer


def add(session: AsyncSession, transfer: Transfer) -> None:
    session.add(transfer)


async def get(session: AsyncSession, transfer_id: uuid.UUID) -> Transfer | None:
    return await session.get(Transfer, transfer_id)


async def list_entries_for_account(
    session: AsyncSession, account_id: uuid.UUID, limit: int
) -> list[Entry]:
    stmt = (
        select(Entry)
        .where(Entry.account_id == account_id)
        .order_by(Entry.created_at.desc(), Entry.id.desc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())
