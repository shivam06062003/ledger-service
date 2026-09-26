import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

# Point the app at a dedicated test database BEFORE any app module is imported,
# since the engine is created from settings at import time. Env vars take
# precedence over .env, so this also wins over a developer's local config.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://ledger:ledger@localhost:5432/ledger_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import asyncpg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.main import app  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def _create_database_if_missing(url: str) -> None:
    parsed = make_url(url)
    conn = await asyncpg.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.host,
        port=parsed.port,
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", parsed.database
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{parsed.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Tests run against the real migrated schema (constraints, triggers and
    all), not Base.metadata.create_all(), so migrations are tested too."""
    asyncio.run(_create_database_if_missing(TEST_DATABASE_URL))
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
async def clean_tables() -> None:
    # TRUNCATE bypasses the append-only row triggers (they fire on UPDATE/DELETE
    # only), which is exactly what a test reset needs.
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE entries, transfers, accounts"))


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An HTTP client that calls the app in-process — no server or network needed."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
