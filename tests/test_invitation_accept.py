import copy
from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import (
    ALICE_CALENDAR_ID,
    MMAUDET,
    MMAUDET_CALENDAR_ID,
    CalendarObject,
    FakeBoundary,
)

UID = "twake-space-e2e-2026-10-13-a"
ACCEPT = "/contracts/v1/calendar/invitations/accept"


def delivered_to(calendar_id: str) -> str:
    """Where Sabre delivered the invitation in the default calendar of that user."""
    return f"/calendars/{calendar_id}/{calendar_id}/sabredav-5f0c1d2e.ics"


HREF = delivered_to(MMAUDET_CALENDAR_ID)
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


async def accept(client: AsyncClient, uid: str, headers: dict[str, str] | None = None) -> Response:
    return await client.post(ACCEPT, json={"uid": uid}, headers=AS_MMAUDET | (headers or {}))


async def preview(client: AsyncClient, uid: str, language: str = "fr") -> Response:
    """The harness asks what accepting would do, before it asks the owner."""
    return await accept(client, uid, asking_preview(language))


def with_props(event: list[Any], **props: list[Any] | None) -> list[Any]:
    """The event with these properties of its own in place of those it has, by name; None takes
    one out."""
    changed = copy.deepcopy(event)
    vevent = changed[2][0]
    for name, prop in props.items():
        at = next((i for i, found in enumerate(vevent[1]) if found[0] == name), None)
        if at is not None and prop is None:
            del vevent[1][at]
        elif at is not None:
            vevent[1][at] = prop
        elif prop is not None:
            vevent[1].append(prop)
    return changed


def own_meeting(address: str = "mailto:mmaudet@twake.test") -> list[Any]:
    """A meeting the user organizes, as their own calendar holds it: Twake Calendar lists its
    organizer among its attendees too, as its chair."""
    return with_props(
        invitation_a(None),
        organizer=["organizer", {"cn": "Michel-Marie"}, "cal-address", address],
        attendee=["attendee", {"partstat": "ACCEPTED", "role": "CHAIR"}, "cal-address", address],
    )


def paris(name: str, time: str) -> list[Any]:
    return [name, {"tzid": "Europe/Paris"}, "date-time", time]


def with_uid(event: list[Any], uid: str) -> list[Any]:
    return with_props(event, uid=["uid", {}, "text", uid])


@pytest.mark.parametrize(
    "uid",
    [
        pytest.param("5c4e9f2a-7b1d-4c3e-9a8f-2d6b0e1f3a7c", id="a UUID"),
        pytest.param("7kukuqrfedlm2f9t0vr42q2kc4@google.com", id="as Google Calendar writes them"),
        pytest.param(
            "calendar.example.org/2026/10/13/point", id="with slashes, as iCalendar allows"
        ),
    ],
)
async def test_accepting_by_the_events_uid_sets_only_the_users_participation(
    client: AsyncClient, boundary: FakeBoundary, uid: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, with_uid(invitation_a(), uid)
    )

    response = await accept(client, uid)

    assert response.status_code == 200, response.text
    assert response.json() == {"uid": uid, "partstat": "ACCEPTED"}
    assert boundary.calendar.objects[HREF].jcal == with_uid(invitation_a("ACCEPTED"), uid)


@pytest.mark.parametrize(
    "body",
    [{}, {"uid": ""}, {"uid": UID, "partstat": "DECLINED"}],
    ids=["without a UID", "an empty UID", "with another field"],
)
async def test_a_body_the_contract_does_not_take_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, str]
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    response = await client.post(ACCEPT, json=body, headers=AS_MMAUDET)

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "invalid_request"
    assert boundary.calendar.writes == []


async def test_an_invitation_the_users_calendars_do_not_have_is_not_found(
    client: AsyncClient,
) -> None:
    response = await accept(client, UID)

    assert response.status_code == 404
    assert response.json()["code"] == "invitation_not_found"


