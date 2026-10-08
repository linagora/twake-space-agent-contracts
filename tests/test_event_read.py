from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import (
    ALICE_CALENDAR_ID,
    DEFAULT_CALENDAR,
    MMAUDET,
    MMAUDET_CALENDAR_ID,
    CalendarObject,
    FakeBoundary,
    attendee,
    email_of,
    jcal_event,
)
from tests.test_event_list import (
    ALICE,
    ALICE_CALENDAR,
    AS_NOBODY,
    alone,
    moved,
    organized_by,
    without_end,
)

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


async def test_occurrences_without_their_series_come_as_the_calendar_keeps_them(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The user was invited to two occurrences of a series their calendar does not hold
    keep(
        boundary,
        alone(
            jcal_event(POINT, "2026-10-26T17:00:00", "2026-10-26T18:00:00"),
            jcal_event(POINT, "2026-10-19T17:00:00", "2026-10-19T18:00:00"),
        ),
    )

    answer = await read_event(client, uid=POINT)
    one = await read_event(client, uid=POINT, recurrence_id="2026-10-26T17:00:00+01:00")

    assert answer["event"] is None
    assert [exception["recurrence_id"] for exception in answer["exceptions"]] == [
        "2026-10-19T17:00:00+02:00",
        "2026-10-26T17:00:00+01:00",
    ]
    assert (one["event"]["recurrence_id"], one["event"]["start"], one["exceptions"]) == (
        "2026-10-26T17:00:00+01:00",
        "2026-10-26T17:00:00+01:00",
        [],
    )


async def test_an_occurrence_of_whole_days_is_read_by_its_day(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    daily = ["rrule", {}, "recur", {"freq": "DAILY", "count": 10}]
    # Another series of the same days, which Calendar gives before it
    keep(
        boundary,
        jcal_event("other", "2026-10-12", "2026-10-13", daily),
        jcal_event("daily", "2026-10-12", "2026-10-13", daily),
    )

    event = (await read_event(client, uid="daily", recurrence_id="2026-10-14"))["event"]

    assert (event["uid"], event["recurrence_id"], event["start"], event["end"]) == (
        "daily",
        "2026-10-14",
        "2026-10-14",
        "2026-10-14",
    )
    assert event["all_day"] is True


async def test_an_event_none_of_the_users_calendars_holds_is_not_found_alike(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    keep(boundary, weekly(5))
    # In Alice's calendar, which the user subscribes to
    boundary.calendar.objects[f"{ALICE_CALENDAR}/alices.ics"] = CalendarObject(
        ALICE_CALENDAR_ID, jcal_event("alices", "2026-10-13T17:00:00", "2026-10-13T18:00:00")
    )
    boundary.calendar.subscriptions[f"/calendars/{MMAUDET_CALENDAR_ID}/alice"] = ALICE_CALENDAR

    responses = [
        await client.get(EVENT, params=params, headers=AS_MMAUDET)
        for params in (
            {"uid": "unknown"},
            {"uid": "alices"},
            {"uid": "alices", "recurrence_id": "2026-10-13T17:00:00+02:00"},
        )
    ]

    # Whether someone else's calendar holds it, the answer does not tell
    assert [response.status_code for response in responses] == [404, 404, 404]
    assert [response.json() for response in responses] == [responses[0].json()] * 3
    assert responses[0].json()["code"] == "event_not_found"


def left_out(series: list[Any], *starts: str) -> list[Any]:
    """The series with the occurrences of those local starts left out, as one EXDATE."""
    zone = next(prop[1]["tzid"] for prop in series[2][0][1] if prop[0] == "dtstart")
    series[2][0][1].append(["exdate", {"tzid": zone}, "date-time", *starts])
    return series


@pytest.mark.parametrize(
    ("uid", "recurrence_id"),
    [
        pytest.param(POINT, "2026-10-19T17:00:00+02:00", id="left out by the series"),
        pytest.param(POINT, "2026-11-16T17:00:00+01:00", id="past its count"),
        pytest.param(POINT, "2026-10-26T18:00:00+01:00", id="at another time than its rule"),
        pytest.param(POINT, "2026-10-26", id="a day of a series of times"),
        pytest.param("daily", "2026-10-14T00:00:00Z", id="a time of a series of days"),
        pytest.param("once", "2026-10-13T17:00:00+02:00", id="of an event that does not repeat"),
    ],
)
async def test_an_occurrence_the_event_does_not_give_is_not_found(
    client: AsyncClient, boundary: FakeBoundary, uid: str, recurrence_id: str
) -> None:
    keep(
        boundary,
        left_out(weekly(5), "2026-10-19T17:00:00"),
        jcal_event("daily", "2026-10-12", "2026-10-13", ["rrule", {}, "recur", {"freq": "DAILY"}]),
        jcal_event("once", "2026-10-13T17:00:00", "2026-10-13T18:00:00"),
    )

    response = await client.get(
        EVENT, params={"uid": uid, "recurrence_id": recurrence_id}, headers=AS_MMAUDET
    )

    assert response.status_code == 404
    assert response.json()["code"] == "event_not_found"


async def test_a_series_gives_the_first_100_occurrences_it_leaves_out_or_keeps_apart(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    days = [date(2026, 10, 12) + timedelta(days=number) for number in range(202)]
    series = jcal_event(
        POINT,
        "2026-10-12T17:00:00",
        "2026-10-12T18:00:00",
        ["rrule", {}, "recur", {"freq": "DAILY", "count": 300}],
    )
    left_out(series, *(f"{day}T17:00:00" for day in reversed(days[:101])))
    for day in days[101:]:
        series = moved(series, f"{day}T17:00:00", f"{day}T18:00:00", f"{day}T19:00:00")
    keep(boundary, series)

    answer = await read_event(client, uid=POINT)

    # The oldest first, in the user's zone
    def paris(day: date) -> str:
        return datetime.combine(day, time(17), ZoneInfo("Europe/Paris")).isoformat()

    recurrence = answer["event"]["recurrence"]
    assert recurrence["excluded"] == [paris(day) for day in days[:100]]
    assert recurrence["excluded_truncated"] is True
    assert [exception["recurrence_id"] for exception in answer["exceptions"]] == [
        paris(day) for day in days[101:201]
    ]
    assert answer["exceptions_truncated"] is True


async def test_the_occurrences_left_out_in_the_hour_the_clocks_repeat_are_told_apart(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    hourly = jcal_event(
        POINT,
        "2026-10-24T22:30:00",
        "2026-10-24T22:45:00",
        ["rrule", {}, "recur", {"freq": "HOURLY"}],
        # 02:30 in Paris twice, the night the clocks go back: an hour apart, the later first
        ["exdate", {}, "date-time", "2026-10-25T01:30:00Z", "2026-10-25T00:30:00Z"],
    )
    keep(boundary, hourly)

    recurrence = (await read_event(client, uid=POINT))["event"]["recurrence"]

    assert recurrence["excluded"] == ["2026-10-25T02:30:00+02:00", "2026-10-25T02:30:00+01:00"]


async def test_an_occurrence_a_series_leaves_out_is_named_as_its_recurrence_id_never_moved(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    series = jcal_event(
        POINT,
        "2026-10-12T17:00:00",
        "2026-10-12T18:00:00",
        ["rrule", {}, "recur", {"freq": "WEEKLY"}],
        # At an offset, which iCalendar does not write but Python reads: before year 1 in UTC
        ["exdate", {}, "date-time", "0001-01-01T00:30:00+01:00"],
    )
    keep(boundary, series)

    recurrence = (await read_event(client, uid=POINT))["event"]["recurrence"]

    # Neither in the user's zone nor in UTC, which datetime cannot convert it to: as written
    assert recurrence["excluded"] == ["0001-01-01T00:30:00+01:00"]


async def test_a_private_event_is_read_in_full_and_the_users_own_meeting_awaits_no_answer(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    keep(
        boundary,
        jcal_event(
            POINT,
            "2026-10-13T17:00:00",
            "2026-10-13T18:00:00",
            ["class", {}, "text", "CONFIDENTIAL"],
            ["summary", {}, "text", "Salary review"],
            organized_by(MMAUDET),
            # Twake Calendar lists the organizer among the attendees, whose answer nobody waits for
            attendee(MMAUDET, "NEEDS-ACTION"),
            attendee(ALICE),
        ),
    )

    event = (await read_event(client, uid=POINT))["event"]

    assert (event["private"], event["untrusted"]["title"]) == (True, "Salary review")
    assert (event["my_partstat"], event["needs_action"]) == ("NEEDS-ACTION", False)


async def test_the_event_is_read_in_utc_when_calendar_gives_no_zone_the_iana_database_has(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The name Windows gives the time zone of Paris
    boundary.calendar.time_zones[MMAUDET] = "Romance Standard Time"
    keep(boundary, jcal_event(POINT, "2026-10-13T17:00:00", "2026-10-13T18:00:00"))

    answer = await read_event(client, uid=POINT)

    assert answer["time_zone"] is None
    assert (answer["event"]["start"], answer["event"]["end"]) == (
        "2026-10-13T15:00:00Z",
        "2026-10-13T16:00:00Z",
    )


async def test_times_in_a_zone_windows_names_are_read_in_the_iana_zone_cldr_gives_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The zone of Los Angeles, as Outlook writes it
    keep(
        boundary,
        jcal_event(
            POINT, "2026-10-13T09:00:00", "2026-10-13T10:00:00", zone="Pacific Standard Time"
        ),
    )

    event = (await read_event(client, uid=POINT))["event"]

    # In the user's zone, Paris, nine hours ahead
    assert (event["start"], event["end"]) == (
        "2026-10-13T18:00:00+02:00",
        "2026-10-13T19:00:00+02:00",
    )


async def test_a_series_in_a_zone_windows_names_keeps_to_its_changes_of_offset(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.time_zones[MMAUDET] = "America/New_York"
    # On Mondays at 17:00 in Berlin, as Outlook writes its zone, but the second
    series = jcal_event(
        POINT,
        "2026-10-12T17:00:00",
        "2026-10-12T18:00:00",
        ["rrule", {}, "recur", {"freq": "WEEKLY", "count": 5}],
        zone="W. Europe Standard Time",
    )
    keep(boundary, left_out(series, "2026-10-19T17:00:00"))

    whole = await read_event(client, uid=POINT)
    # Once Berlin turned its clocks back, a week before New York
    one = await read_event(client, uid=POINT, recurrence_id="2026-10-26T16:00:00Z")

    assert (whole["event"]["start"], whole["event"]["recurrence"]["excluded"]) == (
        "2026-10-12T11:00:00-04:00",
        ["2026-10-19T11:00:00-04:00"],
    )
    assert (one["event"]["recurrence_id"], one["event"]["start"]) == (
        "2026-10-26T12:00:00-04:00",
        "2026-10-26T12:00:00-04:00",
    )


HOUR = "2026-10-13T17:00:00", "2026-10-13T18:00:00"
# The name Windows shows for the zone of Paris, which neither the IANA database nor CLDR gives
SHOWN_ZONE = "(UTC+01:00) Brussels, Copenhagen, Madrid, Paris"


@pytest.mark.parametrize(
    "odd",
    [
        pytest.param(
            jcal_event(POINT, *HOUR, ["rrule", {}, "recur", {"freq": "FORTNIGHTLY"}]),
            id="a frequency iCalendar does not give",
        ),
        pytest.param(
            jcal_event(POINT, *HOUR, ["rrule", {}, "recur", {"freq": "WEEKLY", "byday": "MON"}]),
            id="a part of a rule in another form",
        ),
        pytest.param(
            left_out(jcal_event(POINT, *HOUR, ["rrule", {}, "recur", {"freq": "WEEKLY"}]), "soon"),
            id="an occurrence left out in another form",
        ),
        pytest.param(
            without_end(jcal_event(POINT, *HOUR), ["dtend", {}, "date", "2026-10-14"]),
            id="from a time to a day",
        ),
        # Its offset, the contract cannot tell
        pytest.param(jcal_event(POINT, *HOUR, zone=SHOWN_ZONE), id="a zone no database names"),
        pytest.param(
            moved(weekly(5), "2026-10-19T17:00:00", "2026-10-19T18:00:00", "soon"),
            id="an occurrence kept apart of times in another form",
        ),
    ],
)
async def test_an_event_the_contract_cannot_read_is_an_answer_in_an_unexpected_form(
    client: AsyncClient, boundary: FakeBoundary, odd: list[Any]
) -> None:
    keep(boundary, odd)

    response = await client.get(EVENT, params={"uid": POINT}, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "calendar_unavailable"


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        pytest.param("down", "calendar_unavailable", id="calendar down"),
        # Without the user's time zone, the times to give are not known
        pytest.param("settings_down", "calendar_unavailable", id="settings down"),
        pytest.param("refused_tokens", "calendar_refused", id="token refused"),
    ],
)
async def test_a_calendar_that_fails_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary, failure: str, code: str
) -> None:
    keep(boundary, weekly(5))
    setattr(boundary.calendar, failure, True)

    response = await client.get(EVENT, params={"uid": POINT}, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == code


async def test_a_user_calendar_does_not_know_is_not_found(client: AsyncClient) -> None:
    response = await client.get(EVENT, params={"uid": POINT}, headers=AS_NOBODY)

    assert response.status_code == 404
    assert response.json()["code"] == "calendar_user_not_found"


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({}, id="no uid"),
        pytest.param({"uid": ""}, id="an empty uid"),
        pytest.param(
            {"uid": POINT, "recurrence_id": "2026-10-19T17:00:00"}, id="a time without its offset"
        ),
        pytest.param({"uid": POINT, "recurrence_id": "next monday"}, id="neither time nor day"),
        pytest.param({"uid": POINT, "recurrence_id": "1899-12-31"}, id="a day before 1900"),
        pytest.param({"uid": POINT, "recurrence_id": "9999-01-01T00:00:00Z"}, id="after 9998"),
    ],
)
async def test_a_uid_or_recurrence_id_in_another_form_is_refused(
    client: AsyncClient, params: dict[str, str]
) -> None:
    response = await client.get(EVENT, params=params, headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
