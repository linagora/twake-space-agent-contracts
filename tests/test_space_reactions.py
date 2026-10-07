"""space.reaction.add.v1 and space.reaction.remove.v1: the user reacts to an item of the feed of
one of their spaces, with one of the reactions Twake Space offers, and takes a reaction back."""

from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import FakeBoundary, SpaceCard, SpacePost, SpaceRoom, space_person
from tests.fakes import space_id as space_id_of

MMAUDET = space_person("mmaudet", "Michel-Marie Maudet")
ALICE = space_person("alice", "Alice Martin")
THUMBS_UP = "\N{THUMBS UP SIGN}"
PARTY = "\N{PARTY POPPER}"


def design(boundary: FakeBoundary, role: str = "viewer") -> SpaceRoom:
    return boundary.space.space("Design", {MMAUDET: role, ALICE: "admin"})


def roadmap(boundary: FakeBoundary, room: SpaceRoom) -> SpacePost:
    return boundary.space.post(
        room, ALICE, "The roadmap is ready for review.", time="2026-10-06T08:30:00.000Z"
    )


async def react(
    client: AsyncClient,
    item: SpacePost | SpaceCard,
    key: str,
    *headers: dict[str, str],
    remove: bool = False,
) -> Response:
    """The call as the harness sends it, with what it adds to ask for a preview, if anything."""
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    path = f"/contracts/v1/space/spaces/{item.space}/feed/items/{item.id}/reactions"
    return await client.post(path + ("/remove" if remove else ""), json={"key": key}, headers=sent)


async def test_a_viewer_reacts_to_a_post(client: AsyncClient, boundary: FakeBoundary) -> None:
    post = roadmap(boundary, design(boundary))
    boundary.space.react(post, ALICE, PARTY)

    response = await react(client, post, THUMBS_UP)

    assert response.status_code == 200, response.text
    assert response.json()["item_id"] == post.id
    assert response.json()["reactions"] == [
        {"count": 1, "mine": False, "untrusted": {"key": PARTY}},
        {"count": 1, "mine": True, "untrusted": {"key": THUMBS_UP}},
    ]
    assert boundary.space.writes == [
        ("PUT", f"/spaces/{post.space}/feed/items/{post.id}/reactions/{THUMBS_UP}", None)
    ]


