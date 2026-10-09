"""space.feed.read.v1: the feeds of the user's spaces in Twake Space, their Fil: a card per object
of their apps, a file, an event, a task or an email, the posts of their members, and their
reactions; the feed of one space, or those of all of them, merged."""

from typing import Any

import httpx
import pytest
from httpx import AsyncClient, Response

from tests.fakes import (
    MMAUDET_SPACE_TOKEN,
    FakeBoundary,
    SpaceRoom,
    SpaceToken,
    as_space_owner,
    space_person,
    space_uuid,
)

MMAUDET = space_person("mmaudet", "Michel-Marie Maudet")
# The owner's API token of Space as a token of the organization, which reaches every space, the
# user's or not, with a role of its own
TOKEN_OF_THE_ORGANIZATION = {MMAUDET_SPACE_TOKEN: SpaceToken(None, role="viewer")}
ALICE = space_person("alice", "Alice Martin")
BOB = space_person("bob")
DRIVE = "6650a1b2c3d4e5f6a7b8c9d0-drive"
FEEDS = "/contracts/v1/space/feed"


def named(room: SpaceRoom) -> dict[str, Any]:
    """The space an item of a feed names, as the contracts give it: its feed in Space's web app."""
    return {
        "space_id": room.id,
        "url": f"https://space.twake.test/spaces/{room.id}/feed",
        "untrusted": {"name": room.name},
    }


def items_of(response: Response) -> list[str]:
    """The items of a list of the feeds, by item_id."""
    assert response.status_code == 200, response.text
    return [item["item_id"] for item in response.json()["items"]]


async def test_the_user_reads_the_feed_of_a_space_newest_first(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space(
        "Design", {MMAUDET: "editor", ALICE: "admin", BOB: "viewer"}, resources={"drive": DRIVE}
    )
    card = boundary.space.card(
        design,
        "files",
        "com.twake.drive.file.updated.v1",
        actor=ALICE,
        object={
            "type": "file",
            "id": "a1b2c3",
            "title": "Roadmap.odt",
            "container": {"kind": "drive", "id": DRIVE},
        },
        preview="First draft",
        time="2026-10-05T09:00:00.000Z",
        updated_at="2026-10-05T10:00:00.000Z",
    )
    post = boundary.space.post(
        design, ALICE, "The roadmap is ready for review.", time="2026-10-06T08:30:00.000Z"
    )
    boundary.space.react(post, MMAUDET, "👍")
    boundary.space.react(post, BOB, "👍")
    boundary.space.react(post, BOB, "🎉")

    response = await client.get(FEEDS, params={"space_id": design.id}, headers=as_space_owner())

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {
                "item_id": post.id,
                "kind": "post",
                "category": "messages",
                "time": "2026-10-06T08:30:00Z",
                "updated_at": "2026-10-06T08:30:00Z",
                "edited_at": None,
                "event_type": None,
                "object": None,
                "by": {
                    "kind": "user",
                    "user_id": ALICE.user_id,
                    "you": False,
                    "untrusted": {"name": "Alice Martin"},
                },
                "reactions": [
                    {"count": 2, "mine": True, "untrusted": {"key": "👍"}},
                    {"count": 1, "mine": False, "untrusted": {"key": "🎉"}},
                ],
                "untrusted": {
                    "text": "The roadmap is ready for review.",
                    "title": None,
                    "preview": None,
                    "object_id": None,
                    "state": None,
                },
                "space": named(design),
            },
            {
                "item_id": card.id,
                "kind": "card",
                "category": "files",
                "time": "2026-10-05T09:00:00Z",
                "updated_at": "2026-10-05T10:00:00Z",
                "edited_at": None,
                "event_type": "com.twake.drive.file.updated.v1",
                "object": {"type": "file", "container_kind": "drive", "container_id": DRIVE},
                "by": {
                    "kind": "user",
                    "user_id": ALICE.user_id,
                    "you": False,
                    "untrusted": {"name": "Alice Martin"},
                },
                "reactions": [],
                "untrusted": {
                    "text": None,
                    "title": "Roadmap.odt",
                    "preview": "First draft",
                    "object_id": "a1b2c3",
                    "state": {},
                },
                "space": named(design),
            },
        ],
        "next": None,
        "truncated": False,
    }


