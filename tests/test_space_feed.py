"""space.feed.read.v1: the feed of a space of the user in Twake Space, its Fil: a card per object of
its apps, a file, an event, a task or an email, the posts of its members, and their reactions."""

from typing import Any

from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, space_person, space_uuid

MMAUDET = space_person("mmaudet", "Michel-Marie Maudet")
ALICE = space_person("alice", "Alice Martin")
BOB = space_person("bob")
DRIVE = "6650a1b2c3d4e5f6a7b8c9d0-drive"


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

    response = await client.get(f"/contracts/v1/space/spaces/{design.id}/feed", headers=AS_MMAUDET)

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
            },
        ],
        "next": None,
    }


async def test_older_items_follow_from_the_cursor_of_each_page(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor", ALICE: "admin"})
    posts = [
        boundary.space.post(design, ALICE, f"Update {day}", time=f"2026-10-0{day}T09:00:00.000Z")
        for day in range(1, 6)
    ]
    feed = f"/contracts/v1/space/spaces/{design.id}/feed"

    first = await client.get(feed, params={"limit": 2}, headers=AS_MMAUDET)
    second = await client.get(
        feed, params={"limit": 2, "before": first.json()["next"]}, headers=AS_MMAUDET
    )
    last = await client.get(
        feed, params={"limit": 2, "before": second.json()["next"]}, headers=AS_MMAUDET
    )

    pages = [[item["item_id"] for item in page.json()["items"]] for page in (first, second, last)]
    assert pages == [[posts[4].id, posts[3].id], [posts[2].id, posts[1].id], [posts[0].id]]
    assert last.json()["next"] is None


async def test_a_cursor_space_did_not_write_is_invalid(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "editor"})

    response = await client.get(
        f"/contracts/v1/space/spaces/{design.id}/feed",
        params={"before": "bm90LWEtY3Vyc29y"},
        headers=AS_MMAUDET,
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
    feed = f"/contracts/v1/space/spaces/{design.id}/feed"

    files = await client.get(feed, params={"category": "files"}, headers=AS_MMAUDET)
    messages = await client.get(feed, params={"category": "messages"}, headers=AS_MMAUDET)

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

    response = await client.get(f"/contracts/v1/space/spaces/{design.id}/feed", headers=AS_MMAUDET)

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

    response = await client.get(f"/contracts/v1/space/spaces/{design.id}/feed", headers=AS_MMAUDET)

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
        f"/contracts/v1/space/spaces/{design.id}/feed/items/{post.id}", headers=AS_MMAUDET
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
        headers=AS_MMAUDET,
    )
    other = await client.get(
        f"/contracts/v1/space/spaces/{design.id}/feed/items/{elsewhere.id}", headers=AS_MMAUDET
    )
    not_theirs = await client.get(
        f"/contracts/v1/space/spaces/{finance.id}/feed/items/{secret.id}", headers=AS_MMAUDET
    )

    assert (unknown.status_code, unknown.json()["code"]) == (404, "feed_item_not_found")
    assert (other.status_code, other.json()["code"]) == (404, "feed_item_not_found")
    assert (not_theirs.status_code, not_theirs.json()["code"]) == (404, "space_not_found")


async def test_the_feed_of_a_space_the_user_is_not_a_member_of_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    finance = boundary.space.space("Finance", {ALICE: "admin"})
    boundary.space.post(finance, ALICE, "Budget", time="2026-10-06T08:30:00.000Z")

    response = await client.get(f"/contracts/v1/space/spaces/{finance.id}/feed", headers=AS_MMAUDET)

    assert response.status_code == 404
    assert response.json()["code"] == "space_not_found"
