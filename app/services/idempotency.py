"""Idempotency keys: a retried request is executed at most once.

A client that times out cannot tell whether its transfer happened. With an
Idempotency-Key it can simply retry: the first request that commits wins, and
every retry gets that same response back instead of moving the money again.

The key is claimed inside the same database transaction as the operation:

    BEGIN
      INSERT key ... ON CONFLICT DO NOTHING   <- duplicates wait here
      (key already existed?) -> return the stored response
      run the operation (lock accounts, write the transfer)
      store the response on the key row
    COMMIT                                    <- key and transfer commit together

Because the key and the operation share a transaction, they succeed or fail
together. There is no "in progress" state to get stuck in after a crash, and a
failed request (e.g. insufficient funds) leaves no key behind, so the client
can safely retry once the problem is fixed.
"""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import IDEMPOTENT_REPLAYS
from app.repositories import idempotency_keys as idempotency_repo
from app.services.errors import IdempotencyKeyReused

logger = structlog.get_logger()


@dataclass(frozen=True)
class IdempotencyRequest:
    api_key_id: uuid.UUID
    key: str
    request_hash: str


@dataclass(frozen=True)
class IdempotentResult:
    status_code: int
    body: dict[str, Any]
    replayed: bool


def request_fingerprint(method: str, path: str, payload: BaseModel) -> str:
    """Hash of what the request asks for. Built from the validated model, so
    formatting differences (whitespace, key order) don't count as a change."""
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{method} {path}\n{canonical}".encode()).hexdigest()


async def execute(
    session: AsyncSession,
    request: IdempotencyRequest | None,
    operation: Callable[[], Awaitable[BaseModel]],
    *,
    status_code: int,
) -> IdempotentResult:
    """Run `operation` in one transaction, at most once per idempotency key.

    `operation` must not open its own transaction; this function owns it.
    With `request=None` the operation simply runs in a transaction.
    """
    async with session.begin():
        if request is not None:
            stored = await _claim(session, request)
            if stored is not None:
                return stored

        body = (await operation()).model_dump(mode="json")

        if request is not None:
            await idempotency_repo.save_response(
                session,
                api_key_id=request.api_key_id,
                key=request.key,
                status_code=int(status_code),
                body=body,
            )
    # int(): callers may pass an HTTPStatus enum; store and log the plain number.
    return IdempotentResult(status_code=int(status_code), body=body, replayed=False)


async def _claim(session: AsyncSession, request: IdempotencyRequest) -> IdempotentResult | None:
    """Reserve the key, or return the stored response if it was already used."""
    claimed = await idempotency_repo.insert_if_absent(
        session,
        api_key_id=request.api_key_id,
        key=request.key,
        request_hash=request.request_hash,
    )
    if claimed:
        return None

    existing = await idempotency_repo.get(session, api_key_id=request.api_key_id, key=request.key)
    # The row was committed by another transaction, and rows are only ever
    # committed together with their response, so it must be complete.
    assert existing is not None
    assert existing.status_code is not None and existing.response_body is not None

    if existing.request_hash != request.request_hash:
        raise IdempotencyKeyReused()

    IDEMPOTENT_REPLAYS.inc()
    logger.info("idempotent_replay", idempotency_key=request.key)
    return IdempotentResult(
        status_code=existing.status_code, body=existing.response_body, replayed=True
    )


async def purge_expired(session: AsyncSession, retention: timedelta) -> int:
    """Delete keys older than the retention window. Clients may only rely on
    retries being safe within that window (24h by default, as Stripe does)."""
    async with session.begin():
        return await idempotency_repo.delete_created_before(session, datetime.now(UTC) - retention)
