import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Entry
from app.repositories import accounts as accounts_repo
from app.repositories import transfers as transfers_repo
from app.schemas.account import AccountCreate
from app.services.errors import AccountNotFound


async def create_account(session: AsyncSession, data: AccountCreate) -> Account:
    account = Account(**data.model_dump())
    async with session.begin():
        accounts_repo.add(session, account)
    return account


async def get_account(session: AsyncSession, account_id: uuid.UUID) -> Account:
    account = await accounts_repo.get(session, account_id)
    if account is None:
        raise AccountNotFound(account_id)
    return account


async def list_entries(session: AsyncSession, account_id: uuid.UUID, limit: int) -> list[Entry]:
    await get_account(session, account_id)  # 404 rather than an empty list for unknown IDs
    return await transfers_repo.list_entries_for_account(session, account_id, limit)
