"""Opaque keyset cursors.

A cursor encodes the (created_at, id) position of the last item on a page.
Clients treat it as an opaque string, which leaves us free to change what's
inside without breaking them.
"""

import base64
import json
import uuid
from datetime import datetime

from app.services.errors import InvalidCursor


def encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "id": str(item_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        created_at = datetime.fromisoformat(data["t"])
        item_id = uuid.UUID(data["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidCursor() from exc
    if created_at.tzinfo is None:
        raise InvalidCursor()
    return created_at, item_id
