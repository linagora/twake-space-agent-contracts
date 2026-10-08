"""calendar.invitation.accept.v1 and calendar.invitation.decline.v1: the user accepts or declines
an invitation they received, as themselves."""

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
    Preview,
    Previewing,
    digest_of,
    one_line,
    person,
    quoted,
)
from twake_space_agent_contracts.problems import Problem

EXAMPLE_UID = "5c4e9f2a-7b1d-4c3e-9a8f-2d6b0e1f3a7c"
"""The UID of an event, for the worked call."""

Partstat = Literal["ACCEPTED", "DECLINED"]
"""The user's answer to an invitation, as iCalendar writes their participation."""


# In the body, which holds any text iCalendar allows in a UID, slashes included: the gateway
# routes a path parameter as one segment
class Invitation(BaseModel):
    """The invitation to answer, by the UID of its event."""

    model_config = ConfigDict(extra="forbid")

    uid: Annotated[
        str,
        Field(min_length=1, description=f"The UID of the invitation to answer. {INVITATION_UID}"),
    ]
    series: Annotated[
        bool,
        Field(
            description="true to answer for the whole series of a recurring invitation, each of "
            "its occurrences, those answered already included, once the user said so; false by "
            "default, which refuses a recurring invitation."
        ),
    ] = False


class Answer(BaseModel):
    """The user's answer to the invitation, as their calendar now has it."""

    uid: str
    partstat: Partstat


@dataclass(frozen=True)
class _Words:
    """What a preview of answering tells the owner, in one language."""

    accept: str
    decline: str
    untitled: str
    series: str
    untitled_series: str
    first_time: str
    invited_by: str
    told: str


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        accept="Accepter {what}",
        decline="Refuser {what}",
        untitled="l'invitation sans titre",
        series="toute la série {title}",
        untitled_series="toute la série sans titre",
        first_time="{when} la première fois",
        invited_by="invitation de {organizer}",
        told="Twake Agenda prévient l'organisateur.",
    ),
    "en": _Words(
        accept="Accept {what}",
        decline="Decline {what}",
        untitled="the untitled invitation",
        series="the whole series {title}",
        untitled_series="the whole untitled series",
        first_time="{when} the first time",
        invited_by="an invitation from {organizer}",
        told="Twake Calendar tells the organizer.",
    ),
}


def _summary(
    event: CalendarEvent,
    partstat: Partstat,
    series: bool,
    zone: ZoneInfo | None,
    language: Language,
) -> str:
    """What answering the invitation does, as the owner reads it: the answer, for the whole series
    or not, the event's title, which its organizer wrote, when it takes place, the first time for a
    series, and who organizes it."""
    words = _WORDS[language]
    title = one_line(event.title)
    if not title:
        what = words.untitled_series if series else words.untitled
    elif series:
        what = words.series.format(title=quoted(title, language))
    else:
        what = quoted(title, language)
    answer = words.accept if partstat == "ACCEPTED" else words.decline
    parts = [answer.format(what=what)]
    when = when_it_takes_place(event, zone, language)
    if when is not None:
        parts.append(words.first_time.format(when=when) if series else when)
    organizer = person(*event.organizer, language)
    if organizer is not None:
        parts.append(words.invited_by.format(organizer=organizer))
    return ", ".join(parts) + "\n" + words.told


async def _answer(
    calendar: Calendar, invitation: Invitation, user: User, preview: Preview, partstat: Partstat
) -> Answer | JSONResponse:
    """The user's answer to the invitation, given in their calendar; or, when the harness asks,
    what giving it would do."""
    event = await calendar.find_event(user, invitation.uid)
    answered = event.answered_by(user.email, partstat) if event is not None else None
    # No copy of the user's own, or one that does not invite them, is answered alike, before
    # anything else is checked: the contract never tells that an event exists
    if event is None or answered is None:
        raise Problem(
            status=404,
            code="invitation_not_found",
            title="Invitation not found",
            detail="No invitation to an event of this UID was sent to this user.",
        )
    # A UID names a whole series, not which of its occurrences the invitation is about: the user
    # answers for all of them, or in Calendar
    if event.recurring and not invitation.series:
        raise Problem(
            status=409,
            code="recurring_invitation",
            title="Recurring invitation",
            detail="The invitation repeats, or is one occurrence of a series: once the user said"
            " yes to answering for the whole series, call again with series true; else they"
            " answer it in Calendar.",
        )
    # esn-sabre would not tell the organizer
    if event.cancelled:
        raise Problem(
            status=409,
            code="invitation_cancelled",
            title="Invitation cancelled",
            detail="The organizer cancelled this event: there is nothing to answer.",
        )
    # What the owner allows: the event as the user would answer it, where it is
    digest = digest_of(answered.href, answered.jcal)
    if preview.asked:
        zone = await calendar.time_zone(user)
        series = invitation.series and event.recurring
        return preview.answer(_summary(answered, partstat, series, zone, preview.language), digest)
    preview.check(digest)
    await calendar.save_event(user, answered)
    return Answer(uid=invitation.uid, partstat=partstat)


def _accept(calendar: Calendar, caller: CallerDependency) -> APIRouter:
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
            "the user has said yes to this very invitation. A recurring invitation is refused "
            "unless series is true, which accepts every occurrence of the series: set it only "
            "once the user has said yes to the whole series; one occurrence alone, they answer in "
            f'Calendar. Example: body={{"uid": "{EXAMPLE_UID}"}}.'
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
        return await _answer(calendar, invitation, user, preview, "ACCEPTED")

    return routes


def _decline(calendar: Calendar, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(
        prefix="/contracts/v1/calendar/invitations", tags=["calendar.invitation.decline.v1"]
    )

    @routes.post(
        "/decline",
        operation_id="decline_invitation",
        summary="Decline an invitation on the user's behalf",
        description=(
            "Declines an invitation that the user you act for received, named by the UID of its "
            "event in Calendar: their own participation becomes declined in their calendar, and "
            "Calendar tells the organizer, without a comment. Nothing else in the event changes. "
            "Call it only once the user has said no to this very invitation. A recurring "
            "invitation is refused unless series is true, which declines every occurrence of the "
            "series: set it only once the user has said no to the whole series; one occurrence "
            f'alone, they answer in Calendar. Example: body={{"uid": "{EXAMPLE_UID}"}}.'
        ),
        response_model=Answer,
        # The user's own answer, as accepting is: low, and it tells what it would do
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def decline_invitation(
        invitation: Invitation,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> Answer | JSONResponse:
        return await _answer(calendar, invitation, user, preview, "DECLINED")

    return routes


def routers(calendar: Calendar, caller: CallerDependency) -> list[APIRouter]:
    """The routers of the invitations' contracts, one each."""
    return [_accept(calendar, caller), _decline(calendar, caller)]
