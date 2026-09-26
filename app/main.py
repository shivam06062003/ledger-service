import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, multiprocess

from app.api.errors import register_error_handlers
from app.api.middleware import request_context_middleware
from app.api.routes import accounts, api_keys, health, transfers, webhooks
from app.core.config import get_settings
from app.core.db import engine
from app.core.logging import configure_logging
from app.core.rate_limit import get_rate_limiter
from app.core.tracing import configure_tracing

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("startup", environment=get_settings().environment)
    yield
    # Close pooled connections cleanly so Postgres isn't left with dangling sessions.
    await engine.dispose()
    await get_rate_limiter().close()
    logger.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.middleware("http")(request_context_middleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(accounts.router)
    app.include_router(transfers.router)
    app.include_router(api_keys.router)
    app.include_router(webhooks.endpoints_router)
    app.include_router(webhooks.deliveries_router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        # Scraped by Prometheus. In production, expose this only on the internal
        # network (it is unauthenticated, like the health checks).
        if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
            # Several uvicorn processes: merge every process's metric files, or
            # each scrape would only see whichever process happened to answer.
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
            return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    configure_tracing(settings, app)
    return app


app = create_app()
