"""calendar.event.read.v1: the events of the user's own calendars, over days of their time zone."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import AfterValidator, BaseModel, Field

from twake_space_agent_contracts.calendar import (
    DATA_NOT_INSTRUCTIONS,
    Calendar,
    CalendarEvent,
    EventStatus,
    Participation,
)
from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.event_create import (
    EARLIEST,
    LATEST,
    LONGEST_LOCATION,
    LONGEST_TITLE,
    Day,
    EventText,
)
from twake_space_agent_contracts.previews import one_line
from twake_space_agent_contracts.problems import Problem
from twake_space_agent_contracts.text import EMAIL
from twake_space_agent_contracts.zones import bounded, exact, midnight

DESCRIPTION_START = 200
"""How much of the start of an event's description a list gives, at most."""
LONGEST_DAYS = 31
"""The most days a list reads; and how far before and after them, at most, it reads the
occurrences that may overlap one crossing their edges, so that a long event does not have Calendar
expand months of others."""
LONGEST_ADDRESS = 320
"""How long an email address can be, as mail takes one: 64 characters before the @ and 255 after
it."""


def _within_range(day: date) -> date:
    """A first day among those create_event takes, so that the days after it, 31 at most, exist."""
    if not EARLIEST <= day <= LATEST:
        raise ValueError(f"a day is between {EARLIEST} and {LATEST}")
    return day


FirstDay = Annotated[Day, AfterValidator(_within_range)]


class Occurrence(BaseModel):
    """An occurrence of an event, by its UID and, for a recurring event, its recurrence_id."""

    uid: str
    recurrence_id: datetime | date | None = Field(
        description="Which occurrence of a recurring event it is, by the start the series gives "
        "it, in the zone the days are read in, or, before year 1 or after year 9999 in UTC or in "
        "that zone, as the event writes it, to the second, never moved; null for an event that "
        "does not repeat."
    )


class ListedEventText(EventText):
    """What people wrote of an event, on one line: its title, where it takes place, how its
    description starts and who organizes it."""

    description: str | None = Field(
        description=f"Its start, {DESCRIPTION_START} characters at most, ending with … when it "
        "goes on."
    )
    organizer: str | None = Field(
        description="The organizer's email address, as their calendar wrote it; null without "
        f"one, or when it gives anything but an address, which is {LONGEST_ADDRESS} characters "
        "at most."
    )


class ListedEvent(Occurrence):
    """One occurrence of an event of the user's calendars."""

    start: datetime | date = Field(
        description="When it starts, in the zone the days are read in, with its offset, or in UTC "
        "where that offset counts seconds; its first day, for an event of whole days."
    )
    end: datetime | date = Field(
        description="When it ends, in the zone the days are read in, with its offset, or in UTC "
        "where that offset counts seconds; its last day, for an event of whole days."
    )
    all_day: bool
    status: EventStatus | None = Field(
        description="Whether it takes place, as the organizer says; null when they do not."
    )
    private: bool = Field(
        description="Whether it is private: of the class PRIVATE or CONFIDENTIAL, which Calendar "
        "shows whoever reads its calendar without owning it, the members of a team calendar "
        "aside, as a busy time without its details; or of a class iCalendar does not know, which "
        "it reads as private. The user owns the calendars listed: it is read in full here, for "
        "them."
    )
    my_partstat: Participation | None = Field(
        description="The user's answer, NEEDS-ACTION while they have not given one; null when the "
        "event does not list them among its attendees."
    )
    needs_action: bool = Field(description="Whether the event waits for the user's answer.")
    conflicts: list[Occurrence] = Field(
        description="The occurrences that overlap it, when both take the user's time: neither "
        "declined by the user, cancelled, nor of whole days. The list may not hold them: one "
        "limit or needs_action leaves out, or one before or after the days, which are read "
        f"{LONGEST_DAYS} days around them at most: one further is not named."
    )
    untrusted: ListedEventText


class EventList(BaseModel):
    """The events of the user's calendars over days of their time zone."""

    time_zone: str | None = Field(
        description="The user's IANA time zone, which the days are read in; null when Calendar "
        "gives none the IANA database has: the days are then read in UTC, and every time is in "
        "UTC."
    )
    start: datetime = Field(
        description="When the first day starts, with its offset, or in UTC where it counts seconds."
    )
    end: datetime = Field(
        description="When the last day ends, with its offset, or in UTC where it counts seconds."
    )
    events: list[ListedEvent] = Field(description="By start, one per occurrence.")
    truncated: bool = Field(description="Whether more events are left out than the list holds.")


