from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, Store, invitation
from tests.fakes import MMAUDET_CALENDAR_ID, CalendarObject, FakeBoundary

UID = "twake-space-e2e-2026-10-13-a"
# Where Sabre delivered the invitation in the user's default calendar
HREF = f"/calendars/{MMAUDET_CALENDAR_ID}/{MMAUDET_CALENDAR_ID}/sabredav-5f0c1d2e.ics"
WEEKLY = ["rrule", {}, "recur", {"freq": "WEEKLY", "count": 3}]
ONE_OCCURRENCE = ["recurrence-id", {"tzid": "Europe/Paris"}, "date-time", "2026-10-13T17:00:00"]
CANCELLED = ["status", {}, "text", "CANCELLED"]


def attendee(address: str, partstat: str, **params: str) -> list[Any]:
    return ["attendee", {"partstat": partstat, **params}, "cal-address", f"mailto:{address}"]


def invitation_a(mmaudet_partstat: str | None = "NEEDS-ACTION", *more: list[Any]) -> list[Any]:
    """Invitation A in the user's calendar, as esn-sabre gives it in jCal: the organizer, the
    user unless their participation is None, a colleague who has not answered either, and any
    more properties."""
    mmaudet = (
        []
        if mmaudet_partstat is None
        else [attendee("mmaudet@twake.test", mmaudet_partstat, rsvp="TRUE", cn="Michel-Marie")]
    )
    return [
        "vcalendar",
        [["version", {}, "text", "2.0"], ["prodid", {}, "text", "-//Sabre//Sabre VObject 4.5//EN"]],
        [
            [
                "vevent",
                [
                    ["uid", {}, "text", UID],
                    ["dtstamp", {}, "date-time", "2026-10-06T09:00:00Z"],
                    ["dtstart", {"tzid": "Europe/Paris"}, "date-time", "2026-10-13T17:00:00"],
                    ["dtend", {"tzid": "Europe/Paris"}, "date-time", "2026-10-13T18:00:00"],
                    ["summary", {}, "text", "Point Twake Space E2E"],
                    ["organizer", {"cn": "E2E"}, "cal-address", "mailto:e2e.organizer@twake.test"],
                    attendee("e2e.organizer@twake.test", "ACCEPTED", role="CHAIR"),
                    *mmaudet,
                    attendee("colleague@twake.test", "NEEDS-ACTION", rsvp="TRUE"),
                    ["sequence", {}, "integer", 0],
                    *more,
                ],
                [],
            ]
        ],
    ]


async def accept(client: AsyncClient, event_id: str) -> Response:
    return await client.post(
        f"/contracts/v1/calendar/invitations/{event_id}/accept", headers=AS_MMAUDET
    )


async def invite_mmaudet(store: Store) -> None:
    await store(
        invitation("invitation-a", targets=["mmaudet"], time="2026-10-06T09:00:00Z", uid=UID)
    )


async def test_accepting_sets_only_the_users_participation(
    client: AsyncClient, store: Store, boundary: FakeBoundary
) -> None:
    await invite_mmaudet(store)
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    response = await accept(client, "invitation-a")

    assert response.status_code == 200, response.text
    assert response.json() == {"event_id": "invitation-a", "uid": UID, "partstat": "ACCEPTED"}
    assert boundary.calendar.objects[HREF].jcal == invitation_a("ACCEPTED")


async def test_an_invitation_sent_to_someone_else_is_not_accepted(
    client: AsyncClient, store: Store, boundary: FakeBoundary
) -> None:
    # The user has a copy in Calendar, but the stored invitation went to someone else only
    await store(
        invitation("invitation-a", targets=["colleague"], time="2026-10-06T09:00:00Z", uid=UID)
    )
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    response = await accept(client, "invitation-a")

    assert response.status_code == 404
    assert response.json()["code"] == "invitation_not_found"
    assert boundary.calendar.writes == []


async def test_an_event_other_than_an_invitation_is_not_accepted(
    client: AsyncClient, store: Store, boundary: FakeBoundary
) -> None:
    shared_file = invitation("file-a", targets=["mmaudet"], time="2026-10-06T09:00:00Z", uid=UID)
    shared_file["type"] = "com.twake.drive.file.shared.v1"
    await store(shared_file)
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    response = await accept(client, "file-a")

    assert response.status_code == 404
    assert response.json()["code"] == "invitation_not_found"
    assert boundary.calendar.writes == []


async def test_an_invitation_gone_from_the_calendar_is_reported(
    client: AsyncClient, store: Store
) -> None:
    await invite_mmaudet(store)

    response = await accept(client, "invitation-a")

    assert response.status_code == 404
    assert response.json()["code"] == "invitation_not_in_calendar"


async def test_a_user_the_event_does_not_list_is_not_made_an_attendee(
    client: AsyncClient, store: Store, boundary: FakeBoundary
) -> None:
    await invite_mmaudet(store)
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a(None))

    response = await accept(client, "invitation-a")

    assert response.status_code == 409
    assert response.json()["code"] == "not_an_attendee"
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(
    "recurrence", [WEEKLY, ONE_OCCURRENCE], ids=["a weekly series", "one occurrence of a series"]
)
async def test_a_recurring_invitation_is_left_for_the_user_to_answer(
    client: AsyncClient, store: Store, boundary: FakeBoundary, recurrence: list[Any]
) -> None:
    # The stored invitation does not say which occurrence it is about
    await invite_mmaudet(store)
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, invitation_a("NEEDS-ACTION", recurrence)
    )

    response = await accept(client, "invitation-a")

    assert response.status_code == 409
    assert response.json()["code"] == "recurring_invitation"
    assert boundary.calendar.writes == []


async def test_a_cancelled_invitation_is_not_accepted(
    client: AsyncClient, store: Store, boundary: FakeBoundary
) -> None:
    # Cancelling leaves the event in the user's calendar, and Calendar would not tell anyone
    await invite_mmaudet(store)
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, invitation_a("NEEDS-ACTION", CANCELLED)
    )

    response = await accept(client, "invitation-a")

    assert response.status_code == 409
    assert response.json()["code"] == "invitation_cancelled"
    assert boundary.calendar.writes == []


async def test_the_users_address_is_found_whatever_its_case(
    client: AsyncClient, store: Store, boundary: FakeBoundary
) -> None:
    await invite_mmaudet(store)

    def with_capitals(mmaudet_partstat: str) -> list[Any]:
        event = invitation_a(mmaudet_partstat)
        for prop in event[2][0][1]:
            if prop[3] == "mailto:mmaudet@twake.test":
                prop[3] = "MAILTO:MMaudet@Twake.test"
        return event

    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, with_capitals("NEEDS-ACTION")
    )

    response = await accept(client, "invitation-a")

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == with_capitals("ACCEPTED")
