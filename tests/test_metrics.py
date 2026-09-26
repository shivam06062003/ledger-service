import uuid

from httpx import AsyncClient
from prometheus_client import REGISTRY

from app.jobs import refresh_queue_metrics
from app.webhooks.dispatcher import run_iteration
from tests.helpers import create_account, create_funded_account, transfer
from tests.webhook_fakes import FakeReceiver, dispatch_config, subscribe


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_metrics_endpoint_serves_prometheus_format(client: AsyncClient) -> None:
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in response.text


async def test_requests_are_labelled_by_route_template_not_raw_path(client: AsyncClient) -> None:
    account = await create_account(client)
    labels = {"method": "GET", "route": "/v1/accounts/{account_id}", "status": "200"}
    before = sample("http_requests_total", **labels)

    await client.get(f"/v1/accounts/{account['id']}")
    await client.get(f"/v1/accounts/{account['id']}")

    assert sample("http_requests_total", **labels) == before + 2
    # No series was created for the concrete path (that would explode cardinality).
    assert account["id"] not in (await client.get("/metrics")).text


async def test_unmatched_paths_share_one_label(client: AsyncClient) -> None:
    labels = {"method": "GET", "route": "unmatched", "status": "404"}
    before = sample("http_requests_total", **labels)

    await client.get(f"/wp-admin/{uuid.uuid4()}")

    assert sample("http_requests_total", **labels) == before + 1


async def test_business_metrics(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    transfers_before = sample("ledger_transfers_total", currency="INR")
    volume_before = sample("ledger_transfer_volume_minor_units_total", currency="INR")
    rejections_before = sample("ledger_domain_errors_total", code="insufficient_funds")
    replays_before = sample("ledger_idempotent_replays_total")

    await transfer(client, alice["id"], bob["id"], 300, idempotency_key="m1")
    await transfer(client, alice["id"], bob["id"], 300, idempotency_key="m1")  # replay
    await transfer(client, alice["id"], bob["id"], 5_000)  # insufficient funds

    assert sample("ledger_transfers_total", currency="INR") == transfers_before + 1
    assert sample("ledger_transfer_volume_minor_units_total", currency="INR") == volume_before + 300
    assert sample("ledger_idempotent_replays_total") == replays_before + 1
    assert sample("ledger_domain_errors_total", code="insufficient_funds") == rejections_before + 1


async def test_worker_metrics_and_queue_gauges(client: AsyncClient) -> None:
    await subscribe(client)
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    await transfer(client, alice["id"], bob["id"], 1)

    await refresh_queue_metrics()
    assert sample("ledger_outbox_unpublished_events") >= 1

    delivered_before = sample("ledger_webhook_delivery_attempts_total", outcome="succeeded")
    receiver = FakeReceiver()
    async with receiver.client() as http:
        await run_iteration(http, dispatch_config())
    await refresh_queue_metrics()

    assert sample("ledger_webhook_delivery_attempts_total", outcome="succeeded") == (
        delivered_before + len(receiver.requests)
    )
    assert sample("ledger_outbox_unpublished_events") == 0
    assert sample("ledger_webhook_deliveries_pending") == 0


async def test_status_label_is_the_numeric_code(client: AsyncClient) -> None:
    labels = {"method": "POST", "route": "/v1/accounts", "status": "201"}
    before = sample("http_requests_total", **labels)

    await create_account(client)

    assert sample("http_requests_total", **labels) == before + 1