async def test_older_items_follow_from_the_cursor_of_each_page(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    posts = [
        boundary.space.post(design, ALICE, f"Update {day}", time=f"2026-10-0{day}T09:00:00.000Z")
        for day in range(1, 6)
    ]
    page: dict[str, str | int] = {"space_id": design.id, "limit": 2}

    first = await client.get(FEEDS, params=page, headers=as_space_owner())
    second = await client.get(
        FEEDS, params=page | {"before": first.json()["next"]}, headers=as_space_owner()
    )
    last = await client.get(
        FEEDS, params=page | {"before": second.json()["next"]}, headers=as_space_owner()
    )

    pages = [[item["item_id"] for item in page.json()["items"]] for page in (first, second, last)]
    assert pages == [[posts[4].id, posts[3].id], [posts[2].id, posts[1].id], [posts[0].id]]
    assert last.json()["next"] is None


async def test_a_cursor_space_did_not_write_is_invalid(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor"})

    response = await client.get(
        FEEDS,
        params={"space_id": design.id, "before": "bm90LWEtY3Vyc29y"},
        headers=as_space_owner(),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert response.json()["detail"].startswith("before:")


async def test_a_category_keeps_its_cards_and_messages_the_posts_too(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})

    def card(category: str, title: str, day: int) -> str:
        found = boundary.space.card(
            design,
            category,
            f"com.twake.{category}.created.v1",
            actor=ALICE,
            object={"type": category, "id": title, "title": title},
            time=f"2026-10-0{day}T09:00:00.000Z",
        )
        return found.id

    roadmap = card("files", "Roadmap.odt", 1)
    card("events", "Design review", 2)
    invoice = card("messages", "Invoice", 3)
    post = boundary.space.post(design, ALICE, "Hello", time="2026-10-04T09:00:00.000Z").id
    space = {"space_id": design.id}

    files = await client.get(FEEDS, params=space | {"category": "files"}, headers=as_space_owner())
    messages = await client.get(
        FEEDS, params=space | {"category": "messages"}, headers=as_space_owner()
    )

    assert [item["item_id"] for item in files.json()["items"]] == [roadmap]
    assert [item["item_id"] for item in messages.json()["items"]] == [post, invoice]


async def test_what_people_wrote_comes_without_what_is_unseen_and_a_post_keeps_its_lines(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Titles, previews and what an app tells of an object come from whoever named a file or an
    # event; a post from a member: bidirectional marks, zero-width characters or line breaks
    # would pass for something else
    shown = space_person("alice", "Alice\u202e Martin")
    design = boundary.space.space("Design", {MMAUDET: "editor", shown: "admin"})
    card = boundary.space.card(
        design,
        "events",
        "com.twake.calendar.event.created.v1",
        actor=shown,
        object={"type": "event", "id": "ev\u200b-1", "title": "Road\u202emap\nreview"},
        preview="Agenda\u200b:\r\nitems",
        state={
            "location": "Room\u202e 4\nB",
            "rsvp": {"accepted": 2, "declined": 0},
            "allDay": False,
            "deep": {"a": {"b": {"c": {"d": 1}}}},
            "list": list(range(30)),
        },
        time="2026-10-05T09:00:00.000Z",
    )
    post = boundary.space.post(
        design,
        shown,
        "Line one\u200b\r\n\r\n\r\n\r\nLine\ttwo\u202e",
        time="2026-10-06T09:00:00.000Z",
    )
    boundary.space.react(post, shown, "\N{THUMBS UP SIGN}\u202e")

    response = await client.get(FEEDS, params={"space_id": design.id}, headers=as_space_owner())

    assert response.status_code == 200, response.text
    written, carded = response.json()["items"]
    assert written["item_id"] == post.id
    assert written["untrusted"]["text"] == "Line one\n\nLine two"
    assert written["by"]["untrusted"] == {"name": "Alice Martin"}
    assert written["reactions"][0]["untrusted"] == {"key": "\N{THUMBS UP SIGN}"}
    assert carded["item_id"] == card.id
    assert carded["untrusted"] == {
        "text": None,
        "title": "Roadmap review",
        "preview": "Agenda: items",
        "object_id": "ev-1",
        "state": {
            "location": "Room 4 B",
            "rsvp": {"accepted": 2, "declined": 0},
            "allDay": False,
            "deep": {"a": {"b": None}},
            "list": list(range(20)),
        },
    }


async def test_each_item_says_who_made_it(client: AsyncClient, boundary: FakeBoundary) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})

    def card(day: int, actor: dict[str, Any] | None) -> None:
        boundary.space.card(
            design,
            "activities",
            "com.twake.tasks.task.created.v1",
            actor=actor,
            object={"type": "task", "id": f"task-{day}", "title": f"Task {day}"},
            time=f"2026-10-0{day}T09:00:00.000Z",
        )

    # An application with a token of Space, an attendee outside the space, no one in particular
    card(1, {"type": "token", "id": "4f1d2c3b-1a2b-4c3d-8e4f-5a6b7c8d9e0f", "name": "CI bot"})
    card(2, {"type": "user", "id": None, "email": "guest@example.com"})
    card(3, None)
    boundary.space.post(design, None, "Former member's post", time="2026-10-04T09:00:00.000Z")
    boundary.space.post(design, MMAUDET, "My own post", time="2026-10-05T09:00:00.000Z")

    response = await client.get(FEEDS, params={"space_id": design.id}, headers=as_space_owner())

    assert response.status_code == 200, response.text
    assert [item["by"] for item in response.json()["items"]] == [
        {
            "kind": "user",
            "user_id": MMAUDET.user_id,
            "you": True,
            "untrusted": {"name": "Michel-Marie Maudet"},
        },
        {"kind": "deleted_user", "user_id": None, "you": False, "untrusted": {"name": None}},
        None,
        {"kind": "user", "user_id": None, "you": False, "untrusted": {"name": None}},
        {"kind": "token", "user_id": None, "you": False, "untrusted": {"name": "CI bot"}},
    ]


async def test_the_user_reads_one_item_of_the_feed(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    post = boundary.space.post(
        design,
        ALICE,
        "Ready for review",
        time="2026-10-06T08:30:00.000Z",
        edited_at="2026-10-06T09:00:00.000Z",
    )

    response = await client.get(
        f"/contracts/v1/space/spaces/{design.id}/feed/items/{post.id}", headers=as_space_owner()
    )

    assert response.status_code == 200, response.text
    answer = response.json()
    assert (answer["item_id"], answer["kind"]) == (post.id, "post")
    assert (answer["updated_at"], answer["edited_at"]) == (
        "2026-10-06T09:00:00Z",
        "2026-10-06T09:00:00Z",
    )
    assert answer["untrusted"]["text"] == "Ready for review"


async def test_an_item_outside_the_feed_of_the_space_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "editor", ALICE: "admin"})
    elsewhere = boundary.space.post(roadmap, ALICE, "Hello", time="2026-10-06T08:30:00.000Z")
    finance = boundary.space.space("Finance", {ALICE: "admin"})
    secret = boundary.space.post(finance, ALICE, "Budget", time="2026-10-06T08:30:00.000Z")

    unknown = await client.get(
        f"/contracts/v1/space/spaces/{design.id}/feed/items/{space_uuid('unknown')}",
        headers=as_space_owner(),
    )
    other = await client.get(
        f"/contracts/v1/space/spaces/{design.id}/feed/items/{elsewhere.id}",
        headers=as_space_owner(),
    )
    not_theirs = await client.get(
        f"/contracts/v1/space/spaces/{finance.id}/feed/items/{secret.id}", headers=as_space_owner()
    )

    assert (unknown.status_code, unknown.json()["code"]) == (404, "feed_item_not_found")
    assert (other.status_code, other.json()["code"]) == (404, "feed_item_not_found")
    assert (not_theirs.status_code, not_theirs.json()["code"]) == (404, "space_not_found")


