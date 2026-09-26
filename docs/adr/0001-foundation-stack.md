# ADR 0001: Foundation stack

- **Status:** Accepted
- **Date:** 2026-09-26

## Context

The service will move money between accounts. That puts correctness under
concurrency above everything else: two simultaneous transfers must never
overspend a balance, and a crash must never leave a half-written transfer.
The stack has to support this and still be simple to run locally.

## Decisions

1. **PostgreSQL, not SQLite.** We need real row-level locking
   (`SELECT ... FOR UPDATE`), configurable transaction isolation, and many
   concurrent writers. SQLite locks the entire database on write.
2. **Async SQLAlchemy 2.0 + asyncpg.** The API mostly waits on I/O (database,
   later webhooks). Async lets one process handle many in-flight requests.
   The cost is more care around sessions and lazy loading.
3. **Alembic for schema changes.** Every schema change is a versioned,
   reviewable, reversible migration. We never run `create_all()` against a
   real database.
4. **Layered structure: `api` → `services` → `repositories` → `models`.**
   Routes handle HTTP only. Business rules (e.g. "a transfer must balance")
   live in services, and services own transaction boundaries. This makes the
   rules testable without HTTP and keeps SQL out of route handlers.
5. **Structured JSON logs (structlog) with a request ID on every line.**
   Logs are meant for machines to query ("show every line for request X"),
   not for grep.
6. **Separate liveness and readiness probes.** A database outage should take
   instances out of the load balancer (readiness) without triggering restart
   loops (liveness).

## Consequences

- Local development requires Docker for Postgres. Docker Compose makes this a
  single command.
- Async code requires discipline: no blocking calls in request paths. The
  ruff `ASYNC` rules catch common mistakes.
