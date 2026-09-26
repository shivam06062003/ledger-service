import logging

import structlog
from opentelemetry import trace
from structlog.types import EventDict, WrappedLogger


def add_trace_context(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Stamp log lines with the active trace/span ID, so you can jump from a
    log line straight to its trace in Jaeger, and vice versa."""
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")
    return event_dict


def configure_logging(level: str, json: bool) -> None:
    """Configure structlog for the whole process.

    JSON output is for production (log aggregators parse it); the console renderer
    is for humans reading logs locally. Anything bound with
    `structlog.contextvars.bind_contextvars` (e.g. request_id) is added to every line.
    """
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_trace_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )
