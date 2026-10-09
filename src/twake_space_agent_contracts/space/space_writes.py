"""space.space.create.v1 and space.space.update.v1: the spaces the user creates in Twake Space, of
which they are the only member, as its admin, and those they rename where they are an admin."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.previews import (
    BUDGET,
    Language,
    Previewing,
    digest_of,
    excerpt,
    one_line,
    quoted,
    shown_size,
)
from twake_space_agent_contracts.problems import invalid_request
from twake_space_agent_contracts.space import (
    EXAMPLE_SPACE,
    LONGEST_NAME,
    NO_POSTING,
    UNTRUSTED,
    SpaceId,
)
from twake_space_agent_contracts.space.backend import SpaceOwner, SpaceOwnerDependency, TwakeSpace
from twake_space_agent_contracts.space.spaces import LONGEST_DESCRIPTION, ListedSpace, space_text
from twake_space_agent_contracts.text import seen

SpaceName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=LONGEST_NAME,
        description=f"Plain text, {LONGEST_NAME} characters at most: the name its members see.",
    ),
]


class NewSpace(BaseModel):
    """A space to create, of which the user is the only member, as its admin."""

    model_config = ConfigDict(extra="forbid")

    name: SpaceName
    description: Annotated[
        str | None,
        Field(
            max_length=LONGEST_DESCRIPTION,
            description=f"What the space is for, plain text, {LONGEST_DESCRIPTION} characters at "
            "most.",
        ),
    ] = None


class CreatedSpace(ListedSpace):
    """A new space, of which the user is the only member, as its admin."""

    reachable: bool = Field(
        description="Whether the user's API token of Space reaches the new space: false when it "
        "covers a list of spaces, which Space does not extend to the new one, so that you can "
        "neither read nor change it, and tell the user so."
    )


class NewName(BaseModel):
    """The name to give a space."""

    model_config = ConfigDict(extra="forbid")

    name: SpaceName


@dataclass(frozen=True)
class _CreateWords:
    """What a preview of a new space tells the owner, in one language."""

    create: str
    description: str
    namesakes_already: tuple[str, str]
    """That the user is a member of one space of that name already, or of several."""
    member: str


_CREATE_WORDS: dict[Language, _CreateWords] = {
    "fr": _CreateWords(
        create="Créer l'espace {name} dans Twake Space",
        description="Description :",
        namesakes_already=(
            "Tu es déjà membre d'un espace de ce nom : celui-ci en sera un autre.",
            "Tu es déjà membre de {count} espaces de ce nom : celui-ci en sera un autre.",
        ),
        member="Tu en seras le seul membre, avec le rôle administrateur.",
    ),
    "en": _CreateWords(
        create="Create the space {name} in Twake Space",
        description="Description:",
        namesakes_already=(
            "You are a member of a space of that name already: this one is another.",
            "You are a member of {count} spaces of that name already: this one is another.",
        ),
        member="You will be its only member, as its admin.",
    ),
}


@dataclass(frozen=True)
class _RenameWords:
    """What a preview of a space's new name tells the owner, in one language."""

    rename: str
    members: tuple[str, str]
    """That the space's only member will see the new name, or how many members will."""


_RENAME_WORDS: dict[Language, _RenameWords] = {
    "fr": _RenameWords(
        rename="Renommer l'espace {old} en {new} dans Twake Space",
        members=(
            "Son seul membre verra le nouveau nom.",
            "Ses {count} membres verront le nouveau nom.",
        ),
    ),
    "en": _RenameWords(
        rename="Rename the space {old} to {new} in Twake Space",
        members=(
            "Its only member will see the new name.",
            "Its {count} members will see the new name.",
        ),
    ),
}


def _compared(name: str) -> str:
    """A space's name as the contract compares it with another's: as a reader sees it, whatever
    its case and its blanks."""
    return " ".join(seen(name).split()).casefold()


def _creating(name: str, description: str, namesake_count: int, language: Language) -> str:
    """What creating the space does, as the owner reads it: its name, its description if it has
    one, that the user is a member of spaces of that name already if they are, and that they will
    be its only member."""
    words = _CREATE_WORDS[language]
    head = words.create.format(name=quoted(one_line(name, LONGEST_NAME), language)) + "\n"
    tail = words.member
    if namesake_count:
        one, several = words.namesakes_already
        tail = (one if namesake_count == 1 else several.format(count=namesake_count)) + "\n" + tail
    if not description:
        return head + tail
    head += words.description + "\n"
    # The description takes what the rest leaves of the summary
    room = BUDGET - shown_size(head) - shown_size("\n" + tail)
    return head + excerpt(description, room, language) + "\n" + tail


