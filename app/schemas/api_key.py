import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Scope(StrEnum):
    ACCOUNTS_READ = "accounts:read"
    ACCOUNTS_WRITE = "accounts:write"
    TRANSFERS_READ = "transfers:read"
    TRANSFERS_WRITE = "transfers:write"
    # Implies every other scope, plus system accounts and API key management.
    ADMIN = "admin"


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["checkout-service"])
    scopes: list[Scope] = Field(min_length=1)


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[Scope]
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyRead):
    key: str = Field(description="The secret key. Shown only once; store it securely.")
