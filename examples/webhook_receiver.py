"""Example webhook consumer: how a subscribing service should receive events.

Run by `docker compose --profile demo up webhook-receiver`. It shows the three
things every receiver must do:

1. Verify the signature against the RAW body, before parsing it.
2. Deduplicate on the event ID: delivery is at-least-once.
3. Respond 2xx quickly, and do slow work asynchronously. A slow response times
   out, and the event is retried even though it arrived.
"""

import json
import os

from fastapi import FastAPI, Request, Response

from app.webhooks.signing import SIGNATURE_HEADER, InvalidSignatureError, verify_signature

app = FastAPI(title="Example webhook receiver")
SECRET = os.environ.get("WEBHOOK_SECRET", "")
# In a real service this is a database table with a unique constraint.
processed_event_ids: set[str] = set()


def log(message: str, **fields: object) -> None:
    print(json.dumps({"event": message, **fields}), flush=True)


@app.post("/webhooks/ledger")
async def receive(request: Request) -> Response:
    body = await request.body()

    if SECRET:
        try:
            verify_signature(SECRET, request.headers.get(SIGNATURE_HEADER, ""), body)
        except InvalidSignatureError as exc:
            log("rejected_invalid_signature", reason=str(exc))
            return Response(status_code=400)
    else:
        log("warning_signature_not_verified", hint="set WEBHOOK_SECRET")

    event = json.loads(body)
    if event["id"] in processed_event_ids:
        log("duplicate_ignored", event_id=event["id"])
        return Response(status_code=200)
    processed_event_ids.add(event["id"])

    log(
        "event_received",
        event_id=event["id"],
        type=event["type"],
        verified=bool(SECRET),
        attempt=request.headers.get("Ledger-Delivery-Attempt"),
    )
    return Response(status_code=204)
