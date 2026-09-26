# ADR 0004: Transactional outbox and webhook delivery

- **Status:** Accepted
- **Date:** 2026-09-26

## Context

Other services need to react to ledger changes. For example, checkout marks
an order paid when its transfer lands. We notify them with webhooks. Two
things make this hard:

1. **Dual writes.** "Commit the transfer, then send the webhook" loses the
   event if the process crashes in between. "Send, then commit" announces
   transfers that may roll back.
2. **Unreliable receivers.** Subscribers go down, time out and redeploy. We
   must retry without hammering them or retrying forever.

## Decision

### Transactional outbox

Services write an `outbox_events` row **in the same transaction** as the
change (`app/services/events.py`). The event is durable if and only if the
transfer committed. Idempotent replays return before the operation runs, so
they emit nothing.

A separate **worker process** (`python -m app.worker`) publishes events
asynchronously. The API never makes outbound HTTP calls, so a slow subscriber
cannot slow down transfers.

### Worker pipeline

1. **Relay (fan-out):** lock a batch of unpublished events with
   `FOR UPDATE SKIP LOCKED`, insert one `webhook_deliveries` row per subscribed,
   enabled endpoint, and mark the events published, all in one transaction.
   A unique constraint on `(event_id, endpoint_id)` backs this up.
2. **Deliver:** lease due deliveries (see below), POST them with a signature,
   then record the outcome.

Both steps are safe with any number of worker replicas. `SKIP LOCKED` lets
replicas take disjoint batches instead of queueing behind each other.
**Verified:** with `SKIP LOCKED` removed from the delivery claim, four
concurrent workers deadlock (each locks rows the others need). With it
removed from the outbox claim, the unique constraint rejects the duplicate
fan-out.

### Leases instead of long transactions

Claiming a delivery pushes its `next_attempt_at` forward by a **lease** (60 s)
and commits. The HTTP call then runs with **no transaction open**. Holding
row locks during network I/O would tie up database connections for up to
the HTTP timeout and block other workers. If a worker dies mid-delivery, the
lease expires and another worker retries. This is the same model as an SQS
visibility timeout. The lease must be longer than the HTTP timeout (10 s).

`attempts` is incremented **at claim time**. A payload that crashes the
worker still uses up its retry budget, so it cannot loop forever.

### Retries, backoff, dead letters

- Any non-2xx response, timeout or connection error is a failure. Redirects
  are not followed, since following one could send signed data to a host the
  subscriber never registered.
- Retry delay: `min(cap, base × 2^(attempt−1))` with jitter (a random 50–100%
  of that value). The defaults are base 10 s and cap 1 h, which gives roughly
  10 s, 20 s, 40 s and so on. Without jitter, every delivery that failed
  during an outage would retry at the same moments, hitting the recovering
  receiver with synchronized traffic spikes.
- After `webhook_max_attempts` (10), the delivery is marked `failed`. These
  rows are the **dead-letter queue**. Operators list them with
  `GET /v1/webhook-deliveries?status=failed` and re-queue them with
  `POST /v1/webhook-deliveries/{id}/retry` once the receiver is fixed.

### Delivery guarantee: at-least-once

A worker can crash after the receiver processed an event but before
recording success. Exactly-once delivery over HTTP is impossible, so we
guarantee **at-least-once**, and receivers deduplicate on the event ID (in
the body and the `Ledger-Event-Id` header). Ordering is **not** guaranteed:
retries and parallel workers can reorder events. Receivers should rely on the
data, e.g. `balance_after`, rather than on arrival order.
`examples/webhook_receiver.py` shows the receiving side.

### Signatures

`Ledger-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>`,
the same scheme Stripe uses.

- The HMAC proves authenticity and integrity.
- The signed timestamp lets receivers reject replayed requests older than
  5 minutes.
- Verification uses a constant-time comparison and accepts several `v1`
  values, to allow secret rotation.
- Each endpoint has its own secret (`whsec_...`), shown once at creation. It
  is stored recoverably because we need it to sign. In production it would be
  encrypted with a KMS-managed key.
- Production requires `https` URLs.

## Alternatives considered

| Option | Why not (for now) |
|---|---|
| Send the webhook inside the request | Couples API latency and availability to subscribers, and is a dual write. |
| Celery/RQ task enqueued after commit | Still a dual write: a crash between commit and enqueue loses the event. Also adds a broker to run. The outbox needs only Postgres. |
| Kafka, with Debezium reading the database log (CDC) | The right choice at larger scale or with many consumers. Heavy to run for one service, and our outbox table can feed a CDC pipeline later without changing the write path. |
| `LISTEN/NOTIFY` instead of polling | Cuts latency below the 1 s poll interval. It could be added as a wake-up signal, with polling kept as the safety net. Not needed yet. |

## Consequences

- Webhooks arrive asynchronously, typically within about a second.
- Receivers must verify signatures, deduplicate, and tolerate reordering.
- Outbox and delivery rows grow without limit. A retention job (like the
  idempotency-key purge) belongs in Phase 5, alongside metrics for queue
  depth, delivery latency and dead-letter count.
- SSRF: an admin could register an internal URL. Endpoint management is
  admin-only. Blocking private IP ranges at delivery time is future work
  before any self-service subscription.
