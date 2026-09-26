import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.schemas.api_key import Scope
from app.webhooks.dispatcher import run_iteration
from tests.conftest import ClientFactory
from tests.helpers import create_account, create_funded_account, transfer
from tests.webhook_fakes import FakeReceiver, dispatch_config, subscribe


async def test_create_endpoint_returns_secret_once(client: AsyncClient) -> None:
    created = await subscribe(client, event_types=("transfer.created", "account.created"))

    assert created["secret"].startswith("whsec_")
    assert created["event_types"] == ["account.created", "transfer.created"]
    listed = (await client.get("/v1/webhook-endpoints")).json()
    assert [e["id"] for e in listed] == [created["id"]]
    assert "secret" not in listed[0]


async def test_webhook_management_requires_admin(make_client: ClientFactory) -> None:
    service = await make_client(Scope.TRANSFERS_WRITE, Scope.ACCOUNTS_WRITE)

    response = await service.post(
        "/v1/webhook-endpoints",
        json={"url": "https://x.test/hook", "event_types": ["transfer.created"]},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"url": "not a url", "event_types": ["transfer.created"]},
        {"url": "ftp://x.test/hook", "event_types": ["transfer.created"]},
        {"url": "https://x.test/hook", "event_types": []},
        {"url": "https://x.test/hook", "event_types": ["transfer.deleted"]},
    ],
)
async def test_rejects_invalid_endpoint(client: AsyncClient, payload: dict[str, object]) -> None:
    response = await client.post("/v1/webhook-endpoints", json=payload)

    assert response.status_code == 422


async def test_production_requires_https(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.webhooks.get_settings", lambda: Settings(environment="production")
    )

    response = await client.post(
        "/v1/webhook-endpoints",
        json={"url": "http://x.test/hook", "event_types": ["transfer.created"]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insecure_webhook_url"


async def test_disable_endpoint(client: AsyncClient) -> None:
    endpoint = await subscribe(client)

    response = await client.delete(f"/v1/webhook-endpoints/{endpoint['id']}")

    assert response.status_code == 204
    [listed] = (await client.get("/v1/webhook-endpoints")).json()
    assert listed["enabled"] is False


async def test_disable_unknown_endpoint_returns_404(client: AsyncClient) -> None:
    response = await client.delete("/v1/webhook-endpoints/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


async def test_only_failed_deliveries_can_be_retried(client: AsyncClient) -> None:
    await subscribe(client)
    alice = await create_funded_account(client, 100)
    bob = await create_account(client)
    await transfer(client, alice["id"], bob["id"], 1)
    receiver = FakeReceiver()
    async with receiver.client() as http:
        await run_iteration(http, dispatch_config())

    [delivery, *_] = (await client.get("/v1/webhook-deliveries?status=succeeded")).json()
    response = await client.post(f"/v1/webhook-deliveries/{delivery['id']}/retry")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "delivery_not_retryable"
