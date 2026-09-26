import uuid
from http import HTTPStatus

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account
from app.repositories import accounts as accounts_repo
from app.repositories import transfers as transfers_repo
from app.schemas.account import AccountCreate, AccountRead
from app.schemas.common import Page
from app.schemas.events import EventType
from app.schemas.transfer import EntryRead
from app.services import events, idempotency
from app.services.errors import AccountNotFound
from app.services.idempotency import IdempotencyRequest, IdempotentResult
from app.services.pagination import decode_cursor, encode_cursor


async def create_account(
    session: AsyncSession, data: AccountCreate, *, idempotency_request: IdempotencyRequest | None
) -> IdempotentResult:
    async def create() -> AccountRead:
        account = Account(**data.model_dump())
        accounts_repo.add(session, account)
        await session.flush()  # INSERT now, so server defaults (created_at) are populated
        created = AccountRead.model_validate(account)
        events.record(session, EventType.ACCOUNT_CREATED, created)
        return created

    return await idempotency.execute(
        session, idempotency_request, create, status_code=HTTPStatus.CREATED
    )


async def get_account(session: AsyncSession, account_id: uuid.UUID) -> Account:
    account = await accounts_repo.get(session, account_id)
    if account is None:
        raise AccountNotFound(account_id)
    return account


async def list_entries(
    session: AsyncSession, account_id: uuid.UUID, *, limit: int, cursor: str | None
) -> Page[EntryRead]:
    await get_account(session, account_id)  # 404 rather than an empty list for unknown IDs
    before = decode_cursor(cursor) if cursor else None

    # Fetch one extra row: if it exists, there is another page.
    entries = await transfers_repo.list_entries_for_account(
        session, account_id, limit=limit + 1, before=before
    )
    has_more = len(entries) > limit
    entries = entries[:limit]

    last = entries[-1] if entries else None
    return Page(
        data=[EntryRead.model_validate(entry) for entry in entries],
        next_cursor=encode_cursor(last.created_at, last.id) if has_more and last else None,
    )
