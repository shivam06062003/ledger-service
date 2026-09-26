import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey


def add(session: AsyncSession, api_key: ApiKey) -> None:
    session.add(api_key)


async def get(session: AsyncSession, api_key_id: uuid.UUID) -> ApiKey | None:
    return await session.get(ApiKey, api_key_id)


async def get_active_by_hash(session: AsyncSession, key_hash: str) -> ApiKey | None:
    stmt = select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
    return (await session.scalars(stmt)).one_or_none()
