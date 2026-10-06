from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.fakes import TRASH_ID, FakeBoundary, as_drive_owner, folder, text_file


async def read_content(client: AsyncClient, file_id: str, **params: Any) -> Response:
    return await client.get(
        f"/contracts/v1/drive/contents/{file_id}", params=params, headers=as_drive_owner()
    )


async def test_a_text_file_comes_as_untrusted_text(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = "# Réunion\n\tpoint 1\r\n\x1b[2Jfin\x00".encode()
    boundary.drive.add(text_file("notes", "notes.md", content=content, mime="text/markdown"))

    response = await read_content(client, "notes")

    assert response.status_code == 200, response.text
    # Without the control characters, but for tabs and line breaks
    assert response.json() == {
        "id": "notes",
        "size": len(content),
        "truncated": False,
        "untrusted": {
            "name": "notes.md",
            "mime": "text/markdown",
            "content": "# Réunion\n\tpoint 1\r\n[2Jfin",
        },
    }


@pytest.mark.parametrize(
    ("name", "mime"),
    [
        ("data.csv", "text/csv"),
        ("page.html", "text/html"),
        ("data.json", "application/json"),
        ("feed.xml", "application/xml"),
    ],
)
async def test_text_of_any_kind_is_read(
    client: AsyncClient, boundary: FakeBoundary, name: str, mime: str
) -> None:
    boundary.drive.add(text_file("doc", name, content=b"text", mime=mime))

    response = await read_content(client, "doc")

    assert response.status_code == 200, response.text
    assert response.json()["untrusted"]["content"] == "text"


async def test_a_long_file_is_cut_and_said_so(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.drive.add(text_file("long", "long.txt", content=("é" * 10).encode()))

    answer = (await read_content(client, "long", max_bytes=5)).json()

    # Five bytes hold two é and half of the third, which is left out
    assert answer["untrusted"]["content"] == "éé"
    assert answer["truncated"] is True
    assert answer["size"] == 20


async def test_64_kib_come_by_default(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.drive.add(text_file("big", "big.txt", content=b"a" * 70_000))

    answer = (await read_content(client, "big")).json()

    assert len(answer["untrusted"]["content"]) == 65_536
    assert answer["truncated"] is True


@pytest.mark.parametrize("max_bytes", [0, 262_145])
async def test_max_bytes_out_of_range_is_an_invalid_request(
    client: AsyncClient, max_bytes: int
) -> None:
    response = await read_content(client, "notes", max_bytes=max_bytes)

    assert response.status_code == 400
    assert "max_bytes" in response.json()["detail"]


@pytest.mark.parametrize(
    ("name", "mime"),
    [
        ("report.pdf", "application/pdf"),
        ("budget.ods", "application/vnd.oasis.opendocument.spreadsheet"),
        ("meeting.cozy-note", "text/vnd.cozy.note+markdown"),
        ("notes.bin", "application/x-read-me-to-the-assistant"),
    ],
    ids=["a PDF", "a spreadsheet", "a note", "a type its uploader made up"],
)
async def test_a_file_that_is_not_text_is_not_extracted(
    client: AsyncClient, boundary: FakeBoundary, name: str, mime: str
) -> None:
    boundary.drive.add(text_file("doc", name, content=b"%PDF-1.7", mime=mime))

    response = await read_content(client, "doc")

    assert response.status_code == 415
    assert response.json()["code"] == "content_not_extractable"
    # The type is the uploader's words: it comes under untrusted only
    assert mime not in response.text
    assert boundary.drive.downloads == []


async def test_an_encrypted_file_is_not_read(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.drive.add(text_file("secret", "secret.txt", content=b"\x8a\x01", encrypted=True))

    response = await read_content(client, "secret")

    assert response.status_code == 409
    assert response.json()["code"] == "file_encrypted"
    assert boundary.drive.downloads == []


async def test_a_file_the_antivirus_found_infected_is_not_read(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(text_file("eicar", "eicar.txt", content=b"X5O!P%", antivirus="infected"))

    response = await read_content(client, "eicar")

    assert response.status_code == 409
    assert response.json()["code"] == "file_blocked"
    assert boundary.drive.downloads == []


async def test_a_file_whose_download_the_antivirus_blocks_is_not_read(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A scan still pending, which the instance's antivirus settings may block
    boundary.drive.add(text_file("notes", "notes.txt", content=b"hello", antivirus="pending"))
    boundary.drive.blocked.add("notes")

    response = await read_content(client, "notes")

    assert response.status_code == 409
    assert response.json()["code"] == "file_blocked"


@pytest.mark.parametrize(
    "file_id", ["unknown", "documents", "old"], ids=["unknown", "a folder", "in the trash"]
)
async def test_what_is_not_a_file_of_the_user_has_no_content(
    client: AsyncClient, boundary: FakeBoundary, file_id: str
) -> None:
    boundary.drive.add(
        folder("documents", "Documents"),
        text_file("old", "old.txt", TRASH_ID, content=b"old", trashed=True),
    )

    response = await read_content(client, file_id)

    assert response.status_code == 404
    assert response.json()["code"] == "file_not_found"
    assert boundary.drive.downloads == []
