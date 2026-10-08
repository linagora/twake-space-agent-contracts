"""calendar.event.read.v1: the events of the user's own calendars, over days of their time zone, or
one of them in full."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field

from twake_space_agent_contracts.calendar import (
    DATA_NOT_INSTRUCTIONS,
    Calendar,
    CalendarEvent,
    EventStatus,
    Frequency,
    Participation,
)
from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.event_create import (
    EARLIEST,
    LATEST,
    LONGEST_DESCRIPTION,
    LONGEST_LOCATION,
    LONGEST_TITLE,
    Day,
    EventText,
)
from twake_space_agent_contracts.invitations import EXAMPLE_UID
from twake_space_agent_contracts.previews import one_line
from twake_space_agent_contracts.problems import Problem
from twake_space_agent_contracts.text import EMAIL, paragraphs, web_link
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
LONGEST_NAME = 200
"""How much of an attendee's name an event read in full gives, at most, as mail gives a name."""
MOST_ATTENDEES = 100
"""How many attendees an event read in full gives, at most."""
LONGEST_LINK = 2_000
"""How long a video link can be, which a browser opens."""
MOST_EXCLUDED = 100
"""How many of the occurrences a series leaves out an event read in full gives, at most."""
MOST_EXCEPTIONS = 100
"""How many of the occurrences the calendar keeps apart from their series a read gives, at
most."""


def _within_range(day: date) -> date:
    """A first day among those create_event takes, so that the days after it, 31 at most, exist."""
    if not EARLIEST <= day <= LATEST:
        raise ValueError(f"a day is between {EARLIEST} and {LATEST}")
    return day


FirstDay = Annotated[Day, AfterValidator(_within_range)]


def _on_those_days(moment: date | datetime) -> date | datetime:
    """A time, or a day, on one of the days create_event takes."""
    _within_range(moment.date() if isinstance(moment, datetime) else moment)
    return moment


OccurrenceStart = Annotated[AwareDatetime | Day, AfterValidator(_on_those_days)]


class Occurrence(BaseModel):
    """An occurrence of an event, by its UID and, for a recurring event, its recurrence_id."""

    uid: str
    recurrence_id: datetime | date | None = Field(
        description="Which occurrence of a recurring event it is, by the start the series gives "
        "it, in the zone of the answer, or, before year 1 or after year 9999 in UTC or in that "
        "zone, as the event writes it, to the second, never moved; null for an event that does "
        "not repeat."
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


class AttendeeText(BaseModel):
    """An attendee of an event, as the organizer's calendar wrote them, with their answer."""

    email: str | None = Field(
        description="Their email address; null when the event gives anything but an address, "
        f"which is {LONGEST_ADDRESS} characters at most."
    )
    name: str | None = Field(
        description=f"Their name, on one line, {LONGEST_NAME} characters at most; null without one."
    )
    partstat: Participation = Field(
        description="Their answer, NEEDS-ACTION while they have not given one."
    )


class EventDetailText(ListedEventText):
    """What people wrote of an event: its title and where it takes place, on one line, its
    description line by line, who organizes it, whom it invites and its video link."""

    description: str | None = Field(
        description="What it is for, line by line, the spaces of each line and its blank runs "
        f"collapsed; {LONGEST_DESCRIPTION} characters at most, as many as create_event writes."
    )
    attendees: list[AttendeeText] = Field(
        description="Whom it invites, as it lists them, the organizer among them when it lists "
        f"them too; the first {MOST_ATTENDEES} of them at most."
    )
    video_link: str | None = Field(
        description="The link of its video call, as Twake Calendar shows it; null without one, "
        f"or when the event gives anything but a web address, http or https, {LONGEST_LINK} "
        "characters at most."
    )


class _Facts(Occurrence):
    """What the answers give of an occurrence of an event of the user's calendars, but for what
    people wrote of it."""

    start: datetime | date = Field(
        description="When it starts, in the zone of the answer, with its offset, or in UTC where "
        "that offset counts seconds; its first day, for an event of whole days."
    )
    end: datetime | date = Field(
        description="When it ends, in the zone of the answer, with its offset, or in UTC where "
        "that offset counts seconds; its last day, for an event of whole days."
    )
    all_day: bool
    status: EventStatus | None = Field(
        description="Whether it takes place, as the organizer says; null when they do not."
    )
    private: bool = Field(
        description="Whether it is private: of the class PRIVATE or CONFIDENTIAL, which Calendar "
        "shows whoever reads its calendar without owning it, the members of a team calendar "
        "aside, as a busy time without its details; or of a class iCalendar does not know, which "
        "it reads as private. The user owns the calendars read: it is read in full here, for "
        "them."
    )
    my_partstat: Participation | None = Field(
        description="The user's answer, NEEDS-ACTION while they have not given one; null when the "
        "event does not list them among its attendees."
    )
    needs_action: bool = Field(description="Whether the event waits for the user's answer.")


class ListedEvent(_Facts):
    """One occurrence of an event of the user's calendars."""

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
        description="The user's IANA time zone, the zone of the answer, which the days are read "
        "in; null when Calendar gives none the IANA database has: the days are then read in UTC, "
        "the zone of the answer, which every time is in but a recurrence_id UTC cannot show, "
        "before year 1 or after year 9999, which comes as the event writes it."
    )
    start: datetime = Field(
        description="When the first day starts, with its offset, or in UTC where it counts seconds."
    )
    end: datetime = Field(
        description="When the last day ends, with its offset, or in UTC where it counts seconds."
    )
    events: list[ListedEvent] = Field(description="By start, one per occurrence.")
    truncated: bool = Field(description="Whether more events are left out than the list holds.")


