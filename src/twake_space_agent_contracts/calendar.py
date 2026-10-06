"""The Calendar side service, called as the user with their own token."""

import copy
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel

from twake_space_agent_contracts.caller import User
from twake_space_agent_contracts.problems import Problem

# How esn-sabre (2.4.6 and later) writes UTC times in its JSON free/busy
SABRE_TIME = "%Y%m%dT%H%M%SZ"
# The properties of an event that repeats, or of one occurrence of a series
RECURRENCE = {"rrule", "rdate", "recurrence-id"}


class BusySlot(BaseModel):
    start: datetime
    end: datetime


def _calendar_problem(code: str, title: str, detail: str) -> Problem:
    return Problem(status=502, code=code, title=title, detail=detail)


def _unavailable(detail: str) -> Problem:
    return _calendar_problem("calendar_unavailable", "Calendar unavailable", detail)


def _is_component(value: Any) -> bool:
    """Whether a jCal value is a component: a name, properties and subcomponents."""
    return (
        isinstance(value, list)
        and len(value) == 3
        and isinstance(value[0], str)
        and isinstance(value[1], list)
        and isinstance(value[2], list)
        and all(
            isinstance(prop, list)
            and len(prop) >= 4
            and isinstance(prop[0], str)
            and isinstance(prop[1], dict)
            for prop in value[1]
        )
    )


@dataclass(frozen=True)
class CalendarEvent:
    """An event in one of the user's calendars: its href as esn-sabre writes it, without the
    /dav of the side service, and the event in jCal, its components checked."""

    href: str
    jcal: list[Any]

    def _vevents(self) -> list[list[Any]]:
        return [component for component in self.jcal[2] if component[0] == "vevent"]

    @property
    def recurring(self) -> bool:
        """Whether the event repeats, or holds occurrences of a series."""
        return any(prop[0] in RECURRENCE for vevent in self._vevents() for prop in vevent[1])

    @property
    def cancelled(self) -> bool:
        return any(
            prop[0] == "status" and str(prop[3]).upper() == "CANCELLED"
            for vevent in self._vevents()
            for prop in vevent[1]
        )

    def accepted_by(self, email: str) -> "CalendarEvent | None":
        """The event with the participation of that user accepted, and nothing else changed;
        None if it does not invite them. Addresses compare lowercased, as sabre's iTIP broker
        compares them."""
        address = f"mailto:{email}"
        jcal = copy.deepcopy(self.jcal)
        invited = False
        for component in jcal[2]:
            if component[0] != "vevent":
                continue
            for prop in component[1]:
                if prop[0] == "attendee" and str(prop[3]).lower() == address:
                    prop[1]["partstat"] = "ACCEPTED"
                    invited = True
        return CalendarEvent(self.href, jcal) if invited else None


class Calendar:
    """The Calendar side service, called as the user with their own token."""

    def __init__(self, url: str, http: httpx.AsyncClient) -> None:
        self._url = url
        self._http = http

    async def _request(
        self, user: User, method: str, path: str, *, missing_ok: bool = False, **request: Any
    ) -> httpx.Response | None:
        """Calendar's answer; None when what is asked for is missing and missing_ok is set."""
        headers = {"Authorization": f"Bearer {user.token}", "Accept": "application/json"}
        headers |= request.pop("headers", {})
        try:
            response = await self._http.request(
                method, self._url + path, headers=headers, **request
            )
            if missing_ok and response.status_code == 404:
                return None
            response.raise_for_status()
            return response
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
        except httpx.HTTPError as error:
            raise _unavailable(f"Calendar did not answer {method} {path}.") from error

    async def _call(
        self, user: User, method: str, path: str, *, missing_ok: bool = False, **request: Any
    ) -> Any:
        """Calendar's JSON answer; None when what is asked for is missing and missing_ok is set."""
        response = await self._request(user, method, path, missing_ok=missing_ok, **request)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError as error:
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

    async def find_event(self, user: User, uid: str) -> CalendarEvent | None:
        """The user's own copy of the event of that UID, from the calendars they own."""
        user_id = await self.user_id(user)
        found = await self._call(
            user, "REPORT", f"/dav/calendars/{user_id}.json", json={"uid": uid}, missing_ok=True
        )
        if found is None:
            return None
        try:
            item = found["_embedded"]["dav:item"][0]
            href, jcal = item["_links"]["self"]["href"], item["data"]
        except (IndexError, KeyError, TypeError) as error:
            raise _unavailable("Calendar gave the event in an unexpected form.") from error
        if not (
            isinstance(href, str)
            and _is_component(jcal)
            and all(_is_component(component) for component in jcal[2])
        ):
            raise _unavailable("Calendar gave the event in an unexpected form.")
        return CalendarEvent(href, jcal)

    async def save_event(self, user: User, event: CalendarEvent) -> None:
        """Writes the event back in its place, and esn-sabre tells the organizer of a changed
        participation. The side service does not forward If-Match: the write cannot be
        conditional, so it follows the read at once."""
        await self._request(
            user,
            "PUT",
            "/dav" + event.href,
            content=json.dumps(event.jcal),
            # As the Calendar web app sends jCal: esn-sabre reads jCal from a body that starts
            # with "[", whatever this header says
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
