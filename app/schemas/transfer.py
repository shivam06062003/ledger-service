import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Currency, PositiveAmount


class TransferCreate(BaseModel):
    source_account_id: uuid.UUID
    destination_account_id: uuid.UUID
    amount: PositiveAmount
    currency: Currency
    description: str | None = Field(default=None, max_length=255)


class EntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    transfer_id: uuid.UUID
    account_id: uuid.UUID
    amount: int
    balance_after: int
    created_at: datetime


class TransferRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount: int
    currency: str
    description: str | None
    initiated_by_api_key_id: uuid.UUID | None
    created_at: datetime
    entries: list[EntryRead]
