"""A fake webhook receiver: httpx.MockTransport routes the dispatcher's HTTP
calls to a Python function instead of the network, so tests can simulate
outages, timeouts and recoveries deterministically."""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import text

from app.core.db import engine
from app.webhooks.dispatcher import DispatchConfig

Responder = Callable[[httpx.Request], httpx.Response]


class FakeReceiver:
    def __init__(self, status_code: int = 204) -> None:
        self.requests: list[httpx.Request] = []
        self.respond: Responder = lambda _request: httpx.Response(status_code)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def dispatch_config(**overrides: Any) -> DispatchConfig:
    defaults: dict[str, Any] = {
        "batch_size": 50,
        "max_attempts": 3,
        "lease": timedelta(seconds=60),
        "backoff_base_seconds": 10.0,
        "backoff_cap_seconds": 3600.0,
    }
    return DispatchConfig(**{**defaults, **overrides})


async def subscribe(
    client: Any,
    event_types: tuple[str, ...] = ("transfer.created",),
    url: str = "https://receiver.test/webhooks",
) -> dict[str, Any]:
    response = await client.post(
        "/v1/webhook-endpoints", json={"url": url, "event_types": list(event_types)}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def make_all_deliveries_due() -> None:
    """Fast-forward time: pretend every backoff delay / lease has elapsed."""
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE webhook_deliveries SET next_attempt_at = now()"))


async def deliveries() -> list[Any]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT id, status, attempts, last_status_code, last_error, delivered_at, "
                "next_attempt_at > now() AS scheduled_in_future, "
                "EXTRACT(EPOCH FROM next_attempt_at - now()) AS seconds_until_next "
                "FROM webhook_deliveries ORDER BY created_at"
            )
        )
        return list(result.all())
