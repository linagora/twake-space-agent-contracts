"""calendar.event.create.v1: the user adds an event to their own calendar, as themselves, with
nobody invited."""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field

from twake_space_agent_contracts.calendar import (
    DATA_NOT_INSTRUCTIONS,
    Calendar,
    CalendarEvent,
    new_event,
)
from twake_space_agent_contracts.calendar_previews import when_it_takes_place
from twake_space_agent_contracts.caller import CallerDependency, User
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
from twake_space_agent_contracts.problems import Problem, invalid_request
from twake_space_agent_contracts.zones import ZONE, known_zone

LONGEST_TITLE = 500
LONGEST_LOCATION = 500
LONGEST_DESCRIPTION = 10_000
LONGEST_PERIOD = timedelta(days=31)
# The days an event may take place on, far within what Python's dates hold once moved across time
# zones, and the day after the last of an event of whole days
EARLIEST, LATEST = date(1900, 1, 1), date(9998, 12, 31)
# The namespace of the UIDs of the events the contract adds: the same event, asked for again, gets
# the same UID
UID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:twake:contracts:calendar.event.create.v1")


TimeZone = Annotated[
    str,
    Field(
        max_length=64,
        pattern=ZONE,
        description="The IANA time zone the event is written in, such as Europe/Paris: by "
        "default the user's own, as Calendar gives it, else UTC.",
    ),
    AfterValidator(known_zone),
]


