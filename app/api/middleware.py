import re
import time
import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import RequestResponseEndpoint

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller-supplied ID only if it is short and plain, so it can't be used
# to inject junk into our logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-_.]{1,128}$")

logger = structlog.get_logger()


async def request_context_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Tag every request with an ID, bind it to all logs, and log the outcome.

    If an upstream service already sent X-Request-ID we reuse it, so a single
    request can be traced across services.
    """
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)

    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request_failed", method=request.method, path=request.url.path)
        raise

    response.headers[REQUEST_ID_HEADER] = request_id
    logger.info(
        "request_completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round((time.perf_counter() - start) * 1000, 2),
    )
    return response
