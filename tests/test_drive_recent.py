from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.fakes import TRASH_ID, FakeBoundary, as_drive_owner, folder, text_file


def days_ago(days: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def stored(time: datetime) -> str:
    """A time as cozy-stack writes it in updated_at."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ")


async def recent(client: AsyncClient, **params: Any) -> Response:
    return await client.get(
        "/contracts/v1/drive/recent-files", params=params, headers=as_drive_owner()
    )


def ids(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


async def test_the_files_of_the_last_seven_days_come_newest_first(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(
        text_file("monday", "monday.txt", updated_at=stored(days_ago(3))),
        text_file("today", "today.txt", updated_at=stored(days_ago(0.1))),
        text_file("last-month", "last month.txt", updated_at=stored(days_ago(10))),
        folder("new-folder", "New folder", updated_at=stored(days_ago(1))),
        text_file("trashed", "trashed.txt", TRASH_ID, trashed=True, updated_at=stored(days_ago(1))),
        # Only the file says that it is in the trash, with its folder
        folder("archives", "Archives", TRASH_ID),
        text_file("archived", "a.txt", "archives", trashed=True, updated_at=stored(days_ago(1))),
    )

    assert ids(await recent(client)) == ["today", "monday"]


async def test_since_reaches_further_back(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.drive.add(
        text_file("last-month", "last month.txt", updated_at=stored(days_ago(10))),
        text_file("older", "older.txt", updated_at=stored(days_ago(20))),
    )

    assert ids(await recent(client, since=days_ago(15).isoformat())) == ["last-month"]


async def test_recent_files_come_page_by_page(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.drive.add(
        *(text_file(f"file-{n}", f"{n}.txt", updated_at=stored(days_ago(n + 1))) for n in range(3))
    )

    first = await recent(client, limit=2)
    second = await recent(client, limit=2, cursor=first.json()["next_cursor"])

    assert ids(first) + ids(second) == ["file-0", "file-1", "file-2"]
    assert second.json()["next_cursor"] is None


@pytest.mark.parametrize(
    "since",
    [days_ago(32).isoformat(), "2026-10-01T00:00:00"],
    ids=["more than 31 days back", "without offset"],
)
async def test_a_since_that_cannot_be_read_is_an_invalid_request(
    client: AsyncClient, since: str
) -> None:
    response = await recent(client, since=since)

    assert response.status_code == 400
    assert "since" in response.json()["detail"]
