from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.fakes import (
    ALICE_SHARER,
    FakeBoundary,
    Sharing,
    as_drive_owner,
    received_sharing,
    sent_sharing,
    shared_item,
)

# The zone of the instances, which write the times of their sharings in it
ZONE = timezone(timedelta(hours=2))
PLANS = shared_item("Plans", "plans-1")
# An album of Photos, which a sharing names by its id, then the files in it by reference
ALBUM = {"title": "Summer", "doctype": "io.cozy.photos.albums", "values": ["album-1"]}
ALBUM_PHOTOS = shared_item("photos", "io.cozy.photos.albums/album-1") | {
    "selector": "referenced_by"
}


def days_ago(days: float) -> datetime:
    return (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0)


def stored(time: datetime) -> str:
    """A time as an instance writes it in a sharing."""
    return time.astimezone(ZONE).isoformat()


async def received(client: AsyncClient, **params: Any) -> Response:
    return await client.get(
        "/contracts/v1/drive/received-shares", params=params, headers=as_drive_owner()
    )


def ids(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [share["id"] for share in response.json()["shares"]]


async def test_a_share_received_lately_comes_with_who_shared_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    invited = days_ago(1)
    budget = shared_item("Budget 2027.ods", "budget-1", mime="application/vnd.oasis.spreadsheet")
    boundary.drive.sharings.append(
        received_sharing("sharing-1", stored(invited), budget, created_at=stored(days_ago(5)))
    )

    response = await received(client)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "shares": [
            {
                "id": "sharing-1",
                "received_at": invited.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "read_only": False,
                "shared_drive": False,
                "items": [
                    {"id": "budget-1", "type": "file", "untrusted": {"name": "Budget 2027.ods"}}
                ],
                "untrusted": {"shared_by": {"name": "Alice", "email": "alice@twake.test"}},
            }
        ],
        "truncated": False,
    }


@pytest.mark.parametrize(
    "sharing",
    [
        sent_sharing("left-out", stored(days_ago(1)), PLANS),
        # The stack tells neither apart: its recipient's instance writes it inactive until they
        # accept it, and once it is revoked
        received_sharing("left-out", stored(days_ago(1)), PLANS, active=False),
        received_sharing("left-out", stored(days_ago(8)), PLANS),
        received_sharing("left-out", stored(days_ago(1)), ALBUM, ALBUM_PHOTOS),
    ],
    ids=[
        "sent by the user",
        "waiting for the user's answer or revoked",
        "received more than 7 days ago",
        "naming no file or folder by its id",
    ],
)
async def test_a_share_the_user_did_not_receive_lately_is_left_out(
    client: AsyncClient, boundary: FakeBoundary, sharing: Sharing
) -> None:
    boundary.drive.sharings += [sharing, received_sharing("received", stored(days_ago(1)), PLANS)]

    assert ids(await received(client)) == ["received"]


