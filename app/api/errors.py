"""Every error response uses one shape, so clients handle failures uniformly:

    {"error": {"code": "insufficient_funds", "message": "...", "details": [...]}}

`code` is stable and machine-readable; `message` is for humans and may change.
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.errors import (
    AccountNotFound,
    ApiKeyNotFound,
    CurrencyMismatch,
    DomainError,
    IdempotencyKeyReused,
    InsufficientFunds,
    InvalidCursor,
    PermissionDenied,
    SameAccountTransfer,
    TransferNotFound,
    Unauthenticated,
)

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    AccountNotFound: status.HTTP_404_NOT_FOUND,
    TransferNotFound: status.HTTP_404_NOT_FOUND,
    InsufficientFunds: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CurrencyMismatch: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SameAccountTransfer: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ApiKeyNotFound: status.HTTP_404_NOT_FOUND,
    Unauthenticated: status.HTTP_401_UNAUTHORIZED,
    PermissionDenied: status.HTTP_403_FORBIDDEN,
    IdempotencyKeyReused: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidCursor: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status_code, headers=headers)


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    status_code = _STATUS_BY_ERROR.get(type(exc), status.HTTP_400_BAD_REQUEST)
    # RFC 9110: a 401 must tell the client which auth scheme to use.
    headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, Unauthenticated) else None
    return error_response(status_code, exc.code, exc.message, headers=headers)


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "Request validation failed",
        details=jsonable_encoder(exc.errors()),
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