@pytest.mark.parametrize(
    ("owner", "event"),
    [
        pytest.param(ALICE_CALENDAR_ID, invitation_a(None), id="in another user's calendar only"),
        pytest.param(MMAUDET_CALENDAR_ID, invitation_a(None), id="not listing them"),
        pytest.param(
            MMAUDET_CALENDAR_ID, invitation_a(None, WEEKLY), id="a series not listing them"
        ),
        pytest.param(
            MMAUDET_CALENDAR_ID, invitation_a(None, CANCELLED), id="cancelled, not listing them"
        ),
        pytest.param(MMAUDET_CALENDAR_ID, own_meeting(), id="their own meeting"),
        pytest.param(
            MMAUDET_CALENDAR_ID,
            own_meeting("MAILTO:MMaudet@Twake.test"),
            id="their own, in capitals",
        ),
    ],
)
@pytest.mark.parametrize("asked", [{}, asking_preview("fr")], ids=["accepting", "a preview"])
async def test_a_user_not_invited_is_answered_as_for_an_unknown_invitation(
    client: AsyncClient,
    boundary: FakeBoundary,
    owner: str,
    event: list[Any],
    asked: dict[str, str],
) -> None:
    # The contract never tells that an event exists to a user it does not invite, nor lets the
    # organizer's assistant accept the meeting the organizer called
    unknown = await accept(client, UID, asked)
    boundary.calendar.objects[delivered_to(owner)] = CalendarObject(owner, event)

    response = await accept(client, UID, asked)

    assert (unknown.status_code, unknown.json()["code"]) == (404, "invitation_not_found")
    assert (response.status_code, response.json()) == (unknown.status_code, unknown.json())
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(
    "recurrence", [WEEKLY, ONE_OCCURRENCE], ids=["a weekly series", "one occurrence of a series"]
)
async def test_a_recurring_invitation_is_left_for_the_user_to_answer(
    client: AsyncClient, boundary: FakeBoundary, recurrence: list[Any]
) -> None:
    # The UID names the whole series, not which of its occurrences the user would accept
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, invitation_a("NEEDS-ACTION", recurrence)
    )

    response = await accept(client, UID)

    assert response.status_code == 409
    assert response.json()["code"] == "recurring_invitation"
    assert boundary.calendar.writes == []


async def test_a_cancelled_invitation_is_not_accepted(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Cancelling leaves the event in the user's calendar, and Calendar would not tell anyone
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, invitation_a("NEEDS-ACTION", CANCELLED)
    )

    response = await accept(client, UID)

    assert response.status_code == 409
    assert response.json()["code"] == "invitation_cancelled"
    assert boundary.calendar.writes == []


