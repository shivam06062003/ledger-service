"""HTTP glue for idempotency: header parsing and replay responses. The actual
guarantee lives in app.services.idempotency."""

from typing import Annotated

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.api_keys import Principal
from app.services.idempotency import IdempotencyRequest, IdempotentResult, request_fingerprint

REPLAYED_HEADER = "Idempotent-Replayed"

_DESCRIPTION = (
    "Unique key (e.g. a UUID) that makes retries safe: repeating a request with "
    "the same key returns the original response instead of executing it again. "
    "Keys are kept for 24 hours."
)

IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255, description=_DESCRIPTION)
]
OptionalIdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=255, description=_DESCRIPTION),
]


def idempotency_request(
    principal: Principal, key: str | None, request: Request, body: BaseModel
) -> IdempotencyRequest | None:
    if key is None:
        return None
    return IdempotencyRequest(
        api_key_id=principal.api_key_id,
        key=key,
        request_hash=request_fingerprint(request.method, request.url.path, body),
    )


def to_response(result: IdempotentResult) -> JSONResponse:
    headers = {REPLAYED_HEADER: "true"} if result.replayed else None
    return JSONResponse(result.body, status_code=result.status_code, headers=headers)
