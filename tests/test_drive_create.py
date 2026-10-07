import base64
import hashlib
import json
import re
from typing import Any

import httpx
import pytest
from httpx import AsyncClient, Response

from tests.fakes import (
    ROOT_ID,
    SHARED_DRIVES_ID,
    SHARED_WITH_ME_ID,
    TRASH_ID,
    DriveDoc,
    FakeBoundary,
    Link,
    as_drive_owner,
    folder,
    text_file,
)

DRIVE_APP = "https://mmaudet-drive.twake.test"
NOTES = "# Meeting notes\n\n- Budget approved\n"
MIB = 1_048_576


def new_file(
    folder_id: str = "documents",
    name: str = "Meeting notes.md",
    content: str = NOTES,
    mime: str = "text/markdown",
) -> dict[str, Any]:
    return {"folder_id": folder_id, "name": name, "content": content, "mime": mime}


async def create(client: AsyncClient, body: dict[str, Any]) -> Response:
    # As JSON escapes it, so that a lone surrogate can be sent
    return await client.post(
        "/contracts/v1/drive/files",
        content=json.dumps(body),
        headers=as_drive_owner() | {"Content-Type": "application/json"},
    )


def creations(boundary: FakeBoundary) -> list[httpx.Request]:
    """The requests that asked the stack to create a file."""
    return [
        request
        for request in boundary.drive.requests
        if request.method == "POST" and not request.url.path.startswith("/files/_")
    ]


