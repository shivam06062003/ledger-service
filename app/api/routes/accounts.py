import uuid

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from app.api.auth import AccountsReader, AccountsWriter
from app.api.idempotency import OptionalIdempotencyKey, idempotency_request, to_response
from app.core.db import SessionDep
from app.models import Account
from app.schemas.account import AccountCreate, AccountRead
from app.schemas.api_key import Scope
from app.schemas.common import Page
from app.schemas.transfer import EntryRead
from app.services import accounts as account_service
from app.services.errors import PermissionDenied

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AccountRead)
async def create_account(
    body: AccountCreate,
    request: Request,
    session: SessionDep,
    principal: AccountsWriter,
    idempotency_key: OptionalIdempotencyKey = None,
) -> JSONResponse:
    # System accounts can go negative, i.e. create money from nothing, so only
    # administrators may create them.
    if body.allow_negative_balance and not principal.has(Scope.ADMIN):
        raise PermissionDenied("Creating a system account requires the 'admin' scope")
    result = await account_service.create_account(
        session,
        body,
        idempotency_request=idempotency_request(principal, idempotency_key, request, body),
    )
    return to_response(result)


@router.get("/{account_id}", response_model=AccountRead)
async def get_account(account_id: uuid.UUID, session: SessionDep, _: AccountsReader) -> Account:
    return await account_service.get_account(session, account_id)


@router.get("/{account_id}/entries")
async def list_account_entries(
    account_id: uuid.UUID,
    session: SessionDep,
    _: AccountsReader,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, description="`next_cursor` from the previous page"),
) -> Page[EntryRead]:
    """Account statement, newest first, with the running balance after each entry."""
    return await account_service.list_entries(session, account_id, limit=limit, cursor=cursor)
