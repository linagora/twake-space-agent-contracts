"""space.people.read.v1: the people of the user's organization, as Twake Space finds them in its
directory, to add to a space."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.problems import invalid_request
from twake_space_agent_contracts.space import LONGEST_NAME, UNTRUSTED
from twake_space_agent_contracts.space.backend import TwakeSpace
from twake_space_agent_contracts.text import line

LONGEST_WORDS = 100


class PersonText(BaseModel):
    """The name a person goes by, which they or their organization wrote."""

    display_name: str | None


class Person(BaseModel):
    username: str = Field(description="Who the person is, as add_space_members takes them.")
    email: str
    untrusted: PersonText


class People(BaseModel):
    people: list[Person]
    next_page: int | None = Field(
        description="The page of the people who follow, to pass as page; null after the last."
    )


def router(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.people.read.v1"])

    @routes.get(
        "/people",
        operation_id="search_organization_people",
        summary="Search the people of the user's organization in Twake Space",
        description=(
            "Finds the active people of the organization of the user you act for, whose name, "
            "username or email holds q, 20 a page: those add_space_members can add to a space, "
            f"by their username. Without q, lists them all. {UNTRUSTED} Example, for the people "
            "named Martin: q=martin, page=1."
        ),
    )
    async def search_organization_people(
        user: Annotated[User, Depends(caller)],
        q: Annotated[
            str | None,
            Query(
                min_length=2,
                max_length=LONGEST_WORDS,
                description="The words to find, 2 to 100 characters, such as a name, a username "
                "or part of an email.",
            ),
        ] = None,
        page: Annotated[
            int, Query(ge=1, le=1000, description="The page to give, from 1, the default.")
        ] = 1,
    ) -> People:
        # Space trims the words, and ldap-rest needs two characters to search
        words = None if q is None else line(q, LONGEST_WORDS)[0] or ""
        if words is not None and len(words) < 2:
            raise invalid_request("q: Give 2 to 100 characters to find.")
        found, more = await space.people(user, words, page)
        return People(
            people=[
                Person(
                    username=person.username,
                    email=person.email,
                    untrusted=PersonText(display_name=line(person.display_name, LONGEST_NAME)[0]),
                )
                for person in found
            ],
            next_page=page + 1 if more else None,
        )

    return routes
