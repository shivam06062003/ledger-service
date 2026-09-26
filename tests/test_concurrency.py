"""These tests fire many requests at the same time against a real Postgres.
They are the proof that row locking works: remove `.with_for_update()` from
accounts_repo.lock_many and test_no_overspend fails."""

import asyncio

from httpx import AsyncClient

from tests.helpers import (
    assert_ledger_consistent,
    create_account,
    create_funded_account,
    get_balance,
    transfer,
)


async def test_concurrent_transfers_never_overspend(client: AsyncClient) -> None:
    # 100 in the account, 50 simultaneous attempts to move 10: exactly 10 can succeed.
    alice = await create_funded_account(client, 100)
    bob = await create_account(client)

    responses = await asyncio.gather(
        *(transfer(client, alice["id"], bob["id"], 10) for _ in range(50))
    )

    statuses = [r.status_code for r in responses]
    assert statuses.count(201) == 10
    assert statuses.count(422) == 40
    assert await get_balance(client, alice["id"]) == 0
    assert await get_balance(client, bob["id"]) == 100
    await assert_ledger_consistent()


async def test_opposing_transfers_do_not_deadlock(client: AsyncClient) -> None:
    # A->B and B->A at the same time is the classic deadlock shape. Consistent
    # lock ordering means every request completes; a deadlock would surface as
    # a 500 (Postgres aborts one of the transactions).
    alice = await create_funded_account(client, 1_000)
    bob = await create_funded_account(client, 1_000)

    responses = await asyncio.gather(
        *(
            transfer(client, alice["id"], bob["id"], 1)
            if i % 2 == 0
            else transfer(client, bob["id"], alice["id"], 1)
            for i in range(40)
        )
    )

    assert all(r.status_code == 201 for r in responses)
    assert await get_balance(client, alice["id"]) == 1_000
    assert await get_balance(client, bob["id"]) == 1_000
    await assert_ledger_consistent()
