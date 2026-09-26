from typing import Any

from httpx import AsyncClient

from tests.helpers import create_account, create_funded_account, transfer


async def fetch_all_pages(client: AsyncClient, account_id: str, limit: int) -> list[list[Any]]:
    pages: list[list[Any]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = await client.get(f"/v1/accounts/{account_id}/entries", params=params)
        assert response.status_code == 200
        body = response.json()
        pages.append(body["data"])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


async def test_walks_every_entry_exactly_once_newest_first(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    for amount in (1, 2, 3, 4):
        await transfer(client, alice["id"], bob["id"], amount)

    pages = await fetch_all_pages(client, alice["id"], limit=2)

    assert [len(page) for page in pages] == [2, 2, 1]
    amounts = [entry["amount"] for page in pages for entry in page]
    assert amounts == [-4, -3, -2, -1, 1_000]
    ids = [entry["id"] for page in pages for entry in page]
    assert len(ids) == len(set(ids))


async def test_exact_multiple_of_page_size_has_no_empty_trailing_page(client: AsyncClient) -> None:
    alice = await create_funded_account(client, 1_000)
    bob = await create_account(client)
    await transfer(client, alice["id"], bob["id"], 1)

    pages = await fetch_all_pages(client, alice["id"], limit=2)

    assert [len(page) for page in pages] == [2]


async def test_malformed_cursor_is_rejected(client: AsyncClient) -> None:
    alice = await create_account(client)

    response = await client.get(
        f"/v1/accounts/{alice['id']}/entries", params={"cursor": "not-a-cursor"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"