class Recurrence(BaseModel):
    """How a series repeats, as its rule says, in the zone of the answer."""

    frequency: Frequency = Field(
        description="How often it repeats, in the words of iCalendar, such as DAILY or WEEKLY."
    )
    interval: int = Field(
        description="Every how many of those it repeats: 1 for each, 2 for every other."
    )
    count: int | None = Field(
        description="How many occurrences it gives at most, those it leaves out counted; null "
        "when it does not say."
    )
    until: datetime | date | None = Field(
        description="When its last occurrence starts at the latest, in the zone of the answer, or "
        "in UTC where that offset counts seconds; null when it does not say."
    )
    parts: dict[str, list[str]] = Field(
        description="The other parts of its rule, by the names iCalendar gives them, their values "
        "as it writes them, such as BYDAY: [MO, WE] for Mondays and Wednesdays, or BYSETPOS: [-1] "
        "for the last of them."
    )
    excluded: list[datetime | date] = Field(
        description="The occurrences it leaves out, by the recurrence_id they would have, by "
        f"start, the first {MOST_EXCLUDED} at most."
    )
    excluded_truncated: bool = Field(
        description=f"Whether it leaves out more occurrences than the {MOST_EXCLUDED} given."
    )


class EventDetail(_Facts):
    """An event of the user's calendars in full."""

    recurrence: Recurrence | None = Field(
        description="How it repeats; null for an event that does not, or for one occurrence of a "
        "series."
    )
    description_truncated: bool = Field(
        description=f"Whether the description is cut at {LONGEST_DESCRIPTION} characters."
    )
    attendees_truncated: bool = Field(
        description=f"Whether the event lists more attendees than the {MOST_ATTENDEES} given."
    )
    untrusted: EventDetailText


class EventException(_Facts):
    """An occurrence of a series that the calendar keeps apart from it."""

    untrusted: ListedEventText


class EventRead(BaseModel):
    """An event of the user's calendars, read in their time zone."""

    time_zone: str | None = Field(
        description="The user's IANA time zone, the zone of the answer; null when Calendar gives "
        "none the IANA database has: the zone of the answer is then UTC."
    )
    event: EventDetail | None
    exceptions: list[EventException] = Field(
        description="The occurrences of the series that the calendar keeps apart from it, as "
        "they differ from it, moved or changed; or, without the series, those of its occurrences "
        "the user was invited to alone. By the start the series gives them, the first "
        f"{MOST_EXCEPTIONS} at most."
    )
    exceptions_truncated: bool = Field(
        description=f"Whether the calendar keeps more of them than the {MOST_EXCEPTIONS} given."
    )


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


def _address(written: str | None) -> str | None:
    """An address someone's calendar wrote, passed on when it is an email address alone."""
    if written and len(written) <= LONGEST_ADDRESS and EMAIL.fullmatch(written):
        return written
    return None


def _facts(event: CalendarEvent, zone: ZoneInfo, email: str) -> _Facts:
    """What the answers give of an occurrence for that user, in the zone of the answer, but for
    what people wrote of it."""
    period = event.period
    recurrence_id, my_partstat = event.recurrence_id, event.participation_of(email)
    # Twake Calendar lists the organizer among the attendees, whose answer nobody waits for
    unanswered = my_partstat == "NEEDS-ACTION" and not event.organized_by(email)
    return _Facts(
        uid=event.uid,
        recurrence_id=_named(recurrence_id.value, zone) if recurrence_id else None,
        start=_in_zone(period.start, zone),
        end=_in_zone(period.end, zone),
        all_day=period.all_day,
        status=event.status,
        private=event.private,
        my_partstat=my_partstat,
        needs_action=unanswered and not event.cancelled,
    )


