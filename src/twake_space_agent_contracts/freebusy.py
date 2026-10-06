"""calendar.freebusy.read.v1: whether the user is free, from their calendars, as they see them."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel

from twake_space_agent_contracts.calendar import BusySlot, Calendar
from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.problems import invalid_request

LONGEST_PERIOD = timedelta(days=31)


class FreeBusy(BaseModel):
    """The user's busy slots over a period, in UTC."""

    start: datetime
    end: datetime
    free: bool
    busy: list[BusySlot]


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime

    @classmethod
    def checked(cls, start: datetime, end: datetime) -> "Period":
        """The period in UTC, if it ends after it starts and lasts at most LONGEST_PERIOD."""
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end <= start or end - start > LONGEST_PERIOD:
            raise invalid_request(
                f"The period must end after it starts, and last at most {LONGEST_PERIOD.days} days."
            )
        return cls(start, end)


def router(calendar: Calendar, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/calendar", tags=["calendar.freebusy.read.v1"])

    @routes.get(
        "/freebusy",
        operation_id="read_freebusy",
        summary="Tell whether the user is free over a period",
        description=(
            "Reads the busy slots of the user you act for over a period of at most "
            f"{LONGEST_PERIOD.days} days, from all their calendars. Leave out the events you are "
            "deciding about, such as an invitation, by passing their UIDs as exclude: an "
            "invitation waiting for an answer is already in the user's calendar, so without "
            "exclude it makes the user look busy. free is true when no busy slot is left. "
            "Example, for an invitation whose event has data.object.uid twake-space-e2e-a and "
            "takes place from 17:00 to 18:00 in Paris: start=2026-10-13T17:00:00+02:00, "
            "end=2026-10-13T18:00:00+02:00, exclude=twake-space-e2e-a."
        ),
    )
    async def read_freebusy(
        user: Annotated[User, Depends(caller)],
        start: Annotated[
            AwareDatetime,
            Query(
                description="Start of the period, an RFC 3339 time with its offset, such as "
                "2026-10-13T17:00:00+02:00."
            ),
        ],
        end: Annotated[
            AwareDatetime,
            Query(
                description="End of the period, an RFC 3339 time with its offset, such as "
                "2026-10-13T18:00:00+02:00."
            ),
        ],
        exclude: Annotated[
            list[str] | None,
            Query(
                description="UIDs of the calendar events to leave out. For an invitation, its "
                "UID is data.object.uid of the invitation event."
            ),
        ] = None,
    ) -> FreeBusy:
        period = Period.checked(start, end)
        busy = await calendar.busy(user, period.start, period.end, exclude or [])
        return FreeBusy(start=period.start, end=period.end, free=not busy, busy=busy)

    return routes
