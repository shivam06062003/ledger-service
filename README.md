# Ledger Service

A payments ledger API built on **double-entry bookkeeping**. Money is never
created or destroyed, only moved between accounts. Every transfer is atomic
and idempotent, and it stays correct when many transfers run at the same time.

> **Status:** Phase 1 (foundation) complete. See [Roadmap](#roadmap).

## Tech stack

FastAPI · PostgreSQL 17 · async SQLAlchemy 2.0 · Alembic · structlog · pytest · Docker Compose · GitHub Actions

## Quick start

Prerequisites: Docker Desktop, Python 3.12+.

```bash
make up                                    # db + migrations + api
curl localhost:8000/health/ready           # {"status":"ok","database":"ok"}
open http://localhost:8000/docs            # interactive API docs
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

## Project layout

```
app/
  api/            HTTP layer: routes, middleware (no business logic)
  services/       Business rules and transaction boundaries
  repositories/   Database queries
  models/         SQLAlchemy ORM tables
  schemas/        Pydantic request/response models
  core/           Config, logging, DB engine/session
migrations/       Alembic migrations
tests/
docs/adr/         Architecture Decision Records
```

## Operational features

- **Health probes:** `/health/live` (process up) and `/health/ready` (database reachable).
- **Request tracing:** every response carries `X-Request-ID`. An incoming ID is reused, so a request can be followed across services, and it is attached to every log line.
- **Structured logs:** JSON in containers, pretty console output locally.
- **Migrations gate startup:** in Compose, the API only starts after `alembic upgrade head` succeeds.

## Roadmap

- [x] **Phase 1: Foundation.** Docker Compose, Alembic, CI, health probes, structured logging.
- [ ] **Phase 2: Core ledger.** Accounts, double-entry transfers, row locking, concurrency tests.
- [ ] **Phase 3: API hardening.** Idempotency keys, cursor pagination, error envelope, versioning.
- [ ] **Phase 4: Events.** Transactional outbox, relay worker, signed webhooks with retries and a dead-letter queue.
- [ ] **Phase 5: Operability.** Prometheus metrics, OpenTelemetry tracing, rate limiting, load tests, reconciliation.

## Design decisions

See [`docs/adr/`](docs/adr/).
