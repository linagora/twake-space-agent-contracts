"""calendar.invitation.accept.v1: the user accepts an invitation they received, as themselves."""

from dataclasses import dataclass
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.calendar import INVITATION_UID, Calendar, CalendarEvent
from twake_space_agent_contracts.calendar_previews import when_it_takes_place
from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.previews import (
    Language,
    Previewing,
    digest_of,
    one_line,
    person,
    quoted,
)
from twake_space_agent_contracts.problems import Problem

EXAMPLE_UID = "5c4e9f2a-7b1d-4c3e-9a8f-2d6b0e1f3a7c"
"""The UID of an event, for the worked call."""


# In the body, which holds any text iCalendar allows in a UID, slashes included: the gateway
# routes a path parameter as one segment
class Invitation(BaseModel):
    """The invitation to accept, by the UID of its event."""

    model_config = ConfigDict(extra="forbid")

    uid: Annotated[
        str,
        Field(min_length=1, description=f"The UID of the invitation to accept. {INVITATION_UID}"),
    ]


class Answer(BaseModel):
    """The user's answer to the invitation, as their calendar now has it."""

    uid: str
    partstat: Literal["ACCEPTED"]


@dataclass(frozen=True)
class _Words:
    """What a preview of accepting tells the owner, in one language."""

    accept: str
    untitled: str
    invited_by: str
    told: str


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        accept="Accepter {what}",
        untitled="l'invitation sans titre",
        invited_by="invitation de {organizer}",
        told="Twake Agenda prévient l'organisateur.",
    ),
    "en": _Words(
        accept="Accept {what}",
        untitled="the untitled invitation",
        invited_by="an invitation from {organizer}",
        told="Twake Calendar tells the organizer.",
    ),
}


def _summary(event: CalendarEvent, zone: ZoneInfo | None, language: Language) -> str:
    """What accepting the invitation does, as the owner reads it: the event's title, which its
    organizer wrote, when it takes place, and who organizes it."""
    words = _WORDS[language]
    title = one_line(event.title)
    parts = [words.accept.format(what=quoted(title, language) if title else words.untitled)]
    when = when_it_takes_place(event, zone, language)
    if when is not None:
        parts.append(when)
    organizer = person(*event.organizer, language)
    if organizer is not None:
        parts.append(words.invited_by.format(organizer=organizer))
    return ", ".join(parts) + "\n" + words.told


def router(calendar: Calendar, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(
        prefix="/contracts/v1/calendar/invitations", tags=["calendar.invitation.accept.v1"]
    )

    @routes.post(
        "/accept",
        operation_id="accept_invitation",
        summary="Accept an invitation on the user's behalf",
        description=(
            "Accepts an invitation that the user you act for received, named by the UID of its "
            "event in Calendar: their own participation becomes accepted in their calendar, and "
            "Calendar tells the organizer. Nothing else in the event changes. Call it only once "
            "the user has said yes to this very invitation. A recurring invitation is refused: "
            f'the user answers it in Calendar. Example: body={{"uid": "{EXAMPLE_UID}"}}.'
        ),
        response_model=Answer,
        # The user's own answer, though Calendar tells the organizer: the owner's consent to write
        # in Calendar covers it, and they are not asked to confirm each one. It tells what it
        # would do, for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def accept_invitation(
        invitation: Invitation,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> Answer | JSONResponse:
        event = await calendar.find_event(user, invitation.uid)
        accepted = event.accepted_by(user.email) if event is not None else None
        # No copy of the user's own, or one that does not invite them, is answered alike, before
        # anything else is checked: the contract never tells that an event exists
        if event is None or accepted is None:
            raise Problem(
                status=404,
                code="invitation_not_found",
                title="Invitation not found",
                detail="No invitation to an event of this UID was sent to this user.",
            )
        # A UID names a whole series, not which of its occurrences the invitation is about
        if event.recurring:
            raise Problem(
                status=409,
                code="recurring_invitation",
                title="Recurring invitation",
                detail="The invitation repeats, or is one occurrence of a series: the user"
                " answers it in Calendar.",
            )
        # esn-sabre would not tell the organizer
        if event.cancelled:
            raise Problem(
                status=409,
                code="invitation_cancelled",
                title="Invitation cancelled",
                detail="The organizer cancelled this event: there is nothing to accept.",
            )
        # What the owner allows: the event as they would accept it, where it is
        digest = digest_of(accepted.href, accepted.jcal)
        if preview.asked:
            zone = await calendar.time_zone(user)
            return preview.answer(_summary(accepted, zone, preview.language), digest)
        preview.check(digest)
        await calendar.save_event(user, accepted)
        return Answer(uid=invitation.uid, partstat="ACCEPTED")

    return routes
