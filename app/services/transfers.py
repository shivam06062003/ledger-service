import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Entry, Transfer
from app.repositories import accounts as accounts_repo
from app.repositories import transfers as transfers_repo
from app.schemas.transfer import TransferCreate
from app.services.errors import (
    AccountNotFound,
    CurrencyMismatch,
    InsufficientFunds,
    SameAccountTransfer,
    TransferNotFound,
)

logger = structlog.get_logger()


async def create_transfer(session: AsyncSession, data: TransferCreate) -> Transfer:
    """Move money between two accounts atomically.

    Everything happens in one database transaction: lock both accounts, validate,
    update cached balances, write the transfer and its two entries. Any error
    rolls the whole thing back, so there is never a half-applied transfer.
    """
    if data.source_account_id == data.destination_account_id:
        raise SameAccountTransfer()

    async with session.begin():
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

    logger.info(
        "transfer_created",
        transfer_id=str(transfer.id),
        source_account_id=str(source.id),
        destination_account_id=str(destination.id),
        amount=data.amount,
        currency=data.currency,
    )
    return transfer


async def get_transfer(session: AsyncSession, transfer_id: uuid.UUID) -> Transfer:
    transfer = await transfers_repo.get(session, transfer_id)
    if transfer is None:
        raise TransferNotFound(transfer_id)
    return transfer


def _require(accounts: dict[uuid.UUID, Account], account_id: uuid.UUID) -> Account:
    account = accounts.get(account_id)
    if account is None:
        raise AccountNotFound(account_id)
    return account
