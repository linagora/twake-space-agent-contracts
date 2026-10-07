from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.fakes import TRASH_ID, FakeBoundary, as_drive_owner, folder, text_file


async def search(client: AsyncClient, **params: Any) -> Response:
    return await client.get("/contracts/v1/drive/files", params=params, headers=as_drive_owner())


def ids(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return sorted(item["id"] for item in response.json()["items"])


async def test_a_search_finds_the_names_holding_the_text_whatever_its_case(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(
        folder("budgets", "Budgets"),
        text_file("budget", "budget 2026.csv", "budgets"),
        text_file("q4", "Q4 BUDGET.txt"),
        text_file("notes", "notes.txt"),
    )

    response = await search(client, name="Budget")

    assert ids(response) == ["budget", "budgets", "q4"]
    paths = {item["id"]: item["untrusted"]["path"] for item in response.json()["items"]}
    assert paths["budget"] == "/Budgets/budget 2026.csv"


@pytest.mark.parametrize(("kind", "found"), [("file", ["budget"]), ("directory", ["budgets"])])
async def test_a_search_keeps_the_kind_asked_for(
    client: AsyncClient, boundary: FakeBoundary, kind: str, found: list[str]
) -> None:
    boundary.drive.add(folder("budgets", "Budgets"), text_file("budget", "budget.txt"))

    assert ids(await search(client, name="budget", kind=kind)) == found


async def test_a_search_keeps_the_class_asked_for(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(
        text_file("report-pdf", "report.pdf", mime="application/pdf"),
        text_file("report-txt", "report.txt"),
        folder("reports", "reports"),
    )

    assert ids(await search(client, name="report", **{"class": "pdf"})) == ["report-pdf"]


async def test_a_search_leaves_out_the_trash(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.drive.add(
        text_file("budget", "budget.txt"),
        text_file("old-budget", "budget 2024.txt", TRASH_ID, trashed=True),
        folder("old-budgets", "Budgets", TRASH_ID),
        folder("older-budgets", "Budgets 2023", "old-budgets"),
    )

    assert ids(await search(client, name="budget")) == ["budget"]
    assert ids(await search(client, name="trash")) == []


@pytest.mark.parametrize(("name", "found"), [("v1.2", ["dotted"]), ("(2", ["copy"])])
async def test_the_text_is_searched_as_it_is_written(
    client: AsyncClient, boundary: FakeBoundary, name: str, found: list[str]
) -> None:
    boundary.drive.add(
        text_file("dotted", "v1.2 notes.txt"),
        text_file("other", "v152 notes.txt"),
        text_file("copy", "notes (2).txt"),
    )

    assert ids(await search(client, name=name)) == found


async def test_a_long_search_comes_page_by_page(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(*(text_file(f"budget-{n}", f"budget {n}.txt") for n in range(3)))

    first = await search(client, name="budget", limit=2)
    second = await search(client, name="budget", limit=2, cursor=first.json()["next_cursor"])

    assert ids(first) + ids(second) == ["budget-0", "budget-1", "budget-2"]
    assert second.json()["next_cursor"] is None


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"name": ""},
        {"name": "x" * 101},
        {"name": "budget", "kind": "folder"},
        {"name": "budget", "kind": "directory", "class": "pdf"},
        {"name": "budget", "limit": 0},
        {"name": "budget", "limit": 101},
    ],
    ids=[
        "no name",
        "an empty name",
        "a name too long",
        "an unknown kind",
        "a class of folders",
        "no item",
        "too many items",
    ],
)
async def test_a_search_that_cannot_be_run_is_an_invalid_request(
    client: AsyncClient, params: dict[str, Any]
) -> None:
    response = await search(client, **params)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"


async def test_a_cursor_the_search_did_not_give_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await search(client, name="budget", cursor="g1AAAABforged")

    assert response.status_code == 400
    assert "cursor" in response.json()["detail"]
