"""space.member.add.v1, space.member.update.v1 and space.member.remove.v1: an admin of a space adds
people of their organization to it, changes the role of its members and removes them, with the
owner's API token of Space."""

from typing import Any

from httpx import AsyncClient, Response

from tests.conftest import allowed_after, asking_preview, preview_of
from tests.fakes import (
    FakeBoundary,
    SpaceGroupLink,
    SpaceMembership,
    SpacePerson,
    SpaceRoom,
    as_space_owner,
    space_person,
)

MMAUDET = space_person("mmaudet", "Michel-Marie Maudet")
ALICE = space_person("alice", "Alice Martin")
BOB = space_person("bob", "Bob Durand")
JEANNE = space_person("jmartin", "Jeanne Martin")
PAUL = space_person("pmartin")
CAROL = space_person("carol", "Carol King")
DESIGNERS = "c2a8e1f0-7b3d-4e9a-8f61-2d5b9c0e4a17"


def in_organization(boundary: FakeBoundary, *people: SpacePerson) -> None:
    """Arranges people in the directory of the organization, whatever spaces they are in."""
    boundary.space.people.update({person.user_id: person for person in people})


def design(boundary: FakeBoundary, role: str = "admin") -> SpaceRoom:
    """The space Design, where the user has that role; Jeanne and Paul are in the organization, in
    none of the user's spaces."""
    room = boundary.space.space("Design", {MMAUDET: role, ALICE: "admin", BOB: "viewer"})
    in_organization(boundary, JEANNE, PAUL)
    return room


def with_designers(boundary: FakeBoundary) -> SpaceRoom:
    """The space Design, which the group Designers is linked to, as editors, with Carol in it."""
    room = boundary.space.space(
        "Design",
        {MMAUDET: "admin", ALICE: "admin", BOB: "viewer"},
        groups=[SpaceGroupLink(DESIGNERS, "Designers", "editor", [CAROL])],
    )
    in_organization(boundary, JEANNE, PAUL)
    return room


def sent(boundary: FakeBoundary) -> list[tuple[str, str]]:
    """The writes the contracts sent to Space, by method and path, whether Space took them or
    not."""
    return [(method, path) for method, path in boundary.space.requests if method != "GET"]


def headers_of(*more: dict[str, str]) -> dict[str, str]:
    """What the harness sends, with what it adds to ask for a preview or to make the call its owner
    allowed, if anything."""
    return as_space_owner() | {name: value for headers in more for name, value in headers.items()}


