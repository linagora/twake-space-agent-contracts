"""space.people.read.v1: the people of the user's spaces in Twake Space, found by their username,
email or name, each with the spaces they share with the user."""

import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from twake_space_agent_contracts.problems import invalid_request
from twake_space_agent_contracts.space import (
    LONGEST_NAME,
    MOST_READ,
    NO_POSTING,
    ROLES,
    UNTRUSTED,
)
from twake_space_agent_contracts.space.backend import (
    Member,
    SpaceDetail,
    SpaceOwner,
    SpaceOwnerDependency,
    SpaceSummary,
    TwakeSpace,
    for_each,
)
from twake_space_agent_contracts.text import line

LONGEST_WORDS = 100
MOST_PEOPLE = 20


class SharedSpaceText(BaseModel):
    name: str | None


class SharedSpace(BaseModel):
    """A space the person and the user are both members of."""

    space_id: str
    role: str = Field(description=f"The person's role there: {ROLES}")
    untrusted: SharedSpaceText


class PersonText(BaseModel):
    """The name a person goes by, which they or their organization wrote."""

    display_name: str | None


class Person(BaseModel):
    username: str = Field(
        description="Who the person is in Space, by which add_space_members adds them."
    )
    user_id: str = Field(
        description="Who the person is in Space, as the items of the feeds name who made them."
    )
    email: str
    you: bool = Field(description="Whether the person is the user you act for.")
    spaces: list[SharedSpace] = Field(
        description="The spaces of the user the person is a member of, by name."
    )
    untrusted: PersonText


class People(BaseModel):
    people: list[Person] = Field(description="By username.")
    truncated: bool = Field(
        description=f"Whether more people were found than the {MOST_PEOPLE} the list holds, or "
        f"the user has more spaces than the first {MOST_READ} by name, whose members are searched."
    )


def _folded(text: str | None) -> str:
    """Words as the search compares them: on one line, without what a reader does not see, nor
    case, nor accents."""
    words = unicodedata.normalize("NFKD", (line(text, LONGEST_NAME)[0] or "").casefold())
    return "".join(character for character in words if not unicodedata.combining(character))


def _found(member: Member, wanted: str) -> bool:
    """Whether the member's username, email or name holds the words wanted, folded."""
    return any(
        wanted in _folded(known) for known in (member.username, member.email, member.display_name)
    )


def _person(member: Member, *, you: bool) -> Person:
    """A member found, as the contracts give them, before the spaces they share with the user."""
    return Person(
        username=member.username,
        user_id=member.user_id,
        email=member.email,
        you=you,
        spaces=[],
        untrusted=PersonText(display_name=line(member.display_name, LONGEST_NAME)[0]),
    )


async def read_spaces(space: TwakeSpace, owner: SpaceOwner) -> tuple[list[SpaceDetail], bool]:
    """The user's first MOST_READ spaces by name, with their members, but those the user left, or
    that were deleted, once Space listed them; and whether the user has more spaces."""
    summaries = await space.spaces(owner)

    async def detail_of(summary: SpaceSummary) -> SpaceDetail | None:
        return await space.found_space(owner, summary.space_id)

    found = await for_each(summaries[:MOST_READ], detail_of)
    return [detail for detail in found if detail is not None], len(summaries) > MOST_READ


def router(space: TwakeSpace, owner_of: SpaceOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.people.read.v1"])

    @routes.get(
        "/people",
        operation_id="search_space_people",
        summary="Search the people of the user's spaces in Twake Space",
        description=(
            "Finds the people of the spaces the user you act for is a member of whose username, "
            "email or name holds q, whatever its case and accents: each once, by username, with "
            "the spaces they share with the user and their role there. It searches the members "
            f"of the user's first {MOST_READ} spaces by name, and gives {MOST_PEOPLE} people at "
            "most. Space does not let an assistant search the directory of the organization: "
            "someone who shares no space with the user is not found, so ask the user for their "
            f"username. {NO_POSTING} {UNTRUSTED} Example, for the people named Martin: q=martin."
        ),
    )
    async def search_space_people(
        owner: Annotated[SpaceOwner, Depends(owner_of)],
        q: Annotated[
            str,
            Query(
                min_length=2,
                max_length=LONGEST_WORDS,
                description="The words to find, 2 to 100 characters, such as a name, a username "
                "or part of an email.",
            ),
        ],
    ) -> People:
        words = line(q, LONGEST_WORDS)[0] or ""
        if len(words) < 2:
            raise invalid_request("q: Give 2 to 100 characters to find.")
        wanted = _folded(words)
        details, more = await read_spaces(space, owner)
        found: dict[str, Person] = {}
        for detail in details:
            me = detail.user_id_of(owner.user.email)
            shared = SharedSpaceText(name=line(detail.name, LONGEST_NAME)[0])
            for member in detail.members:
                if not _found(member, wanted):
                    continue
                person = found.get(member.user_id)
                if person is None:
                    person = found[member.user_id] = _person(member, you=member.user_id == me)
                person.spaces.append(
                    SharedSpace(space_id=detail.space_id, role=member.role, untrusted=shared)
                )
        listed = sorted(found.values(), key=lambda person: person.username)
        return People(
            people=listed[:MOST_PEOPLE],
            truncated=len(listed) > MOST_PEOPLE or more,
        )

    return routes