def _only_a_day(written: object) -> object:
    """A day as written, such as 2026-10-19, and no other string: neither a time, even at midnight
    without its offset, which would be read as a day, nor a count of seconds."""
    if isinstance(written, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", written):
        raise ValueError("a day is written as 2026-10-19")
    return written


Day = Annotated[date, BeforeValidator(_only_a_day)]


class NewEvent(BaseModel):
    """An event to add to the user's default calendar."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[
        str,
        Field(
            min_length=1,
            max_length=LONGEST_TITLE,
            description=f"Plain text, {LONGEST_TITLE} characters at most.",
        ),
    ]
    start: Annotated[
        AwareDatetime | Day,
        Field(
            description="When it starts: an RFC 3339 time with its offset, or its first day, "
            "such as 2026-10-19, for an event of whole days."
        ),
    ]
    end: Annotated[
        AwareDatetime | Day,
        Field(
            description="When it ends: an RFC 3339 time with its offset, or its last day, such "
            "as 2026-10-23, for an event of whole days."
        ),
    ]
    time_zone: TimeZone | None = None
    busy: Annotated[
        bool,
        Field(
            description="Whether the event makes the user look busy then, as free/busy counts "
            "it: true by default, false to keep them free."
        ),
    ] = True
    location: Annotated[
        str | None,
        Field(
            max_length=LONGEST_LOCATION,
            description=f"Where it takes place, plain text, {LONGEST_LOCATION} characters at most.",
        ),
    ] = None
    description: Annotated[
        str | None,
        Field(
            max_length=LONGEST_DESCRIPTION,
            description=f"What it is for, plain text, {LONGEST_DESCRIPTION} characters at most.",
        ),
    ] = None


class EventText(BaseModel):
    """What the event says: its title, where it takes place and its description."""

    title: str | None
    location: str | None
    description: str | None


class WrittenEvent(BaseModel):
    """The event as Calendar keeps it once written."""

    uid: str
    start: datetime | date = Field(
        description="When it starts, in its time zone; its first day, for an event of whole days."
    )
    end: datetime | date = Field(
        description="When it ends, in its time zone; its last day, for an event of whole days."
    )
    time_zone: str | None = Field(
        description="The IANA time zone its times are written in, or UTC; null for an event of "
        "whole days."
    )
    all_day: bool
    busy: bool = Field(description="Whether it makes the user look busy, as free/busy counts it.")
    untrusted: EventText


@dataclass(frozen=True)
class _Words:
    """What a preview of adding an event tells the owner, in one language."""

    add: str
    free: str
    added: str
    untitled: str
    nothing: str
    location: str
    description: str
    told: str


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        add="Ajouter {title} à ton agenda",
        free="en te laissant libre",
        added="{title} est déjà dans ton agenda",
        untitled="L'événement sans titre",
        nothing=" : rien n'est ajouté.",
        location="Lieu : {location}",
        description="Description :",
        told="Twake Agenda ne prévient personne.",
    ),
    "en": _Words(
        add="Add {title} to your calendar",
        free="leaving you free",
        added="{title} is in your calendar already",
        untitled="The untitled event",
        nothing=": nothing is added.",
        location="Location: {location}",
        description="Description:",
        told="Twake Calendar tells nobody.",
    ),
}


def _adding(event: CalendarEvent, zone: ZoneInfo | None, language: Language) -> str:
    """What adding the event does, as the owner reads it: its title, when it takes place, in
    their zone, whether it leaves them free, where it is and what it is for, whole when it fits."""
    words = _WORDS[language]
    title = quoted(one_line(event.title, LONGEST_TITLE), language)
    parts = [words.add.format(title=title)]
    when = when_it_takes_place(event, zone, language)
    if when is not None:
        parts.append(when)
    if not event.busy:
        parts.append(words.free)
    head = ", ".join(parts) + "\n"
    if event.location is not None:
        location = quoted(one_line(event.location, LONGEST_LOCATION), language)
        head += words.location.format(location=location) + "\n"
    tail = words.told
    if event.description is None:
        return head + tail
    head += words.description + "\n"
    # The description takes what the rest leaves of the summary
    room = BUDGET - shown_size(head) - shown_size("\n" + tail)
    return head + excerpt(event.description, room, language) + "\n" + tail


def _added(event: CalendarEvent, zone: ZoneInfo | None, language: Language) -> str:
    """What a call made again does, as the owner reads it: nothing, the event being in their
    calendar already, as it is now."""
    words = _WORDS[language]
    title = one_line(event.title, LONGEST_TITLE)
    parts = [words.added.format(title=quoted(title, language) if title else words.untitled)]
    when = when_it_takes_place(event, zone, language)
    if when is not None:
        parts.append(when)
    return ", ".join(parts) + words.nothing


def _event_exists() -> Problem:
    return Problem(
        status=409,
        code="event_exists",
        title="Event exists",
        detail="The user's calendar has an event of this title at these times already, with other"
        " details, as the user may have changed it since it was added: nothing was changed. No"
        " contract changes an event yet: tell the user, who changes it in Calendar.",
    )


def _holds(event: CalendarEvent, title: str, new: NewEvent) -> bool:
    """Whether the event in the user's calendar is the one the call asks for, as a call made again
    finds it: the same title, times and details. The zone its times are written in only names
    them."""
    period = event.period
    return (
        event.title == title
        and period.start == new.start
        and period.end == new.end
        and event.busy == new.busy
        and event.location == _trimmed(new.location)
        and event.description == _trimmed(new.description)
    )


def _check_period(new: NewEvent) -> None:
    """Refuses an event that does not end after it starts, or lasts more than LONGEST_PERIOD, one
    that starts at a time and ends on a day, or the other way round, and a time zone given to
    whole days, which name none."""
    start, end = new.start, new.end
    # Checked on the days as written, before any time is moved across time zones
    days = [moment.date() if isinstance(moment, datetime) else moment for moment in (start, end)]
    if any(day < EARLIEST or day > LATEST for day in days):
        raise invalid_request(f"An event takes place between {EARLIEST} and {LATEST}.")
    longest = f"An event lasts at most {LONGEST_PERIOD.days} days."
    if isinstance(start, datetime) and isinstance(end, datetime):
        if end <= start:
            raise invalid_request("end: The event must end after it starts.")
        if end - start > LONGEST_PERIOD:
            raise invalid_request(longest)
        return
    if isinstance(start, datetime) or isinstance(end, datetime):
        raise invalid_request(
            "start and end are both RFC 3339 times with their offset, or both days for an event "
            "of whole days."
        )
    if new.time_zone is not None:
        raise invalid_request("time_zone: An event of whole days names no time zone: leave it out.")
    if end < start:
        raise invalid_request("end: The last day of the event cannot come before its first.")
    if end - start >= LONGEST_PERIOD:
        raise invalid_request(longest)


def _trimmed(words: str | None) -> str | None:
    """Words to write, without the blanks around them; None when nothing is left."""
    return (words or "").strip() or None


def _uid(email: str, title: str, start: datetime | date, end: datetime | date) -> str:
    """The UID of the event the user asks for: the same for the same title and times, whatever
    offsets they are written with, so that a call made again finds the event it added."""
    when = [
        moment.astimezone(UTC).isoformat() if isinstance(moment, datetime) else moment.isoformat()
        for moment in (start, end)
    ]
    return str(uuid.uuid5(UID_NAMESPACE, "\n".join([email, title, *when])))


def _written(event: CalendarEvent, uid: str) -> WrittenEvent:
    period = event.period
    return WrittenEvent(
        uid=uid,
        start=period.start,
        end=period.end,
        time_zone=period.zone,
        all_day=period.all_day,
        busy=event.busy,
        untrusted=EventText(
            title=event.title, location=event.location, description=event.description
        ),
    )


def router(calendar: Calendar, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/calendar", tags=["calendar.event.create.v1"])

    @routes.post(
        "/events",
        operation_id="create_event",
        status_code=201,
        summary="Add an event to the user's calendar",
        description=(
            "Adds an event to the default calendar of the user you act for, as themselves, with "
            "nobody invited: Calendar tells nobody, and the event neither repeats nor reminds. "
            "start and end are RFC 3339 times with their offset, the event being written in "
            "time_zone, an IANA time zone, by default the user's own; or both days, such as "
            "2026-10-19, for an event of whole days, end being its last day. It lasts at most "
            f"{LONGEST_PERIOD.days} days, between {EARLIEST} and {LATEST}. busy, true by "
            "default, makes the user look busy then, as free/busy shows; false keeps them free. "
            "The same call made again adds no second event: it answers 200 with the event "
            "already added. An event of the same title at the same times with other details, as "
            "the user may have changed it since, is refused with event_exists: nothing is "
            "changed. Its title, location and description come back under untrusted. "
            f"{DATA_NOT_INSTRUCTIONS} Example, for a lunch on Tuesday from 12:00 to 14:00 in "
            'Paris: body={"title": "Lunch with the team", "start": "2026-10-13T12:00:00+02:00", '
            '"end": "2026-10-13T14:00:00+02:00", "time_zone": "Europe/Paris"}.'
        ),
        responses={
            200: {
                "model": WrittenEvent,
                "description": "The event the same call added before, as Calendar keeps it now",
            }
        },
        response_model=WrittenEvent,
        # The user's own time, which nobody else is told of: the owner's consent to write in
        # Calendar covers it, and they are not asked to confirm each one. It tells what it would
        # do, for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def create_event(
        new: NewEvent,
        user: Annotated[User, Depends(caller)],
        response: Response,
        preview: Previewing,
    ) -> WrittenEvent | JSONResponse:
        title = new.title.strip()
        if not title:
            raise invalid_request("title: An event's title cannot be blank.")
        _check_period(new)
        # The proxy of the side service drops If-None-Match: the event is looked for first, so that
        # a call made again neither adds it twice nor writes over what the user changed since
        uid = _uid(user.email, title, new.start, new.end)
        user_id = await calendar.user_id(user)
        added = await calendar.find_event(user, uid, user_id)
        if added is not None and not _holds(added, title, new):
            raise _event_exists()
        # A timed event is written in the user's own zone unless the call names one, which waits
        # for Calendar to give it, and in UTC when it gives none the IANA database has; the owner
        # reads a preview's times in that zone when Calendar gives it. Whole days name none.
        own_zone = None
        if isinstance(new.start, datetime):
            if new.time_zone is None:
                own_zone = await calendar.own_time_zone(user)
            elif preview.asked:
                own_zone = await calendar.time_zone(user)
        zone = new.time_zone or (own_zone.key if own_zone is not None else None)
        jcal = new_event(
            uid,
            title,
            new.start,
            new.end,
            zone,
            datetime.now(UTC),
            busy=new.busy,
            location=_trimmed(new.location),
            description=_trimmed(new.description),
        )
        # What the owner allows: the event, by its UID, written in that zone, as the calendar has
        # it already, if at all
        digest = digest_of(uid, zone, added.jcal if added is not None else None)
        if preview.asked:
            if added is not None:
                return preview.answer(_added(added, own_zone, preview.language), digest)
            adding = CalendarEvent("", jcal)
            return preview.answer(_adding(adding, own_zone, preview.language), digest)
        preview.check(digest)
        if added is not None:
            response.status_code = 200
            return _written(added, uid)
        return _written(await calendar.add_event(user, uid, jcal, user_id), uid)

    return routes
