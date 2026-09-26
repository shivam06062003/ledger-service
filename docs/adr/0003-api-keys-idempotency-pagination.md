# ADR 0003: API keys, idempotency and pagination

- **Status:** Accepted
- **Date:** 2026-09-26

## Context

The ledger is an internal platform service. Its callers are other backend
services (checkout, payouts, refunds), not end users. Those callers need
credentials with limited permissions, a safe way to retry after timeouts, and
a way to read long account histories.

## 1. Authentication: API keys with scopes

Each calling service gets its own key, sent as `Authorization: Bearer ldg_...`.
Keys carry scopes: `accounts:read`, `accounts:write`, `transfers:read`,
`transfers:write` and `admin`. `admin` implies all the others and is the only
scope that can create system accounts (which can mint money) or manage keys.

- **Stored as SHA-256 hashes, not bcrypt.** bcrypt's slowness defends
  low-entropy human passwords against brute force. Our keys are 256 random
  bits, which cannot be brute-forced, and they are verified on every request,
  so a fast hash is correct. It also allows a direct indexed lookup by hash.
  A database leak exposes no usable keys.
- **Shown once.** The plaintext is returned only in the creation response.
- **Revocation** sets `revoked_at` rather than deleting the row, because
  transfers reference the key that initiated them (the audit trail).
- **Bootstrap:** the first admin key is created with a CLI command
  (`python -m app.cli create-api-key`), since the API needs a key to issue keys.
- **Why not JWT?** JWTs suit stateless user sessions across many services.
  For service credentials we want instant revocation and an audit link to a
  database row, so an opaque key checked against the database fits better.
  The cost is one indexed lookup per request; a short-lived cache can remove
  it if it ever shows up in profiles.

## 2. Idempotency keys

A client whose request times out cannot tell whether the transfer happened.
Retrying blindly could move the money twice. `POST /v1/transfers` therefore
**requires** an `Idempotency-Key` header. `POST /v1/accounts` accepts one
optionally.

**Design:** the key is claimed in the **same database transaction** as the
operation.

1. `INSERT ... ON CONFLICT DO NOTHING` reserves the key. If a concurrent
   request with the same key is still in flight, Postgres makes this insert
   **wait** on the unique index until that transaction commits or rolls back.
2. If the key already existed, the stored response is returned with
   `Idempotent-Replayed: true`, provided the request fingerprint matches.
3. Otherwise the transfer runs, and the response is saved on the key row.
4. `COMMIT` makes the key and the transfer durable together.

**Consequences of sharing a transaction:**

- There is no "in progress" state that could get stuck after a crash. A
  committed key always has its response.
- **Failed requests leave no key.** An `insufficient_funds` error rolls
  everything back, so the client can top up and retry with the same key.
  Stripe instead records errors. We chose not to, because a failed transfer
  had no side effects to protect.
- This only works because the operation is entirely inside our database.
  Once we call external systems (Phase 4 webhooks), those calls go through a
  transactional outbox so they keep the same all-or-nothing property.

**Other rules:**

- Keys are scoped per API key, so two services can't collide.
- A key reused with a different request (a different fingerprint of method,
  path and canonical body) is rejected with `idempotency_key_reused`, never
  silently replayed.
- Keys are retained for 24 hours, then removed by
  `python -m app.cli purge-idempotency-keys`.
- Responses are stored as `JSON` rather than `JSONB`, so replays are
  byte-identical.

**Verified:** 20 concurrent identical requests produce exactly one transfer.
With idempotency disabled, the same test moves money 10 times.

## 3. Cursor (keyset) pagination

Account statements return `{"data": [...], "next_cursor": "..."}`. The
cursor encodes the `(created_at, id)` of the last item, and the next page
queries `WHERE (created_at, id) < (cursor)` using the index
`(account_id, created_at, id)`.

- **Why not OFFSET:** `OFFSET 100000` makes Postgres read and discard 100,000
  rows, so deep pages get slower and slower. Rows inserted while a client
  pages also shift offsets, causing skipped or duplicated items. Keyset
  pagination is constant-time per page and stable.
- **`id` as tie-breaker:** entries written in one transaction share a
  `created_at` (Postgres `now()` is the transaction start time).
- **Opaque cursor:** base64-encoded JSON, so its contents can change without
  breaking clients.
- **Known limitation:** `created_at` is the transaction start time, not the
  commit time. A transaction that commits just after a client has paged past
  its timestamp would not appear in that walk. For statements, which are read
  newest-first, this only affects transfers in flight at that exact moment.

## Consequences

- Every write endpoint now depends on authentication and the idempotency
  table. Clients must send keys, which is standard for payment APIs.
- The entries list response changed shape. This is acceptable before any
  external consumers exist; after that, it would require a `/v2` route.
