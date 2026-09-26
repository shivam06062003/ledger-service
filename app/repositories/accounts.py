import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account


def add(session: AsyncSession, account: Account) -> None:
    session.add(account)


async def get(session: AsyncSession, account_id: uuid.UUID) -> Account | None:
    return await session.get(Account, account_id)


async def lock_many(session: AsyncSession, account_ids: Sequence[uuid.UUID]) -> list[Account]:
    """SELECT ... FOR UPDATE: lock the rows until the transaction ends.

    Any other transaction trying to lock the same rows waits here, which is what
    makes check-then-update (read balance, verify, write) safe under concurrency.

    ORDER BY id makes every transaction acquire locks in the same order. Without
    it, a transfer A->B and a concurrent B->A could each hold one lock and wait
    forever for the other (a deadlock).

    populate_existing: if these accounts are already in the session's identity
    map, overwrite them with the freshly locked values instead of silently
    returning stale in-memory balances.
    """
    stmt = (
        select(Account)
        .where(Account.id.in_(account_ids))
        .order_by(Account.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list((await session.scalars(stmt)).all())
