import asyncio
import uuid
from pathlib import Path

import httpx
from httpx import AsyncClient

from app.core.db import SessionLocal
from app.repositories import webhooks as webhooks_repo
from app.webhooks.dispatcher import backoff_delay, deliver_due, relay_outbox, run_iteration
from app.webhooks.signing import SIGNATURE_HEADER, verify_signature
from app.worker import run
from tests.helpers import create_account, create_funded_account, transfer
from tests.webhook_fakes import (
    FakeReceiver,
    deliveries,
    dispatch_config,
    make_all_deliveries_due,
    subscribe,
)

CONFIG = dispatch_config()


async def setup_accounts(client: AsyncClient) -> tuple[dict[str, str], dict[str, str]]:
    """Two accounts, with setup events already relayed so each test starts
    from an empty outbox and only sees the events it causes."""
    alice = await create_funded_account(client, 10_000)
    bob = await create_account(client)
    while await relay_outbox(CONFIG):
        pass
    return alice, bob


async def test_delivers_signed_event_to_subscriber(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    endpoint = await subscribe(client)
    created = (await transfer(client, alice["id"], bob["id"], 250)).json()
    receiver = FakeReceiver()

    async with receiver.client() as http:
        await run_iteration(http, CONFIG)

    [request] = receiver.requests
    verify_signature(endpoint["secret"], request.headers[SIGNATURE_HEADER], request.content)
    [event] = receiver.events()
    assert event["type"] == "transfer.created"
    assert event["data"] == created
    assert request.headers["Ledger-Event-Id"] == event["id"]
    [delivery] = await deliveries()
    assert delivery.status == "succeeded"
    assert delivery.delivered_at is not None


async def test_endpoints_only_receive_subscribed_event_types(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    await subscribe(client, event_types=("account.created",))
    await transfer(client, alice["id"], bob["id"], 1)
    await create_account(client)
    receiver = FakeReceiver()

    async with receiver.client() as http:
        await run_iteration(http, CONFIG)

    assert [event["type"] for event in receiver.events()] == ["account.created"]


async def test_failed_attempt_schedules_retry_with_backoff(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    await subscribe(client)
    await transfer(client, alice["id"], bob["id"], 1)
    receiver = FakeReceiver(status_code=500)

    async with receiver.client() as http:
        await run_iteration(http, CONFIG)
        # Not due yet, so an immediate second pass must not resend.
        await run_iteration(http, CONFIG)

    assert len(receiver.requests) == 1
    [delivery] = await deliveries()
    assert (delivery.status, delivery.attempts, delivery.last_status_code) == ("pending", 1, 500)
    # First retry: 50-100% of the 10s base delay.
    assert 4 < delivery.seconds_until_next <= 10


async def test_recovers_after_receiver_outage(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    await subscribe(client)
    await transfer(client, alice["id"], bob["id"], 1)
    receiver = FakeReceiver(status_code=503)

    async with receiver.client() as http:
        await run_iteration(http, CONFIG)
        receiver.respond = lambda _request: httpx.Response(200)  # receiver comes back
        await make_all_deliveries_due()
        await run_iteration(http, CONFIG)

    first, second = receiver.requests
    assert first.headers["Ledger-Event-Id"] == second.headers["Ledger-Event-Id"]
    assert [
        first.headers["Ledger-Delivery-Attempt"],
        second.headers["Ledger-Delivery-Attempt"],
    ] == [
        "1",
        "2",
    ]
    [delivery] = await deliveries()
    assert (delivery.status, delivery.attempts) == ("succeeded", 2)


async def test_dead_letters_after_max_attempts_then_manual_retry(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    await subscribe(client)
    await transfer(client, alice["id"], bob["id"], 1)
    receiver = FakeReceiver(status_code=500)

    async with receiver.client() as http:
        for _ in range(CONFIG.max_attempts + 2):
            await run_iteration(http, CONFIG)
            await make_all_deliveries_due()

        assert len(receiver.requests) == CONFIG.max_attempts
        [delivery] = await deliveries()
        assert delivery.status == "failed"

        dead_letters = (await client.get("/v1/webhook-deliveries?status=failed")).json()
        assert [d["id"] for d in dead_letters] == [str(delivery.id)]

        # An operator fixes the receiver and re-queues the dead letter.
        receiver.respond = lambda _request: httpx.Response(200)
        retried = await client.post(f"/v1/webhook-deliveries/{delivery.id}/retry")
        assert retried.status_code == 200
        assert (retried.json()["status"], retried.json()["attempts"]) == ("pending", 0)
        await run_iteration(http, CONFIG)

    [delivery] = await deliveries()
    assert delivery.status == "succeeded"


async def test_network_errors_are_recorded_and_retried(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    await subscribe(client)
    await transfer(client, alice["id"], bob["id"], 1)
    receiver = FakeReceiver()

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    receiver.respond = refuse

    async with receiver.client() as http:
        await run_iteration(http, CONFIG)

    [delivery] = await deliveries()
    assert delivery.status == "pending"
    assert delivery.last_status_code is None
    assert "ConnectError" in delivery.last_error


async def test_redirects_count_as_failures(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    await subscribe(client)
    await transfer(client, alice["id"], bob["id"], 1)
    receiver = FakeReceiver()
    receiver.respond = lambda _request: httpx.Response(
        301, headers={"Location": "https://elsewhere.test/"}
    )

    async with receiver.client() as http:
        await run_iteration(http, CONFIG)

    assert len(receiver.requests) == 1  # the redirect was not followed
    [delivery] = await deliveries()
    assert (delivery.status, delivery.last_status_code) == ("pending", 301)


async def test_concurrent_workers_deliver_each_event_exactly_once(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    await subscribe(client)
    for _ in range(20):
        await transfer(client, alice["id"], bob["id"], 1)
    receiver = FakeReceiver()
    small_batches = dispatch_config(batch_size=3)

    async with receiver.client() as http:
        # Four "worker replicas" racing over the same backlog.
        while sum(await asyncio.gather(*(relay_outbox(small_batches) for _ in range(4)))):
            pass
        while sum(await asyncio.gather(*(deliver_due(http, small_batches) for _ in range(4)))):
            pass

    event_ids = [request.headers["Ledger-Event-Id"] for request in receiver.requests]
    assert len(event_ids) == 20
    assert len(set(event_ids)) == 20
    assert len(await deliveries()) == 20


async def test_crashed_worker_lease_expires_and_delivery_is_retried(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    await subscribe(client)
    await transfer(client, alice["id"], bob["id"], 1)
    await relay_outbox(CONFIG)

    # A worker claims the delivery... and dies before sending it.
    async with SessionLocal() as session, session.begin():
        claimed = await webhooks_repo.claim_due_deliveries(session, limit=10, lease=CONFIG.lease)
    assert len(claimed) == 1

    receiver = FakeReceiver()
    async with receiver.client() as http:
        # While the lease is active, other workers leave it alone.
        assert await deliver_due(http, CONFIG) == 0
        await make_all_deliveries_due()  # lease expires
        assert await deliver_due(http, CONFIG) == 1

    [delivery] = await deliveries()
    # The crashed attempt still counts, so a delivery that kills workers can't
    # retry forever.
    assert (delivery.status, delivery.attempts) == ("succeeded", 2)


async def test_disabled_endpoint_receives_nothing(client: AsyncClient) -> None:
    alice, bob = await setup_accounts(client)
    endpoint = await subscribe(client)
    await transfer(client, alice["id"], bob["id"], 1)
    await relay_outbox(CONFIG)  # delivery row already exists...

    await client.delete(f"/v1/webhook-endpoints/{endpoint['id']}")  # ...then disabled
    await transfer(client, alice["id"], bob["id"], 1)
    receiver = FakeReceiver()
    async with receiver.client() as http:
        await run_iteration(http, CONFIG)

    assert receiver.requests == []


def test_backoff_grows_exponentially_with_jitter_and_cap() -> None:
    def delay(attempts: int, rand: float) -> float:
        return backoff_delay(attempts, base=10, cap=3600, rand=lambda: rand)

    assert (delay(1, 0.0), delay(1, 1.0)) == (5, 10)
    assert delay(4, 1.0) == 80
    assert delay(30, 1.0) == 3600


async def test_worker_loop_heartbeats_and_stops_gracefully(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    stop = asyncio.Event()

    task = asyncio.create_task(
        run(stop, config=CONFIG, poll_interval=0.05, heartbeat_path=heartbeat, http_timeout=1)
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert heartbeat.exists()


async def test_unknown_delivery_retry_returns_404(client: AsyncClient) -> None:
    response = await client.post(f"/v1/webhook-deliveries/{uuid.uuid4()}/retry")

    assert response.status_code == 404
