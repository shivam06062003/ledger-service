from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.tracing import SAMPLER
from app.models import OutboxEvent
from app.webhooks.dispatcher import run_iteration
from tests.helpers import create_account, create_funded_account, transfer
from tests.webhook_fakes import FakeReceiver, dispatch_config, subscribe

_exporter = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def tracer_provider() -> None:
    # The global provider can only be set once per process; the in-memory
    # exporter is cleared per test instead.
    provider = TracerProvider(sampler=SAMPLER)
    provider.add_span_processor(SimpleSpanProcessor(_exporter))
    trace.set_tracer_provider(provider)


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    _exporter.clear()
    yield _exporter


async def test_trace_crosses_the_outbox_into_the_webhook_delivery(
    client: AsyncClient, spans: InMemorySpanExporter
) -> None:
    await subscribe(client)
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    tracer = trace.get_tracer("test")

    # Stand-in for the FastAPI server span that instrumentation creates in production.
    with tracer.start_as_current_span("POST /v1/transfers", kind=SpanKind.SERVER) as request_span:
        created = (await transfer(client, alice["id"], bob["id"], 10)).json()
    trace_id = format(request_span.get_span_context().trace_id, "032x")

    # 1. The outbox row carries the request's trace context.
    async with SessionLocal() as session:
        events = (await session.scalars(select(OutboxEvent))).all()
    [event] = [e for e in events if e.payload.get("id") == created["id"]]
    assert event.trace_context is not None
    assert trace_id in event.trace_context["traceparent"]

    # 2. The worker's delivery joins that trace and forwards it to the subscriber.
    receiver = FakeReceiver()
    async with receiver.client() as http:
        await run_iteration(http, dispatch_config())
    [request] = [r for r in receiver.requests if created["id"].encode() in r.content]
    assert trace_id in request.headers["traceparent"]

    [delivery_span] = [
        s
        for s in spans.get_finished_spans()
        if s.attributes and s.attributes.get("ledger.event_id") == str(event.id)
    ]
    assert delivery_span.kind == SpanKind.CONSUMER
    assert format(delivery_span.context.trace_id, "032x") == trace_id
    assert delivery_span.attributes is not None
    assert delivery_span.attributes["http.response.status_code"] == 204
    assert delivery_span.name == "webhook deliver transfer.created"


async def test_untraced_requests_store_no_trace_context(client: AsyncClient) -> None:
    account = await create_account(client)

    async with SessionLocal() as session:
        events = (await session.scalars(select(OutboxEvent))).all()
    [event] = [e for e in events if e.payload["id"] == account["id"]]
    assert event.trace_context is None


def test_sampler_starts_traces_only_at_entry_points(spans: InMemorySpanExporter) -> None:
    tracer = trace.get_tracer("test")

    with tracer.start_as_current_span("SELECT 1 (background poll)", kind=SpanKind.CLIENT):
        pass
    with (
        tracer.start_as_current_span("GET /v1/accounts", kind=SpanKind.SERVER),
        tracer.start_as_current_span("SELECT accounts", kind=SpanKind.CLIENT),
    ):
        pass

    assert sorted(s.name for s in spans.get_finished_spans()) == [
        "GET /v1/accounts",
        "SELECT accounts",
    ]
