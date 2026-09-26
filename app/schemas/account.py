import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Currency


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    currency: Currency
    allow_negative_balance: bool = Field(
        default=False,
        description="System accounts only (e.g. an external funding source).",
    )


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    currency: str
    balance: int
    allow_negative_balance: bool
    created_at: datetime