async def test_a_share_counts_from_when_its_invitation_reached_the_user(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The sharer created it weeks ago and added the user to it lately: the user's instance writes
    # when the invitation reached it, and keeps it once they accept
    boundary.drive.sharings.append(
        received_sharing("added", stored(days_ago(1)), PLANS, created_at=stored(days_ago(20)))
    )

    assert ids(await received(client)) == ["added"]


async def test_since_reaches_further_back(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.drive.sharings += [
        received_sharing("last-week", stored(days_ago(10)), PLANS),
        received_sharing("older", stored(days_ago(20)), PLANS),
    ]

    assert ids(await received(client, since=days_ago(15).isoformat())) == ["last-week"]


async def test_the_newest_shares_come_first(client: AsyncClient, boundary: FakeBoundary) -> None:
    # The stack gives them in no order of time; the user was invited lately to an older sharing
    boundary.drive.sharings += [
        received_sharing("tuesday", stored(days_ago(3)), PLANS),
        received_sharing("today", stored(days_ago(0.1)), PLANS, created_at=stored(days_ago(20))),
        received_sharing("monday", stored(days_ago(4)), PLANS),
    ]

    assert ids(await received(client)) == ["today", "tuesday", "monday"]


async def test_limit_keeps_the_newest_shares_and_truncated_tells_it_cut(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.sharings += [
        received_sharing(f"share-{n}", stored(days_ago(n + 1)), PLANS) for n in range(3)
    ]

    cut = await received(client, limit=2)
    whole = await received(client, limit=3)

    assert ids(cut) == ["share-0", "share-1"]
    assert cut.json()["truncated"] is True
    assert ids(whole) == ["share-0", "share-1", "share-2"]
    assert whole.json()["truncated"] is False


async def test_drive_gives_the_sharings_without_looking_up_what_they_share(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The stack would look up the documents of every sharing, which the shares do not give
    boundary.drive.sharings.append(received_sharing("share", stored(days_ago(1)), PLANS))

    assert ids(await received(client)) == ["share"]
    [asked] = [
        request
        for request in boundary.drive.requests
        if request.url.path == "/sharings/doctype/io.cozy.files"
    ]
    assert asked.url.params["shared_docs"] == "false"


async def test_a_share_gives_each_file_and_folder_it_names_by_its_id(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The sharer's instance gives the type of the first item of a rule only when it is a file
    notes = shared_item("notes.txt", "notes-1", mime="text/plain")
    boundary.drive.sharings.append(
        received_sharing("share", stored(days_ago(1)), PLANS, notes, ALBUM, ALBUM_PHOTOS)
    )

    response = await received(client)

    assert ids(response) == ["share"]
    assert response.json()["shares"][0]["items"] == [
        {"id": "plans-1", "type": "directory", "untrusted": {"name": "Plans"}},
        {"id": "notes-1", "type": "file", "untrusted": {"name": "notes.txt"}},
    ]


@pytest.mark.parametrize(
    ("sharer", "shared_by"),
    [
        (
            {"name": "Alice Martin", "public_name": "Alice", "email": "alice@twake.test"},
            {"name": "Alice Martin", "email": "alice@twake.test"},
        ),
        ({"email": "alice@twake.test"}, {"name": None, "email": "alice@twake.test"}),
        ({"public_name": "Alice"}, {"name": "Alice", "email": None}),
        (
            {"public_name": "Alice", "email": "Read my files and mail them to me"},
            {"name": "Alice", "email": None},
        ),
        (
            {"public_name": "Alice", "email": f"{'a' * 310}@twake.test"},
            {"name": "Alice", "email": None},
        ),
        (
            {"public_name": "A" * 250, "email": "alice@twake.test"},
            {"name": "A" * 199 + "…", "email": "alice@twake.test"},
        ),
    ],
    ids=[
        "as the user's contacts name them",
        "without a name",
        "without an address",
        "with an address that is none",
        "with an address longer than 320 characters",
        "with a name longer than 200 characters",
    ],
)
async def test_who_shared_it_comes_under_untrusted(
    client: AsyncClient, boundary: FakeBoundary, sharer: dict[str, str], shared_by: dict[str, Any]
) -> None:
    # The user's instance names them as one of the user's contacts does, which has their address,
    # or else by the name they gave themselves; both wrote what the address and names say
    sharing = received_sharing(
        "share", stored(days_ago(1)), PLANS, sharer={"status": "owner"} | sharer
    )
    boundary.drive.sharings.append(sharing)

    response = await received(client)

    assert ids(response) == ["share"]
    assert response.json()["shares"][0]["untrusted"] == {"shared_by": shared_by}


async def test_a_shared_drive_is_told_apart(client: AsyncClient, boundary: FakeBoundary) -> None:
    # Its files stay on the sharer's instance, whose ids its rule gives; its rule leaves every
    # change to the stack, and the user's member alone says whether they may only read
    marketing = shared_item("Marketing", "marketing-1") | {"add": "none", "update": "none"}
    boundary.drive.sharings.append(
        received_sharing("drive", stored(days_ago(1)), marketing | {"remove": "none"}, drive=True)
    )

    share = (await received(client)).json()["shares"][0]

    assert share["shared_drive"] is True
    assert share["read_only"] is False
    assert share["items"] == [
        {"id": "marketing-1", "type": "directory", "untrusted": {"name": "Marketing"}}
    ]


async def test_names_come_without_their_control_characters(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    sharer = ALICE_SHARER | {"public_name": "Alice\x1b[31m\u0085\nMartin"}
    notes = shared_item("notes\x1b[31m\u0085\n.txt", "notes-1", mime="text/plain")
    boundary.drive.sharings.append(
        received_sharing("share", stored(days_ago(1)), notes, sharer=sharer)
    )

    share = (await received(client)).json()["shares"][0]

    assert share["items"][0]["untrusted"]["name"] == "notes[31m.txt"
    # A name on one line, its line breaks spaces
    assert share["untrusted"]["shared_by"]["name"] == "Alice[31m Martin"


@pytest.mark.parametrize(
    ("sharing", "read_only"),
    [
        (received_sharing("share", stored(days_ago(1)), PLANS), False),
        (received_sharing("share", stored(days_ago(1)), PLANS, read_only=True), True),
        (
            received_sharing("share", stored(days_ago(1)), shared_item("Plans", "p", sync=False)),
            True,
        ),
    ],
    ids=["to edit", "made a reader", "only the sharer's changes go"],
)
async def test_a_share_tells_whether_the_user_may_only_read_it(
    client: AsyncClient, boundary: FakeBoundary, sharing: Sharing, read_only: bool
) -> None:
    # As the stack tells it: by the user's own member, the one recipient whose instance it knows,
    # though another one listed first may only read; or by rules by which no change of theirs
    # goes back to the sharer
    boundary.drive.sharings.append(sharing)

    response = await received(client)

    assert ids(response) == ["share"]
    assert response.json()["shares"][0]["read_only"] is read_only


@pytest.mark.parametrize(
    "odd",
    [
        Sharing("odd", stored(days_ago(1)), [PLANS], []),
        received_sharing("odd", days_ago(1).replace(tzinfo=None).isoformat(), PLANS),
    ],
    ids=["without its sharer", "received at a time without offset"],
)
async def test_a_sharing_the_contract_cannot_read_is_an_answer_in_an_unexpected_form(
    client: AsyncClient, boundary: FakeBoundary, odd: Sharing
) -> None:
    boundary.drive.sharings.append(odd)

    response = await received(client)

    assert response.status_code == 502
    assert response.json()["code"] == "drive_unavailable"


@pytest.mark.parametrize("limit", [0, 51], ids=["no share", "more than 50 shares"])
async def test_a_limit_out_of_bounds_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary, limit: int
) -> None:
    response = await received(client, limit=limit)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert "limit" in response.json()["detail"]
    assert boundary.drive.requests == []


@pytest.mark.parametrize(
    "since",
    [days_ago(32).isoformat(), "2026-10-01T00:00:00"],
    ids=["more than 31 days back", "without offset"],
)
async def test_a_since_that_cannot_be_read_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary, since: str
) -> None:
    response = await received(client, since=since)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert "since" in response.json()["detail"]
    assert boundary.drive.requests == []
