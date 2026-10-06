"""calendar.freebusy.read.v1: whether the user is free, from their calendars, as they see them."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.problems import Problem, invalid_request

LONGEST_PERIOD = timedelta(days=31)
# How esn-sabre (2.4.6 and later) writes UTC times in its JSON free/busy
SABRE_TIME = "%Y%m%dT%H%M%SZ"


class BusySlot(BaseModel):
    start: datetime
    end: datetime


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


def _calendar_problem(code: str, title: str, detail: str) -> Problem:
    return Problem(status=502, code=code, title=title, detail=detail)


class Calendar:
    """The Calendar side service, called as the user with their own token."""

    def __init__(self, url: str, http: httpx.AsyncClient) -> None:
        self._url = url
        self._http = http

    async def _call(self, user: User, method: str, path: str, **request: Any) -> Any:
        headers = {"Authorization": f"Bearer {user.token}", "Accept": "application/json"}
        try:
            response = await self._http.request(
                method, self._url + path, headers=headers, **request
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (401, 403):
                raise _calendar_problem(
                    "calendar_refused",
                    "Calendar refused the user's token",
                    f"Calendar answered {error.response.status_code} to {method} {path}.",
                ) from error
            raise _calendar_problem(
                "calendar_unavailable",
                "Calendar unavailable",
                f"Calendar answered {error.response.status_code} to {method} {path}.",
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise _calendar_problem(
                "calendar_unavailable",
                "Calendar unavailable",
                f"Calendar did not answer {method} {path}.",
            ) from error

    async def user_id(self, user: User) -> str:
        found = await self._call(user, "GET", "/api/users", params={"email": user.email})
        if not isinstance(found, list) or not found or "_id" not in found[0]:
            raise Problem(
                status=404,
                code="calendar_user_not_found",
                title="Calendar user not found",
                detail="Calendar has no user with the email of the user you act for.",
            )
        return str(found[0]["_id"])

    async def busy(self, user: User, period: Period, exclude: list[str]) -> list[BusySlot]:
        user_id = await self.user_id(user)
        answer = await self._call(
            user,
            "POST",
            "/dav/calendars/freebusy",
            json={
                "start": period.start.strftime(SABRE_TIME),
                "end": period.end.strftime(SABRE_TIME),
                "users": [user_id],
                "uids": exclude,
            },
        )
        try:
            slots = [
                BusySlot(
                    start=datetime.strptime(slot["start"], SABRE_TIME).replace(tzinfo=UTC),
                    end=datetime.strptime(slot["end"], SABRE_TIME).replace(tzinfo=UTC),
                )
                for user_free_busy in answer["users"]
                for calendar in user_free_busy.get("calendars", [])
                for slot in calendar.get("busy", [])
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise _calendar_problem(
                "calendar_unavailable",
                "Calendar unavailable",
                "Calendar gave free/busy in an unexpected form.",
            ) from error
        return sorted(slots, key=lambda slot: slot.start)


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
            "invitation waiting for an answer is already in the user's calendar. free is true "
            "when no busy slot is left."
        ),
    )
    async def read_freebusy(
        user: Annotated[User, Depends(caller)],
        start: Annotated[
            AwareDatetime,
            Query(description="Start of the period, an RFC 3339 time with its offset."),
        ],
        end: Annotated[
            AwareDatetime,
            Query(description="End of the period, an RFC 3339 time with its offset."),
        ],
        exclude: Annotated[
            list[str] | None,
            Query(description="UIDs of the calendar events to leave out, such as an invitation."),
        ] = None,
    ) -> FreeBusy:
        period = Period.checked(start, end)
        busy = await calendar.busy(user, period, exclude or [])
        return FreeBusy(start=period.start, end=period.end, free=not busy, busy=busy)

    return routes
