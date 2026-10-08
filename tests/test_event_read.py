from typing import Any

from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import (
    DEFAULT_CALENDAR,
    MMAUDET,
    MMAUDET_CALENDAR_ID,
    CalendarObject,
    FakeBoundary,
    attendee,
    email_of,
    jcal_event,
)
from tests.test_event_list import ALICE, moved, organized_by

EVENT = "/contracts/v1/calendar/event"
# A UID as iCalendar allows it, with slashes, which a segment of a path cannot hold
POINT = "calendar.example.org/2026/10/13/point"


def keep(boundary: FakeBoundary, *events: list[Any], calendar: str = DEFAULT_CALENDAR) -> None:
    """Puts the events in one of the user's calendars, their default one unless named, each under
    a name of its own, as Sabre names an invitation it delivers: a UID may hold slashes, which a
    name cannot."""
    for jcal in events:
        name = f"sabredav-{len(boundary.calendar.objects) + 1}.ics"
        boundary.calendar.objects[f"{calendar}/{name}"] = CalendarObject(MMAUDET_CALENDAR_ID, jcal)


async def read_event(client: AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.get(EVENT, params=params, headers=AS_MMAUDET)
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    return answer


async def test_an_event_is_read_by_its_uid_slashes_included_in_the_user_time_zone(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.time_zones[MMAUDET] = "America/New_York"
    # Written in the time zone of Paris by its organizer
    keep(boundary, jcal_event(POINT, "2026-10-13T17:00:00", "2026-10-13T18:00:00"))

    answer = await read_event(client, uid=POINT)

    # The user's zone, which the caller keeps as theirs: never the zone an organizer wrote
    assert answer["time_zone"] == "America/New_York"
    event = answer["event"]
    assert (event["uid"], event["recurrence_id"], event["start"], event["end"]) == (
        POINT,
        None,
        "2026-10-13T11:00:00-04:00",
        "2026-10-13T12:00:00-04:00",
    )


async def test_what_people_wrote_comes_under_untrusted_the_description_line_by_line(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    keep(
        boundary,
        jcal_event(
            POINT,
            "2026-10-13T17:00:00",
            "2026-10-13T18:00:00",
            # A right-to-left override, which a reader does not see
            ["summary", {}, "text", "Point\N{RIGHT-TO-LEFT OVERRIDE} Twake\nSpace"],
            ["location", {}, "text", "Salle  Turing"],
            [
                "description",
                {},
                "text",
                "Ordre du jour :\n\n\n\n- la démo\N{ZERO WIDTH SPACE}\n  - les  questions  \n",
            ],
            organized_by(ALICE),
        ),
    )

    answer = await read_event(client, uid=POINT)

    # Each line on its own, its spaces and blank lines collapsed, without what a reader does not
    # see, such as a zero-width space
    event = answer["event"]
    assert event["untrusted"] == {
        "title": "Point Twake Space",
        "location": "Salle Turing",
        "description": "Ordre du jour :\n\n- la démo\n- les questions",
        "organizer": ALICE,
        "attendees": [],
        "video_link": None,
    }
    assert event["description_truncated"] is False


async def test_the_attendees_come_with_their_answers_and_the_user_with_theirs(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    bob, carol = email_of("bob"), email_of("carol")
    keep(
        boundary,
        jcal_event(
            POINT,
            "2026-10-13T17:00:00",
            "2026-10-13T18:00:00",
            organized_by(ALICE),
            # Twake Calendar lists the organizer among the attendees, as its chair
            attendee(ALICE, "ACCEPTED", cn="Alice\N{ZERO WIDTH SPACE}  Martin", role="CHAIR"),
            # Addresses compare lowercased, as Calendar compares them
            attendee(MMAUDET.upper(), "needs-action"),
            attendee(bob, "DECLINED", cn="Bob"),
            # No answer, or one iCalendar does not know, which it reads as none yet
            attendee(carol),
            attendee(email_of("dave"), "X-MAYBE"),
            # Calendar writes the address of anyone it invites, but anyone can write anything
            ["attendee", {"cn": "Salle Turing"}, "cal-address", "urn:uuid:salle-turing"],
        ),
    )

    answer = await read_event(client, uid=POINT)

    event = answer["event"]
    assert (event["my_partstat"], event["needs_action"]) == ("NEEDS-ACTION", True)
    assert event["untrusted"]["attendees"] == [
        {"email": ALICE, "name": "Alice Martin", "partstat": "ACCEPTED"},
        {"email": MMAUDET.upper(), "name": None, "partstat": "NEEDS-ACTION"},
        {"email": bob, "name": "Bob", "partstat": "DECLINED"},
        {"email": carol, "name": None, "partstat": "NEEDS-ACTION"},
        {"email": email_of("dave"), "name": None, "partstat": "NEEDS-ACTION"},
        {"email": None, "name": "Salle Turing", "partstat": "NEEDS-ACTION"},
    ]
    assert event["attendees_truncated"] is False


async def test_the_recurrence_is_summed_up_in_the_user_time_zone(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.time_zones[MMAUDET] = "America/New_York"
    rule = {"freq": "WEEKLY", "interval": "2", "byday": ["MO", "WE"], "wkst": "MO"}
    keep(
        boundary,
        jcal_event(
            POINT,
            "2026-10-12T17:00:00",
            "2026-10-12T18:00:00",
            ["rrule", {}, "recur", rule | {"until": "2026-12-31T22:59:59Z"}],
            # Two values in one property, as jCal writes them, then one more
            ["exdate", {"tzid": "Europe/Paris"}, "date-time", "2026-11-09T17:00:00"],
            [
                "exdate",
                {"tzid": "Europe/Paris"},
                "date-time",
                "2026-10-26T17:00:00",
                "2026-10-28T17:00:00",
            ],
        ),
        jcal_event(
            "daily",
            "2026-10-12",
            "2026-10-13",
            ["rrule", {}, "recur", {"freq": "DAILY", "count": 10}],
            ["exdate", {}, "date", "2026-10-14"],
        ),
        jcal_event("once", "2026-10-12T17:00:00", "2026-10-12T18:00:00"),
    )

    recurrences = {
        uid: (await read_event(client, uid=uid))["event"]["recurrence"]
        for uid in (POINT, "daily", "once")
    }

    # The days the clocks change in Paris and New York come between the occurrences excluded
    assert recurrences == {
        POINT: {
            "frequency": "WEEKLY",
            "interval": 2,
            "count": None,
            "until": "2026-12-31T17:59:59-05:00",
            "parts": {"BYDAY": ["MO", "WE"], "WKST": ["MO"]},
            "excluded": [
                "2026-10-26T12:00:00-04:00",
                "2026-10-28T12:00:00-04:00",
                "2026-11-09T11:00:00-05:00",
            ],
            "excluded_truncated": False,
        },
        "daily": {
            "frequency": "DAILY",
            "interval": 1,
            "count": 10,
            "until": None,
            "parts": {},
            "excluded": ["2026-10-14"],
            "excluded_truncated": False,
        },
        "once": None,
    }


def weekly(count: int) -> list[Any]:
    """A series of an hour on Mondays at 17:00 in Paris, from 12 October 2026, named Point."""
    return jcal_event(
        POINT,
        "2026-10-12T17:00:00",
        "2026-10-12T18:00:00",
        ["rrule", {}, "recur", {"freq": "WEEKLY", "count": count}],
        ["summary", {}, "text", "Point"],
    )


async def test_a_series_comes_with_its_occurrences_that_differ_from_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Moved past the day the clocks go back in Paris, then earlier on its own day
    later = moved(weekly(5), "2026-10-26T17:00:00", "2026-10-27T09:00:00", "2026-10-27T10:00:00")
    keep(
        boundary, moved(later, "2026-10-19T17:00:00", "2026-10-19T16:00:00", "2026-10-19T17:00:00")
    )

    answer = await read_event(client, uid=POINT)

    assert (answer["event"]["start"], answer["event"]["recurrence"]["count"]) == (
        "2026-10-12T17:00:00+02:00",
        5,
    )
    # By the start the series gives them
    exceptions = answer["exceptions"]
    assert [
        (exception["uid"], exception["recurrence_id"], exception["start"], exception["end"])
        for exception in exceptions
    ] == [
        (
            POINT,
            "2026-10-19T17:00:00+02:00",
            "2026-10-19T16:00:00+02:00",
            "2026-10-19T17:00:00+02:00",
        ),
        (
            POINT,
            "2026-10-26T17:00:00+01:00",
            "2026-10-27T09:00:00+01:00",
            "2026-10-27T10:00:00+01:00",
        ),
    ]
    assert [exception["untrusted"]["title"] for exception in exceptions] == ["Point", "Point"]
    assert answer["exceptions_truncated"] is False


async def test_an_occurrence_is_read_by_its_recurrence_id_kept_apart_or_given_by_its_series(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    keep(
        boundary,
        moved(weekly(5), "2026-10-26T17:00:00", "2026-10-27T09:00:00", "2026-10-27T10:00:00"),
    )

    # Kept apart from the series, as it was moved: named in another offset than the answer's
    kept = await read_event(client, uid=POINT, recurrence_id="2026-10-26T16:00:00Z")
    # Given by the series, at the start it gives it
    given = await read_event(client, uid=POINT, recurrence_id="2026-10-19T17:00:00+02:00")

    assert [
        (
            answer["event"]["recurrence_id"],
            answer["event"]["start"],
            answer["event"]["end"],
            answer["event"]["recurrence"],
            answer["exceptions"],
        )
        for answer in (kept, given)
    ] == [
        (
            "2026-10-26T17:00:00+01:00",
            "2026-10-27T09:00:00+01:00",
            "2026-10-27T10:00:00+01:00",
            None,
            [],
        ),
        (
            "2026-10-19T17:00:00+02:00",
            "2026-10-19T17:00:00+02:00",
            "2026-10-19T18:00:00+02:00",
            None,
            [],
        ),
    ]
    assert given["event"]["untrusted"]["title"] == "Point"


async def test_a_long_description_and_many_attendees_are_cut_which_the_event_tells(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    keep(
        boundary,
        jcal_event(
            POINT,
            "2026-10-13T17:00:00",
            "2026-10-13T18:00:00",
            ["description", {}, "text", "a" * 10_001],
            *(attendee(email_of(f"guest-{number:03d}"), "ACCEPTED") for number in range(101)),
        ),
    )

    event = (await read_event(client, uid=POINT))["event"]

    assert event["untrusted"]["description"] == "a" * 10_000
    assert event["description_truncated"] is True
    attendees = event["untrusted"]["attendees"]
    assert [attendee["email"] for attendee in attendees] == [
        email_of(f"guest-{number:03d}") for number in range(100)
    ]
    assert event["attendees_truncated"] is True


def video(link: str) -> list[Any]:
    """The video link of an event, as Twake Calendar writes it."""
    return ["x-openpaas-videoconference", {}, "unknown", link]


def conference(link: str, *features: str) -> list[Any]:
    """A conference of an event, as RFC 7986 writes it, video unless it says otherwise."""
    said = list(features) or ["AUDIO", "VIDEO"]
    return ["conference", {"feature": said, "label": "Join video call"}, "uri", link]


async def test_the_video_link_is_the_one_calendar_shows(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    hour = "2026-10-13T17:00:00", "2026-10-13T18:00:00"
    meet = "https://meet.twake.test/abc-defg-hij"
    keep(
        boundary,
        jcal_event("visio", *hour, video(meet), conference(meet)),
        # Another client gives its link as RFC 7986 does alone, beside a phone bridge
        jcal_event(
            "external",
            *hour,
            conference("tel:+33123456789", "PHONE"),
            conference("https://meet.example.org/x"),
        ),
        # Removed in Twake Calendar, where an empty link outlives the conference of another client
        jcal_event("removed", *hour, video(""), conference(meet)),
        # Anything but a web link, which a reader could open for one
        jcal_event("script", *hour, video("javascript:alert(1)")),
        jcal_event("hidden", *hour, video("https://meet.twake.test/\N{RIGHT-TO-LEFT OVERRIDE}x")),
        jcal_event("none", *hour),
    )

    links = {
        uid: (await read_event(client, uid=uid))["event"]["untrusted"]["video_link"]
        for uid in ("visio", "external", "removed", "script", "hidden", "none")
    }

    assert links == {
        "visio": meet,
        "external": "https://meet.example.org/x",
        "removed": None,
        "script": None,
        "hidden": None,
        "none": None,
    }