async def test_the_feed_of_a_space_the_user_is_not_a_member_of_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    finance = boundary.space.space("Finance", {ALICE: "admin"})
    boundary.space.post(finance, ALICE, "Budget", time="2026-10-06T08:30:00.000Z")

    response = await client.get(FEEDS, params={"space_id": finance.id}, headers=as_space_owner())

    assert response.status_code == 404
    assert response.json()["code"] == "space_not_found"


async def test_without_a_space_the_feeds_of_the_users_spaces_come_merged_newest_first(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "viewer", BOB: "admin"})
    finance = boundary.space.space("Finance", {ALICE: "admin"})
    kickoff = boundary.space.post(design, ALICE, "Kick-off", time="2026-10-05T09:00:00.000Z")
    plan = boundary.space.post(roadmap, BOB, "Q4 plan", time="2026-10-06T09:00:00.000Z")
    mockups = boundary.space.post(design, MMAUDET, "Mockups", time="2026-10-07T09:00:00.000Z")
    boundary.space.post(finance, ALICE, "Budget", time="2026-10-07T10:00:00.000Z")

    response = await client.get(FEEDS, headers=as_space_owner())

    assert response.status_code == 200, response.text
    answer = response.json()
    assert [(item["item_id"], item["by"]["you"], item["space"]) for item in answer["items"]] == [
        (mockups.id, True, named(design)),
        (plan.id, False, named(roadmap)),
        (kickoff.id, False, named(design)),
    ]
    assert (answer["next"], answer["truncated"]) == (None, False)


