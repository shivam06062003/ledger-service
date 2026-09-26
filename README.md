# Ledger Service

[![CI](https://github.com/shivam06062003/ledger-service/actions/workflows/ci.yml/badge.svg)](https://github.com/shivam06062003/ledger-service/actions/workflows/ci.yml)

A payments ledger API built on **double-entry bookkeeping**. Money is never
created or destroyed, only moved between accounts. Every transfer is atomic,
and correctness is enforced both in the application and in PostgreSQL itself.

> **Status:** Phase 2 (core ledger) complete. See [Roadmap](#roadmap).

## Highlights

- **No double-spending under concurrency.** Transfers lock both accounts with `SELECT ... FOR UPDATE`, always in ID order to prevent deadlocks. A test fires 50 simultaneous transfers at one account and asserts exactly the affordable number succeed. With the lock removed, the same test shows all 50 succeeding.
- **The database enforces the rules itself:**
  - a `CHECK` constraint blocks overdrafts
  - triggers make ledger rows **append-only**, so history can't be edited
  - a deferred constraint trigger rejects any transfer whose entries don't sum to zero, checked at `COMMIT`
- **Auditable by design:** cached balances for fast reads, immutable entries with `balance_after` for statements, and reconciliation checks that verify the two always agree.
- **Production basics:** Docker Compose, Alembic migrations with round-trip checks in CI, structured JSON logs with request IDs, liveness and readiness probes, strict typing.

## Tech stack

FastAPI · PostgreSQL 17 · async SQLAlchemy 2.0 · Alembic · structlog · pytest · Docker Compose · GitHub Actions

## Quick start

Prerequisites: Docker Desktop, Python 3.12+.

```bash
make up                                    # db + migrations + api
curl localhost:8000/health/ready           # {"status":"ok","database":"ok"}
open http://localhost:8000/docs            # interactive API docs
```

### Try it

```bash
# A system account (money entering the ledger) and a customer wallet
FUNDING=$(curl -s localhost:8000/v1/accounts -H 'content-type: application/json' \
  -d '{"name":"Bank settlement","currency":"INR","allow_negative_balance":true}' | jq -r .id)
ALICE=$(curl -s localhost:8000/v1/accounts -H 'content-type: application/json' \
  -d '{"name":"Alice","currency":"INR"}' | jq -r .id)

# Deposit ₹500.00 (amounts are in paise)
curl -s localhost:8000/v1/transfers -H 'content-type: application/json' \
  -d "{\"source_account_id\":\"$FUNDING\",\"destination_account_id\":\"$ALICE\",\"amount\":50000,\"currency\":\"INR\"}" | jq

curl -s localhost:8000/v1/accounts/$ALICE/entries | jq
```

### Local development (app outside Docker)

```bash
cp .env.example .env
make install        # .venv with app + dev tools
make db             # Postgres only
make migrate
make check          # lint + typecheck + tests (same as CI)
.venv/bin/uvicorn app.main:app --reload
```

Tests use a separate `ledger_test` database, created and migrated automatically.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/accounts` | Create an account |
| `GET` | `/v1/accounts/{id}` | Account with current balance |
| `GET` | `/v1/accounts/{id}/entries` | Statement, newest first, with running balance |
| `POST` | `/v1/transfers` | Move money atomically |
| `GET` | `/v1/transfers/{id}` | Transfer with its debit and credit entries |

Errors use one consistent format:

```json
{"error": {"code": "insufficient_funds", "message": "Account … has insufficient funds"}}
```

## Project layout

```
app/
  api/            HTTP layer: routes, middleware, error mapping (no business logic)
  services/       Business rules and transaction boundaries
  repositories/   Database queries
  models/         SQLAlchemy ORM tables
  schemas/        Pydantic request/response models
  core/           Config, logging, DB engine/session
migrations/       Alembic migrations (including hand-written triggers)
tests/            API, concurrency and database-integrity tests
docs/adr/         Architecture Decision Records
```

## Roadmap

- [x] **Phase 1: Foundation.** Docker Compose, Alembic, CI, health probes, structured logging.
- [x] **Phase 2: Core ledger.** Accounts, double-entry transfers, row locking, database-enforced invariants, concurrency tests.
- [ ] **Phase 3: API hardening.** Idempotency keys, API-key auth with scopes, cursor pagination.
- [ ] **Phase 4: Events.** Transactional outbox, relay worker, signed webhooks with retries and a dead-letter queue.
- [ ] **Phase 5: Operability.** Prometheus metrics, OpenTelemetry tracing, rate limiting, load tests, scheduled reconciliation.

## Design decisions

- [ADR 0001: Foundation stack](docs/adr/0001-foundation-stack.md)
- [ADR 0002: Ledger model and concurrency control](docs/adr/0002-ledger-model-and-concurrency.md)
