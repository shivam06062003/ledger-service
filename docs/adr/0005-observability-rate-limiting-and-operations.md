# ADR 0005: Observability, rate limiting and operations

- **Status:** Accepted
- **Date:** 2026-09-26

## Context

A payments service has to answer four questions in production:

1. Is it healthy?
2. Where does the time go?
3. Is anyone overwhelming it?
4. Is the money still right?

It also needs periodic housekeeping, and a known performance envelope.

## 1. Metrics (Prometheus)

- The API exposes `/metrics`. The worker serves its own on port 9100, since
  it has no web framework.
- **Cardinality control.** Requests are labelled by **route template**
  (`/v1/accounts/{account_id}`), never the raw path. Raw paths would create a
  time series per account and eventually exhaust Prometheus memory. Unmatched
  paths, such as scanner 404s, share one `unmatched` label. Tests assert that
  no concrete ID appears in `/metrics`.
- **Business metrics** sit alongside the technical ones: committed transfers
  and volume per currency, rejections by error code, idempotent replays,
  rate-limited requests, webhook outcomes, queue depth, the age of the oldest
  pending webhook, and reconciliation discrepancies.
- **Multiprocess mode.** The API runs several uvicorn processes, and each
  would otherwise report only its own counters. `PROMETHEUS_MULTIPROC_DIR`
  makes every process write to shared files that `/metrics` merges.
  `serve.sh` clears the directory on start so restarts don't double-count.
- **Worker gauges** (queue depth) are refreshed by every replica from the
  database. They all report the same value, so dashboards aggregate with
  `max()`, not `sum()`.
- **Alert rules** (`observability/prometheus/alerts.yml`, validated in CI with
  `promtool`) cover reconciliation discrepancies, stale reconciliation, 5xx
  rate, slow transfers, webhooks falling behind, dead letters, and the rate
  limiter being down.

## 2. Tracing (OpenTelemetry → Jaeger)

- FastAPI, SQLAlchemy and httpx are auto-instrumented, and spans are exported
  over OTLP/HTTP. Tracing is off unless `OTEL_ENABLED=true`, in which case
  OpenTelemetry's no-op tracer costs nothing.
- **Traces cross the async boundary.** When a service writes an outbox event,
  it stores the current W3C `traceparent` on the row. The worker restores it,
  so a webhook delivery seconds later, in another process, appears in the
  **same trace** as the API request that caused it. The worker also forwards
  `traceparent` to the subscriber.
- **Sampler.** `ParentBased(EntryPointSpansOnly)` starts traces only at
  SERVER and CONSUMER spans. Without it, the worker's once-per-second polling
  queries and health-check queries would each start a root trace and bury
  real traffic.
- **Log correlation.** Every log line written inside a span carries
  `trace_id` and `span_id`, so you can jump from a log line to its trace.
- **Gotcha found while verifying this:** the SQLAlchemy instrumentation
  silently did nothing on SQLAlchemy 2.1, which it doesn't support yet
  (visible only as a warning). SQLAlchemy is pinned to `<2.1` with a comment.
  Checking traces in Jaeger is what caught it; unit tests could not have.

## 3. Rate limiting (token bucket in Redis)

- Each API key has a bucket of `burst` tokens, refilled at `rate` per second.
  The defaults are 100 and 50/s. This allows short bursts and caps the
  sustained rate.
- **Atomicity.** The read, refill, spend and write steps run as **one Lua
  script**, which Redis executes atomically. Separate GET and SET calls from
  several API processes would race. A test fires 50 concurrent hits at a
  burst of 5 and gets exactly 5 through.
- **Redis's clock** (`TIME`) is used rather than each API host's clock, so
  replicas with clock skew can't disagree about refills.
- **Fail-open.** If Redis is down or slow (250 ms timeout), requests are
  allowed through and `ledger_rate_limiter_errors_total` fires an alert. A
  rate-limiter outage should not become a payments outage. A public API
  facing abuse might choose fail-closed instead.
- **Applied per key, after authentication**, so one noisy client can't starve
  the others. Floods of unauthenticated requests belong at the edge (load
  balancer or WAF).
- 429 responses carry `Retry-After`. A known gap: `RateLimit-Remaining` is not
  sent on successful responses, because idempotent routes return their own
  response objects.

## 4. Reconciliation

The scheduled job, and `python -m app.cli reconcile` (which exits with code 1
on any problem), re-verify three invariants:

