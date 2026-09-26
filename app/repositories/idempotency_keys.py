import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdempotencyKey


async def insert_if_absent(
    session: AsyncSession, *, api_key_id: uuid.UUID, key: str, request_hash: str
) -> bool:
    """Try to reserve the key. Returns False if it already exists.

    If another in-flight transaction has inserted the same key but not yet
    committed, Postgres makes this INSERT wait on the unique index until that
    transaction finishes. That is what serializes duplicate concurrent requests.
    """
    stmt = (
        insert(IdempotencyKey)
        .values(api_key_id=api_key_id, key=key, request_hash=request_hash)
        .on_conflict_do_nothing(index_elements=["api_key_id", "key"])
        .returning(IdempotencyKey.key)
    )
    return (await session.execute(stmt)).first() is not None


async def get(session: AsyncSession, *, api_key_id: uuid.UUID, key: str) -> IdempotencyKey | None:
    stmt = select(IdempotencyKey).where(
        IdempotencyKey.api_key_id == api_key_id, IdempotencyKey.key == key
    )
    return await session.scalar(stmt)


async def save_response(
    session: AsyncSession,
    *,
    api_key_id: uuid.UUID,
    key: str,
    status_code: int,
    body: dict[str, Any],
) -> None:
    stmt = (
        update(IdempotencyKey)
        .where(IdempotencyKey.api_key_id == api_key_id, IdempotencyKey.key == key)
        .values(status_code=status_code, response_body=body)
    )
    await session.execute(stmt)


async def delete_created_before(session: AsyncSession, cutoff: datetime) -> int:
    stmt = (
        delete(IdempotencyKey)
        .where(IdempotencyKey.created_at < cutoff)
        .returning(IdempotencyKey.key)
    )
    return len((await session.execute(stmt)).all())
