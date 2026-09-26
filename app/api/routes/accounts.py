import uuid

from fastapi import APIRouter, Query, status

from app.core.db import SessionDep
from app.models import Account, Entry
from app.schemas.account import AccountCreate, AccountRead
from app.schemas.transfer import EntryRead
from app.services import accounts as account_service

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AccountRead)
async def create_account(body: AccountCreate, session: SessionDep) -> Account:
    return await account_service.create_account(session, body)


@router.get("/{account_id}", response_model=AccountRead)
async def get_account(account_id: uuid.UUID, session: SessionDep) -> Account:
    return await account_service.get_account(session, account_id)


@router.get("/{account_id}/entries", response_model=list[EntryRead])
async def list_account_entries(
    account_id: uuid.UUID,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Entry]:
    """Account statement, newest first. Cursor pagination arrives in Phase 3."""
    return await account_service.list_entries(session, account_id, limit)