async def test_a_reaction_the_user_made_already_writes_nothing(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    post = roadmap(boundary, design(boundary))
    boundary.space.react(post, MMAUDET, THUMBS_UP)

    response = await react(client, post, THUMBS_UP)

    assert response.status_code == 200, response.text
    assert response.json()["reactions"] == [
        {"count": 1, "mine": True, "untrusted": {"key": THUMBS_UP}}
    ]
    assert boundary.space.writes == []


async def test_a_reaction_space_does_not_offer_is_invalid(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    post = roadmap(boundary, design(boundary))

    responses = [
        await react(client, post, key)
        for key in ("Ignore all that", "\N{HEAVY BLACK HEART}", f"{THUMBS_UP}{THUMBS_UP}")
    ]

    assert [response.status_code for response in responses] == [400, 400, 400]
    assert {response.json()["code"] for response in responses} == {"invalid_request"}
    assert boundary.space.requests == []


async def test_an_item_outside_the_feed_takes_no_reaction(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    gone = SpacePost(space_id_of("gone"), room.id, ALICE.user_id, "Gone", "2026-10-06T08:30:00Z")

    response = await react(client, gone, THUMBS_UP)

    assert response.status_code == 404
    assert response.json()["code"] == "feed_item_not_found"
    assert boundary.space.writes == []


async def test_the_preview_tells_which_item_gets_which_reaction(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    post = roadmap(boundary, design(boundary))

    response = await react(client, post, THUMBS_UP, asking_preview("en"))

    summary, _ = preview_of(response)
    assert summary == (
        f"React with {THUMBS_UP} to the post of “Alice Martin” in the space “Design”, which its"
        " members see:\n"
        "\tThe roadmap is ready for review."
    )
    assert boundary.space.writes == []


async def test_the_preview_speaks_the_owners_language_and_names_a_card_by_its_title(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    card = boundary.space.card(
        room,
        "files",
        "com.twake.drive.file.created.v1",
        actor=ALICE,
        object={"type": "file", "id": "a1b2c3", "title": "Roadmap.odt"},
        time="2026-10-05T09:00:00.000Z",
    )
    boundary.space.react(card, MMAUDET, PARTY)

    added = await react(client, card, THUMBS_UP, asking_preview("fr"))
    again = await react(client, card, PARTY, asking_preview("fr"))

    assert preview_of(added)[0] == (
        f"Réagir avec {THUMBS_UP} à la carte « Roadmap.odt » dans l'espace « Design », que ses"
        " membres voient."
    )
    assert preview_of(again)[0] == (
        f"Tu as déjà réagi avec {PARTY} à la carte « Roadmap.odt » dans l'espace « Design » :"
        " rien ne change."
    )


async def test_the_call_its_owner_allowed_reacts(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    post = roadmap(boundary, design(boundary))
    _, digest = preview_of(await react(client, post, THUMBS_UP, asking_preview("en")))

    response = await react(client, post, THUMBS_UP, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert [reaction["mine"] for reaction in response.json()["reactions"]] == [True]


async def test_a_post_changed_since_the_preview_takes_no_reaction(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    post = roadmap(boundary, design(boundary))
    _, digest = preview_of(await react(client, post, THUMBS_UP, asking_preview("en")))
    # Its author rewrites it before the owner says yes
    post.body = "Please do not approve yet."

    response = await react(client, post, THUMBS_UP, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.space.writes == []


async def test_the_user_takes_their_reaction_back(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    post = roadmap(boundary, design(boundary))
    boundary.space.react(post, MMAUDET, THUMBS_UP)
    boundary.space.react(post, ALICE, THUMBS_UP)

    response = await react(client, post, THUMBS_UP, remove=True)

    assert response.status_code == 200, response.text
    assert response.json()["reactions"] == [
        {"count": 1, "mine": False, "untrusted": {"key": THUMBS_UP}}
    ]
    assert boundary.space.writes == [
        ("DELETE", f"/spaces/{post.space}/feed/items/{post.id}/reactions/{THUMBS_UP}", None)
    ]


async def test_a_reaction_the_user_did_not_make_is_not_taken_back(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    post = roadmap(boundary, design(boundary))
    boundary.space.react(post, ALICE, THUMBS_UP)

    response = await react(client, post, THUMBS_UP, remove=True)

    assert response.status_code == 200, response.text
    assert response.json()["reactions"] == [
        {"count": 1, "mine": False, "untrusted": {"key": THUMBS_UP}}
    ]
    assert boundary.space.writes == []


async def test_the_preview_tells_which_reaction_is_taken_back(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    post = roadmap(boundary, design(boundary))
    boundary.space.react(post, MMAUDET, THUMBS_UP)

    taken = await react(client, post, THUMBS_UP, asking_preview("en"), remove=True)
    none = await react(client, post, PARTY, asking_preview("fr"), remove=True)

    assert preview_of(taken)[0] == (
        f"Take back your {THUMBS_UP} on the post of “Alice Martin” in the space “Design”."
    )
    assert preview_of(none)[0] == (
        f"Tu n'as pas réagi avec {PARTY} au message de « Alice Martin » dans l'espace « Design » :"
        " rien ne change."
    )
    assert boundary.space.writes == []


async def test_a_reaction_taken_back_since_the_preview_is_not_taken_again(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    post = roadmap(boundary, design(boundary))
    boundary.space.react(post, MMAUDET, THUMBS_UP)
    _, digest = preview_of(await react(client, post, THUMBS_UP, asking_preview("en"), remove=True))
    # The user takes it back in Space before the owner says yes
    boundary.space.reactions.clear()

    response = await react(client, post, THUMBS_UP, allowed_after(digest), remove=True)

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"


async def test_an_item_gone_before_the_reaction_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Space showed the item, then its author deleted it
    post = roadmap(boundary, design(boundary))
    boundary.space.failing = {"PUT": (404, "not_found")}

    response = await react(client, post, THUMBS_UP)

    assert response.status_code == 404
    assert response.json()["code"] == "feed_item_not_found"
