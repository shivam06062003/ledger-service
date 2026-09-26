from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.middleware import request_context_middleware
from app.api.routes import health
from app.core.config import get_settings
from app.core.db import engine
from app.core.logging import configure_logging

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("startup", environment=get_settings().environment)
    yield
    # Close pooled connections cleanly so Postgres isn't left with dangling sessions.
    await engine.dispose()
    logger.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.middleware("http")(request_context_middleware)
    app.include_router(health.router)
    return app


app = create_app()