def _renaming(old: str, new: str, member_count: int, language: Language) -> str:
    """What renaming the space does, as the owner reads it: its name, the new one, and how many
    members will see it."""
    words = _RENAME_WORDS[language]
    names = {
        "old": quoted(one_line(old, LONGEST_NAME), language),
        "new": quoted(one_line(new, LONGEST_NAME), language),
    }
    one, several = words.members
    seeing = one if member_count == 1 else several.format(count=member_count)
    return words.rename.format(**names) + "\n" + seeing


def _create(space: TwakeSpace, owner_of: SpaceOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.space.create.v1"])

    @routes.post(
        "/spaces",
        operation_id="create_space",
        status_code=201,
        summary="Create a space in Twake Space as the user",
        description=(
            "Creates a space in Twake Space, as the user you act for, of which they are the only "
            "member, as its admin, with every tab: chat, tasks, drive, mail and calendar. Each "
            "call creates a new space, even of a name the user has already: after an error, look "
            "for it with list_spaces before calling again. reachable false tells that the user's "
            "API token of Space covers a list of spaces, which the new one is not on: you can "
            "then neither read nor change it, and tell the user so. A token of the organization "
            f"rather than of the user's account answers needs_an_account. {NO_POSTING} "
            f"{UNTRUSTED} Example, for the launch of a product: "
            'body={"name": "Projet Y", "description": "The launch of product Y"}.'
        ),
        response_model=CreatedSpace,
        # A new space the user alone is a member of: the owner's consent to write in Space covers
        # it, and they are not asked to confirm each one. It tells what it would do, for when they
        # are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def create_space(
        new: NewSpace, owner: Annotated[SpaceOwner, Depends(owner_of)], preview: Previewing
    ) -> CreatedSpace | JSONResponse:
        name, description = new.name.strip(), (new.description or "").strip()
        if not name:
            raise invalid_request("name: A space's name cannot be blank.")
        # Space takes a space of a name the user has already as another one
        compared = _compared(name)
        namesake_ids = sorted(
            found.space_id
            for found in await space.spaces(owner)
            if _compared(found.name) == compared
        )
        # What the owner allows: a space of that name and description, beside those they have of
        # that name already
        digest = digest_of(name, description, namesake_ids)
        if preview.asked:
            summary = _creating(name, description, len(namesake_ids), preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        created = await space.create(owner, name, description)
        # A token made for a list of spaces does not reach the one it creates
        reachable = await space.found_space(owner, created.space_id) is not None
        return CreatedSpace(
            space_id=created.space_id,
            url=space.feed_url(created.space_id),
            role=created.role,
            member_count=created.member_count,
            untrusted=space_text(created.name, created.description),
            reachable=reachable,
        )

    return routes


def _update(space: TwakeSpace, owner_of: SpaceOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.space.update.v1"])

    @routes.patch(
        "/spaces/{space_id}",
        operation_id="rename_space",
        summary="Rename a space in Twake Space as the user",
        description=(
            "Renames a space of Twake Space the user you act for is an admin of, by the space_id "
            "list_spaces gives: every member of the space sees the new name at once. It changes "
            "the name alone. A space where the user is not an admin answers not_space_admin, and "
            f"nothing changes. {NO_POSTING} {UNTRUSTED} Example: {EXAMPLE_SPACE}, "
            'body={"name": "Brand design"}.'
        ),
        response_model=ListedSpace,
        # Every member of the space sees the new name at once: the owner confirms each one, shown
        # the name it has and the new one
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def rename_space(
        space_id: SpaceId,
        new: NewName,
        owner: Annotated[SpaceOwner, Depends(owner_of)],
        preview: Previewing,
    ) -> ListedSpace | JSONResponse:
        name = new.name.strip()
        if not name:
            raise invalid_request("name: A space's name cannot be blank.")
        # Space lets its admins alone rename it: refused before the owner is asked
        detail = await space.administered(owner, space_id)
        # What the owner allows: this name given to the space they were shown by its name now
        digest = digest_of(space_id, detail.name, name)
        if preview.asked:
            summary = _renaming(detail.name, name, len(detail.members), preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        await space.rename(owner, space_id, name)
        return ListedSpace(
            space_id=detail.space_id,
            url=space.feed_url(detail.space_id),
            role=detail.role,
            member_count=len(detail.members),
            untrusted=space_text(name, detail.description),
        )

    return routes


def routers(space: TwakeSpace, owner_of: SpaceOwnerDependency) -> list[APIRouter]:
    """The routers of the contracts that write spaces, one each."""
    return [_create(space, owner_of), _update(space, owner_of)]