async def test_the_feeds_of_all_the_spaces_go_back_seven_days_unless_told(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The clock reads 2026-10-08 at 07:00 UTC: 7 days back is 2026-10-01 at 07:00 UTC
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "viewer", BOB: "admin"})
    september = boundary.space.post(design, ALICE, "Old news", time="2026-09-30T09:00:00.000Z")
    early = boundary.space.post(roadmap, BOB, "Plan", time="2026-10-01T06:59:59.000Z")
    week = boundary.space.post(roadmap, BOB, "This week", time="2026-10-01T07:00:00.000Z")
    today = boundary.space.post(design, ALICE, "Today", time="2026-10-08T06:00:00.000Z")

    default = await client.get(FEEDS, headers=as_space_owner())
    told = await client.get(
        FEEDS, params={"since": "2026-10-05T00:00:00+02:00"}, headers=as_space_owner()
    )
    further = await client.get(
        FEEDS, params={"since": "2026-09-01T00:00:00Z"}, headers=as_space_owner()
    )

    assert items_of(default) == [today.id, week.id]
    assert items_of(told) == [today.id]
    assert items_of(further) == [today.id, week.id, early.id, september.id]
    assert {response.json()["truncated"] for response in (default, told, further)} == {False}


async def test_a_time_without_its_offset_is_invalid(client: AsyncClient) -> None:
    response = await client.get(
        FEEDS, params={"since": "2026-10-05T00:00:00"}, headers=as_space_owner()
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"


async def test_the_newest_items_of_all_the_spaces_come_limit_at_most_truncated_beyond(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "viewer", BOB: "admin"})
    posts = [
        boundary.space.post(
            (design, roadmap)[day % 2], MMAUDET, f"Day {day}", time=f"2026-10-0{day}T09:00:00.000Z"
        )
        for day in range(2, 8)
    ]
    # Older than the week: left out, without making the list one that is cut
    for day in (10, 20):
        boundary.space.post(design, ALICE, "Last month", time=f"2026-09-{day}T09:00:00.000Z")

    three = await client.get(FEEDS, params={"limit": 3}, headers=as_space_owner())
    six = await client.get(FEEDS, params={"limit": 6}, headers=as_space_owner())
    two_of_design = await client.get(
        FEEDS, params={"limit": 2, "category": "messages"}, headers=as_space_owner()
    )

    newest = [post.id for post in reversed(posts)]
    assert (items_of(three), three.json()["truncated"]) == (newest[:3], True)
    assert (items_of(six), six.json()["truncated"]) == (newest, False)
    assert (items_of(two_of_design), two_of_design.json()["truncated"]) == (newest[:2], True)


async def test_a_category_keeps_its_items_in_the_feeds_of_all_the_spaces(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "viewer", BOB: "admin"})

    def card(room: SpaceRoom, category: str, title: str, day: int) -> str:
        found = boundary.space.card(
            room,
            category,
            f"com.twake.{category}.created.v1",
            actor=ALICE,
            object={"type": category, "id": title, "title": title},
            time=f"2026-10-0{day}T09:00:00.000Z",
        )
        return found.id

    mockups = card(design, "files", "Mockups.odp", 2)
    card(design, "events", "Design review", 3)
    budget = card(roadmap, "files", "Budget.ods", 4)
    post = boundary.space.post(roadmap, BOB, "Hello", time="2026-10-05T09:00:00.000Z").id

    files = await client.get(FEEDS, params={"category": "files"}, headers=as_space_owner())
    messages = await client.get(FEEDS, params={"category": "messages"}, headers=as_space_owner())

    assert items_of(files) == [budget, mockups]
    assert items_of(messages) == [post]