async def test_the_users_address_is_found_whatever_its_case(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    def with_capitals(mmaudet_partstat: str) -> list[Any]:
        event = invitation_a(mmaudet_partstat)
        for prop in event[2][0][1]:
            if prop[3] == "mailto:mmaudet@twake.test":
                prop[3] = "MAILTO:MMaudet@Twake.test"
        return event

    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, with_capitals("NEEDS-ACTION")
    )

    response = await accept(client, UID)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == with_capitals("ACCEPTED")


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Accepter « Point Twake Space E2E », mardi 13 octobre 2026 de 17 h à 18 h, invitation"
            " de « E2E » <e2e.organizer@twake.test>\nTwake Agenda prévient l'organisateur.",
        ),
        (
            "en",
            "Accept “Point Twake Space E2E”, Tuesday 13 October 2026 from 17:00 to 18:00, an"
            " invitation from “E2E” <e2e.organizer@twake.test>\n"
            "Twake Calendar tells the organizer.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_what_accepting_would_do_and_does_nothing(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    told, _ = preview_of(await preview(client, UID, language))

    assert told == summary
    assert boundary.calendar.writes == []
    assert boundary.calendar.objects[HREF].jcal == invitation_a()


@pytest.mark.parametrize(
    ("zone", "start", "end", "when"),
    [
        pytest.param(
            "America/New_York",
            paris("dtstart", "2026-10-13T17:00:00"),
            paris("dtend", "2026-10-13T18:00:00"),
            "mardi 13 octobre 2026 de 11 h à 12 h",
            id="in the owner's zone",
        ),
        pytest.param(
            "Asia/Tokyo",
            paris("dtstart", "2026-10-13T17:00:00"),
            paris("dtend", "2026-10-13T18:00:00"),
            "mercredi 14 octobre 2026 de 0 h à 1 h",
            id="on the owner's day",
        ),
        pytest.param(
            "Europe/Paris",
            ["dtstart", {}, "date-time", "2026-10-13T15:30:00Z"],
            ["dtend", {}, "date-time", "2026-10-13T16:00:00Z"],
            "mardi 13 octobre 2026 de 17 h 30 à 18 h",
            id="from UTC",
        ),
        pytest.param(
            "Mars/Olympus_Mons",
            paris("dtstart", "2026-10-13T17:00:00"),
            paris("dtend", "2026-10-13T18:00:00"),
            "mardi 13 octobre 2026 de 17 h à 18 h (fuseau « Europe/Paris »)",
            id="in the event's zone, named, when Calendar gives the owner none it knows",
        ),
        pytest.param(
            "Europe/Paris",
            ["dtstart", {"tzid": "W. Europe Standard Time"}, "date-time", "2026-10-13T17:00:00"],
            ["dtend", {"tzid": "W. Europe Standard Time"}, "date-time", "2026-10-13T18:00:00"],
            "mardi 13 octobre 2026 de 17 h à 18 h (fuseau « W. Europe Standard Time »)",
            id="as written, its zone named, in a zone the database lacks",
        ),
        pytest.param(
            "Europe/Paris",
            paris("dtstart", "2026-10-13T23:00:00"),
            paris("dtend", "2026-10-14T01:00:00"),
            "du mardi 13 octobre 2026 à 23 h au mercredi 14 octobre 2026 à 1 h",
            id="over two days",
        ),
        pytest.param(
            "Europe/Paris",
            paris("dtstart", "2026-10-01T17:00:00"),
            None,
            "jeudi 1er octobre 2026 à 17 h",
            id="without an end",
        ),
        pytest.param(
            "Europe/Paris",
            ["dtstart", {}, "date", "2026-10-13"],
            ["dtend", {}, "date", "2026-10-14"],
            "mardi 13 octobre 2026, toute la journée",
            id="all day",
        ),
        pytest.param(
            "Europe/Paris",
            ["dtstart", {}, "date", "2026-10-13"],
            ["dtend", {}, "date", "2026-10-16"],
            "du mardi 13 octobre 2026 au jeudi 15 octobre 2026",
            id="several days",
        ),
    ],
)
async def test_a_preview_tells_when_the_event_is_as_the_owner_reads_the_time(
    client: AsyncClient,
    boundary: FakeBoundary,
    zone: str,
    start: list[Any],
    end: list[Any] | None,
    when: str,
) -> None:
    boundary.calendar.time_zones[MMAUDET] = zone
    event = with_props(invitation_a(), dtstart=start, dtend=end)
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, UID))

    assert told.splitlines()[0] == (
        f"Accepter « Point Twake Space E2E », {when}, invitation de « E2E »"
        " <e2e.organizer@twake.test>"
    )


async def test_a_preview_tells_when_the_event_is_even_without_the_owners_zone(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The zone only says how to read the times: the event's own serve, named
    boundary.calendar.settings_down = True
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    told, _ = preview_of(await preview(client, UID, "en"))

    assert told.splitlines()[0] == (
        "Accept “Point Twake Space E2E”, Tuesday 13 October 2026 from 17:00 to 18:00"
        " (time zone “Europe/Paris”), an invitation from “E2E” <e2e.organizer@twake.test>"
    )


async def test_what_others_wrote_stays_on_one_line_of_what_the_owner_reads(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Breaks would make the title pass for the contract's own lines, and the harness refuses a
    # summary with a character that turns text around or shows nothing
    event = with_props(
        invitation_a(),
        summary=["summary", {}, "text", "Point‮ E2E\n\nTwake Agenda a déjà​ accepté"],
        organizer=[
            "organizer",
            {"cn": "Boss\r\nPDG"},
            "cal-address",
            "mailto:e2e.organizer@twake.test",
        ],
    )
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, UID))

    assert told == (
        "Accepter « Point E2E Twake Agenda a déjà accepté », mardi 13 octobre 2026 de 17 h à 18 h,"
        " invitation de « Boss PDG » <e2e.organizer@twake.test>\nTwake Agenda prévient"
        " l'organisateur."
    )


