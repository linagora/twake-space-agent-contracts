"""space.member.add.v1, space.member.update.v1 and space.member.remove.v1: an admin of a space adds
people of their organization to it, changes the role of its members and removes them."""

from typing import Any

from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import FakeBoundary, SpaceRoom, space_person

MMAUDET = space_person("mmaudet", "Michel-Marie Maudet")
ALICE = space_person("alice", "Alice Martin")
BOB = space_person("bob", "Bob Durand")
JEANNE = space_person("jmartin", "Jeanne Martin")
PAUL = space_person("pmartin")


def design(boundary: FakeBoundary, role: str = "admin") -> SpaceRoom:
    room = boundary.space.space("Design", {MMAUDET: role, ALICE: "admin", BOB: "viewer"})
    boundary.space.people(JEANNE, PAUL)
    return room


async def add(
    client: AsyncClient, room: SpaceRoom, body: Any, *headers: dict[str, str]
) -> Response:
    """The call as the harness sends it, with what it adds to ask for a preview, if anything."""
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.post(
        f"/contracts/v1/space/spaces/{room.id}/members", json=body, headers=sent
    )


async def test_an_admin_adds_people_of_their_organization_to_a_space(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await add(client, room, {"usernames": ["jmartin", "pmartin"], "role": "editor"})

    assert response.status_code == 200, response.text
    assert [
        (member["username"], member["role"], member["you"]) for member in response.json()["members"]
    ] == [
        ("alice", "admin", False),
        ("bob", "viewer", False),
        ("jmartin", "editor", False),
        ("mmaudet", "admin", True),
        ("pmartin", "editor", False),
    ]
    assert boundary.space.writes == [
        (
            "POST",
            f"/spaces/{room.id}/members",
            {"usernames": ["jmartin", "pmartin"], "role": "editor"},
        )
    ]


async def test_only_an_admin_adds_members(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary, "editor")

    response = await add(client, room, {"usernames": ["jmartin"], "role": "viewer"})

    assert response.status_code == 403
    assert response.json()["code"] == "not_space_admin"
    assert boundary.space.writes == []


async def test_an_admin_role_lost_before_the_write_is_named_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Space showed the user as an admin, then another admin made them an editor
    room = design(boundary)
    boundary.space.failing = {"POST": (403, "not_space_admin")}

    response = await add(client, room, {"usernames": ["jmartin"], "role": "viewer"})

    assert response.status_code == 403
    assert response.json()["code"] == "not_space_admin"


async def test_someone_the_organization_does_not_have_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    # Someone of another organization, whom the user never finds
    boundary.space.people(space_person("carol"), organization="acme")

    responses = [
        await add(client, room, {"usernames": ["jmartin", username], "role": "viewer"})
        for username in ("nobody", "carol", "martin")
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert {response.json()["code"] for response in responses} == {"person_not_found"}
    assert [response.json()["usernames"] for response in responses] == [
        ["nobody"],
        ["carol"],
        ["martin"],
    ]
    assert boundary.space.writes == []


async def test_people_are_found_whatever_the_case_of_their_username(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await add(client, room, {"usernames": ["JMartin"], "role": "viewer"})

    assert response.status_code == 200, response.text
    assert boundary.space.writes == [
        ("POST", f"/spaces/{room.id}/members", {"usernames": ["jmartin"], "role": "viewer"})
    ]


async def test_a_member_of_another_role_is_not_added_again(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await add(client, room, {"usernames": ["jmartin", "bob"], "role": "editor"})

    assert response.status_code == 409
    assert response.json()["code"] == "member_exists"
    assert response.json()["members"] == [{"user_id": BOB.user_id, "role": "viewer"}]
    assert boundary.space.writes == []


async def test_members_of_that_role_already_are_left_as_they_are(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    some = await add(client, room, {"usernames": ["bob", "jmartin"], "role": "viewer"})
    written = list(boundary.space.writes)
    none = await add(client, room, {"usernames": ["bob", "jmartin"], "role": "viewer"})

    assert some.status_code == none.status_code == 200
    assert written == [
        ("POST", f"/spaces/{room.id}/members", {"usernames": ["jmartin"], "role": "viewer"})
    ]
    assert boundary.space.writes == written


async def test_space_refusing_the_people_after_the_contract_found_them_is_named_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Someone made them a member, or their account was disabled, between the read and the write
    room = design(boundary)
    body = {"usernames": ["jmartin"], "role": "viewer"}

    boundary.space.failing = {"POST": (409, "MEMBER_EXISTS")}
    exists = await add(client, room, body)
    boundary.space.failing = {"POST": (404, "USER_NOT_FOUND")}
    gone = await add(client, room, body)

    assert (exists.status_code, exists.json()["code"]) == (409, "member_exists")
    assert (gone.status_code, gone.json()["code"]) == (404, "person_not_found")
    assert gone.json()["usernames"] == ["jmartin"]


async def test_the_preview_names_whom_the_space_takes_in_and_as_what(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    body = {"usernames": ["jmartin", "pmartin", "bob"], "role": "viewer"}

    english = await add(client, room, body, asking_preview("en"))
    french = await add(client, room, body, asking_preview("fr"))

    assert preview_of(english)[0] == (
        "Add to the space “Design”, as viewers, these people, who then see all it holds:\n"
        "\t“Jeanne Martin” <jmartin@twake.test>\n"
        "\t<pmartin@twake.test>\n"
        "Members already, left as they are:\n"
        "\t“Bob Durand” <bob@twake.test>"
    )
    assert preview_of(french)[0] == (
        "Ajouter à l'espace « Design », comme lecteurs, ces personnes, qui en voient alors tout"
        " le contenu :\n"
        "\t« Jeanne Martin » <jmartin@twake.test>\n"
        "\t<pmartin@twake.test>\n"
        "Déjà membres, laissés tels quels :\n"
        "\t« Bob Durand » <bob@twake.test>"
    )
    assert boundary.space.writes == []


async def test_the_preview_says_when_nothing_changes(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await add(
        client, room, {"usernames": ["bob"], "role": "viewer"}, asking_preview("en")
    )

    assert preview_of(response)[0] == (
        "Nothing changes in the space “Design”: these people are viewers there already:\n"
        "\t“Bob Durand” <bob@twake.test>"
    )


async def test_the_people_its_owner_allowed_are_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    body = {"usernames": ["jmartin"], "role": "editor"}
    _, digest = preview_of(await add(client, room, body, asking_preview("en")))

    response = await add(client, room, body, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert room.members[JEANNE.user_id] == (JEANNE, "editor")


async def test_people_whose_membership_changed_since_the_preview_are_not_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    body = {"usernames": ["jmartin", "pmartin"], "role": "editor"}
    _, digest = preview_of(await add(client, room, body, asking_preview("en")))
    # Another admin adds one of them before the owner says yes
    room.members[PAUL.user_id] = (PAUL, "editor")

    response = await add(client, room, body, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.space.writes == []


async def change(
    client: AsyncClient, room: SpaceRoom, user_id: str, body: Any, *headers: dict[str, str]
) -> Response:
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.patch(
        f"/contracts/v1/space/spaces/{room.id}/members/{user_id}", json=body, headers=sent
    )


async def test_an_admin_changes_the_role_of_a_member(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await change(client, room, BOB.user_id, {"role": "editor"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "user_id": BOB.user_id,
        "username": "bob",
        "email": "bob@twake.test",
        "role": "editor",
        "you": False,
        "untrusted": {"display_name": "Bob Durand"},
    }
    assert boundary.space.writes == [
        ("PATCH", f"/spaces/{room.id}/members/{BOB.user_id}", {"role": "editor"})
    ]
    assert room.members[BOB.user_id] == (BOB, "editor")


async def test_a_role_is_changed_by_an_admin_for_a_member_only(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    outsider = await change(client, room, JEANNE.user_id, {"role": "editor"})
    room.members[MMAUDET.user_id] = (MMAUDET, "editor")
    not_admin = await change(client, room, BOB.user_id, {"role": "editor"})

    assert (outsider.status_code, outsider.json()["code"]) == (404, "member_not_found")
    assert (not_admin.status_code, not_admin.json()["code"]) == (403, "not_space_admin")
    assert boundary.space.writes == []


async def test_the_same_role_changes_nothing(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary)

    response = await change(client, room, BOB.user_id, {"role": "viewer"})

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "viewer"
    assert boundary.space.writes == []


async def test_the_last_admin_keeps_their_role(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = boundary.space.space("Design", {MMAUDET: "admin", BOB: "viewer"})

    response = await change(client, room, MMAUDET.user_id, {"role": "editor"})

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin"
    assert room.members[MMAUDET.user_id] == (MMAUDET, "admin")


async def test_a_member_space_no_longer_finds_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Removed by another admin, or a member through a linked group only, whom ldap-rest does not
    # take as a member of their own
    room = design(boundary)
    boundary.space.failing = {"PATCH": (404, "MEMBER_NOT_FOUND")}

    response = await change(client, room, BOB.user_id, {"role": "editor"})

    assert response.status_code == 404
    assert response.json()["code"] == "member_not_found"


async def test_the_preview_tells_the_new_role_and_the_former_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    english = await change(client, room, BOB.user_id, {"role": "admin"}, asking_preview("en"))
    french = await change(client, room, BOB.user_id, {"role": "editor"}, asking_preview("fr"))
    same = await change(client, room, BOB.user_id, {"role": "viewer"}, asking_preview("en"))

    assert preview_of(english)[0] == (
        "Make “Bob Durand” <bob@twake.test> an admin of the space “Design”, instead of a viewer:"
        " they then add, change and remove its members."
    )
    assert preview_of(french)[0] == (
        "Faire de « Bob Durand » <bob@twake.test> un éditeur de l'espace « Design », au lieu"
        " d'un lecteur."
    )
    assert preview_of(same)[0] == (
        "“Bob Durand” <bob@twake.test> is a viewer of the space “Design” already: nothing changes."
    )
    assert boundary.space.writes == []


async def test_a_role_changed_since_the_preview_is_left_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    _, digest = preview_of(
        await change(client, room, BOB.user_id, {"role": "admin"}, asking_preview("en"))
    )
    room.members[BOB.user_id] = (BOB, "editor")

    changed = await change(client, room, BOB.user_id, {"role": "admin"}, allowed_after(digest))
    room.members[BOB.user_id] = (BOB, "viewer")
    allowed = await change(client, room, BOB.user_id, {"role": "admin"}, allowed_after(digest))

    assert (changed.status_code, changed.json()["code"]) == (409, "changed_since_preview")
    assert allowed.status_code == 200, allowed.text
    assert room.members[BOB.user_id] == (BOB, "admin")


async def remove(
    client: AsyncClient, room: SpaceRoom, user_id: str, *headers: dict[str, str]
) -> Response:
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.delete(
        f"/contracts/v1/space/spaces/{room.id}/members/{user_id}", headers=sent
    )


async def test_an_admin_removes_a_member(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary)

    response = await remove(client, room, BOB.user_id)

    assert response.status_code == 200, response.text
    assert (response.json()["user_id"], response.json()["role"]) == (BOB.user_id, "viewer")
    assert boundary.space.writes == [("DELETE", f"/spaces/{room.id}/members/{BOB.user_id}", None)]
    assert BOB.user_id not in room.members


async def test_a_member_is_removed_by_an_admin_and_the_last_admin_stays(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = boundary.space.space("Design", {MMAUDET: "admin", BOB: "viewer"})
    outsider = await remove(client, room, JEANNE.user_id)
    last = await remove(client, room, MMAUDET.user_id)
    room.members[MMAUDET.user_id] = (MMAUDET, "editor")
    not_admin = await remove(client, room, BOB.user_id)

    assert (outsider.status_code, outsider.json()["code"]) == (404, "member_not_found")
    assert (last.status_code, last.json()["code"]) == (409, "last_admin")
    assert (not_admin.status_code, not_admin.json()["code"]) == (403, "not_space_admin")
    assert list(room.members) == [MMAUDET.user_id, BOB.user_id]


async def test_the_preview_tells_who_leaves_the_space(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    english = await remove(client, room, BOB.user_id, asking_preview("en"))
    french = await remove(client, room, BOB.user_id, asking_preview("fr"))
    yourself = await remove(client, room, MMAUDET.user_id, asking_preview("en"))

    assert preview_of(english)[0] == (
        "Remove “Bob Durand” <bob@twake.test> from the space “Design”: they no longer see what it"
        " holds."
    )
    assert preview_of(french)[0] == (
        "Retirer « Bob Durand » <bob@twake.test> de l'espace « Design » : cette personne n'en"
        " voit plus le contenu."
    )
    assert preview_of(yourself)[0] == (
        "Remove yourself from the space “Design”: you no longer see what it holds."
    )
    assert boundary.space.writes == []


async def test_a_member_changed_since_the_preview_stays(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    _, digest = preview_of(await remove(client, room, BOB.user_id, asking_preview("en")))
    room.members[BOB.user_id] = (BOB, "admin")

    response = await remove(client, room, BOB.user_id, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert room.members[BOB.user_id] == (BOB, "admin")


async def test_the_preview_of_twenty_people_of_long_names_stays_within_what_the_harness_shows(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    people = [space_person(f"person{number:02}", "\N{GRINNING FACE}" * 255) for number in range(20)]
    boundary.space.people(*people)

    response = await add(
        client,
        room,
        {"usernames": [person.username for person in people], "role": "viewer"},
        asking_preview("en"),
    )

    summary, _ = preview_of(response)
    lines = summary.splitlines()
    assert len(lines) == 21
    assert all(
        line.endswith(f"<person{number:02}@twake.test>") for number, line in enumerate(lines[1:])
    )
