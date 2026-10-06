import pytest
from httpx import AsyncClient, Response

from tests.fakes import ROOT_ID, TRASH_ID, FakeBoundary, as_drive_owner, folder, text_file

DRIVE_APP = "https://mmaudet-drive.twake.test"


async def read(client: AsyncClient, file_id: str) -> Response:
    return await client.get(f"/contracts/v1/drive/files/{file_id}", headers=as_drive_owner())


async def test_reading_a_file_gives_its_details_and_a_link_to_open_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(
        folder("documents", "Documents"),
        text_file(
            "budget",
            "Budget 2026.csv",
            "documents",
            content=b"a;b\n",
            mime="text/csv",
            updated_at="2026-10-05T09:14:22.123456789+02:00",
        ),
    )

    response = await read(client, "budget")

    assert response.status_code == 200, response.text
    # Nothing else of what the stack keeps: no checksum, location, thumbnail or source account
    assert response.json() == {
        "id": "budget",
        "type": "file",
        "folder_id": "documents",
        "size": 4,
        "created_at": "2026-09-01T08:00:00Z",
        "updated_at": "2026-10-05T07:14:22.123456Z",
        "web_url": f"{DRIVE_APP}/#/folder/documents/file/budget",
        "untrusted": {
            "name": "Budget 2026.csv",
            "path": "/Documents/Budget 2026.csv",
            "mime": "text/csv",
            "class": "text",
        },
    }


async def test_reading_a_folder_gives_its_details(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(folder("documents", "Documents"), folder("projects", "Projets", "documents"))

    answer = (await read(client, "projects")).json()

    assert answer["type"] == "directory"
    assert answer["folder_id"] == "documents"
    assert answer["web_url"] == f"{DRIVE_APP}/#/folder/projects"
    assert answer["untrusted"] == {
        "name": "Projets",
        "path": "/Documents/Projets",
        "mime": None,
        "class": None,
    }


async def test_drive_is_on_its_own_subdomain_without_flat_subdomains(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.flat_subdomains = False
    boundary.drive.add(text_file("notes", "notes.txt"))

    answer = (await read(client, "notes")).json()

    assert answer["web_url"] == f"https://drive.mmaudet.twake.test/#/folder/{ROOT_ID}/file/notes"


async def test_names_come_without_their_control_characters(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(text_file("notes", "notes\x1b[31m\u0085\n.txt"))

    answer = (await read(client, "notes")).json()

    assert answer["untrusted"]["name"] == "notes[31m.txt"
    assert answer["untrusted"]["path"] == "/notes[31m.txt"


@pytest.mark.parametrize(
    "file_id",
    ["unknown", "old", "archives", "draft", TRASH_ID],
    ids=["unknown", "in the trash", "a folder in the trash", "in a trashed folder", "the trash"],
)
async def test_what_is_in_the_trash_answers_like_what_does_not_exist(
    client: AsyncClient, boundary: FakeBoundary, file_id: str
) -> None:
    boundary.drive.add(
        text_file("old", "old.txt", TRASH_ID, trashed=True),
        folder("archives", "Archives", TRASH_ID),
        text_file("draft", "draft.txt", "archives", trashed=True),
    )

    response = await read(client, file_id)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "file_not_found"