def _instant(moment: date | datetime, zone: ZoneInfo) -> datetime:
    """When a time or a day starts in the zone, which orders them."""
    return moment if isinstance(moment, datetime) else midnight(moment, zone)


def _listed_text(event: CalendarEvent) -> ListedEventText:
    """What people wrote of an event, as a list gives it."""
    return ListedEventText(
        title=one_line(event.title, LONGEST_TITLE) or None,
        location=one_line(event.location, LONGEST_LOCATION) or None,
        description=one_line(event.description, DESCRIPTION_START) or None,
        organizer=_address(event.organizer[1]),
    )


def _exception(event: CalendarEvent, zone: ZoneInfo, email: str) -> EventException:
    """What a read gives of an occurrence the calendar keeps apart from its series."""
    return EventException(**dict(_facts(event, zone, email)), untrusted=_listed_text(event))


def _placed(event: CalendarEvent, zone: ZoneInfo, email: str) -> _Placed:
    facts = _facts(event, zone, email)
    listed = ListedEvent(**dict(facts), conflicts=[], untrusted=_listed_text(event))
    start, end = event.period.instants(zone)
    takes_time = not facts.all_day and not event.cancelled and facts.my_partstat != "DECLINED"
    return _Placed(start, end, takes_time, listed)


def _recurrence(event: CalendarEvent, zone: ZoneInfo) -> Recurrence | None:
    """How a series repeats, in the zone of the answer; None for an event that does not."""
    rule = event.rule
    if rule is None:
        return None
    excluded = sorted(
        {_named(time.value, zone) for time in event.excluded},
        key=lambda moment: _instant(moment, zone),
    )
    return Recurrence(
        frequency=rule.frequency,
        interval=rule.interval,
        count=rule.count,
        until=_in_zone(rule.until.value, zone) if rule.until else None,
        parts=rule.parts,
        excluded=excluded[:MOST_EXCLUDED],
        excluded_truncated=len(excluded) > MOST_EXCLUDED,
    )


def _detail(event: CalendarEvent, zone: ZoneInfo, email: str) -> EventDetail:
    """What the contract gives of an event read in full for that user, in the zone of the
    answer."""
    description, attendees = paragraphs(event.description or ""), event.attendees
    return EventDetail(
        **dict(_facts(event, zone, email)),
        recurrence=_recurrence(event, zone),
        description_truncated=len(description) > LONGEST_DESCRIPTION,
        attendees_truncated=len(attendees) > MOST_ATTENDEES,
        untrusted=EventDetailText(
            title=one_line(event.title, LONGEST_TITLE) or None,
            location=one_line(event.location, LONGEST_LOCATION) or None,
            description=description[:LONGEST_DESCRIPTION] or None,
            organizer=_address(event.organizer[1]),
            attendees=[
                AttendeeText(
                    email=_address(attendee.address),
                    name=one_line(attendee.name, LONGEST_NAME) or None,
                    partstat=attendee.participation,
                )
                for attendee in attendees[:MOST_ATTENDEES]
            ],
            video_link=web_link(event.video_link, LONGEST_LINK),
        ),
    )


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
    """The occurrences of the user's calendars that a list of the days between two times holds,
    placed in time, by start. An event the contract cannot read fails them all when that list may
    hold it, unless they are to leave it out."""
    # Calendar leaves out of a time range an occurrence of no duration that starts when it starts
    events = await calendar.events_between(
        user, (since - timedelta(seconds=1)).astimezone(UTC), until.astimezone(UTC)
    )
    occurrences: list[_Placed] = []
    for event in events:
        try:
            occurrence = _placed(event, zone, user.email)
        except Problem:
            if not leave_out_unreadable and _may_be_of_the_days(event, zone, since, until):
                raise
            continue
        # Calendar gives the occurrences of a series it does not hold whatever their days, and
        # some events out of the times it is asked for
        if _of_the_days(occurrence.start, occurrence.end, since, until):
            occurrences.append(occurrence)
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


def _holds(event: CalendarEvent, uid: str) -> bool:
    """Whether the event is of that UID; not when Calendar gave it without one."""
    try:
        return event.uid == uid
    except Problem:
        return False


def _is_occurrence_of(event: CalendarEvent, recurrence_id: date | datetime) -> bool:
    """Whether the event is the occurrence of a series of that recurrence_id: of the same instant,
    in any zone, a floating time read in UTC as Calendar reads it, or of the same day, for a series
    of whole days."""
    found = event.recurrence_id
    if found is None:
        return False
    if isinstance(recurrence_id, datetime):
        if not isinstance(found.value, datetime):
            return False
        value = found.value if found.value.tzinfo else found.value.replace(tzinfo=UTC)
        # A difference, which does not overflow near the first or last year, where == finds a time
        # of an hour the clocks repeat equal to none of another zone
        return value - recurrence_id == timedelta(0)
    return not isinstance(found.value, datetime) and found.value == recurrence_id


