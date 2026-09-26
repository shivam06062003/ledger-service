"""Prometheus metrics. Naming follows Prometheus conventions: `_total` for
counters, base units (seconds), and low-cardinality labels only.

Cardinality matters: every distinct label combination is a separate time
series held in memory. Labelling requests by raw path (/v1/accounts/<uuid>)
would create one series per account and eventually take Prometheus down, so
routes are labelled by their template (/v1/accounts/{account_id}).
"""

from prometheus_client import Counter, Gauge, Histogram

# --- API ---------------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
DOMAIN_ERRORS = Counter(
    "ledger_domain_errors_total", "Requests rejected by a business rule", ["code"]
)
TRANSFERS = Counter("ledger_transfers_total", "Committed transfers", ["currency"])
TRANSFER_VOLUME = Counter(
    "ledger_transfer_volume_minor_units_total",
    "Sum of committed transfer amounts, in minor units",
    ["currency"],
)
IDEMPOTENT_REPLAYS = Counter(
    "ledger_idempotent_replays_total", "Requests answered from a stored idempotent response"
)
RATE_LIMITED = Counter("ledger_rate_limited_total", "Requests rejected by the rate limiter")
RATE_LIMITER_ERRORS = Counter(
    "ledger_rate_limiter_errors_total",
    "Rate limiter backend failures (the request was allowed through: fail-open)",
)

# --- Worker ------------------------------------------------------------------
OUTBOX_RELAYED = Counter("ledger_outbox_events_relayed_total", "Outbox events fanned out")
WEBHOOK_ATTEMPTS = Counter(
    "ledger_webhook_delivery_attempts_total",
    "Webhook delivery attempts by outcome",
    ["outcome"],  # succeeded | retry_scheduled | dead_lettered
)
WEBHOOK_DURATION = Histogram(
    "ledger_webhook_delivery_duration_seconds",
    "Time for a subscriber to respond to a webhook",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
# Queue depth gauges, refreshed by every worker replica from the database. All
# replicas report the same value, so dashboards aggregate them with max().
OUTBOX_BACKLOG = Gauge("ledger_outbox_unpublished_events", "Events waiting to be relayed")
DELIVERIES_PENDING = Gauge("ledger_webhook_deliveries_pending", "Deliveries awaiting (re)try")
DELIVERIES_DEAD_LETTERED = Gauge(
    "ledger_webhook_deliveries_dead_lettered", "Deliveries that exhausted their retries"
)
OLDEST_PENDING_DELIVERY_AGE = Gauge(
    "ledger_webhook_oldest_pending_delivery_age_seconds",
    "Age of the oldest pending delivery (how far behind webhooks are)",
)
RECONCILIATION_DISCREPANCIES = Gauge(
    "ledger_reconciliation_discrepancies",
    "Problems found by the last reconciliation (should always be 0)",
    ["check"],
)
RECONCILIATION_LAST_RUN = Gauge(
    "ledger_reconciliation_last_run_timestamp_seconds", "When reconciliation last completed"
)
JOB_RUNS = Counter(
    "ledger_job_runs_total", "Scheduled job executions", ["job", "outcome"]
)  # outcome: succeeded | failed
