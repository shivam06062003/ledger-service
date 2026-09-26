import logging

import structlog


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