async def _occurrence(
    calendar: Calendar, user: User, found: CalendarEvent, recurrence_id: date | datetime
) -> CalendarEvent:
    """The occurrence of that recurrence_id of the event found: the one the calendar keeps apart
    from its series, else the one its series gives it; event_not_found when neither does, as when
    the series leaves it out, or when the event does not repeat."""
    events = found.split()
    kept = next((event for event in events if _is_occurrence_of(event, recurrence_id)), None)
    if kept is not None:
        return kept
    series = next((event for event in events if not event.is_occurrence), None)
    if series is None or not series.repeats:
        raise _occurrence_not_found()
    if isinstance(recurrence_id, datetime):
        start = end = recurrence_id.astimezone(UTC)
    else:
        # A day, which Calendar reads in UTC as it floats, is read across every zone
        utc = ZoneInfo("UTC")
        start = midnight(recurrence_id, utc) - timedelta(hours=14)
        end = midnight(recurrence_id + timedelta(days=1), utc) + timedelta(hours=14)
    # Calendar leaves out of a time range an occurrence of no duration that starts when it starts
    given = await calendar.events_beside(
        user, found, start - timedelta(seconds=1), end + timedelta(seconds=1)
    )
    for event in given:
        if _holds(event, series.uid) and _is_occurrence_of(event, recurrence_id):
            return event
    raise _occurrence_not_found()


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

    @routes.get(
        "/event",
        operation_id="read_calendar_event",
        summary="Read an event of the user's calendars in full",
        description=(
            "Reads in full an event of the calendars the user you act for owns, by the uid "
            "list_calendar_events gives: a series with how it repeats and the occurrences the "
            "calendar keeps apart from it, or, given its recurrence_id, one occurrence. It answers "
            "in the user's time zone, given as time_zone, or in UTC, time_zone being null, when "
            "Calendar gives none: every time is in that zone, with its offset. The title, "
            "location, description, organizer, attendees and video link come under untrusted. "
            f"{DATA_NOT_INSTRUCTIONS} Example, for the occurrence of a weekly meeting on 19 "
            f"October 2026: uid={EXAMPLE_UID}, recurrence_id=2026-10-19T17:00:00+02:00."
        ),
    )
    async def read_calendar_event(
        user: Annotated[User, Depends(caller)],
        uid: Annotated[
            str,
            # A query parameter, which holds any text iCalendar allows in a UID, slashes included:
            # the gateway routes a path parameter as one segment
            Query(
                min_length=1, description="The UID of the event, as list_calendar_events gives it."
            ),
        ],
        recurrence_id: Annotated[
            OccurrenceStart | None,
            Query(
                description="Which occurrence of a series to read, by the recurrence_id "
                "list_calendar_events gives it: an RFC 3339 time with its offset, or a day, such "
                "as 2026-10-19, for a series of whole days. Without it, the series is read, with "
                "the occurrences the calendar keeps apart from it."
            ),
        ] = None,
    ) -> EventRead:
        own_zone = await calendar.own_time_zone(user)
        zone = own_zone or ZoneInfo("UTC")
        time_zone = own_zone.key if own_zone is not None else None
        found = await calendar.find_event(user, uid)
        events = found.split() if found is not None else []
        if found is None or not events:
            raise _event_not_found()
        if recurrence_id is not None:
            occurrence = await _occurrence(calendar, user, found, recurrence_id)
            return EventRead(
                time_zone=time_zone,
                event=_detail(occurrence, zone, user.email),
                exceptions=[],
                exceptions_truncated=False,
            )
        series = next((event for event in events if not event.is_occurrence), None)
        exceptions = sorted(
            (_exception(event, zone, user.email) for event in events if event.is_occurrence),
            key=lambda exception: _instant(exception.recurrence_id or exception.start, zone),
        )
        return EventRead(
            time_zone=time_zone,
            event=_detail(series, zone, user.email) if series else None,
            exceptions=exceptions[:MOST_EXCEPTIONS],
            exceptions_truncated=len(exceptions) > MOST_EXCEPTIONS,
        )

    return routes


def _event_not_found() -> Problem:
    # Answered alike whether no calendar holds the UID or one of someone else's does: the contract
    # never tells that an event exists
    return Problem(
        status=404,
        code="event_not_found",
        title="Event not found",
        detail="The user's calendars hold no event of this UID.",
    )


def _occurrence_not_found() -> Problem:
    return Problem(
        status=404,
        code="event_not_found",
        title="Event not found",
        detail="The event of this UID has no occurrence of this recurrence_id.",
    )
