"""Ledger reconciliation: independently re-verify the invariants that every
transfer is supposed to maintain. Runs on a schedule in the worker, and on
demand via `python -m app.cli reconcile`.

Database constraints already prevent most corruption. Reconciliation is the
backstop that also catches what constraints can't express: cached balances
drifting from their entries (a bug, a bad manual fix, a restore gone wrong).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text

from app.core.db import engine

SAMPLE_LIMIT = 100


@dataclass(frozen=True)
class ReconciliationReport:
    ledger_total: int
    unbalanced_transfer_ids: list[uuid.UUID]
    drifted_account_ids: list[uuid.UUID]

    @property
    def ok(self) -> bool:
        return (
            self.ledger_total == 0
            and not self.unbalanced_transfer_ids
            and not self.drifted_account_ids
        )


async def reconcile() -> ReconciliationReport:
    # REPEATABLE READ: all three queries see ONE consistent snapshot. Under the
    # default READ COMMITTED each query gets a fresh snapshot, so a transfer
    # committing between "sum the entries" and "read the balances" would show up
    # as a false discrepancy.
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="REPEATABLE READ")
        async with conn.begin():
            # 1. Money is never created or destroyed.
            total = await conn.scalar(text("SELECT COALESCE(SUM(amount), 0) FROM entries"))
            # 2. Every transfer balances on its own.
            unbalanced = await conn.scalars(
                text(
                    "SELECT transfer_id FROM entries GROUP BY transfer_id "
                    "HAVING SUM(amount) <> 0 LIMIT :limit"
                ),
                {"limit": SAMPLE_LIMIT},
            )
            unbalanced_ids = list(unbalanced.all())
            # 3. Cached balances equal the sum of each account's entries.
            drifted = await conn.scalars(
                text(
                    """
                    SELECT a.id
                    FROM accounts a
                    LEFT JOIN entries e ON e.account_id = a.id
                    GROUP BY a.id, a.balance
                    HAVING a.balance <> COALESCE(SUM(e.amount), 0)
                    LIMIT :limit
                    """
                ),
                {"limit": SAMPLE_LIMIT},
            )
            drifted_ids = list(drifted.all())
    return ReconciliationReport(int(total or 0), unbalanced_ids, drifted_ids)
