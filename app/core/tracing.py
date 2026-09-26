"""OpenTelemetry tracing.

A trace follows one request through every component it touches. The unusual
part here is crossing the async boundary: when a transfer writes its outbox
event, the current trace context (a W3C `traceparent` string) is stored on the
event row. The worker restores it when delivering the webhook, so the delivery,
seconds later and in another process, appears in the SAME trace as the
original API request, and the traceparent header is forwarded to the
subscriber too.
"""

from collections.abc import Sequence

from fastapi import FastAPI
from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import Decision, ParentBased, Sampler, SamplingResult
from opentelemetry.trace import Link, SpanKind
from opentelemetry.util.types import Attributes

from app.core.config import Settings

tracer = trace.get_tracer("ledger")


class EntryPointSpansOnly(Sampler):
    """Start new traces only at real entry points: incoming requests (SERVER)
    and processed messages (CONSUMER).

    Without this, instrumented background activity (the worker polling the
    database every second, health-check queries) would start a new root trace
    for every query, burying useful traces in noise. Wrapped in ParentBased, a
    span with a parent simply follows the parent's decision.
    """

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: trace.TraceState | None = None,
    ) -> SamplingResult:
        if kind in (SpanKind.SERVER, SpanKind.CONSUMER):
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, trace_state)
        return SamplingResult(Decision.DROP, None, trace_state)

    def get_description(self) -> str:
        return "EntryPointSpansOnly"


SAMPLER = ParentBased(root=EntryPointSpansOnly())


def configure_tracing(settings: Settings, app: FastAPI | None = None) -> None:
    """Install the SDK and auto-instrumentation. A no-op unless OTEL_ENABLED:
    the API then uses OpenTelemetry's built-in no-op tracer, costing nothing."""
    if not settings.otel_enabled:
        return

    # Imported lazily so tests and local runs don't pay for them.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from app.core.db import engine

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name}),
        sampler=SAMPLER,
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    HTTPXClientInstrumentor().instrument()
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*,metrics")


def current_trace_context() -> dict[str, str]:
    """The active trace context as a carrier dict (empty if nothing is traced)."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def context_from(carrier: dict[str, str] | None) -> Context:
    return propagate.extract(carrier or {})
