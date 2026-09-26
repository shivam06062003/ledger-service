import re
import time
import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.core.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller-supplied ID only if it is short and plain, so it can't be used
# to inject junk into our logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-_.]{1,128}$")
# Polled every few seconds by Prometheus and Docker; still measured, but
# logging each one would drown out real traffic.
_UNLOGGED_PATHS = frozenset({"/metrics", "/health/live", "/health/ready"})

logger = structlog.get_logger()


async def request_context_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Tag every request with an ID, bind it to all logs, record metrics, and
    log the outcome.

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
        _observe(request, 500, start)
        logger.exception("request_failed", method=request.method, path=request.url.path)
        raise

    duration = _observe(request, response.status_code, start)
    response.headers[REQUEST_ID_HEADER] = request_id
    if request.url.path not in _UNLOGGED_PATHS:
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 2),
        )
    return response


def _observe(request: Request, status_code: int, start: float) -> float:
    duration = time.perf_counter() - start
    # The matched route's template, e.g. /v1/accounts/{account_id}, never the
    # raw path: one time series per route, not one per account. Unmatched
    # paths (404s from scanners) all share one label.
    route = request.scope.get("route")
    template = getattr(route, "path", "unmatched")
    HTTP_REQUESTS.labels(request.method, template, str(status_code)).inc()
    HTTP_REQUEST_DURATION.labels(request.method, template).observe(duration)
    return duration
