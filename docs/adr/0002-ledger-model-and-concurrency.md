# ADR 0002: Ledger model and concurrency control

- **Status:** Accepted
- **Date:** 2026-09-26

## Context

A transfer must be atomic (all or nothing) and must never overdraw an account,
even when many transfers hit the same account at the same moment. The data
must also stay auditable: we need to be able to prove where every unit of
money came from.

## Decisions

### 1. Money is stored as integer minor units (`BIGINT`)

₹12.34 is stored as `1234`. Floats cannot represent most decimal fractions
exactly (`0.1 + 0.2 != 0.3`), and rounding errors in a ledger are
unacceptable. `NUMERIC` would also work, but integers are faster, map cleanly
to Python `int`, and make "no fractional paise" impossible to get wrong.

### 2. Double-entry: transfers are made of entries that sum to zero

Each transfer writes one debit entry (negative) and one credit entry
(positive). Money is never created or destroyed, only moved, so the sum of
all entries is always zero. Money enters the system through **system
accounts** (e.g. a bank settlement account) that are allowed to go negative;
a negative system balance means "money we owe to the outside world".

### 3. Entries are the source of truth; `accounts.balance` is a cache

Computing a balance as `SUM(entries)` on every read gets slower as history
grows. Instead, each account keeps a cached `balance`, updated **in the same
transaction** that writes the entries, so the two cannot diverge. Each entry
also stores `balance_after`, which gives running-balance statements with no
extra work. A reconciliation check (see `tests/helpers.py`, and a scheduled
job in Phase 5) verifies `balance == SUM(entries)` for every account.

### 4. Pessimistic row locking with a consistent lock order

A transfer runs `SELECT ... FOR UPDATE` on both accounts, then checks the
balance, then writes. The lock makes this check-then-write sequence safe:
a concurrent transfer on the same account waits until we commit.

Locks are always taken in account-ID order. Without that, transfer A→B and a
simultaneous B→A could each hold one lock while waiting for the other, which
is a deadlock.

**Alternatives considered:**

| Approach | Why not (for now) |
|---|---|
| No locking, plain read then write | Loses updates. Proven by removing the lock: 50 of 50 concurrent ₹10 transfers succeeded from a ₹100 account. |
| Atomic `UPDATE ... SET balance = balance - x WHERE balance >= x` | Works for the source account only; we also need consistent `balance_after` values for both accounts, which requires reading locked rows anyway. |
| Optimistic locking (version column, retry on conflict) | Better when conflicts are rare. For a hot account (a merchant receiving many payments), constant retries waste work. |
| `SERIALIZABLE` isolation | Correct, but it requires retry logic on every serialization failure. Row locks give us the same safety under the default `READ COMMITTED`. |

**Trade-off:** all transfers touching one account run one at a time. That
caps throughput for a very hot account. Phase 5 load tests will measure the
ceiling.

### 5. The database enforces the invariants, not only the application

- A `CHECK` constraint means a normal account's balance can never be negative.
- Triggers make `transfers` and `entries` **append-only**: UPDATE and DELETE
  are rejected. Mistakes are corrected with a new reversing transfer, never
  by editing history, as in real accounting.
- A **deferred constraint trigger** checks at `COMMIT` that every transfer's
  entries sum to zero.

Application bugs, ad-hoc SQL and future services all hit the same wall.

## Consequences

- Transfers on the same account are serialized, which limits the throughput
  of a single hot account.
- Test data can't be reset with `DELETE`; tests use `TRUNCATE`, which skips
  row-level triggers.
- Creating accounts with `allow_negative_balance` must be restricted to
  administrators. There is no auth yet; this arrives with API keys and scopes
  in Phase 3.