async def test_a_markdown_file_is_created_in_the_users_folder(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(folder("documents", "Documents"))

    response = await create(client, new_file())

    assert response.status_code == 201, response.text
    answer = response.json()
    created = boundary.drive.docs[answer["id"]]
    assert (created.dir_id, created.name, created.content, created.mime) == (
        "documents",
        "Meeting notes.md",
        NOTES.encode(),
        "text/markdown",
    )
    assert answer == {
        "id": created.id,
        "type": "file",
        "folder_id": "documents",
        "size": len(NOTES.encode()),
        "created_at": "2026-09-01T08:00:00Z",
        "updated_at": "2026-10-05T09:00:00Z",
        "web_url": f"{DRIVE_APP}/#/folder/documents/file/{created.id}",
        "untrusted": {
            "name": "Meeting notes.md",
            "path": "/Documents/Meeting notes.md",
            "mime": "text/markdown",
            "class": "text",
        },
    }
    # Its content checked on arrival, and never executable
    [creation] = creations(boundary)
    md5 = base64.b64encode(hashlib.md5(NOTES.encode()).digest()).decode()
    assert creation.headers["content-md5"] == md5
    assert "Executable" not in creation.url.params


async def test_a_plain_text_file_is_created_at_the_top_of_the_drive(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(client, new_file("root", "todo.txt", "call Paul", "text/plain"))

    assert response.status_code == 201, response.text
    answer = response.json()
    assert (answer["folder_id"], answer["untrusted"]["mime"]) == (ROOT_ID, "text/plain")
    assert answer["untrusted"]["path"] == "/todo.txt"
    assert boundary.drive.docs[answer["id"]].content == b"call Paul"


async def test_a_file_of_one_mib_in_utf8_is_created(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(folder("documents", "Documents"))

    response = await create(client, new_file(content="é" * (MIB // 2)))

    assert response.status_code == 201, response.text
    assert response.json()["size"] == MIB


@pytest.mark.parametrize(
    "taken_by",
    [
        text_file("notes", "notes.md", "documents", content=b"mine"),
        folder("notes", "notes.md", "documents"),
    ],
    ids=["by a file", "by a folder"],
)
async def test_a_name_taken_in_the_folder_is_refused_and_nothing_replaced(
    client: AsyncClient, boundary: FakeBoundary, taken_by: DriveDoc
) -> None:
    boundary.drive.add(folder("documents", "Documents"), taken_by)

    response = await create(client, new_file(name="notes.md"))

    assert response.status_code == 409
    assert response.json()["code"] == "name_taken"
    assert boundary.drive.child("documents", "notes.md") == taken_by
    assert len([doc for doc in boundary.drive.docs.values() if doc.dir_id == "documents"]) == 1


@pytest.mark.parametrize(
    "folder_id",
    ["team", "minutes", "2026", "budget", "product"],
    ids=[
        "a folder the user shared",
        "in a shared folder",
        "deep in a shared folder",
        "a folder shared with the user",
        "a shared drive",
    ],
)
async def test_a_folder_shared_with_others_takes_no_file(
    client: AsyncClient, boundary: FakeBoundary, folder_id: str
) -> None:
    # The stack references the sharing from the root it shares, on the side of each member
    boundary.drive.add(
        folder("team", "Team", sharing="sharing-sent"),
        folder("minutes", "Minutes", "team"),
        folder("2026", "2026", "minutes"),
        folder(SHARED_WITH_ME_ID, "Shared with me"),
        folder("budget", "Budget", SHARED_WITH_ME_ID, sharing="sharing-received"),
        folder(SHARED_DRIVES_ID, "Drives"),
        folder("product", "Product", SHARED_DRIVES_ID, sharing="sharing-drive"),
    )

    response = await create(client, new_file(folder_id))

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "folder_shared"
    assert creations(boundary) == []


@pytest.mark.parametrize("folder_id", ["photos", "2026"], ids=["the folder", "a folder above"])
@pytest.mark.parametrize(
    "expires_at", [None, "2099-01-01T00:00:00Z"], ids=["for good", "until it expires"]
)
async def test_a_folder_shared_by_a_link_takes_no_file(
    client: AsyncClient, boundary: FakeBoundary, folder_id: str, expires_at: str | None
) -> None:
    # Whoever holds the link reads what the folder holds
    boundary.drive.add(folder("photos", "Photos"), folder("2026", "2026", "photos"))
    boundary.drive.links.append(Link(["photos"], expires_at=expires_at))

    response = await create(client, new_file(folder_id))

    assert response.status_code == 409
    assert response.json()["code"] == "folder_shared"
    assert creations(boundary) == []


async def test_every_link_counts_however_many_there_are(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(folder("photos", "Photos"))
    boundary.drive.links += [Link([f"file-{n}"]) for n in range(150)] + [Link(["photos"])]

    response = await create(client, new_file("photos"))

    assert response.status_code == 409
    assert response.json()["code"] == "folder_shared"


async def test_an_expired_link_shares_nothing(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.drive.add(folder("photos", "Photos"))
    boundary.drive.links.append(Link(["photos"], expires_at="2026-01-01T00:00:00Z"))

    response = await create(client, new_file("photos"))

    assert response.status_code == 201, response.text


async def test_a_folder_that_holds_shared_ones_takes_the_file(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # What is shared below the folder is not shared with the new file
    boundary.drive.add(
        folder("documents", "Documents"),
        folder("team", "Team", "documents", sharing="sharing-sent"),
        folder("photos", "Photos", "documents"),
    )
    boundary.drive.links.append(Link(["photos"]))

    response = await create(client, new_file("documents"))

    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "folder_id",
    ["unknown", "notes", TRASH_ID, "archives"],
    ids=["unknown", "a file", "the trash", "in the trash"],
)
async def test_what_is_not_a_folder_of_the_user_takes_no_file(
    client: AsyncClient, boundary: FakeBoundary, folder_id: str
) -> None:
    boundary.drive.add(text_file("notes", "notes.txt"), folder("archives", "Archives", TRASH_ID))

    response = await create(client, new_file(folder_id))

    assert response.status_code == 404
    assert response.json()["code"] == "folder_not_found"
    assert creations(boundary) == []


async def test_a_folder_trashed_before_the_write_takes_no_file(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The user trashed it in Drive between the contract's checks and its write
    boundary.drive.add(folder("documents", "Documents"))
    boundary.drive.trashed_meanwhile.add("documents")

    response = await create(client, new_file())

    assert response.status_code == 404
    assert response.json()["code"] == "folder_not_found"


async def test_a_drive_without_room_for_the_file_says_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.add(folder("documents", "Documents"))
    boundary.drive.free_space = 10

    response = await create(client, new_file())

    assert response.status_code == 409
    assert response.json()["code"] == "quota_exceeded"
    assert boundary.drive.child("documents", "Meeting notes.md") is None


async def test_a_folder_out_of_the_tokens_reach_answers_like_a_missing_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A Drive token that may only read: the stack refuses the write with 403, which answers like
    # its 404, as for anything out of the token's reach
    boundary.drive.add(folder("documents", "Documents"))
    boundary.drive.read_only_token = True

    response = await create(client, new_file())

    assert response.status_code == 404
    assert response.json()["code"] == "folder_not_found"
    assert boundary.drive.child("documents", "Meeting notes.md") is None


async def test_a_failure_to_find_the_drive_app_leaves_no_file_behind(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The link to the new file needs the instance's capabilities: read before the write, they
    # fail with nothing written, and the call made again creates the file instead of finding its
    # name taken
    boundary.drive.add(folder("documents", "Documents"))
    boundary.drive.capabilities_down = True

    failed = await create(client, new_file())
    boundary.drive.capabilities_down = False
    again = await create(client, new_file())

    assert (failed.status_code, failed.json()["code"]) == (502, "drive_unavailable")
    assert again.status_code == 201, again.text


async def test_the_gateway_is_given_the_rules_of_a_name(client: AsyncClient) -> None:
    # It checks each call against the document: a pattern tells it the names a pattern can tell
    document = (await client.get("/openapi.json")).json()
    create_file = document["paths"]["/contracts/v1/drive/files"]["post"]
    name = create_file["requestBody"]["content"]["application/json"]["schema"]["properties"]["name"]

    assert re.search(name["pattern"], "Meeting notes.md")
    for refused in ("drafts/notes.md", ".md", "notes", "notes.cozy-note", "notes.pdf"):
        assert not re.search(name["pattern"], refused), refused
    # What it cannot tell, its description does
    assert "invisible or direction-changing" in name["description"]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"name": "notes.txt"}, "name"),
        ({"name": "notes"}, "name"),
        ({"name": "notes.cozy-note"}, "name"),
        ({"name": "todo.md", "mime": "text/plain"}, "name"),
        ({"name": "drafts/notes.md"}, "name"),
        ({"name": "notes\x1b[31m.md"}, "name"),
        ({"name": "invoice\u202etxt.md"}, "name"),
        ({"name": "notes\u200b.md"}, "name"),
        ({"name": "no\u00adtes.md"}, "name"),
        ({"name": "notes\U000e0041.md"}, "name"),
        ({"name": "notes\u2028.md"}, "name"),
        ({"name": ".md"}, "name"),
        ({"name": "notes\ud800.md"}, "name"),
        ({"name": "n" * 253 + ".md"}, "name"),
        ({"mime": "text/html"}, "mime"),
        ({"content": "é" * (MIB // 2) + "a"}, "content"),
        ({"content": "a\ud800b"}, "content"),
        ({"folder_id": ".."}, "folder_id"),
        ({"overwrite": True}, "overwrite"),
    ],
    ids=[
        "Markdown named as plain text",
        "no extension",
        "a note",
        "plain text named as Markdown",
        "a path",
        "a control character",
        "a direction override",
        "a zero-width space",
        "a soft hyphen",
        "a tag character",
        "a line separator",
        "a hidden file",
        "a name that is not text",
        "a name of 256 characters",
        "HTML",
        "over 1 MiB in UTF-8",
        "content that is not text",
        "not a folder id",
        "a field the contract does not take",
    ],
)
async def test_a_file_the_contract_does_not_create_never_reaches_drive(
    client: AsyncClient, boundary: FakeBoundary, change: dict[str, Any], field: str
) -> None:
    response = await create(client, new_file() | change)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert response.json()["detail"].startswith(f"{field}: ")
    assert boundary.drive.requests == []
