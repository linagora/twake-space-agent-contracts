"""calendar.availability.read.v1: when the user and some people are all free, by free/busy alone."""

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel

from twake_space_agent_contracts.calendar import BusySlot, Calendar
from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.problems import Problem, invalid_email, invalid_request
from twake_space_agent_contracts.text import EMAIL

LONGEST_WINDOW = timedelta(days=14)
MOST_PEOPLE = 10
MOST_SLOTS = 20
SHORTEST_MEETING, LONGEST_MEETING = 15, 480
# The user's business hours, Monday to Friday in their time zone: slots start every half hour
# within them. ponytail: fixed hours, read the user's own from Calendar if they ever differ.
OPENS, CLOSES = time(9, 0), time(18, 0)
STEP = timedelta(minutes=30)


class Slot(BaseModel):
    start: datetime
    end: datetime


class Slots(BaseModel):
    """The slots where everybody is free, in the user's time zone, earliest first."""

    start: datetime
    end: datetime
    duration: int
    time_zone: str
    slots: list[Slot]
    truncated: bool


def free_slots(
    busy: Sequence[BusySlot],
    start: datetime,
    end: datetime,
    duration: timedelta,
    zone: ZoneInfo,
) -> tuple[list[Slot], bool]:
    """The slots of that duration, between two times and within the business hours of the zone,
    that overlap no busy slot; MOST_SLOTS at most, and whether more were left out."""
    slots: list[Slot] = []
    day, last = start.astimezone(zone).date(), end.astimezone(zone).date()
    while day <= last:
        if day.weekday() < 5:
            at = datetime.combine(day, OPENS, zone)
            closes = min(datetime.combine(day, CLOSES, zone), end)
            while at + duration <= closes:
                until = at + duration
                if at >= start and not any(b.start < until and b.end > at for b in busy):
                    if len(slots) == MOST_SLOTS:
                        return slots, True
                    slots.append(Slot(start=at, end=until))
                at += STEP
        day += timedelta(days=1)
    return slots, False


def person_not_found(email: str) -> Problem:
    return Problem(
        status=404,
        code="person_not_found",
        title="Person not found",
        detail=f"Calendar has no user of the email {email}, whose free/busy cannot be read.",
    )


def router(calendar: Calendar, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/calendar", tags=["calendar.availability.read.v1"])

    @routes.get(
        "/availability/slots",
        operation_id="find_meeting_slots",
        summary="Find when the user and some people are all free",
        description=(
            "Finds the times when the user you act for and up to "
            f"{MOST_PEOPLE} other people are all free for a meeting, over a period of at most "
            f"{LONGEST_WINDOW.days} days. It reads free/busy alone, never what anyone's events "
            "are. Slots are within the user's business hours, Monday to Friday 09:00 to 18:00 "
            f"in their time zone, start every half hour, and come earliest first, {MOST_SLOTS} "
            "at most: truncated says there are more. A person who is not a user of Calendar is "
            "answered person_not_found. Pass the email of each person once per value. Example, "
            "for a meeting of 30 minutes with two colleagues in the week of 12 October: "
            "email=alice@example.com, email=bob@example.com, duration=30, "
            "start=2026-10-12T00:00:00+02:00, end=2026-10-16T00:00:00+02:00."
        ),
    )
    async def find_meeting_slots(
        user: Annotated[User, Depends(caller)],
        email: Annotated[
            list[str],
            Query(
                min_length=1,
                max_length=MOST_PEOPLE,
                description=f"The email of each person to meet, {MOST_PEOPLE} at most, such as "
                "alice@example.com.",
            ),
        ],
        duration: Annotated[
            int,
            Query(
                ge=SHORTEST_MEETING,
                le=LONGEST_MEETING,
                description="How long the meeting lasts, in minutes.",
            ),
        ],
        start: Annotated[
            AwareDatetime,
            Query(
                description="Start of the period, an RFC 3339 time with its offset, such as "
                "2026-10-12T00:00:00+02:00."
            ),
        ],
        end: Annotated[
            AwareDatetime,
            Query(
                description="End of the period, an RFC 3339 time with its offset, such as "
                "2026-10-16T00:00:00+02:00."
            ),
        ],
    ) -> Slots:
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end <= start or end - start > LONGEST_WINDOW:
            raise invalid_request(
                f"The period must end after it starts, and last at most {LONGEST_WINDOW.days} days."
            )
        for address in email:
            if not EMAIL.fullmatch(address.strip()):
                raise invalid_email(
                    f"email: {address!r} is not an email address, such as alice@example.com."
                )
        # Each person once, the user's own address left out: they are in already
        others = sorted({a.strip().lower() for a in email} - {user.email.lower()})
        ids = [await calendar.user_id(user)]
        for address in others:
            found = await calendar.person_id(user, address)
            if found is None:
                raise person_not_found(address)
            ids.append(found)
        busy = await calendar.busy_of(user, ids, start, end)
        zone = await calendar.own_time_zone(user) or ZoneInfo("UTC")
        slots, truncated = free_slots(
            [slot for slots in busy.values() for slot in slots],
            start,
            end,
            timedelta(minutes=duration),
            zone,
        )
        return Slots(
            start=start.astimezone(zone),
            end=end.astimezone(zone),
            duration=duration,
            time_zone=zone.key,
            slots=slots,
            truncated=truncated,
        )

    return routes
