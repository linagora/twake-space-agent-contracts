from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.fakes import ROOT_ID, TRASH_ID, FakeBoundary, as_drive_owner, folder, text_file

DRIVE_APP = "https://mmaudet-drive.twake.test"


async def list_items(client: AsyncClient, folder_id: str, **params: Any) -> Response:
    return await client.get(
        f"/contracts/v1/drive/folders/{folder_id}/items", params=params, headers=as_drive_owner()
    )


def ids(answer: dict[str, Any]) -> list[str]:
    return [item["id"] for item in answer["items"]]


async def test_the_top_of_the_drive_lists_its_folders_then_its_files(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(
        text_file("notes", "notes.txt", content=b"hello", updated_at="2026-10-05T09:14:22Z"),
        folder("documents", "Documents"),
        text_file("old", "old.txt", TRASH_ID, trashed=True),
    )

    response = await list_items(client, "root")

    assert response.status_code == 200, response.text
    answer = response.json()
    assert ids(answer) == ["documents", "notes"]
    assert answer["items"][1] == {
        "id": "notes",
        "type": "file",
        "folder_id": ROOT_ID,
        "mime": "text/plain",
        "class": "text",
        "size": 5,
        "created_at": "2026-09-01T08:00:00Z",
        "updated_at": "2026-10-05T09:14:22Z",
        "web_url": f"{DRIVE_APP}/#/folder/{ROOT_ID}/file/notes",
        "untrusted": {"name": "notes.txt", "path": "/notes.txt"},
    }
    assert answer["next_cursor"] is None


async def test_a_folder_lists_its_items_with_their_paths(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(
        folder("documents", "Documents"),
        folder("projects", "Projets", "documents"),
        text_file("budget", "Budget.csv", "documents", mime="text/csv"),
    )

    answer = (await list_items(client, "documents")).json()

    assert answer["folder"] == {
        "id": "documents",
        "type": "directory",
        "folder_id": ROOT_ID,
        "mime": None,
        "class": None,
        "size": None,
        "created_at": "2026-09-01T08:00:00Z",
        "updated_at": "2026-10-05T09:00:00Z",
        "web_url": f"{DRIVE_APP}/#/folder/documents",
        "untrusted": {"name": "Documents", "path": "/Documents"},
    }
    assert [(item["id"], item["untrusted"]["path"]) for item in answer["items"]] == [
        ("projects", "/Documents/Projets"),
        ("budget", "/Documents/Budget.csv"),
    ]


async def test_a_long_folder_comes_page_by_page(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(*(text_file(f"file-{n}", f"file {n}.txt") for n in range(5)))

    first = (await list_items(client, "root", limit=2)).json()
    second = (await list_items(client, "root", limit=2, cursor=first["next_cursor"])).json()
    third = (await list_items(client, "root", limit=2, cursor=second["next_cursor"])).json()

    assert [ids(page) for page in (first, second, third)] == [
        ["file-0", "file-1"],
        ["file-2", "file-3"],
        ["file-4"],
    ]
    assert third["next_cursor"] is None


async def test_a_list_holds_twenty_items_by_default(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(*(text_file(f"file-{n:02}", f"file {n:02}.txt") for n in range(25)))

    answer = (await list_items(client, "root")).json()

    assert len(answer["items"]) == 20
    assert answer["next_cursor"] is not None


@pytest.mark.parametrize("cursor", ["next", "-2", "2.0"])
async def test_a_cursor_the_contract_did_not_give_is_an_invalid_request(
    client: AsyncClient, cursor: str
) -> None:
    response = await list_items(client, "root", cursor=cursor)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert "cursor" in response.json()["detail"]


@pytest.mark.parametrize(
    "folder_id",
    ["unknown", TRASH_ID, "archives", "notes", "secret"],
    ids=["unknown", "the trash", "in the trash", "a file", "one the token may not read"],
)
async def test_a_folder_the_user_cannot_list_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary, folder_id: str
) -> None:
    boundary.drive.add(
        folder("archives", "Archives", TRASH_ID),
        text_file("notes", "notes.txt"),
        folder("secret", "Secret"),
    )
    boundary.drive.forbidden.add("secret")

    response = await list_items(client, folder_id)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "folder_not_found"


@pytest.mark.parametrize("limit", [0, 101])
async def test_a_limit_out_of_range_is_an_invalid_request(client: AsyncClient, limit: int) -> None:
    response = await list_items(client, "root", limit=limit)

    assert response.status_code == 400
    assert "limit" in response.json()["detail"]


@pytest.mark.parametrize("folder_id", ["%2E%2E", "a%3Fb"], ids=["a dot segment", "a query"])
async def test_an_id_that_is_not_one_of_drive_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary, folder_id: str
) -> None:
    response = await list_items(client, folder_id)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.drive.requests == []