async def test_the_feeds_of_the_first_fifty_spaces_by_name_are_read_and_truncated_says_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    rooms = [
        boundary.space.space(f"Space {number:02}", {MMAUDET: "viewer", ALICE: "admin"})
        for number in range(51)
    ]
    posts = [
        boundary.space.post(room, ALICE, room.name, time=f"2026-10-07T09:{number:02}:00.000Z")
        for number, room in enumerate(rooms)
    ]

    response = await client.get(FEEDS, params={"limit": 50}, headers=as_space_owner())

    assert items_of(response) == [post.id for post in reversed(posts[:50])]
    assert response.json()["truncated"] is True
    read = {path for method, path in boundary.space.requests if path.endswith("/feed")}
    assert read == {f"/spaces/{room.id}/feed" for room in rooms[:50]}


async def test_space_is_called_five_times_at_once_at_most(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    for number in range(12):
        room = boundary.space.space(f"Space {number:02}", {MMAUDET: "viewer"})
        boundary.space.post(room, MMAUDET, room.name, time="2026-10-07T09:00:00.000Z")

    response = await client.get(FEEDS, headers=as_space_owner())

    assert len(items_of(response)) == 12
    assert boundary.space.most_at_once == 5


async def test_a_cursor_needs_the_space_whose_feed_it_pages(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.space.space("Design", {MMAUDET: "editor"})

    response = await client.get(
        FEEDS, params={"before": "bm90LWEtY3Vyc29y"}, headers=as_space_owner()
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert response.json()["detail"].startswith("before:")
    assert boundary.space.requests == []


async def test_since_bounds_the_feed_of_one_space_too(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    posts = [
        boundary.space.post(design, ALICE, f"Update {day}", time=f"2026-09-2{day}T09:00:00.000Z")
        for day in range(1, 6)
    ]
    page: dict[str, str | int] = {
        "space_id": design.id,
        "since": "2026-09-23T00:00:00Z",
        "limit": 2,
    }

    first = await client.get(FEEDS, params=page, headers=as_space_owner())
    last = await client.get(
        FEEDS, params=page | {"before": first.json()["next"]}, headers=as_space_owner()
    )

    assert items_of(first) == [posts[4].id, posts[3].id]
    assert items_of(last) == [posts[2].id]
    assert last.json()["next"] is None


@pytest.mark.parametrize("gone", ["Design", "Roadmap"])
async def test_a_space_gone_while_the_feeds_are_read_is_left_out(
    client: AsyncClient, boundary: FakeBoundary, monkeypatch: pytest.MonkeyPatch, gone: str
) -> None:
    # The user left the space, or its admin deleted it, once Space listed it, before the
    # contract read its feed
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "viewer", BOB: "admin"})
    kept = {
        "Design": boundary.space.post(roadmap, BOB, "Q4 plan", time="2026-10-06T09:00:00.000Z"),
        "Roadmap": boundary.space.post(design, ALICE, "Kick-off", time="2026-10-05T09:00:00.000Z"),
    }
    handle = boundary.space.handle

    def leaving(request: httpx.Request) -> httpx.Response:
        answer = handle(request)
        if request.url.path == "/spaces":
            del boundary.space.spaces[space_uuid(gone)]
        return answer

    monkeypatch.setattr(boundary.space, "handle", leaving)

    response = await client.get(FEEDS, headers=as_space_owner())

    assert items_of(response) == [kept[gone].id]


async def test_the_users_own_items_are_told_apart_in_all_the_feeds_with_a_token_of_the_organization(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The first space by name may be one the user is not a member of
    boundary.space.tokens.update(TOKEN_OF_THE_ORGANIZATION)
    finance = boundary.space.space("Finance", {ALICE: "admin"})
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "viewer", BOB: "admin"})
    budget = boundary.space.post(finance, ALICE, "Budget", time="2026-10-06T09:00:00.000Z")
    mockups = boundary.space.post(roadmap, MMAUDET, "Mockups", time="2026-10-07T09:00:00.000Z")

    response = await client.get(FEEDS, headers=as_space_owner())

    assert response.status_code == 200, response.text
    assert [(item["item_id"], item["by"]["you"]) for item in response.json()["items"]] == [
        (mockups.id, True),
        (budget.id, False),
    ]
