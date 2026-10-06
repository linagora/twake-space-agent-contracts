"""The Calendar side service, called as the user with their own token."""

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel

from twake_space_agent_contracts.caller import User
from twake_space_agent_contracts.problems import Problem

# How esn-sabre (2.4.6 and later) writes UTC times in its JSON free/busy
SABRE_TIME = "%Y%m%dT%H%M%SZ"


class BusySlot(BaseModel):
    start: datetime
    end: datetime


def _calendar_problem(code: str, title: str, detail: str) -> Problem:
    return Problem(status=502, code=code, title=title, detail=detail)


def _unavailable(detail: str) -> Problem:
    return _calendar_problem("calendar_unavailable", "Calendar unavailable", detail)


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
            raise _unavailable(
                f"Calendar answered {error.response.status_code} to {method} {path}."
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise _unavailable(f"Calendar did not answer {method} {path}.") from error

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

    async def busy(
        self, user: User, start: datetime, end: datetime, exclude: list[str]
    ) -> list[BusySlot]:
        """The user's busy slots between two UTC times, but for the events of the given UIDs."""
        user_id = await self.user_id(user)
        answer = await self._call(
            user,
            "POST",
            "/dav/calendars/freebusy",
            json={
                "start": start.strftime(SABRE_TIME),
                "end": end.strftime(SABRE_TIME),
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
            raise _unavailable("Calendar gave free/busy in an unexpected form.") from error
        return sorted(slots, key=lambda slot: slot.start)
