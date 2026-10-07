"""space.post.create.v1, space.post.update.v1 and space.post.delete.v1: the user posts in the feed
of one of their spaces, which every member sees, and edits and deletes their own posts."""

from typing import Any

from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import (
    FakeBoundary,
    SpaceMembership,
    SpacePerson,
    SpacePost,
    SpaceRoom,
    space_person,
    space_uuid,
)

MMAUDET = space_person("mmaudet", "Michel-Marie Maudet")
ALICE = space_person("alice", "Alice Martin")
BOB = space_person("bob")


def design(boundary: FakeBoundary, role: str = "editor") -> SpaceRoom:
    return boundary.space.space("Design", {MMAUDET: role, ALICE: "admin", BOB: "viewer"})


async def post(
    client: AsyncClient, room: SpaceRoom, body: Any, *headers: dict[str, str]
) -> Response:
    """The call as the harness sends it, with what it adds to ask for a preview, if anything."""
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.post(
        f"/contracts/v1/space/spaces/{room.id}/feed/posts", json=body, headers=sent
    )


async def test_an_editor_posts_in_the_feed_of_a_space(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await post(client, room, {"text": "  The roadmap is ready.\nReview it by Friday. "})

    assert response.status_code == 201, response.text
    answer = response.json()
    assert (answer["kind"], answer["category"], answer["edited_at"]) == ("post", "messages", None)
    assert answer["by"] == {
        "kind": "user",
        "user_id": MMAUDET.user_id,
        "you": True,
        "untrusted": {"name": "Michel-Marie Maudet"},
    }
    assert answer["untrusted"]["text"] == "The roadmap is ready.\nReview it by Friday."
    assert boundary.space.writes == [
        (
            "POST",
            f"/spaces/{room.id}/feed/posts",
            {"body": "The roadmap is ready.\nReview it by Friday."},
        )
    ]
    assert [stored.body for stored in boundary.space.posts.values()] == [
        "The roadmap is ready.\nReview it by Friday."
    ]


async def test_a_viewer_posts_nothing(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary, "viewer")

    response = await post(client, room, {"text": "Hello"})

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden_role"
    assert boundary.space.writes == []


async def test_a_role_lost_before_the_post_is_named_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Space showed the user as an editor, then an admin made them a viewer
    room = design(boundary)
    boundary.space.failing = {"POST": (403, "cannot_post")}

    response = await post(client, room, {"text": "Hello"})

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden_role"


async def test_a_blank_or_too_long_text_is_invalid(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    responses = [
        await post(client, room, body)
        for body in ({"text": " \n "}, {"text": "x" * 4001}, {"text": "Hi", "pinned": True}, {})
    ]

    assert [response.status_code for response in responses] == [400, 400, 400, 400]
    assert {response.json()["code"] for response in responses} == {"invalid_request"}
    assert boundary.space.requests == []


async def test_the_preview_tells_where_the_post_goes_and_who_sees_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    text = {"text": "The roadmap is ready.\nReview it by Friday."}

    english = await post(client, room, text, asking_preview("en"))
    french = await post(client, room, text, asking_preview("fr"))

    assert preview_of(english)[0] == (
        "Post in the feed of the space “Design”, which its 3 members see:\n"
        "\tThe roadmap is ready.\n"
        "\tReview it by Friday."
    )
    assert preview_of(french)[0] == (
        "Publier dans le fil de l'espace « Design », que ses 3 membres voient :\n"
        "\tThe roadmap is ready.\n"
        "\tReview it by Friday."
    )
    assert boundary.space.writes == []


async def test_the_call_its_owner_allowed_posts(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    _, digest = preview_of(await post(client, room, {"text": "Hello"}, asking_preview("en")))

    response = await post(client, room, {"text": "Hello"}, allowed_after(digest))

    assert response.status_code == 201, response.text
    assert [stored.body for stored in boundary.space.posts.values()] == ["Hello"]


async def test_a_post_to_members_changed_since_the_preview_is_not_posted(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    _, digest = preview_of(await post(client, room, {"text": "Hello"}, asking_preview("en")))
    # An admin adds someone before the owner says yes: more people would read it
    carol = space_person("carol")
    room.members[carol.user_id] = SpaceMembership(carol, "viewer")

    response = await post(client, room, {"text": "Hello"}, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.space.writes == []


async def edit(
    client: AsyncClient, written: SpacePost, body: Any, *headers: dict[str, str]
) -> Response:
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.patch(
        f"/contracts/v1/space/spaces/{written.space}/feed/posts/{written.id}",
        json=body,
        headers=sent,
    )


def own_post(boundary: FakeBoundary, room: SpaceRoom) -> SpacePost:
    return boundary.space.post(
        room, MMAUDET, "The roadmap is ready.", time="2026-10-06T08:30:00.000Z"
    )


async def test_the_author_edits_their_post(client: AsyncClient, boundary: FakeBoundary) -> None:
    written = own_post(boundary, design(boundary))
    boundary.space.now = "2026-10-07T11:00:00.000Z"

    response = await edit(client, written, {"text": "The roadmap is ready, at last."})

    assert response.status_code == 200, response.text
    answer = response.json()
    assert (answer["item_id"], answer["edited_at"]) == (written.id, "2026-10-07T11:00:00Z")
    assert answer["untrusted"]["text"] == "The roadmap is ready, at last."
    assert boundary.space.writes == [
        (
            "PATCH",
            f"/spaces/{written.space}/feed/posts/{written.id}",
            {"body": "The roadmap is ready, at last."},
        )
    ]


async def test_only_the_author_edits_a_post(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary, "admin")
    theirs = boundary.space.post(room, ALICE, "Hello", time="2026-10-06T08:30:00.000Z")

    response = await edit(client, theirs, {"text": "Bye"})

    assert response.status_code == 403
    assert response.json()["code"] == "not_author"
    assert boundary.space.writes == []


async def test_a_card_is_no_post_to_edit(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary)
    card = boundary.space.card(
        room,
        "files",
        "com.twake.drive.file.created.v1",
        actor=MMAUDET,
        object={"type": "file", "id": "a1b2c3", "title": "Roadmap.odt"},
        time="2026-10-05T09:00:00.000Z",
    )
    as_post = SpacePost(card.id, room.id, MMAUDET.user_id, "", card.time)

    response = await edit(client, as_post, {"text": "Bye"})

    assert response.status_code == 409
    assert response.json()["code"] == "not_a_post"
    assert boundary.space.writes == []


async def test_a_post_outside_the_feed_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    gone = SpacePost(space_uuid("gone"), room.id, MMAUDET.user_id, "", "2026-10-05T09:00:00Z")

    response = await edit(client, gone, {"text": "Bye"})

    assert response.status_code == 404
    assert response.json()["code"] == "feed_item_not_found"
    assert boundary.space.writes == []


async def test_the_same_text_changes_nothing(client: AsyncClient, boundary: FakeBoundary) -> None:
    written = own_post(boundary, design(boundary))

    response = await edit(client, written, {"text": " The roadmap is ready. "})

    assert response.status_code == 200, response.text
    assert response.json()["edited_at"] is None
    assert boundary.space.writes == []


async def test_space_refuses_anyone_but_the_author_whom_the_contract_cannot_tell(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # No member has the user's email: the contract cannot tell whose the post is, Space can
    elsewhere = SpacePerson(MMAUDET.user_id, "mmaudet", "michel@twake.test")
    room = boundary.space.space("Design", {elsewhere: "editor", ALICE: "admin"})
    theirs = boundary.space.post(room, ALICE, "Hello", time="2026-10-06T08:30:00.000Z")

    response = await edit(client, theirs, {"text": "Bye"})

    assert response.status_code == 403
    assert response.json()["code"] == "not_author"
    assert theirs.body == "Hello"


async def test_the_preview_tells_the_new_text_and_the_former_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    written = own_post(boundary, design(boundary))

    english = await edit(client, written, {"text": "Ready at last."}, asking_preview("en"))
    french = await edit(client, written, {"text": "Ready at last."}, asking_preview("fr"))
    same = await edit(client, written, {"text": "The roadmap is ready."}, asking_preview("en"))

    assert preview_of(english)[0] == (
        "Change your post in the feed of the space “Design”, which its 3 members see, to:\n"
        "\tReady at last.\n"
        "Instead of:\n"
        "\tThe roadmap is ready."
    )
    assert preview_of(french)[0] == (
        "Modifier ton message dans le fil de l'espace « Design », que ses 3 membres voient,"
        " en :\n"
        "\tReady at last.\n"
        "Au lieu de :\n"
        "\tThe roadmap is ready."
    )
    assert preview_of(same)[0] == (
        "Your post in the feed of the space “Design” says so already: nothing changes."
    )
    assert boundary.space.writes == []


async def test_a_post_changed_since_the_preview_is_left_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    written = own_post(boundary, design(boundary))
    _, digest = preview_of(await edit(client, written, {"text": "Ready."}, asking_preview("en")))
    # The user edits it in Space before the owner says yes
    written.body = "The roadmap is ready, see the board."

    response = await edit(client, written, {"text": "Ready."}, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.space.writes == []


async def test_the_edit_its_owner_allowed_is_made(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    written = own_post(boundary, design(boundary))
    _, digest = preview_of(await edit(client, written, {"text": "Ready."}, asking_preview("en")))

    response = await edit(client, written, {"text": "Ready."}, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert written.body == "Ready."


async def delete(client: AsyncClient, written: SpacePost, *headers: dict[str, str]) -> Response:
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.delete(
        f"/contracts/v1/space/spaces/{written.space}/feed/posts/{written.id}", headers=sent
    )


async def test_the_author_deletes_their_post_with_its_reactions(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    written = own_post(boundary, design(boundary))
    boundary.space.react(written, ALICE, "\N{PARTY POPPER}")

    response = await delete(client, written)

    assert response.status_code == 200, response.text
    assert (response.json()["item_id"], response.json()["untrusted"]["text"]) == (
        written.id,
        "The roadmap is ready.",
    )
    assert boundary.space.writes == [
        ("DELETE", f"/spaces/{written.space}/feed/posts/{written.id}", None)
    ]
    assert (boundary.space.posts, boundary.space.reactions) == ({}, [])


async def test_only_the_author_deletes_a_post(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary, "admin")
    theirs = boundary.space.post(room, ALICE, "Hello", time="2026-10-06T08:30:00.000Z")

    response = await delete(client, theirs)

    assert response.status_code == 403
    assert response.json()["code"] == "not_author"
    assert boundary.space.writes == []


async def test_the_preview_tells_which_post_goes_for_good(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    written = own_post(boundary, design(boundary))

    english = await delete(client, written, asking_preview("en"))
    french = await delete(client, written, asking_preview("fr"))

    assert preview_of(english)[0] == (
        "Delete your post from the feed of the space “Design”, for good, with its reactions:\n"
        "\tThe roadmap is ready."
    )
    assert preview_of(french)[0] == (
        "Supprimer ton message du fil de l'espace « Design », définitivement, avec ses"
        " réactions :\n"
        "\tThe roadmap is ready."
    )
    assert boundary.space.writes == []


async def test_a_post_changed_since_the_preview_is_not_deleted(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    written = own_post(boundary, design(boundary))
    _, digest = preview_of(await delete(client, written, asking_preview("en")))
    written.body = "Keep this one: the board links to it."

    changed = await delete(client, written, allowed_after(digest))
    _, again = preview_of(await delete(client, written, asking_preview("en")))
    allowed = await delete(client, written, allowed_after(again))

    assert (changed.status_code, changed.json()["code"]) == (409, "changed_since_preview")
    assert allowed.status_code == 200, allowed.text
    assert boundary.space.posts == {}


async def test_the_preview_of_the_longest_posts_stays_within_what_the_harness_shows(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Characters of four bytes, as many as Space takes: the summary shows the beginning of each
    # text and says how much it leaves out
    room = design(boundary)
    longest = "\N{GRINNING FACE}" * 4000
    written = boundary.space.post(room, MMAUDET, longest, time="2026-10-06T08:30:00.000Z")

    posted = await post(client, room, {"text": longest}, asking_preview("en"))
    edited = await edit(
        client, written, {"text": "\N{THUMBS UP SIGN}" * 4000}, asking_preview("en")
    )

    for response in (posted, edited):
        summary, _ = preview_of(response)
        assert "more characters are not shown)" in summary


async def test_a_text_holding_a_control_or_format_character_is_refused(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The preview leaves out what a reader does not see, which members would read as posted: a
    # right-to-left override would turn the rest of the post around for them alone
    room = design(boundary)
    written = own_post(boundary, room)
    text = {"text": "Approved for 1000\u202e EUR"}

    posted = await post(client, room, text)
    edited = await edit(client, written, text)

    for response in (posted, edited):
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_request"
        assert "U+202E RIGHT-TO-LEFT OVERRIDE" in response.json()["detail"]
    assert boundary.space.writes == []
