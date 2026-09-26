"""API-key authentication and scope-based authorization.

Usage in a route:

    async def create_transfer(..., principal: TransfersWriter) -> ...

The dependency rejects the request with 401 (no valid key) or 403 (valid key
without the required scope) before the route body runs.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.db import SessionLocal
from app.core.metrics import RATE_LIMITED
from app.core.rate_limit import get_rate_limiter
from app.schemas.api_key import Scope
from app.services import api_keys as api_key_service
from app.services.api_keys import Principal
from app.services.errors import PermissionDenied, RateLimited, Unauthenticated

# auto_error=False: we raise our own error so it uses the standard envelope.
_bearer = HTTPBearer(auto_error=False, description="API key, sent as `Bearer ldg_...`")


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise Unauthenticated("Missing API key. Send it as 'Authorization: Bearer <key>'.")
    # A short-lived session of its own: the request's main session must stay
    # free of an open transaction so services can begin() their own.
    async with SessionLocal() as session:
        principal = await api_key_service.authenticate(session, credentials.credentials)
    structlog.contextvars.bind_contextvars(api_key_id=str(principal.api_key_id))

    # Limited per key, after authentication: one noisy client can't starve the
    # rest. (Unauthenticated floods are the job of the edge/load balancer.)
    limit = await get_rate_limiter().hit(str(principal.api_key_id))
    if not limit.allowed:
        RATE_LIMITED.inc()
        raise RateLimited(limit.retry_after_seconds)
    return principal


def require_scope(scope: Scope) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not principal.has(scope):
            raise PermissionDenied(f"This API key lacks the '{scope}' scope")
        return principal

    return dependency


AccountsReader = Annotated[Principal, Depends(require_scope(Scope.ACCOUNTS_READ))]
AccountsWriter = Annotated[Principal, Depends(require_scope(Scope.ACCOUNTS_WRITE))]
TransfersReader = Annotated[Principal, Depends(require_scope(Scope.TRANSFERS_READ))]
TransfersWriter = Annotated[Principal, Depends(require_scope(Scope.TRANSFERS_WRITE))]
Admin = Annotated[Principal, Depends(require_scope(Scope.ADMIN))]