async def add(
    client: AsyncClient, room: SpaceRoom, body: Any, *headers: dict[str, str]
) -> Response:
    return await client.post(
        f"/contracts/v1/space/spaces/{room.id}/members", json=body, headers=headers_of(*headers)
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
    # Space keeps the directory of the organization to a person's session
    assert [path for _, path in boundary.space.requests if not path.startswith("/spaces")] == []


async def test_only_an_admin_adds_members(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary, "editor")

    response = await add(client, room, {"usernames": ["jmartin"], "role": "viewer"})

    assert response.status_code == 403
    assert response.json()["code"] == "not_space_admin"
    assert sent(boundary) == []


async def test_an_admin_role_lost_before_the_write_is_named_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Space showed the user as an admin, then another admin made them an editor
    room = design(boundary)

    def demoted() -> None:
        room.members[MMAUDET.user_id] = SpaceMembership(MMAUDET, "editor")

    boundary.space.while_writing = demoted

    response = await add(client, room, {"usernames": ["jmartin"], "role": "viewer"})

    assert response.status_code == 403
    assert response.json()["code"] == "not_space_admin"
    assert JEANNE.user_id not in room.listed()


async def test_a_username_the_organization_does_not_have_adds_nobody(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Space passes the whole list on to ldap-rest, which takes all the people or none, and does
    # not say which username it lacks
    room = design(boundary)
    body = {"usernames": ["jmartin", "nobody"], "role": "viewer"}

    response = await add(client, room, body)

    assert response.status_code == 404
    assert response.json()["code"] == "person_not_found"
    assert (response.json()["usernames"], response.json()["entered"]) == (
        ["jmartin", "nobody"],
        [],
    )
    assert response.json()["detail"].endswith("Nobody was added.")
    assert boundary.space.writes == [("POST", f"/spaces/{room.id}/members", body)]
    assert JEANNE.user_id not in room.listed()


async def test_those_the_space_lists_all_the_same_once_a_username_is_refused_are_named(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Another admin adds Jeanne while Space takes the write: the space lists her once read again
    room = design(boundary)

    def added() -> None:
        room.members[JEANNE.user_id] = SpaceMembership(JEANNE, "editor")

    boundary.space.while_writing = added

    response = await add(client, room, {"usernames": ["JMartin", "nobody"], "role": "viewer"})

    assert response.status_code == 404
    assert response.json()["code"] == "person_not_found"
    assert response.json()["usernames"] == ["nobody"]
    assert response.json()["entered"] == [
        {"user_id": JEANNE.user_id, "username": "jmartin", "role": "editor"}
    ]
    assert "entered names" in response.json()["detail"]


async def test_each_person_is_added_once_whatever_the_case_of_their_username(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await add(client, room, {"usernames": ["JMartin", "jmartin"], "role": "viewer"})

    assert response.status_code == 200, response.text
    assert boundary.space.writes == [
        ("POST", f"/spaces/{room.id}/members", {"usernames": ["JMartin"], "role": "viewer"})
    ]
    assert room.members[JEANNE.user_id] == SpaceMembership(JEANNE, "viewer")


async def test_a_member_of_another_role_is_not_added_again(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await add(client, room, {"usernames": ["jmartin", "bob"], "role": "editor"})

    assert response.status_code == 409
    assert response.json()["code"] == "member_exists"
    assert response.json()["members"] == [{"user_id": BOB.user_id, "role": "viewer"}]
    assert sent(boundary) == []


async def test_members_of_that_role_already_are_left_as_they_are(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    some = await add(client, room, {"usernames": ["bob", "jmartin"], "role": "viewer"})
    written = sent(boundary)
    none = await add(client, room, {"usernames": ["bob", "jmartin"], "role": "viewer"})

    assert some.status_code == none.status_code == 200
    assert boundary.space.writes == [
        ("POST", f"/spaces/{room.id}/members", {"usernames": ["jmartin"], "role": "viewer"})
    ]
    assert sent(boundary) == written


async def test_space_refusing_a_member_of_another_role_after_the_contract_read_the_space(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Another admin adds Jeanne as an editor while Space takes the write: ldap-rest refuses her
    room = design(boundary)

    def added() -> None:
        room.members[JEANNE.user_id] = SpaceMembership(JEANNE, "editor")

    boundary.space.while_writing = added

    response = await add(client, room, {"usernames": ["jmartin"], "role": "viewer"})

    assert response.status_code == 409
    assert response.json()["code"] == "member_exists"
    assert response.json()["members"] == []
    assert room.members[JEANNE.user_id] == SpaceMembership(JEANNE, "editor")


async def test_adding_a_member_through_a_linked_group_makes_them_a_direct_member(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # ldap-rest's member routes see the direct members alone: it adds Carol as one, whose
    # strongest role the space then lists
    room = with_designers(boundary)

    response = await add(client, room, {"usernames": ["carol"], "role": "admin"})

    assert response.status_code == 200, response.text
    assert room.members[CAROL.user_id] == SpaceMembership(CAROL, "admin")
    assert [
        (member["username"], member["role"])
        for member in response.json()["members"]
        if member["username"] == "carol"
    ] == [("carol", "admin")]
    assert boundary.space.writes == [
        ("POST", f"/spaces/{room.id}/members", {"usernames": ["carol"], "role": "admin"})
    ]


async def test_a_direct_member_of_another_role_in_a_space_with_groups_adds_nobody(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The contract cannot tell Bob, a direct member, from someone the space lists through a
    # group: ldap-rest refuses him, and nobody is added
    room = with_designers(boundary)

    response = await add(client, room, {"usernames": ["jmartin", "bob"], "role": "editor"})

    assert response.status_code == 409
    assert response.json()["code"] == "member_exists"
    assert response.json()["members"] == [{"user_id": BOB.user_id, "role": "viewer"}]
    assert JEANNE.user_id not in room.listed()
    assert room.members[BOB.user_id] == SpaceMembership(BOB, "viewer")


def marketing(boundary: FakeBoundary) -> SpaceRoom:
    """Another space of the user's, where Jeanne and Paul are members."""
    return boundary.space.space("Marketing", {MMAUDET: "viewer", JEANNE: "editor", PAUL: "viewer"})


async def test_the_preview_names_whom_the_space_takes_in_and_as_what(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    marketing(boundary)
    body = {"usernames": ["jmartin", "pmartin", "bob"], "role": "viewer"}

    english = await add(client, room, body, asking_preview("en"))
    french = await add(client, room, body, asking_preview("fr"))

    assert preview_of(english)[0] == (
        "Add to the space “Design”, as viewers, these people, who then see all it holds:\n"
        f"\t“Jeanne Martin” <{JEANNE.email}>\n"
        f"\t<{PAUL.email}>\n"
        "Members already, left as they are:\n"
        f"\t“Bob Durand” <{BOB.email}>"
    )
    assert preview_of(french)[0] == (
        "Ajouter à l'espace « Design », comme lecteurs, ces personnes, qui en voient alors tout"
        " le contenu :\n"
        f"\t« Jeanne Martin » <{JEANNE.email}>\n"
        f"\t<{PAUL.email}>\n"
        "Déjà membres, laissés tels quels :\n"
        f"\t« Bob Durand » <{BOB.email}>"
    )
    assert sent(boundary) == []
    # It knows people by the members of the user's spaces: Space keeps the directory of the
    # organization to a person's session
    assert [path for _, path in boundary.space.requests if not path.startswith("/spaces")] == []


async def test_the_preview_flags_a_username_none_of_the_users_spaces_has(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Jeanne is a person of the organization in none of the user's spaces: the contract cannot
    # tell her username from one the organization does not have
    room = design(boundary)
    body = {"usernames": ["jmartin"], "role": "editor"}

    english = await add(client, room, body, asking_preview("en"))
    french = await add(client, room, body, asking_preview("fr"))

    assert preview_of(english)[0] == (
        "Add to the space “Design”, as editors, these people, who then see all it holds:\n"
        "\t“jmartin” (in none of your spaces: check this username)"
    )
    assert preview_of(french)[0] == (
        "Ajouter à l'espace « Design », comme éditeurs, ces personnes, qui en voient alors tout"
        " le contenu :\n"
        "\t« jmartin » (dans aucun de tes espaces : vérifie cet identifiant)"
    )


async def test_the_preview_says_when_nothing_changes(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await add(
        client, room, {"usernames": ["bob"], "role": "viewer"}, asking_preview("en")
    )

    assert preview_of(response)[0] == (
        "Nothing changes in the space “Design”: these people are viewers there already:\n"
        f"\t“Bob Durand” <{BOB.email}>"
    )
    # Nobody to add, whom the user's other spaces would name
    assert ("GET", "/spaces") not in boundary.space.requests


async def test_the_people_its_owner_allowed_are_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    body = {"usernames": ["jmartin"], "role": "editor"}
    _, digest = preview_of(await add(client, room, body, asking_preview("en")))

    response = await add(client, room, body, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert room.members[JEANNE.user_id] == SpaceMembership(JEANNE, "editor")


async def test_people_whose_membership_changed_since_the_preview_are_not_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    body = {"usernames": ["jmartin", "pmartin"], "role": "editor"}
    _, digest = preview_of(await add(client, room, body, asking_preview("en")))
    # Another admin adds one of them before the owner says yes
    room.members[PAUL.user_id] = SpaceMembership(PAUL, "editor")

    response = await add(client, room, body, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert sent(boundary) == []


async def test_the_preview_of_twenty_people_of_long_names_stays_within_what_the_harness_shows(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    people = [space_person(f"person{number:02}", "\N{GRINNING FACE}" * 255) for number in range(20)]
    boundary.space.space("Others", {MMAUDET: "viewer"} | dict.fromkeys(people, "viewer"))

    response = await add(
        client,
        room,
        {"usernames": [person.username for person in people], "role": "viewer"},
        asking_preview("en"),
    )

    lines = preview_of(response)[0].splitlines()
    assert len(lines) == 21
    assert all(
        line.endswith(f"<{person.email}>") for person, line in zip(people, lines[1:], strict=True)
    )


async def test_the_preview_tells_that_adding_makes_members_through_a_group_direct_ones(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = with_designers(boundary)
    marketing(boundary)
    body = {"usernames": ["jmartin", "carol"], "role": "admin"}

    english = await add(client, room, body, asking_preview("en"))
    french = await add(client, room, body, asking_preview("fr"))

    assert preview_of(english)[0] == (
        "Add to the space “Design”, as admins, these people, who then see all it holds:\n"
        f"\t“Jeanne Martin” <{JEANNE.email}>\n"
        "Members already, maybe through a linked group: this makes them direct members, as"
        " admins, and adds nobody if one of them is a direct member of another role:\n"
        f"\t“Carol King” <{CAROL.email}> (editor)"
    )
    assert preview_of(french)[0] == (
        "Ajouter à l'espace « Design », comme administrateurs, ces personnes, qui en voient alors"
        " tout le contenu :\n"
        f"\t« Jeanne Martin » <{JEANNE.email}>\n"
        "Déjà membres, peut-être par un groupe lié : ceci en fait des membres directs, comme"
        " administrateurs, et n'ajoute personne si l'un d'eux est membre direct d'un autre rôle :\n"
        f"\t« Carol King » <{CAROL.email}> (éditeur)"
    )
    assert sent(boundary) == []


async def change(
    client: AsyncClient, room: SpaceRoom, user_id: str, body: Any, *headers: dict[str, str]
) -> Response:
    return await client.patch(
        f"/contracts/v1/space/spaces/{room.id}/members/{user_id}",
        json=body,
        headers=headers_of(*headers),
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
        "email": BOB.email,
        "role": "editor",
        "you": False,
        "untrusted": {"display_name": "Bob Durand"},
    }
    assert boundary.space.writes == [
        ("PATCH", f"/spaces/{room.id}/members/{BOB.user_id}", {"role": "editor"})
    ]
    assert room.members[BOB.user_id] == SpaceMembership(BOB, "editor")


async def test_a_role_is_changed_by_an_admin_for_a_member_only(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    outsider = await change(client, room, JEANNE.user_id, {"role": "editor"})
    room.members[MMAUDET.user_id] = SpaceMembership(MMAUDET, "editor")
    not_admin = await change(client, room, BOB.user_id, {"role": "editor"})

    assert (outsider.status_code, outsider.json()["code"]) == (404, "member_not_found")
    assert (not_admin.status_code, not_admin.json()["code"]) == (403, "not_space_admin")
    assert sent(boundary) == []


async def test_the_same_role_changes_nothing(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary)

    response = await change(client, room, BOB.user_id, {"role": "viewer"})

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "viewer"
    assert sent(boundary) == []


async def test_the_last_admin_of_its_own_keeps_their_role(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # ldap-rest counts the direct admins alone: a group linked as admin counts for none
    room = boundary.space.space(
        "Design",
        {MMAUDET: "admin", BOB: "viewer"},
        groups=[SpaceGroupLink(DESIGNERS, "Designers", "admin", [CAROL])],
    )

    response = await change(client, room, MMAUDET.user_id, {"role": "editor"})

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin"
    assert "a group linked as admin counting for none" in response.json()["detail"]
    assert room.members[MMAUDET.user_id] == SpaceMembership(MMAUDET, "admin")


async def test_a_member_through_a_linked_group_gets_a_role_of_their_own(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Space has ldap-rest add Carol as a direct member, and lists the stronger of her role and
    # the group's
    room = with_designers(boundary)

    weaker = await change(client, room, CAROL.user_id, {"role": "viewer"})
    stronger = await change(client, room, CAROL.user_id, {"role": "admin"})

    assert weaker.status_code == stronger.status_code == 200
    assert (weaker.json()["user_id"], weaker.json()["role"]) == (CAROL.user_id, "editor")
    assert (stronger.json()["user_id"], stronger.json()["role"]) == (CAROL.user_id, "admin")
    assert room.members[CAROL.user_id] == SpaceMembership(CAROL, "admin")


async def test_a_member_removed_meanwhile_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Another admin removes Bob while Space takes the write
    room = design(boundary)

    def removed() -> None:
        del room.members[BOB.user_id]

    boundary.space.while_writing = removed

    response = await change(client, room, BOB.user_id, {"role": "editor"})

    assert response.status_code == 404
    assert response.json()["code"] == "member_not_found"
    assert boundary.space.writes == []


async def test_the_preview_tells_the_new_role_and_the_former_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    english = await change(client, room, BOB.user_id, {"role": "admin"}, asking_preview("en"))
    french = await change(client, room, BOB.user_id, {"role": "editor"}, asking_preview("fr"))
    same = await change(client, room, BOB.user_id, {"role": "viewer"}, asking_preview("en"))

    assert preview_of(english)[0] == (
        f"Make “Bob Durand” <{BOB.email}> an admin of the space “Design”, instead of a viewer:"
        " they then add, change and remove its members."
    )
    assert preview_of(french)[0] == (
        f"Faire de « Bob Durand » <{BOB.email}> un éditeur de l'espace « Design », au lieu"
        " d'un lecteur."
    )
    assert preview_of(same)[0] == (
        f"“Bob Durand” <{BOB.email}> is a viewer of the space “Design” already: nothing changes."
    )
    assert sent(boundary) == []


async def test_the_preview_of_a_role_in_a_space_with_groups_tells_the_stronger_one_is_kept(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = with_designers(boundary)

    english = await change(client, room, BOB.user_id, {"role": "editor"}, asking_preview("en"))
    french = await change(client, room, BOB.user_id, {"role": "editor"}, asking_preview("fr"))

    assert preview_of(english)[0] == (
        f"Make “Bob Durand” <{BOB.email}> an editor of the space “Design”, instead of a viewer.\n"
        "If they are a member through a linked group too, they keep the stronger of this role and"
        " the group's."
    )
    assert preview_of(french)[0] == (
        f"Faire de « Bob Durand » <{BOB.email}> un éditeur de l'espace « Design », au lieu"
        " d'un lecteur.\n"
        "Si cette personne en est aussi membre par un groupe lié, elle garde le plus fort de ce"
        " rôle et de celui du groupe."
    )


async def test_a_role_changed_since_the_preview_is_left_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    _, digest = preview_of(
        await change(client, room, BOB.user_id, {"role": "admin"}, asking_preview("en"))
    )
    room.members[BOB.user_id] = SpaceMembership(BOB, "editor")

    changed = await change(client, room, BOB.user_id, {"role": "admin"}, allowed_after(digest))
    room.members[BOB.user_id] = SpaceMembership(BOB, "viewer")
    allowed = await change(client, room, BOB.user_id, {"role": "admin"}, allowed_after(digest))

    assert (changed.status_code, changed.json()["code"]) == (409, "changed_since_preview")
    assert allowed.status_code == 200, allowed.text
    assert room.members[BOB.user_id] == SpaceMembership(BOB, "admin")


async def remove(
    client: AsyncClient, room: SpaceRoom, user_id: str, *headers: dict[str, str]
) -> Response:
    return await client.delete(
        f"/contracts/v1/space/spaces/{room.id}/members/{user_id}", headers=headers_of(*headers)
    )


async def test_an_admin_removes_a_member(client: AsyncClient, boundary: FakeBoundary) -> None:
    room = design(boundary)

    response = await remove(client, room, BOB.user_id)

    assert response.status_code == 200, response.text
    assert (response.json()["user_id"], response.json()["role"]) == (BOB.user_id, "viewer")
    assert boundary.space.writes == [("DELETE", f"/spaces/{room.id}/members/{BOB.user_id}", None)]
    assert BOB.user_id not in room.members


async def test_a_member_is_removed_by_an_admin_and_the_last_admin_of_its_own_stays(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = boundary.space.space(
        "Design",
        {MMAUDET: "admin", BOB: "viewer"},
        groups=[SpaceGroupLink(DESIGNERS, "Designers", "admin", [CAROL])],
    )
    outsider = await remove(client, room, JEANNE.user_id)
    last = await remove(client, room, MMAUDET.user_id)
    room.members[MMAUDET.user_id] = SpaceMembership(MMAUDET, "editor")
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
        f"Remove “Bob Durand” <{BOB.email}> from the space “Design”: they no longer see what it"
        " holds."
    )
    assert preview_of(french)[0] == (
        f"Retirer « Bob Durand » <{BOB.email}> de l'espace « Design » : cette personne n'en"
        " voit plus le contenu."
    )
    assert preview_of(yourself)[0] == (
        "Remove yourself from the space “Design”: you no longer see what it holds."
    )
    assert sent(boundary) == []


async def test_a_member_changed_since_the_preview_stays(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)
    _, digest = preview_of(await remove(client, room, BOB.user_id, asking_preview("en")))
    room.members[BOB.user_id] = SpaceMembership(BOB, "admin")

    response = await remove(client, room, BOB.user_id, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert room.members[BOB.user_id] == SpaceMembership(BOB, "admin")


async def test_a_member_through_a_linked_group_is_answered_as_they_were(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Space takes the removal of someone ldap-rest has through a group alone for done, though the
    # group keeps them in
    room = with_designers(boundary)

    response = await remove(client, room, CAROL.user_id)

    assert response.status_code == 200, response.text
    assert (response.json()["user_id"], response.json()["role"]) == (CAROL.user_id, "editor")
    assert boundary.space.writes == [("DELETE", f"/spaces/{room.id}/members/{CAROL.user_id}", None)]


async def test_the_user_who_removes_themselves_is_answered_as_they_were(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = design(boundary)

    response = await remove(client, room, MMAUDET.user_id)

    assert response.status_code == 200, response.text
    assert (response.json()["you"], response.json()["role"]) == (True, "admin")
    assert MMAUDET.user_id not in room.listed()


async def test_the_preview_of_a_removal_warns_that_a_linked_group_keeps_its_people_in(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    room = with_designers(boundary)

    english = await remove(client, room, CAROL.user_id, asking_preview("en"))
    french = await remove(client, room, CAROL.user_id, asking_preview("fr"))
    yourself = await remove(client, room, MMAUDET.user_id, asking_preview("fr"))

    assert preview_of(english)[0] == (
        f"Remove “Carol King” <{CAROL.email}> from the space “Design”: they no longer see what"
        " it holds.\n"
        "Unless they are a member through a linked group, who stays one while the group is linked"
        " and they are in it."
    )
    assert preview_of(french)[0] == (
        f"Retirer « Carol King » <{CAROL.email}> de l'espace « Design » : cette personne n'en"
        " voit plus le contenu.\n"
        "Sauf si elle en est membre par un groupe lié, et le reste tant que le groupe est lié et"
        " qu'elle en fait partie."
    )
    assert preview_of(yourself)[0] == (
        "Te retirer de l'espace « Design » : tu n'en vois plus le contenu.\n"
        "Sauf si tu en es membre par un groupe lié, et le restes tant que le groupe est lié et que"
        " tu en fais partie."
    )