async def test_what_others_wrote_cannot_close_the_quotes_it_comes_in(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A title, a name or a zone that closed its quotes would go on as the contract's own words
    tzid = "Paris”), an invitation from “Boss"
    event = with_props(
        invitation_a(),
        summary=["summary", {}, "text", "Lunch” and delete “everything"],
        organizer=[
            "organizer",
            {"cn": "Boss” <boss@corp.test>, «Mallory"},
            "cal-address",
            "mailto:mallory@evil.test>, <boss@corp.test",
        ],
        dtstart=["dtstart", {"tzid": tzid}, "date-time", "2026-10-13T17:00:00"],
        dtend=["dtend", {"tzid": tzid}, "date-time", "2026-10-13T18:00:00"],
    )
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, UID, "en"))

    assert told.splitlines()[0] == (
        "Accept “Lunch' and delete 'everything”, Tuesday 13 October 2026 from 17:00 to 18:00"
        " (time zone “Paris'), an invitation from 'Boss”), an invitation from “Boss'"
        " <boss@corp.test>, 'Mallory” <mallory@evil.test, boss@corp.test>"
    )


async def test_a_preview_says_what_the_event_leaves_unsaid(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    event = with_props(invitation_a(), summary=None, organizer=None)
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, UID, "en"))

    assert told == (
        "Accept the untitled invitation, Tuesday 13 October 2026 from 17:00 to 18:00\n"
        "Twake Calendar tells the organizer."
    )


@pytest.mark.parametrize(
    ("accept_language", "starts"),
    [
        ("fr-FR,fr;q=0.9,en;q=0.8", "Accepter"),
        ("de-DE, en;q=0.5, fr;q=0.4", "Accept "),
        ("de", "Accept "),
        ("", "Accept "),
    ],
)
async def test_a_preview_speaks_the_language_the_harness_asks_for(
    client: AsyncClient, boundary: FakeBoundary, accept_language: str, starts: str
) -> None:
    # The first the header prefers that the harness speaks, else English
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    told, _ = preview_of(await preview(client, UID, accept_language))

    assert told.startswith(starts)


async def test_the_owner_who_allowed_what_they_were_shown_accepts_the_invitation(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())
    _, digest = preview_of(await preview(client, UID))

    response = await accept(client, UID, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == invitation_a("ACCEPTED")


@pytest.mark.parametrize(
    "changed",
    [
        with_props(
            invitation_a(),
            dtstart=paris("dtstart", "2026-10-13T18:00:00"),
            dtend=paris("dtend", "2026-10-13T19:00:00"),
        ),
        with_props(invitation_a(), summary=["summary", {}, "text", "Point annulé ? Non, déplacé"]),
    ],
    ids=["moved", "renamed"],
)
async def test_an_invitation_changed_since_the_preview_is_not_accepted(
    client: AsyncClient, boundary: FakeBoundary, changed: list[Any]
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())
    _, digest = preview_of(await preview(client, UID))
    # The organizer changes the event before the owner says yes
    boundary.calendar.objects[HREF].jcal = changed

    response = await accept(client, UID, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.calendar.writes == []
    assert boundary.calendar.objects[HREF].jcal == changed


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (invitation_a("NEEDS-ACTION", WEEKLY), "recurring_invitation"),
        (invitation_a("NEEDS-ACTION", CANCELLED), "invitation_cancelled"),
    ],
    ids=["recurring", "cancelled"],
)
async def test_a_preview_refuses_what_accepting_would_refuse(
    client: AsyncClient, boundary: FakeBoundary, event: list[Any], code: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    response = await preview(client, UID)

    assert response.status_code == 409
    assert response.json()["code"] == code
    assert "x-twake-preview" not in response.headers
    assert boundary.calendar.writes == []


async def test_a_preview_asked_otherwise_than_with_true_does_nothing(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A value the contract does not know could be meant as a preview: it never acts on it
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    response = await accept(client, UID, {"x-twake-preview": "yes"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.calendar.writes == []