@dataclass(frozen=True)
class _Placed:
    """An occurrence placed in time, from when it starts to when it ends, whole days from midnight
    to midnight in the zone the days are read in, with what the list gives of it."""

    start: datetime
    end: datetime
    takes_time: bool
    """Whether it takes the user's time, as conflicts count it: neither of whole days, cancelled,
    nor declined by the user."""
    listed: ListedEvent

    @property
    def name(self) -> tuple[str, date | datetime | None]:
        """Which occurrence it is, by its uid and recurrence_id, however often Calendar gives it."""
        return self.listed.uid, self.listed.recurrence_id


def _named(moment: date | datetime, zone: ZoneInfo) -> date | datetime:
    """A recurrence_id, which names its occurrence: a time in the zone, else, before year 1 or
    after year 9999 in UTC or in the zone, which datetime cannot convert it through, as the event
    writes it, a floating one in UTC as Calendar reads it; to the second, and never moved to the
    first or last time every zone can show, which would give two occurrences one name. A day as
    it is."""
    if not isinstance(moment, datetime):
        return moment
    written = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    try:
        return exact(written.astimezone(zone))
    except OverflowError:
        return exact(written)


def _in_zone(moment: date | datetime, zone: ZoneInfo) -> date | datetime:
    """A time in the zone, with its offset, or in UTC when that offset counts seconds; a floating
    one read in UTC as Calendar reads it, and one out of those every zone can show as the first or
    last of them. A day as it is."""
    if not isinstance(moment, datetime):
        return moment
    return exact(bounded(moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(zone))


def _placed(event: CalendarEvent, zone: ZoneInfo, email: str) -> _Placed:
    period = event.period
    recurrence_id, my_partstat = event.recurrence_id, event.participation_of(email)
    organizer = event.organizer[1]
    # Twake Calendar lists the organizer among the attendees, whose answer nobody waits for
    unanswered = my_partstat == "NEEDS-ACTION" and not event.organized_by(email)
    listed = ListedEvent(
        uid=event.uid,
        recurrence_id=_named(recurrence_id.value, zone) if recurrence_id else None,
        start=_in_zone(period.start, zone),
        end=_in_zone(period.end, zone),
        all_day=period.all_day,
        status=event.status,
        private=event.private,
        my_partstat=my_partstat,
        needs_action=unanswered and not event.cancelled,
        conflicts=[],
        untrusted=ListedEventText(
            title=one_line(event.title, LONGEST_TITLE) or None,
            location=one_line(event.location, LONGEST_LOCATION) or None,
            description=one_line(event.description, DESCRIPTION_START) or None,
            # What the organizer's calendar wrote, passed on when it is an address alone
            organizer=(
                organizer
                if organizer and len(organizer) <= LONGEST_ADDRESS and EMAIL.fullmatch(organizer)
                else None
            ),
        ),
    )
    start, end = period.instants(zone)
    takes_time = not period.all_day and not event.cancelled and my_partstat != "DECLINED"
    return _Placed(start, end, takes_time, listed)


def _of_the_days(first: datetime, last: datetime, start: datetime, end: datetime) -> bool:
    """Whether a list of the days from start to end holds an occurrence from first to last: one
    that starts on them, or before them and ends after they start, so that one of no duration is
    listed on the day it starts."""
    return start <= first < end or first < start < last


def _may_be_of_the_days(
    event: CalendarEvent, zone: ZoneInfo, start: datetime, end: datetime
) -> bool:
    """Whether a list of the days from start to end may hold an event the contract cannot read:
    unless its times, when they can be read, place it out of them."""
    try:
        first, last = event.period.instants(zone)
    except Problem:
        return True
    return _of_the_days(first, last, start, end)


async def _occurrences(
    calendar: Calendar,
    user: User,
    zone: ZoneInfo,
    since: datetime,
    until: datetime,
    *,
    leave_out_unreadable: bool = False,
) -> list[_Placed]:
    """The occurrences of the user's calendars between two times, placed in time, by start; those
    without their series come whatever their days. An event the contract cannot read fails them
    all when a list of the days between the two times may hold it, unless they are to leave it
    out."""
    # Calendar leaves out of a time range an occurrence of no duration that starts when it starts
    events = await calendar.events_between(
        user, (since - timedelta(seconds=1)).astimezone(UTC), until.astimezone(UTC)
    )
    occurrences: list[_Placed] = []
    for event in events:
        try:
            occurrences.append(_placed(event, zone, user.email))
        except Problem:
            if not leave_out_unreadable and _may_be_of_the_days(event, zone, since, until):
                raise
    return sorted(occurrences, key=lambda occurrence: occurrence.start)


def _listed(
    occurrences: list[_Placed], start: datetime, end: datetime, needs_action: bool
) -> list[_Placed]:
    """The occurrences a list of the days from start to end holds; with needs_action, those of
    them alone that wait for the user's answer."""
    return [
        occurrence
        for occurrence in occurrences
        if _of_the_days(occurrence.start, occurrence.end, start, end)
        and (occurrence.listed.needs_action or not needs_action)
    ]


def _reach(listed: list[_Placed], start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """From when to when the occurrences that may overlap those listed take place: the days, and
    beyond them, the times of those listed that take the user's time."""
    timed = [occurrence for occurrence in listed if occurrence.takes_time]
    return (
        min([start, *(occurrence.start for occurrence in timed)]),
        max([end, *(occurrence.end for occurrence in timed)]),
    )


def _with_conflicts(listed: list[_Placed], occurrences: list[_Placed]) -> list[ListedEvent]:
    """What the list gives of the occurrences it holds, each with the occurrences read that
    overlap it, when both take the user's time."""
    return [
        occurrence.listed.model_copy(
            update={
                "conflicts": [
                    Occurrence(uid=other.listed.uid, recurrence_id=other.listed.recurrence_id)
                    for other in occurrences
                    if other.name != occurrence.name
                    and occurrence.takes_time
                    and other.takes_time
                    and other.start < occurrence.end
                    and occurrence.start < other.end
                ]
            }
        )
        for occurrence in listed
    ]


def router(calendar: Calendar, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/calendar", tags=["calendar.event.read.v1"])

    @routes.get(
        "/events",
        operation_id="list_calendar_events",
        summary="List the events of the user's calendars over days",
        description=(
            "Lists the events of the calendars the user you act for owns, one per occurrence, "
            "from midnight on the day from to midnight days days later, in the user's time zone, "
            "given as time_zone, or in UTC, time_zone being null, when Calendar gives none. Every "
            "time is in that zone, with its offset, or in UTC where that offset counts seconds, "
            "which RFC 3339 does not write; a recurrence_id before year 1 or after year 9999, in "
            "UTC or in that zone, comes as the event writes it, to the second. Pass "
            "needs_action=true to keep only the "
            "invitations waiting for the user's answer, before limit cuts the list. The title, "
            "location, start of the description and organizer's address of each event come under "
            f"untrusted. {DATA_NOT_INSTRUCTIONS} Example, for the user's day on 9 October 2026: "
            "from=2026-10-09, days=1."
        ),
    )
    async def list_calendar_events(
        user: Annotated[User, Depends(caller)],
        first_day: Annotated[
            FirstDay,
            Query(
                alias="from",
                description=f"The first day, such as 2026-10-09, between {EARLIEST} and {LATEST}.",
            ),
        ],
        days: Annotated[
            int,
            Query(ge=1, le=LONGEST_DAYS, description="How many days from the first, 1 by default."),
        ] = 1,
        limit: Annotated[
            int, Query(ge=1, le=100, description="How many events to return, 20 by default.")
        ] = 20,
        needs_action: Annotated[
            bool,
            Query(
                description="Whether to keep only the occurrences waiting for the user's answer, "
                "whose needs_action is true, false by default; limit and truncated count those "
                "kept. Their conflicts still name all the occurrences they overlap."
            ),
        ] = False,
    ) -> EventList:
        # Without a zone the IANA database has, the days are read in UTC, which is not the user's
        own_zone = await calendar.own_time_zone(user)
        zone = own_zone or ZoneInfo("UTC")
        start, end = midnight(first_day, zone), midnight(first_day + timedelta(days=days), zone)
        occurrences = await _occurrences(calendar, user, zone, start, end)
        listed = _listed(occurrences, start, end, needs_action)
        # An occurrence of the days that starts before them or ends after them may overlap others
        # out of them, which are read as far as it goes, LONGEST_DAYS days around them at most
        since, until = _reach(listed[:limit], start, end)
        since = max(since, midnight(first_day - timedelta(days=LONGEST_DAYS), zone))
        until = min(until, midnight(first_day + timedelta(days=days + LONGEST_DAYS), zone))
        if (since, until) != (start, end):
            # Those out of the days are read for the conflicts alone: one the contract cannot read
            # is left out of them, the list of the days being whole without it
            occurrences = await _occurrences(
                calendar, user, zone, since, until, leave_out_unreadable=True
            )
            listed = _listed(occurrences, start, end, needs_action)
        return EventList(
            time_zone=own_zone.key if own_zone is not None else None,
            start=exact(start),
            end=exact(end),
            events=_with_conflicts(listed[:limit], occurrences),
            truncated=len(listed) > limit,
        )

    return routes