- all entries sum to zero
- each transfer sums to zero
- each account's cached balance equals the sum of its entries

The three queries run in one **`REPEATABLE READ` snapshot**. Under the default
`READ COMMITTED`, a transfer committing between "sum the entries" and "read
the balances" would appear as a false discrepancy. Tests use the same code
(`assert_ledger_consistent`), and a test corrupts a balance to prove it is
detected.

## 5. Scheduled jobs

The worker runs reconciliation (every 5 min) and retention (hourly). To run
each job **once per interval across all replicas**, a job is claimed with one
atomic statement:

```sql
UPDATE scheduled_jobs SET last_started_at = now()
WHERE name = :job AND (last_started_at IS NULL OR last_started_at <= now() - :interval)
RETURNING name
```

Only one replica gets the row back. No lock is held while the job runs, and
after a crash the job simply runs at the next interval. Advisory locks were
considered: they stop two runs *overlapping*, but they don't stop every
replica running the job once per interval. Kubernetes CronJobs are the
production alternative. This approach needs no extra infrastructure.

**Retention** deletes succeeded deliveries, and events with no remaining
deliveries, after 30 days, **in batches** of 5,000 per transaction. That
keeps locks and WAL bursts small. Pending and dead-lettered data is never
deleted.

## 6. Load test results

These come from k6 (`make loadtest`) on an **Apple M1 laptop**, with Docker
limited to 8 vCPUs and 4 GB RAM. Postgres, Redis, 4 API processes, the
worker, the demo webhook receiver *and the load generator itself* all share
those cores. Tracing and rate limiting were off. Every transfer also produced
a webhook, delivered live.

| Scenario | Throughput | Latency | Errors |
|---|---|---|---|
| Spread, max throughput (50 users) | **212–243 transfers/s** (3 runs) | p95 511–795 ms (saturated: queueing) | 0 |
| Spread, fixed 100/s | 100/s sustained | **p50 ≈ 10 ms**, p95 366–674 ms | 0 |
| Hot account, max throughput (50 users) | **~112 transfers/s** | p95 ≈ 1.2 s (saturated) | 0 |
| Hot account, fixed 60/s | 60/s sustained | **p95 188 ms** | 0 |

**Correctness under load:** after about 80,000 transfers across these runs,
reconciliation reports **zero discrepancies**.

**Investigation.**

- With one API process, throughput was ~196/s and the API container was at
  101% CPU (one core), while Postgres was at 57%: CPU-bound in Python. Moving
  to 4 processes raised throughput only to ~230/s, because the whole 8-core VM
  became the bottleneck (API 345%, Postgres 167%, plus worker and k6).
- The long tail (p50 10 ms vs p95 400 ms+) was investigated one hypothesis
  at a time:
  1. *Commit fsync on Docker's virtual disk:* disabling `synchronous_commit`
     made no difference, so this was **rejected**.
  2. *Pool churn* (overflow connections closed on return): a fixed pool of 20
     halved new connections (214 → 104), but latency didn't improve, so this
     was **rejected**.
  3. *Tracing the slow requests:* 83% of their time was inside database
     calls, and in 159 of 200 slow traces the biggest span was `connect`,
     i.e. **waiting for a pooled connection**. Connections were held "idle in
     transaction" while their event loop waited for CPU. This is **CPU
     starvation on a shared laptop, showing up as pool queueing**.
- The hot account is capped by design (ADR 0002): its row lock serializes
  transfers, so throughput ≈ 1 ÷ lock hold time. Lock hold time includes the
  Python work between SQL statements.

**What would move the numbers**, in order of expected impact:

1. Run the load generator on a separate machine. Right now it competes for
   the same CPUs.
2. Reduce database round trips per transfer. There are currently about 10,
   plus a separate API-key lookup. For example, cache API-key lookups for a
   few seconds, and post the transfer in one SQL statement or stored
   procedure. This also shortens row-lock hold time, which directly raises
   hot-account throughput.
3. Scale API replicas horizontally behind a load balancer, with PgBouncer in
   front of Postgres.
4. For extreme hot accounts, split the account into sub-accounts or batch
   postings. This is a domain decision.

## Consequences

- Redis is now a runtime dependency. Losing it degrades rate limiting only.
- The observability stack is optional (`make observability`), so plain
  `make up` stays light.
- These numbers are a laptop baseline, not a capacity plan. The method
  (fixed-rate latency runs, saturation runs, and a hypothesis log backed by
  traces) is the part that carries over.
