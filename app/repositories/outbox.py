from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent


def add(session: AsyncSession, event: OutboxEvent) -> None:
    session.add(event)


async def claim_unpublished(session: AsyncSession, limit: int) -> list[OutboxEvent]:
    """Lock a batch of unpublished events for this transaction.

    SKIP LOCKED: rows another worker has already locked are skipped instead of
    waited on, so several worker replicas split the backlog between them
    rather than queueing behind each other.
    """
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())
