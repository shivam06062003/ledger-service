import uuid
from http import HTTPStatus

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import TRANSFER_VOLUME, TRANSFERS
from app.models import Account, Entry, Transfer
from app.repositories import accounts as accounts_repo
from app.repositories import transfers as transfers_repo
from app.schemas.events import EventType
from app.schemas.transfer import TransferCreate, TransferRead
from app.services import events, idempotency
from app.services.errors import (
    AccountNotFound,
    CurrencyMismatch,
    InsufficientFunds,
    SameAccountTransfer,
    TransferNotFound,
)
from app.services.idempotency import IdempotencyRequest, IdempotentResult

logger = structlog.get_logger()


async def create_transfer(
    session: AsyncSession,
    data: TransferCreate,
    *,
    initiated_by: uuid.UUID,
    idempotency_request: IdempotencyRequest,
) -> IdempotentResult:
    """Move money between two accounts atomically and at most once.

    Everything happens in one database transaction: claim the idempotency key,
    lock both accounts, validate, update cached balances, write the transfer
    and its two entries, add a transfer.created event to the outbox, record the
    response. Any error rolls the whole thing
    back, so there is never a half-applied transfer.
    """
    if data.source_account_id == data.destination_account_id:
        raise SameAccountTransfer()

    async def post() -> TransferRead:
        transfer = TransferRead.model_validate(await _post_transfer(session, data, initiated_by))
        # Same transaction as the transfer: the event exists iff the transfer does.
        events.record(session, EventType.TRANSFER_CREATED, transfer)
        return transfer

    result = await idempotency.execute(
        session, idempotency_request, post, status_code=HTTPStatus.CREATED
    )
    if not result.replayed:
        TRANSFERS.labels(data.currency).inc()
        TRANSFER_VOLUME.labels(data.currency).inc(data.amount)
        logger.info(
            "transfer_created",
            transfer_id=result.body["id"],
            source_account_id=str(data.source_account_id),
            destination_account_id=str(data.destination_account_id),
            amount=data.amount,
            currency=data.currency,
        )
    return result


async def get_transfer(session: AsyncSession, transfer_id: uuid.UUID) -> Transfer:
    transfer = await transfers_repo.get(session, transfer_id)
    if transfer is None:
        raise TransferNotFound(transfer_id)
    return transfer


async def _post_transfer(
    session: AsyncSession, data: TransferCreate, initiated_by: uuid.UUID
) -> Transfer:
    """Must run inside a transaction (the caller owns it)."""
    locked = {
        account.id: account
        for account in await accounts_repo.lock_many(
            session, [data.source_account_id, data.destination_account_id]
        )
    }
    source = _require(locked, data.source_account_id)
    destination = _require(locked, data.destination_account_id)

    for account in (source, destination):
        if account.currency != data.currency:
            raise CurrencyMismatch(account.id, account.currency, data.currency)

    # Safe to check-then-act: we hold the row lock, so no concurrent
    # transfer can change this balance until we commit.
    if not source.allow_negative_balance and source.balance < data.amount:
        raise InsufficientFunds(source.id)

    source.balance -= data.amount
    destination.balance += data.amount

    transfer = Transfer(
        amount=data.amount,
        currency=data.currency,
        description=data.description,
        initiated_by_api_key_id=initiated_by,
        entries=[
            Entry(account_id=source.id, amount=-data.amount, balance_after=source.balance),
            Entry(
                account_id=destination.id,
                amount=data.amount,
                balance_after=destination.balance,
            ),
        ],
    )
    transfers_repo.add(session, transfer)
    await session.flush()  # INSERT now, so ids and created_at are populated
    return transfer


def _require(accounts: dict[uuid.UUID, Account], account_id: uuid.UUID) -> Account:
    account = accounts.get(account_id)
    if account is None:
        raise AccountNotFound(account_id)
    return account
