import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey
from app.repositories import api_keys as api_keys_repo
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyRead, Scope
from app.services.errors import ApiKeyNotFound, Unauthenticated

# Recognisable prefix: makes leaked keys easy to spot in logs and lets
# secret scanners (e.g. GitHub's) match them.
KEY_PREFIX = "ldg_"
DISPLAY_PREFIX_LENGTH = 12


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of the current request."""

    api_key_id: uuid.UUID
    name: str
    scopes: frozenset[Scope]

    def has(self, scope: Scope) -> bool:
        return Scope.ADMIN in self.scopes or scope in self.scopes


def hash_api_key(plaintext: str) -> str:
    """SHA-256, not bcrypt. bcrypt is deliberately slow to resist brute force on
    low-entropy human passwords. API keys carry 256 random bits, which can't be
    brute-forced anyway, and we must verify one on every request, so a fast
    hash is the right tool. It also allows a direct indexed lookup by hash."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def create_api_key(session: AsyncSession, data: ApiKeyCreate) -> ApiKeyCreated:
    plaintext = KEY_PREFIX + secrets.token_urlsafe(32)
    api_key = ApiKey(
        name=data.name,
        prefix=plaintext[:DISPLAY_PREFIX_LENGTH],
        key_hash=hash_api_key(plaintext),
        scopes=sorted({scope.value for scope in data.scopes}),
    )
    async with session.begin():
        api_keys_repo.add(session, api_key)
    return ApiKeyCreated(**ApiKeyRead.model_validate(api_key).model_dump(), key=plaintext)


async def authenticate(session: AsyncSession, plaintext: str) -> Principal:
    api_key = await api_keys_repo.get_active_by_hash(session, hash_api_key(plaintext))
    if api_key is None:
        raise Unauthenticated("Invalid or revoked API key")
    return Principal(
        api_key_id=api_key.id,
        name=api_key.name,
        scopes=frozenset(Scope(scope) for scope in api_key.scopes),
    )


async def revoke_api_key(session: AsyncSession, api_key_id: uuid.UUID) -> None:
    """Idempotent: revoking an already-revoked key keeps its original timestamp."""
    async with session.begin():
        api_key = await api_keys_repo.get(session, api_key_id)
        if api_key is None:
            raise ApiKeyNotFound(api_key_id)
        if api_key.revoked_at is None:
            api_key.revoked_at = datetime.now(UTC)
