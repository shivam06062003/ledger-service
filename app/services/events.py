from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tracing import current_trace_context
from app.models import OutboxEvent
from app.repositories import outbox as outbox_repo
from app.schemas.events import EventType


def record(session: AsyncSession, event_type: EventType, data: BaseModel) -> None:
    """Add a domain event to the outbox. Must be called inside the transaction
    that makes the change, so the event commits (or rolls back) with it."""
    outbox_repo.add(
        session,
        OutboxEvent(
            event_type=event_type.value,
            payload=data.model_dump(mode="json"),
            trace_context=current_trace_context() or None,
        ),
    )
