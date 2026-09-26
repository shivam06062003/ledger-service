from collections.abc import AsyncIterator

from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from app.core.db import get_session
from app.main import app


async def test_live_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_checks_database(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_ready_returns_503_when_database_is_down(client: AsyncClient) -> None:
    class BrokenSession:
        async def execute(self, *_: object) -> None:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    async def broken_session() -> AsyncIterator[BrokenSession]:
        yield BrokenSession()

    app.dependency_overrides[get_session] = broken_session

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


async def test_generates_request_id_when_missing(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert len(response.headers["X-Request-ID"]) == 32


async def test_propagates_valid_incoming_request_id(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "upstream-abc-123"})

    assert response.headers["X-Request-ID"] == "upstream-abc-123"


async def test_replaces_malformed_incoming_request_id(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "bad id\twith junk"})

    assert response.headers["X-Request-ID"] != "bad id\twith junk"
    assert len(response.headers["X-Request-ID"]) == 32
