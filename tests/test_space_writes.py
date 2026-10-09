"""space.space.create.v1 and space.space.update.v1: the spaces the user creates in Twake Space, of
which they are the only member, as its admin, and those they rename where they are an admin."""

from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import allowed_after, asking_preview, preview_of
from tests.fakes import (
    MMAUDET_SPACE_TOKEN,
    FakeBoundary,
    SpacePerson,
    SpaceToken,
    as_space_owner,
    space_person,
    space_uuid,
)

MMAUDET = space_person("mmaudet")
ALICE = space_person("alice", "Alice Martin")
BOB = space_person("bob")


async def create(
    client: AsyncClient, headers: dict[str, str] | None = None, **body: Any
) -> Response:
    return await client.post(
        "/contracts/v1/space/spaces", json=body, headers=as_space_owner() | (headers or {})
    )


async def rename(
    client: AsyncClient, space_id: str, headers: dict[str, str] | None = None, **body: Any
) -> Response:
    return await client.patch(
        f"/contracts/v1/space/spaces/{space_id}",
        json=body,
        headers=as_space_owner() | (headers or {}),
    )


async def test_a_new_space_has_the_user_as_its_only_member_and_admin(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(client, name="Projet Y", description="The launch of product Y")

    assert response.status_code == 201, response.text
    answer = response.json()
    created = boundary.space.spaces[answer["space_id"]]
    assert answer == {
        "space_id": created.id,
        "url": f"https://space.twake.test/spaces/{created.id}/feed",
        "role": "admin",
        "member_count": 1,
        "reachable": True,
        "untrusted": {"name": "Projet Y", "description": "The launch of product Y"},
    }
    assert boundary.space.writes == [
        ("POST", "/spaces", {"name": "Projet Y", "description": "The launch of product Y"})
    ]
    assert [(member.person, member.role) for member in created.members.values()] == [
        (MMAUDET, "admin")
    ]
    # Every tab, as Space gives a space whose creator picked none
    assert created.apps == ["chat", "tasks", "drive", "mail", "calendar"]


async def test_a_space_without_a_description_gets_none(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(client, name="  Projet Y ")

    assert response.status_code == 201, response.text
    assert response.json()["untrusted"] == {"name": "Projet Y", "description": None}
    assert boundary.space.writes == [("POST", "/spaces", {"name": "Projet Y", "description": ""})]


async def test_a_token_made_for_a_list_of_spaces_does_not_reach_the_one_it_creates(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The owner chose the spaces the token reaches when they made it in Space, which adds none it
    # creates
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "admin"})
    boundary.space.tokens[MMAUDET_SPACE_TOKEN] = SpaceToken(
        MMAUDET.user_id, space_ids=frozenset({roadmap.id})
    )

    response = await create(client, name="Projet Y")

    assert response.status_code == 201, response.text
    assert response.json()["reachable"] is False
    assert boundary.space.spaces[response.json()["space_id"]].name == "Projet Y"


async def test_a_token_of_the_organization_creates_no_space(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Space makes the account of the token the admin of the space it creates: a token of the
    # organization has none
    boundary.space.tokens[MMAUDET_SPACE_TOKEN] = SpaceToken(None, role="admin")

    response = await create(client, name="Projet Y")

    assert response.status_code == 403
    assert response.json()["code"] == "needs_an_account"
    assert boundary.space.spaces == {}


@pytest.mark.parametrize(
    ("held", "missing"),
    [
        pytest.param({"space:read", "feed:read"}, "space:write", id="write"),
        # The spaces of that name the user has already are read first
        pytest.param({"feed:read", "space:write"}, "space:read", id="read"),
    ],
)
async def test_a_token_that_may_not_create_spaces_is_refused_with_the_scope_it_lacks(
    client: AsyncClient, boundary: FakeBoundary, held: set[str], missing: str
) -> None:
    boundary.space.tokens[MMAUDET_SPACE_TOKEN] = SpaceToken(MMAUDET.user_id, frozenset(held))

    response = await create(client, name="Projet Y")

    assert response.status_code == 403
    assert (response.json()["code"], response.json()["scope"]) == ("space_scope_missing", missing)
    assert boundary.space.spaces == {}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"name": " "}, id="blank name"),
        pytest.param({"name": "x" * 256}, id="name too long"),
        pytest.param({"name": "Projet Y", "description": "x" * 1001}, id="description too long"),
        pytest.param({"description": "The launch of product Y"}, id="no name"),
        # Every space the contract creates gets every tab, in Space's colors
        pytest.param({"name": "Projet Y", "apps": ["chat"]}, id="apps"),
        pytest.param({"name": "Projet Y", "color": "#336699"}, id="color"),
    ],
)
async def test_a_space_the_contract_does_not_create_is_refused_before_space_is_called(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    response = await create(client, **body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.space.requests == []


async def test_a_name_space_counts_longer_is_refused_as_space_refuses_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # 200 characters for the contract, 400 for Space, which counts an emoji as two
    response = await create(client, name="🚀" * 200)

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Space refused the name or the description: Space counts characters in UTF-16 code units,"
        " an emoji counting two, and takes 1 to 255 in a name, 1,000 at most in a description."
    )
    assert boundary.space.spaces == {}


@pytest.mark.parametrize(
    ("language", "description", "summary"),
    [
        (
            "fr",
            "The launch of product Y",
            "Créer l'espace « Projet Y » dans Twake Space\n"
            "Description :\n"
            "\tThe launch of product Y\n"
            "Tu en seras le seul membre, avec le rôle administrateur.",
        ),
        (
            "en",
            "The launch of product Y\nand its press release",
            "Create the space “Projet Y” in Twake Space\n"
            "Description:\n"
            "\tThe launch of product Y\n"
            "\tand its press release\n"
            "You will be its only member, as its admin.",
        ),
        (
            "en",
            None,
            "Create the space “Projet Y” in Twake Space\n"
            "You will be its only member, as its admin.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_what_would_be_created_and_creates_nothing(
    client: AsyncClient,
    boundary: FakeBoundary,
    language: str,
    description: str | None,
    summary: str,
) -> None:
    response = await create(
        client, asking_preview(language), name="Projet Y", description=description
    )

    told, _ = preview_of(response)
    assert told == summary
    assert boundary.space.writes == []
    assert boundary.space.spaces == {}


@pytest.mark.parametrize(
    ("spaces", "told"),
    [
        (["Projet Y"], "You are a member of a space of that name already: this one is another."),
        (
            ["Projet Y", "projet  Y"],
            "You are a member of 2 spaces of that name already: this one is another.",
        ),
    ],
)
async def test_a_preview_tells_of_the_spaces_of_that_name_already(
    client: AsyncClient, boundary: FakeBoundary, spaces: list[str], told: str
) -> None:
    # Space takes a space of a name the user has already as another one
    for name in spaces:
        boundary.space.space(name, {MMAUDET: "viewer", ALICE: "admin"})

    response = await create(client, asking_preview("en"), name="Projet Y")

    summary, _ = preview_of(response)
    assert summary.splitlines()[1] == told


async def test_the_owner_who_allowed_what_they_were_shown_gets_the_space(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(await create(client, asking_preview("fr"), name="Projet Y"))

    response = await create(client, allowed_after(digest), name="Projet Y")

    assert response.status_code == 201, response.text
    assert [room.name for room in boundary.space.spaces.values()] == ["Projet Y"]


async def test_a_space_of_that_name_created_since_the_preview_stops_the_call(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(await create(client, asking_preview("fr"), name="Projet Y"))
    # The same call went through meanwhile, as when the agent tried it twice
    created = await create(client, name="Projet Y")
    assert created.status_code == 201, created.text

    response = await create(client, allowed_after(digest), name="Projet Y")

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert len(boundary.space.spaces) == 1


async def test_an_admin_renames_their_space(client: AsyncClient, boundary: FakeBoundary) -> None:
    brand = boundary.space.space(
        "Brand", {MMAUDET: "admin", ALICE: "editor"}, description="Our brand"
    )

    response = await rename(client, brand.id, name=" Brand design ")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "space_id": brand.id,
        "url": f"https://space.twake.test/spaces/{brand.id}/feed",
        "role": "admin",
        "member_count": 2,
        "untrusted": {"name": "Brand design", "description": "Our brand"},
    }
    # The name alone, its tabs left as they are
    assert boundary.space.writes == [("PATCH", f"/spaces/{brand.id}", {"name": "Brand design"})]
    assert (brand.name, brand.apps) == (
        "Brand design",
        ["chat", "tasks", "drive", "mail", "calendar"],
    )


async def test_a_name_the_space_has_already_writes_nothing(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    brand = boundary.space.space("Brand", {MMAUDET: "admin", ALICE: "editor"})

    response = await rename(client, brand.id, name=" Brand ")

    assert response.status_code == 200, response.text
    assert response.json()["untrusted"]["name"] == "Brand"
    # Space would rename it all the same, and tell every member
    assert boundary.space.writes == []


@pytest.mark.parametrize("headers", [{}, asking_preview("en")], ids=["call", "preview"])
@pytest.mark.parametrize("role", ["viewer", "editor"])
async def test_only_an_admin_of_the_space_renames_it(
    client: AsyncClient, boundary: FakeBoundary, role: str, headers: dict[str, str]
) -> None:
    brand = boundary.space.space("Brand", {MMAUDET: role, ALICE: "admin"})

    response = await rename(client, brand.id, headers, name="Brand design")

    assert response.status_code == 403
    assert response.json() == {
        "type": "urn:twake:problem:not_space_admin",
        "title": "Not a space admin",
        "status": 403,
        "detail": f"The user is not an admin of space {brand.id}: only its admins may change it.",
        "code": "not_space_admin",
    }
    # Refused before anything is written, and before the owner is asked
    assert ("PATCH", f"/spaces/{brand.id}") not in boundary.space.requests
    assert brand.name == "Brand"


async def test_renaming_a_space_the_token_does_not_reach_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await rename(client, space_uuid("Brand"), name="Brand design")

    assert response.status_code == 404
    assert response.json()["code"] == "space_not_found"
    assert boundary.space.writes == []


@pytest.mark.parametrize(
    ("held", "missing"),
    [
        pytest.param({"space:read", "feed:read"}, "space:write", id="write"),
        # The space is read first, for the user's role there and its name
        pytest.param({"feed:read", "space:write"}, "space:read", id="read"),
    ],
)
async def test_a_token_that_may_not_rename_spaces_is_refused_with_the_scope_it_lacks(
    client: AsyncClient, boundary: FakeBoundary, held: set[str], missing: str
) -> None:
    brand = boundary.space.space("Brand", {MMAUDET: "admin"})
    boundary.space.tokens[MMAUDET_SPACE_TOKEN] = SpaceToken(MMAUDET.user_id, frozenset(held))

    response = await rename(client, brand.id, name="Brand design")

    assert response.status_code == 403
    assert (response.json()["code"], response.json()["scope"]) == ("space_scope_missing", missing)
    assert brand.name == "Brand"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"name": " "}, id="blank name"),
        pytest.param({"name": "x" * 256}, id="name too long"),
        pytest.param({}, id="no name"),
        # It changes the name alone
        pytest.param({"name": "Brand design", "apps": ["chat"]}, id="apps"),
    ],
)
async def test_a_name_the_contract_does_not_give_is_refused_before_space_is_called(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    brand = boundary.space.space("Brand", {MMAUDET: "admin"})

    response = await rename(client, brand.id, **body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.space.requests == []


async def test_a_new_name_space_counts_longer_is_refused_as_space_refuses_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    brand = boundary.space.space("Brand", {MMAUDET: "admin"})

    # 200 characters for the contract, 400 for Space, which counts an emoji as two
    response = await rename(client, brand.id, name="🚀" * 200)

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Space refused the name: Space counts characters in UTF-16 code units, an emoji counting"
        " two, and takes 1 to 255 in a name, 1,000 at most in a description."
    )
    assert brand.name == "Brand"


@pytest.mark.parametrize(
    ("language", "members", "summary"),
    [
        (
            "fr",
            {MMAUDET: "admin", ALICE: "editor", BOB: "viewer"},
            "Renommer l'espace « Brand » en « Brand design » dans Twake Space\n"
            "Ses 3 membres verront le nouveau nom.",
        ),
        (
            "en",
            {MMAUDET: "admin"},
            "Rename the space “Brand” to “Brand design” in Twake Space\n"
            "Its only member will see the new name.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_both_names_and_who_sees_them_and_renames_nothing(
    client: AsyncClient,
    boundary: FakeBoundary,
    language: str,
    members: dict[SpacePerson, str],
    summary: str,
) -> None:
    brand = boundary.space.space("Brand", members)

    response = await rename(client, brand.id, asking_preview(language), name="Brand design")

    told, _ = preview_of(response)
    assert told == summary
    assert boundary.space.writes == []
    assert brand.name == "Brand"


async def test_the_owner_who_allowed_the_new_name_gets_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    brand = boundary.space.space("Brand", {MMAUDET: "admin"})
    _, digest = preview_of(
        await rename(client, brand.id, asking_preview("fr"), name="Brand design")
    )

    response = await rename(client, brand.id, allowed_after(digest), name="Brand design")

    assert response.status_code == 200, response.text
    assert brand.name == "Brand design"


async def test_a_space_renamed_since_the_preview_stops_the_call(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    brand = boundary.space.space("Brand", {MMAUDET: "admin", ALICE: "admin"})
    _, digest = preview_of(
        await rename(client, brand.id, asking_preview("fr"), name="Brand design")
    )
    # Another admin renamed it meanwhile
    brand.name = "Brand book"

    response = await rename(client, brand.id, allowed_after(digest), name="Brand design")

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert (boundary.space.writes, brand.name) == ([], "Brand book")
