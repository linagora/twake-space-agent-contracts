from collections.abc import Callable
from datetime import UTC, datetime
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
    FakeClock,
)
from tests.test_invitation_accept import (
    CANCELLED,
    HREF,
    ONE_OCCURRENCE,
    UID,
    UIDS,
    WEEKLY,
    delivered_to,
    invitation_a,
    own_meeting,
    paris,
    weekly_series,
    with_props,
    with_uid,
)

INVITATIONS = "/contracts/v1/calendar/invitations"
OPERATIONS = ["accept", "decline"]
"""The contracts that answer an invitation, by the last segment of their path: the rules they
share hold for both."""
ANSWERS = [
    pytest.param("accept", "ACCEPTED", id="accept"),
    pytest.param("decline", "DECLINED", id="decline"),
]
"""Each of them, and the participation it gives the user."""


async def answer(
    client: AsyncClient,
    operation: str,
    uid: str,
    headers: dict[str, str] | None = None,
    **body: Any,
) -> Response:
    return await client.post(
        f"{INVITATIONS}/{operation}",
        json={"uid": uid, **body},
        headers=AS_MMAUDET | (headers or {}),
    )


async def preview(
    client: AsyncClient, operation: str, uid: str, language: str = "fr", **body: Any
) -> Response:
    """The harness asks what answering would do, before it asks the owner."""
    return await answer(client, operation, uid, asking_preview(language), **body)


def occurrence_alone(mmaudet_partstat: str, *more: list[Any]) -> list[Any]:
    """The user's copy of the second occurrence of invitation A, which the organizer moved to 18:00
    and invited them to alone, as esn-sabre writes it: without the series; their participation,
    and any more properties."""
    event = weekly_series("NEEDS-ACTION", mmaudet_partstat, *more)
    del event[2][0]
    return event


def weekly_days(mmaudet_partstat: str, retitled_partstat: str) -> list[Any]:
    """Invitation A as a weekly series of whole days in the user's calendar: its days, then the
    second, which the organizer retitled; the user's participation in each."""
    series = with_props(
        invitation_a(mmaudet_partstat, WEEKLY),
        dtstart=["dtstart", {}, "date", "2026-10-13"],
        dtend=["dtend", {}, "date", "2026-10-14"],
    )
    retitled = with_props(
        invitation_a(retitled_partstat, ["recurrence-id", {}, "date", "2026-10-20"]),
        dtstart=["dtstart", {}, "date", "2026-10-20"],
        dtend=["dtend", {}, "date", "2026-10-21"],
        summary=["summary", {}, "text", "Point Twake Space E2E, au bureau"],
    )
    series[2].append(retitled[2][0])
    return series


def series_written_in(mmaudet_partstat: str, moved_partstat: str, tzid: str | None) -> list[Any]:
    """Invitation A as a weekly series in the user's calendar, its times written in that zone, or
    in none, which iCalendar calls floating: its occurrences, then the second, which the organizer
    moved to 18:00; the user's participation in each."""
    series = weekly_series(mmaudet_partstat, moved_partstat)
    for vevent in series[2]:
        for prop in vevent[1]:
            if prop[0] in ("dtstart", "dtend", "recurrence-id"):
                prop[1].pop("tzid")
                if tzid is not None:
                    prop[1]["tzid"] = tzid
    return series


def floating_series(mmaudet_partstat: str, moved_partstat: str) -> list[Any]:
    """That series, its times floating."""
    return series_written_in(mmaudet_partstat, moved_partstat, None)


def outlook_series(mmaudet_partstat: str, moved_partstat: str) -> list[Any]:
    """That series, its times in Outlook's name for the zone of Paris, which the IANA database
    lacks."""
    return series_written_in(mmaudet_partstat, moved_partstat, "W. Europe Standard Time")


def moved_without_end(mmaudet_partstat: str, moved_partstat: str) -> list[Any]:
    """Invitation A as a weekly series in the user's calendar: its occurrences, then the second,
    which the organizer moved to 18:00, written without a DTEND, which iCalendar ends when it
    starts; the user's participation in each."""
    series = weekly_series(mmaudet_partstat, moved_partstat)
    moved = series[2][1][1]
    moved[:] = [prop for prop in moved if prop[0] != "dtend"]
    return series


def lasting_an_hour(mmaudet_partstat: str, moved_partstat: str) -> list[Any]:
    """That series, the end of the moved occurrence written as a DURATION of an hour."""
    series = moved_without_end(mmaudet_partstat, moved_partstat)
    series[2][1][1].append(["duration", {}, "duration", "PT1H"])
    return series


def day_without_end(mmaudet_partstat: str, retitled_partstat: str) -> list[Any]:
    """Invitation A as a weekly series of whole days in the user's calendar, the day the organizer
    retitled written without a DTEND, which iCalendar ends at the end of that day; the user's
    participation in each."""
    series = weekly_days(mmaudet_partstat, retitled_partstat)
    retitled = series[2][1][1]
    retitled[:] = [prop for prop in retitled if prop[0] != "dtend"]
    return series


def day_moved_to_9999(mmaudet_partstat: str, retitled_partstat: str) -> list[Any]:
    """That series, the day the organizer retitled moved to 9999-12-31, the last day datetime
    holds, without a DTEND, which iCalendar ends in year 10000; the user's participation in
    each."""
    series = day_without_end(mmaudet_partstat, retitled_partstat)
    retitled = series[2][1][1]
    retitled[:] = [
        ["dtstart", {}, "date", "9999-12-31"] if prop[0] == "dtstart" else prop for prop in retitled
    ]
    return series


def lasting(duration: str) -> Callable[[str, str], list[Any]]:
    """Invitation A as a weekly series in the user's calendar, the occurrence the organizer moved
    to 18:00 written without a DTEND, lasting that DURATION; by the user's participation in each."""

    def series(mmaudet_partstat: str, moved_partstat: str) -> list[Any]:
        event = moved_without_end(mmaudet_partstat, moved_partstat)
        event[2][1][1].append(["duration", {}, "duration", duration])
        return event

    return series


def lasting_from(start: str, duration: str) -> Callable[[str, str], list[Any]]:
    """That series, the occurrence the organizer moved starting at that time instead, written at
    an offset from UTC, and lasting that DURATION; by the user's participation in each."""

    def series(mmaudet_partstat: str, moved_partstat: str) -> list[Any]:
        event = lasting(duration)(mmaudet_partstat, moved_partstat)
        moved = event[2][1]
        moved[1] = [
            ["dtstart", {}, "date-time", start] if prop[0] == "dtstart" else prop
            for prop in moved[1]
        ]
        return event

    return series


def days_alone_ending_in_year_1(mmaudet_partstat: str, first_partstat: str) -> list[Any]:
    """The user's copy of the first two days of invitation A as a weekly series of whole days,
    which the organizer invited them to alone, the first ending on 0001-01-01, whose midnight in
    Paris is before the first time datetime holds: the user's participation in the second, then in
    the first."""
    event = weekly_days(first_partstat, mmaudet_partstat)
    first = event[2][0][1]
    first[:] = [
        ["dtend", {}, "date", "0001-01-01"] if prop[0] == "dtend" else prop
        for prop in first
        if prop[0] != "rrule"
    ]
    first.append(["recurrence-id", {}, "date", "2026-10-13"])
    return event


def alone_ending_in_year_10000(mmaudet_partstat: str, first_partstat: str) -> list[Any]:
    """The user's copy of the first two occurrences of invitation A, which the organizer invited
    them to alone, the first ending on 9999-12-31 at 23:30 in UTC, in year 10000 in Paris: the
    user's participation in the second, then in the first."""
    return with_props(
        occurrences_alone(first_partstat, mmaudet_partstat),
        dtend=["dtend", {}, "date-time", "9999-12-31T23:30:00Z"],
    )


def in_zone(boundary: FakeBoundary, zone: str | None) -> None:
    """Calendar gives the user that time zone, or, for None, fails to give one."""
    if zone is None:
        boundary.calendar.settings_down = True
    else:
        boundary.calendar.time_zones[MMAUDET] = zone


def occurrences_alone(
    first_partstat: str = "NEEDS-ACTION", second_partstat: str = "NEEDS-ACTION", *more: list[Any]
) -> list[Any]:
    """The user's copy of the first two occurrences of invitation A, which the organizer invited
    them to alone, as esn-sabre writes it: without the series; the user's participation in each,
    and any more properties of the second."""
    event = invitation_a(first_partstat, ONE_OCCURRENCE)
    event[2].append(occurrence_alone(second_partstat, *more)[2][0])
    return event


def series_one_cancelled(mmaudet_partstat: str) -> list[Any]:
    """Invitation A as a weekly series in the user's calendar, the second occurrence of which the
    organizer moved, then cancelled alone: the user's participation in the series."""
    return weekly_series(mmaudet_partstat, "NEEDS-ACTION", CANCELLED)


def occurrences_one_cancelled(mmaudet_partstat: str) -> list[Any]:
    """The user's copy of the first two occurrences of invitation A, which the organizer invited
    them to alone, then cancelled the second of: the user's participation in the first."""
    return occurrences_alone(mmaudet_partstat, "NEEDS-ACTION", CANCELLED)


CONFIRMED = ["status", {}, "text", "CONFIRMED"]
"""An occurrence its organizer confirmed: esn-sabre reads the status of the last VEVENT that has
one, which a cancelled occurrence written before it is not."""


def series_one_cancelled_amid(mmaudet_partstat: str) -> list[Any]:
    """Invitation A as a weekly series in the user's calendar, the second occurrence of which the
    organizer moved, then cancelled alone, and the third moved to 18:00 and confirmed after it:
    the user's participation in the series and in the third."""
    series = series_one_cancelled(mmaudet_partstat)
    third = with_props(
        invitation_a(mmaudet_partstat, paris("recurrence-id", "2026-10-27T17:00:00"), CONFIRMED),
        dtstart=paris("dtstart", "2026-10-27T18:00:00"),
        dtend=paris("dtend", "2026-10-27T19:00:00"),
    )
    series[2].append(third[2][0])
    return series


def occurrences_first_cancelled(mmaudet_partstat: str) -> list[Any]:
    """The user's copy of the first two occurrences of invitation A, which the organizer invited
    them to alone, then cancelled the first of and confirmed the second: the user's participation
    in the second."""
    event = occurrences_alone("NEEDS-ACTION", mmaudet_partstat, CONFIRMED)
    event[2][0][1].append(CANCELLED)
    return event


@pytest.mark.parametrize("uid", UIDS)
@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_answering_by_the_events_uid_sets_only_the_users_participation(
    client: AsyncClient, boundary: FakeBoundary, operation: str, partstat: str, uid: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, with_uid(invitation_a(), uid)
    )

    response = await answer(client, operation, uid)

    assert response.status_code == 200, response.text
    assert response.json() == {"uid": uid, "partstat": partstat}
    assert boundary.calendar.objects[HREF].jcal == with_uid(invitation_a(partstat), uid)


@pytest.mark.parametrize(
    "body",
    [{}, {"uid": ""}, {"uid": UID, "partstat": "DECLINED"}],
    ids=["without a UID", "an empty UID", "with another field"],
)
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_body_the_contract_does_not_take_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary, operation: str, body: dict[str, str]
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    response = await client.post(f"{INVITATIONS}/{operation}", json=body, headers=AS_MMAUDET)

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "invalid_request"
    assert boundary.calendar.writes == []


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_an_invitation_the_users_calendars_do_not_have_is_not_found(
    client: AsyncClient, operation: str
) -> None:
    response = await answer(client, operation, UID)

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
        pytest.param(
            MMAUDET_CALENDAR_ID, with_props(own_meeting(), rrule=WEEKLY), id="their own series"
        ),
    ],
)
@pytest.mark.parametrize("asked", [{}, asking_preview("fr")], ids=["answering", "a preview"])
@pytest.mark.parametrize("series", [False, True], ids=["once", "for the whole series"])
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_user_not_invited_is_answered_as_for_an_unknown_invitation(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    series: bool,
    asked: dict[str, str],
    owner: str,
    event: list[Any],
) -> None:
    # The contract never tells that an event exists to a user it does not invite, nor lets the
    # organizer's assistant answer the meeting, or the series, the organizer called
    unknown = await answer(client, operation, UID, asked, series=series)
    boundary.calendar.objects[delivered_to(owner)] = CalendarObject(owner, event)

    response = await answer(client, operation, UID, asked, series=series)

    assert (unknown.status_code, unknown.json()["code"]) == (404, "invitation_not_found")
    assert (response.status_code, response.json()) == (unknown.status_code, unknown.json())
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(invitation_a("NEEDS-ACTION", WEEKLY), id="a weekly series"),
        pytest.param(
            weekly_series("NEEDS-ACTION", "NEEDS-ACTION"), id="a series, one occurrence moved"
        ),
        pytest.param(occurrences_alone(), id="occurrences without their series"),
        # The organizer cancelled one occurrence, not the invitation
        pytest.param(series_one_cancelled("NEEDS-ACTION"), id="a series, one occurrence cancelled"),
        pytest.param(
            occurrences_one_cancelled("NEEDS-ACTION"),
            id="occurrences without their series, one cancelled",
        ),
    ],
)
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_recurring_invitation_is_refused_unless_for_the_whole_series(
    client: AsyncClient, boundary: FakeBoundary, operation: str, event: list[Any]
) -> None:
    # The UID names the whole series, not which of its occurrences the user would answer
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    response = await answer(client, operation, UID)

    assert response.status_code == 409
    assert response.json()["code"] == "recurring_invitation"
    assert boundary.calendar.writes == []


@pytest.mark.parametrize("series", [False, True], ids=["once", "for the whole series"])
@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_an_occurrence_the_user_was_invited_to_alone_is_answered_as_an_event(
    client: AsyncClient, boundary: FakeBoundary, operation: str, partstat: str, series: bool
) -> None:
    # Their copy holds that occurrence without its series: it is all there is to answer
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, occurrence_alone("NEEDS-ACTION")
    )

    response = await answer(client, operation, UID, series=series)

    assert response.status_code == 200, response.text
    assert response.json() == {"uid": UID, "partstat": partstat}
    assert boundary.calendar.objects[HREF].jcal == occurrence_alone(partstat)


@pytest.mark.parametrize("series", [False, True], ids=["once", "for the whole series"])
@pytest.mark.parametrize(
    ("operation", "answer_words"),
    [
        pytest.param("accept", "Accepter", id="accept"),
        pytest.param("decline", "Refuser", id="decline"),
    ],
)
async def test_a_preview_tells_of_an_occurrence_the_user_was_invited_to_alone(
    client: AsyncClient, boundary: FakeBoundary, operation: str, answer_words: str, series: bool
) -> None:
    # When that occurrence takes place, not the series it belongs to
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, occurrence_alone("NEEDS-ACTION")
    )

    told, _ = preview_of(await preview(client, operation, UID, series=series))

    assert told.splitlines()[0] == (
        f"{answer_words} « Point Twake Space E2E », mardi 20 octobre 2026 de 18 h à 19 h,"
        " invitation de « E2E » <e2e.organizer@twake.test>"
    )


@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_answering_the_whole_series_answers_each_of_its_occurrences(
    client: AsyncClient, boundary: FakeBoundary, operation: str, partstat: str
) -> None:
    # The user's answer to one occurrence included, once they said so for the whole series
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, weekly_series("NEEDS-ACTION", "TENTATIVE")
    )

    response = await answer(client, operation, UID, series=True)

    assert response.status_code == 200, response.text
    assert response.json() == {"uid": UID, "partstat": partstat}
    assert boundary.calendar.objects[HREF].jcal == weekly_series(partstat, partstat)


ANSWERED_BEFORE = [
    pytest.param("accept", "ACCEPTED", "DECLINED", id="accept"),
    pytest.param("decline", "DECLINED", "ACCEPTED", id="decline"),
]
"""Each contract, the participation it gives the user, and the other answer, which they gave an
occurrence before."""


@pytest.mark.parametrize(
    ("series", "zone", "over_since"),
    [
        # The moved occurrence ends at 19:00 in Paris, 17:00 in UTC
        pytest.param(
            weekly_series,
            "Europe/Paris",
            datetime(2026, 10, 20, 17, 0, 1, tzinfo=UTC),
            id="at a time",
        ),
        pytest.param(
            lasting_an_hour,
            "Europe/Paris",
            datetime(2026, 10, 20, 17, 0, 1, tzinfo=UTC),
            id="after its duration",
        ),
        # Without an end, the moved occurrence ends when it starts, at 16:00 in UTC
        pytest.param(
            moved_without_end,
            "Europe/Paris",
            datetime(2026, 10, 20, 16, 0, 1, tzinfo=UTC),
            id="as it starts, without an end",
        ),
        # Times in no zone the IANA database has, read in the user's
        pytest.param(
            floating_series,
            "Europe/Paris",
            datetime(2026, 10, 20, 17, 0, 1, tzinfo=UTC),
            id="at a floating time",
        ),
        pytest.param(
            outlook_series,
            "Europe/Paris",
            datetime(2026, 10, 20, 17, 0, 1, tzinfo=UTC),
            id="at a time of a zone the database lacks",
        ),
        # The day the organizer retitled ends at midnight in the user's zone, 22:00 in UTC
        pytest.param(
            weekly_days, "Europe/Paris", datetime(2026, 10, 20, 22, 0, 1, tzinfo=UTC), id="on a day"
        ),
        pytest.param(
            day_without_end,
            "Europe/Paris",
            datetime(2026, 10, 20, 22, 0, 1, tzinfo=UTC),
            id="on a day without an end",
        ),
        # In UTC when Calendar gives the user no zone the database has, or fails to give one
        pytest.param(
            weekly_days,
            "Mars/Olympus_Mons",
            datetime(2026, 10, 21, 0, 0, 1, tzinfo=UTC),
            id="on a day, without the user's zone",
        ),
        pytest.param(
            floating_series,
            None,
            datetime(2026, 10, 20, 19, 0, 1, tzinfo=UTC),
            id="at a floating time, when Calendar fails to give the user's zone",
        ),
    ],
)
@pytest.mark.parametrize(("operation", "partstat", "answered_before"), ANSWERED_BEFORE)
async def test_answering_the_whole_series_leaves_the_occurrences_over_as_they_are(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    operation: str,
    partstat: str,
    answered_before: str,
    series: Callable[[str, str], list[Any]],
    zone: str | None,
    over_since: datetime,
) -> None:
    # As Twake Calendar answers a series: the answer the user gave an occurrence over stays, and
    # its organizer is not told of it again
    in_zone(boundary, zone)
    clock.wall = over_since
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, series("NEEDS-ACTION", answered_before)
    )

    response = await answer(client, operation, UID, series=True)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == series(partstat, answered_before)


@pytest.mark.parametrize(
    ("series", "zone", "under_way"),
    [
        pytest.param(
            weekly_series,
            "Europe/Paris",
            datetime(2026, 10, 20, 17, tzinfo=UTC),
            id="at a time, as it ends",
        ),
        pytest.param(
            lasting_an_hour,
            "Europe/Paris",
            datetime(2026, 10, 20, 17, tzinfo=UTC),
            id="after its duration, as it ends",
        ),
        pytest.param(
            moved_without_end,
            "Europe/Paris",
            datetime(2026, 10, 20, 16, tzinfo=UTC),
            id="as it starts and ends, without an end",
        ),
        pytest.param(
            floating_series,
            "Europe/Paris",
            datetime(2026, 10, 20, 17, tzinfo=UTC),
            id="at a floating time, as it ends",
        ),
        pytest.param(
            outlook_series,
            "Europe/Paris",
            datetime(2026, 10, 20, 17, tzinfo=UTC),
            id="at a time of a zone the database lacks, as it ends",
        ),
        pytest.param(
            weekly_days,
            "Europe/Paris",
            datetime(2026, 10, 20, 22, tzinfo=UTC),
            id="on a day, as it ends",
        ),
        pytest.param(
            day_without_end,
            "Europe/Paris",
            datetime(2026, 10, 20, 22, tzinfo=UTC),
            id="on a day without an end, as it ends",
        ),
        # 18:00 in Los Angeles: its day goes on there, though not in UTC
        pytest.param(
            weekly_days,
            "America/Los_Angeles",
            datetime(2026, 10, 21, 1, tzinfo=UTC),
            id="on a day of a zone behind UTC",
        ),
        pytest.param(
            weekly_days,
            "Mars/Olympus_Mons",
            datetime(2026, 10, 21, tzinfo=UTC),
            id="on a day, as it ends without the user's zone",
        ),
        pytest.param(
            floating_series,
            None,
            datetime(2026, 10, 20, 19, tzinfo=UTC),
            id="at a floating time, as it ends when Calendar fails to give the user's zone",
        ),
    ],
)
@pytest.mark.parametrize(("operation", "partstat", "answered_before"), ANSWERED_BEFORE)
async def test_answering_the_whole_series_answers_an_occurrence_not_over(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    operation: str,
    partstat: str,
    answered_before: str,
    series: Callable[[str, str], list[Any]],
    zone: str | None,
    under_way: datetime,
) -> None:
    # Under way, or ending that very time: not over
    in_zone(boundary, zone)
    clock.wall = under_way
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, series("NEEDS-ACTION", answered_before)
    )

    response = await answer(client, operation, UID, series=True)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == series(partstat, partstat)


@pytest.mark.parametrize(
    ("event", "now", "over", "first_time"),
    [
        # Not over by any time datetime holds
        pytest.param(
            day_moved_to_9999,
            datetime(2026, 11, 1, tzinfo=UTC),
            False,
            "Tuesday 13 October 2026, all day",
            id="a day on 9999-12-31 without an end",
        ),
        pytest.param(
            lasting("P3000000D"),
            datetime(2026, 11, 1, tzinfo=UTC),
            False,
            "Tuesday 13 October 2026 from 17:00 to 18:00",
            id="a duration of 3,000,000 days",
        ),
        pytest.param(
            lasting(f"P{'9' * 5000}D"),
            datetime(2026, 11, 1, tzinfo=UTC),
            False,
            "Tuesday 13 October 2026 from 17:00 to 18:00",
            id="a duration of more digits than Python reads",
        ),
        # Shown ending at the last time every zone can show, 9999-12-31 at midnight in UTC
        pytest.param(
            alone_ending_in_year_10000,
            datetime(2026, 10, 15, tzinfo=UTC),
            False,
            "from Tuesday 13 October 2026 at 17:00 to Friday 31 December 9999 at 01:00",
            id="a time late on 9999-12-31 in UTC",
        ),
        # Over since before any time datetime holds
        pytest.param(
            days_alone_ending_in_year_1,
            datetime(2026, 10, 8, tzinfo=UTC),
            True,
            "Tuesday 13 October 2026, all day",
            id="days ending on 0001-01-01",
        ),
    ],
)
@pytest.mark.parametrize(("operation", "partstat", "answered_before"), ANSWERED_BEFORE)
async def test_an_occurrence_ending_past_the_days_datetime_holds_is_answered_as_any_other(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    operation: str,
    partstat: str,
    answered_before: str,
    event: Callable[[str, str], list[Any]],
    now: datetime,
    over: bool,
    first_time: str,
) -> None:
    # An organizer may end an occurrence past year 9999, or before year 1, for a user in Paris: it
    # is over or not as any other, its end read as the last or first time datetime holds, and the
    # owner is told when the series takes place the first time
    clock.wall = now
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, event("NEEDS-ACTION", answered_before)
    )

    told, digest = preview_of(await preview(client, operation, UID, "en", series=True))
    response = await answer(client, operation, UID, allowed_after(digest), series=True)

    assert f", {first_time} the first time, " in told.splitlines()[0]
    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == event(
        partstat, answered_before if over else partstat
    )


@pytest.mark.parametrize(
    ("event", "when"),
    [
        pytest.param(
            with_props(
                invitation_a(),
                dtstart=["dtstart", {}, "date", "2026-10-13"],
                dtend=["dtend", {}, "date", "0001-01-01"],
            ),
            "Tuesday 13 October 2026, all day",
            id="days ending on 0001-01-01",
        ),
        pytest.param(
            with_props(invitation_a(), dtend=["dtend", {}, "date-time", "9999-12-31T23:30:00Z"]),
            "from Tuesday 13 October 2026 at 17:00 to Friday 31 December 9999 at 01:00",
            id="a time late on 9999-12-31 in UTC",
        ),
    ],
)
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_preview_tells_an_invitation_ending_past_the_days_datetime_holds(
    client: AsyncClient, boundary: FakeBoundary, operation: str, event: list[Any], when: str
) -> None:
    # An invitation that does not repeat, for a user in Paris, as the preview tells an occurrence
    # of a whole series
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, operation, UID, "en"))

    assert f", {when}, " in told.splitlines()[0]


@pytest.mark.parametrize(
    ("start", "end", "when"),
    [
        # Told as the first or last time every zone can show, as list_calendar_events gives it
        pytest.param(
            ["dtstart", {}, "date-time", "0001-01-01T00:00:00Z"],
            ["dtend", {}, "date-time", "2026-10-13T18:00:00Z"],
            "from Tuesday 2 January 1 at 00:00 to Tuesday 13 October 2026 at 18:00"
            " (time zone “UTC”)",
            id="a start before year 1",
        ),
        pytest.param(
            paris("dtstart", "2026-10-13T17:00:00"),
            paris("dtend", "9999-12-31T23:59:00"),
            "from Tuesday 13 October 2026 at 17:00 to Friday 31 December 9999 at 01:00"
            " (time zone “Europe/Paris”)",
            id="an end after 9999-12-31 at midnight UTC",
        ),
        # The end in the zone of the start, which the preview names for both
        pytest.param(
            paris("dtstart", "2026-10-13T17:00:00"),
            ["dtend", {"tzid": "America/New_York"}, "date-time", "2026-10-13T12:00:00"],
            "Tuesday 13 October 2026 from 17:00 to 18:00 (time zone “Europe/Paris”)",
            id="an end in another zone",
        ),
        pytest.param(
            paris("dtstart", "2026-10-13T17:00:00"),
            ["dtend", {"tzid": "America/New_York"}, "date-time", "9999-12-31T23:59:00"],
            "from Tuesday 13 October 2026 at 17:00 to Friday 31 December 9999 at 01:00"
            " (time zone “Europe/Paris”)",
            id="an end after year 9999 in another zone",
        ),
        # Neither in a zone the IANA database has: the end tells nothing sure
        pytest.param(
            ["dtstart", {"tzid": "W. Europe Standard Time"}, "date-time", "2026-10-13T17:00:00"],
            ["dtend", {"tzid": "Eastern Standard Time"}, "date-time", "2026-10-13T19:00:00"],
            "Tuesday 13 October 2026 at 17:00 (time zone “W. Europe Standard Time”)",
            id="an end floating in another zone",
        ),
    ],
)
@pytest.mark.parametrize(
    "zone", [None, "Mars/Olympus_Mons"], ids=["no zone", "a zone the database lacks"]
)
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_preview_without_the_users_zone_tells_the_times_in_the_zone_of_the_start(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    zone: str | None,
    start: list[Any],
    end: list[Any],
    when: str,
) -> None:
    in_zone(boundary, zone)
    event = with_props(invitation_a(), dtstart=start, dtend=end)
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, operation, UID, "en"))

    assert f", {when}, " in told.splitlines()[0]


def written_at(index: int, name: str, time: str) -> Callable[[str, str], list[Any]]:
    """Invitation A as a weekly series in the user's calendar, a time of its VEVENT at that index,
    the series or the occurrence the organizer moved to 18:00, written at an offset from UTC:
    neither RFC 5545 nor jCal writes one, nor Calendar as far as is known, but the contract reads
    it. By the user's participation in each."""

    def series(mmaudet_partstat: str, moved_partstat: str) -> list[Any]:
        event = weekly_series(mmaudet_partstat, moved_partstat)
        vevent = event[2][index]
        vevent[1] = [
            [name, {}, "date-time", time] if prop[0] == name else prop for prop in vevent[1]
        ]
        return event

    return series


@pytest.mark.parametrize(
    ("event", "over"),
    [
        pytest.param(
            written_at(0, "dtstart", "0001-01-01T00:00:00+05:00"),
            False,
            id="a series starting before year 1 in UTC",
        ),
        pytest.param(
            written_at(1, "recurrence-id", "0001-01-01T00:00:00+05:00"),
            False,
            id="an occurrence named before year 1 in UTC",
        ),
        pytest.param(
            written_at(1, "dtend", "9999-12-31T23:00:00-05:00"),
            False,
            id="an occurrence ending after year 9999 in UTC",
        ),
        pytest.param(
            written_at(1, "dtend", "0001-01-01T00:00:00+05:00"),
            True,
            id="an occurrence ending before year 1 in UTC",
        ),
    ],
)
@pytest.mark.parametrize("zone", ["Europe/Paris", "America/Los_Angeles", "Pacific/Kiritimati"])
@pytest.mark.parametrize(("operation", "partstat", "answered_before"), ANSWERED_BEFORE)
async def test_a_time_at_an_offset_past_the_times_datetime_holds_is_answered_as_any_other(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    operation: str,
    partstat: str,
    answered_before: str,
    zone: str,
    event: Callable[[str, str], list[Any]],
    over: bool,
) -> None:
    # Read as the first or last time datetime holds, as list_calendar_events reads it: the moved
    # occurrence would end on 20 October
    in_zone(boundary, zone)
    clock.wall = datetime(2026, 10, 14, tzinfo=UTC)
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, event("NEEDS-ACTION", answered_before)
    )

    _, digest = preview_of(await preview(client, operation, UID, "en", series=True))
    response = await answer(client, operation, UID, allowed_after(digest), series=True)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == event(
        partstat, answered_before if over else partstat
    )


@pytest.mark.parametrize(
    ("now", "over"),
    [
        pytest.param(datetime(2026, 10, 9, 9, 30, tzinfo=UTC), False, id="as it ends"),
        pytest.param(datetime(2026, 10, 9, 9, 30, 1, tzinfo=UTC), True, id="after it ends"),
    ],
)
@pytest.mark.parametrize(("operation", "partstat", "answered_before"), ANSWERED_BEFORE)
async def test_a_duration_counts_from_a_start_at_an_offset_as_written(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    operation: str,
    partstat: str,
    answered_before: str,
    now: datetime,
    over: bool,
) -> None:
    # 739897 days and 14 hours 30 minutes after 0001-01-01 at 00:00 at +05:00, five hours before
    # the first time UTC holds, is 2026-10-09 at 14:30 at +05:00: 9:30 in UTC
    event = lasting_from("0001-01-01T00:00:00+05:00", "P739897DT14H30M")
    clock.wall = now
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, event("NEEDS-ACTION", answered_before)
    )

    response = await answer(client, operation, UID, series=True)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == event(
        partstat, answered_before if over else partstat
    )


@pytest.mark.parametrize(
    ("event", "moment"),
    [
        # The two occurrences end on 13 and 20 October
        pytest.param(occurrences_alone(), datetime(2026, 11, 1, tzinfo=UTC), id="each over"),
        pytest.param(
            occurrences_one_cancelled("NEEDS-ACTION"),
            datetime(2026, 10, 14, tzinfo=UTC),
            id="over, or cancelled",
        ),
    ],
)
@pytest.mark.parametrize("asked", [{}, asking_preview("fr")], ids=["answering", "a preview"])
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_whole_series_with_no_occurrence_left_to_answer_is_refused(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    operation: str,
    asked: dict[str, str],
    event: list[Any],
    moment: datetime,
) -> None:
    # The user's copy holds occurrences without their series, each over or cancelled: the call
    # would change nothing, while its answer would say that the calendar holds the user's
    clock.wall = moment
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    response = await answer(client, operation, UID, asked, series=True)

    assert response.status_code == 409
    assert response.json()["code"] == "nothing_to_answer"
    assert "x-twake-preview" not in response.headers
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(
    ("event", "series"),
    [
        pytest.param(invitation_a, False, id="once"),
        pytest.param(
            lambda partstat: weekly_series(partstat, partstat), True, id="for the whole series"
        ),
    ],
)
@pytest.mark.parametrize("asked", [{}, asking_preview("fr")], ids=["answering", "a preview"])
@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_answering_as_the_user_answered_already_is_refused(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    partstat: str,
    asked: dict[str, str],
    event: Callable[[str], list[Any]],
    series: bool,
) -> None:
    # esn-sabre tells the organizer of a participation that changes, and of no other: the call
    # would write the event as it is, and tell nobody
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event(partstat))

    response = await answer(client, operation, UID, asked, series=series)

    assert response.status_code == 409
    assert response.json()["code"] == "nothing_to_answer"
    assert "x-twake-preview" not in response.headers
    assert boundary.calendar.writes == []


CANCELLED_SERIES = with_props(
    weekly_series("NEEDS-ACTION", "NEEDS-ACTION", CANCELLED), status=CANCELLED
)
"""A series its organizer cancelled, as esn-sabre writes it in the user's copy: each occurrence
cancelled."""
CANCELLED_OCCURRENCE = occurrence_alone("NEEDS-ACTION", CANCELLED)
"""An occurrence the organizer invited the user to alone, then cancelled."""
CANCELLED_OCCURRENCES = with_props(
    occurrences_alone("NEEDS-ACTION", "NEEDS-ACTION", CANCELLED), status=CANCELLED
)
"""Occurrences the organizer invited the user to without their series, then cancelled each of."""


@pytest.mark.parametrize(
    ("event", "series"),
    [
        pytest.param(invitation_a("NEEDS-ACTION", CANCELLED), False, id="once"),
        pytest.param(invitation_a("NEEDS-ACTION", WEEKLY, CANCELLED), False, id="a series, once"),
        pytest.param(
            invitation_a("NEEDS-ACTION", WEEKLY, CANCELLED), True, id="for the whole series"
        ),
        pytest.param(CANCELLED_SERIES, False, id="each occurrence, once"),
        pytest.param(CANCELLED_SERIES, True, id="each occurrence, for the whole series"),
        pytest.param(CANCELLED_OCCURRENCE, False, id="an occurrence alone"),
        pytest.param(CANCELLED_OCCURRENCE, True, id="an occurrence alone, for the whole series"),
        pytest.param(CANCELLED_OCCURRENCES, False, id="occurrences without their series"),
        pytest.param(
            CANCELLED_OCCURRENCES, True, id="occurrences without their series, for the whole series"
        ),
    ],
)
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_cancelled_invitation_is_not_answered(
    client: AsyncClient, boundary: FakeBoundary, operation: str, event: list[Any], series: bool
) -> None:
    # Cancelling leaves the event in the user's calendar, and Calendar would not tell anyone: the
    # owner is not asked either about the whole series of a cancelled event
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    response = await answer(client, operation, UID, series=series)

    assert response.status_code == 409
    assert response.json()["code"] == "invitation_cancelled"
    assert boundary.calendar.writes == []


CANCELLED_AMONG = [
    pytest.param(series_one_cancelled, id="in a series"),
    pytest.param(series_one_cancelled_amid, id="in a series, before another occurrence"),
    pytest.param(occurrences_one_cancelled, id="among occurrences without their series"),
    pytest.param(
        occurrences_first_cancelled,
        id="first among occurrences without their series",
    ),
]
"""Copies that hold an occurrence cancelled alone, the last of the copy or not, by the user's
participation in the others."""


@pytest.mark.parametrize("event", CANCELLED_AMONG)
@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_answering_the_whole_series_leaves_an_occurrence_cancelled_alone_as_it_is(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    partstat: str,
    event: Callable[[str], list[Any]],
) -> None:
    # The organizer cancelled that occurrence, not the invitation: the user answers the others
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event("NEEDS-ACTION"))

    response = await answer(client, operation, UID, series=True)

    assert response.status_code == 200, response.text
    assert response.json() == {"uid": UID, "partstat": partstat}
    assert boundary.calendar.objects[HREF].jcal == event(partstat)


@pytest.mark.parametrize("event", CANCELLED_AMONG)
@pytest.mark.parametrize(
    ("language", "told"),
    [
        pytest.param(
            "fr",
            "Au moins une occurrence est annulée : Twake Agenda pourrait ne pas prévenir"
            " l'organisateur.",
            id="fr",
        ),
        pytest.param(
            "en",
            "At least one occurrence is cancelled: Twake Calendar may not tell the organizer.",
            id="en",
        ),
    ],
)
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_preview_of_a_series_holding_a_cancelled_occurrence_says_who_may_not_be_told(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    language: str,
    told: str,
    event: Callable[[str], list[Any]],
) -> None:
    # esn-sabre may then send the organizer no reply: the owner is not told that it does
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event("NEEDS-ACTION"))

    summary, _ = preview_of(await preview(client, operation, UID, language, series=True))

    assert summary.splitlines()[1:] == [told]
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_the_users_address_is_found_whatever_its_case(
    client: AsyncClient, boundary: FakeBoundary, operation: str, partstat: str
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

    response = await answer(client, operation, UID)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == with_capitals(partstat)


@pytest.mark.parametrize(
    ("operation", "language", "summary"),
    [
        pytest.param(
            "accept",
            "fr",
            "Accepter « Point Twake Space E2E », mardi 13 octobre 2026 de 17 h à 18 h, invitation"
            " de « E2E » <e2e.organizer@twake.test>\nTwake Agenda prévient l'organisateur.",
            id="accept-fr",
        ),
        pytest.param(
            "accept",
            "en",
            "Accept “Point Twake Space E2E”, Tuesday 13 October 2026 from 17:00 to 18:00, an"
            " invitation from “E2E” <e2e.organizer@twake.test>\n"
            "Twake Calendar tells the organizer.",
            id="accept-en",
        ),
        pytest.param(
            "decline",
            "fr",
            "Refuser « Point Twake Space E2E », mardi 13 octobre 2026 de 17 h à 18 h, invitation"
            " de « E2E » <e2e.organizer@twake.test>\nTwake Agenda prévient l'organisateur.",
            id="decline-fr",
        ),
        pytest.param(
            "decline",
            "en",
            "Decline “Point Twake Space E2E”, Tuesday 13 October 2026 from 17:00 to 18:00, an"
            " invitation from “E2E” <e2e.organizer@twake.test>\n"
            "Twake Calendar tells the organizer.",
            id="decline-en",
        ),
    ],
)
async def test_a_preview_tells_the_owner_what_answering_would_do_and_does_nothing(
    client: AsyncClient, boundary: FakeBoundary, operation: str, language: str, summary: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    told, _ = preview_of(await preview(client, operation, UID, language))

    assert told == summary
    assert boundary.calendar.writes == []
    assert boundary.calendar.objects[HREF].jcal == invitation_a()


@pytest.mark.parametrize(
    ("operation", "language", "event", "first_line"),
    [
        pytest.param(
            "accept",
            "fr",
            weekly_series("NEEDS-ACTION", "DECLINED"),
            "Accepter toute la série « Point Twake Space E2E », mardi 13 octobre 2026 de 17 h à"
            " 18 h la première fois, invitation de « E2E » <e2e.organizer@twake.test>",
            id="accept-fr",
        ),
        pytest.param(
            "decline",
            "fr",
            weekly_series("NEEDS-ACTION", "ACCEPTED"),
            "Refuser toute la série « Point Twake Space E2E », mardi 13 octobre 2026 de 17 h à 18 h"
            " la première fois, invitation de « E2E » <e2e.organizer@twake.test>",
            id="decline-fr",
        ),
        pytest.param(
            "decline",
            "en",
            weekly_series("NEEDS-ACTION", "ACCEPTED"),
            "Decline the whole series “Point Twake Space E2E”, Tuesday 13 October 2026 from 17:00"
            " to 18:00 the first time, an invitation from “E2E” <e2e.organizer@twake.test>",
            id="decline-en",
        ),
        pytest.param(
            "decline",
            "en",
            with_props(invitation_a("NEEDS-ACTION", WEEKLY), summary=None),
            "Decline the whole untitled series, Tuesday 13 October 2026 from 17:00 to 18:00 the"
            " first time, an invitation from “E2E” <e2e.organizer@twake.test>",
            id="untitled",
        ),
    ],
)
async def test_a_preview_of_answering_the_whole_series_says_so(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    language: str,
    event: list[Any],
    first_line: str,
) -> None:
    # When the series starts, that the owner tells it from another
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, operation, UID, language, series=True))

    assert told.splitlines()[0] == first_line
    assert boundary.calendar.writes == []


def later_first(event: list[Any]) -> list[Any]:
    """The copy, its VEVENTs in the reverse order, as another calendar may write them, the later
    occurrence, now first, retitled."""
    return with_props(
        [event[0], event[1], event[2][::-1]],
        summary=["summary", {}, "text", "Point Twake Space E2E, au bureau"],
    )


def moved_before_it_starts() -> list[Any]:
    """Invitation A as a weekly series in the user's calendar, its second occurrence moved by the
    organizer to the day before the first, and retitled."""
    series = weekly_series("NEEDS-ACTION", "NEEDS-ACTION")
    moved = with_props(
        [series[0], series[1], [series[2][1]]],
        dtstart=paris("dtstart", "2026-10-12T17:00:00"),
        dtend=paris("dtend", "2026-10-12T18:00:00"),
        summary=["summary", {}, "text", "Point Twake Space E2E, avancé"],
    )
    series[2][1] = moved[2][0]
    return series


def first_moved_later() -> list[Any]:
    """Invitation A as a weekly series in the user's calendar, its first occurrence moved by the
    organizer from 17:00 to 18:00, and retitled: written apart, with the series' start as its
    RECURRENCE-ID."""
    series = invitation_a("NEEDS-ACTION", WEEKLY)
    moved = with_props(
        invitation_a("NEEDS-ACTION", ONE_OCCURRENCE),
        dtstart=paris("dtstart", "2026-10-13T18:00:00"),
        dtend=paris("dtend", "2026-10-13T19:00:00"),
        summary=["summary", {}, "text", "Point Twake Space E2E, décalé"],
    )
    series[2].append(moved[2][0])
    return series


def first_moved_after_the_second() -> list[Any]:
    """Invitation A as a weekly series in the user's calendar, its first occurrence moved by the
    organizer to 22 October, after the second, which the series' rule gives on 20 October, and
    retitled."""
    series = invitation_a("NEEDS-ACTION", WEEKLY)
    moved = with_props(
        invitation_a("NEEDS-ACTION", ONE_OCCURRENCE),
        dtstart=paris("dtstart", "2026-10-22T17:00:00"),
        dtend=paris("dtend", "2026-10-22T18:00:00"),
        summary=["summary", {}, "text", "Point Twake Space E2E, reporté"],
    )
    series[2].append(moved[2][0])
    return series


def excluded(series: list[Any], *exdates: list[str]) -> list[Any]:
    """The series, occurrences cancelled as esn-sabre cancels one: their starts in Paris, in an
    EXDATE for each list of them."""
    own = series[2][0]
    props = [["exdate", {"tzid": "Europe/Paris"}, "date-time", *starts] for starts in exdates]
    return [series[0], series[1], [[own[0], [*own[1], *props], own[2]], *series[2][1:]]]


def first_excluded(series: list[Any]) -> list[Any]:
    """The series, its first occurrence cancelled as esn-sabre cancels one: the series' start in
    its EXDATE."""
    return excluded(series, ["2026-10-13T17:00:00"])


def first_cancelled(series: list[Any]) -> list[Any]:
    """The series, its first occurrence written apart, retitled, and cancelled alone."""
    cancelled = with_props(
        invitation_a("NEEDS-ACTION", ONE_OCCURRENCE, CANCELLED),
        summary=["summary", {}, "text", "Point Twake Space E2E, annulé"],
    )
    return [series[0], series[1], [*series[2], cancelled[2][0]]]


def day_moved_to_a_time() -> list[Any]:
    """Invitation A as a weekly series of whole days in the user's calendar, from Tuesday 13
    October, the day the organizer retitled moved to a time: that Tuesday from 00:30 to 01:30 in
    Paris, which is Monday from 15:30 to 16:30 in Los Angeles."""
    series = weekly_days("NEEDS-ACTION", "NEEDS-ACTION")
    moved = with_props(
        [series[0], series[1], [series[2][1]]],
        dtstart=paris("dtstart", "2026-10-13T00:30:00"),
        dtend=paris("dtend", "2026-10-13T01:30:00"),
    )
    series[2][1] = moved[2][0]
    return series


def floating_moved_to_a_time() -> list[Any]:
    """Invitation A as a weekly series in the user's calendar, its times floating, the second
    occurrence moved by the organizer to a time in Paris: Tuesday 13 October from 12:00 to 13:00,
    from 3:00 to 4:00 in Los Angeles, and from 0:00 to 1:00 on Wednesday in Kiritimati."""
    series = floating_series("NEEDS-ACTION", "NEEDS-ACTION")
    moved = with_props(
        [series[0], series[1], [series[2][1]]],
        dtstart=paris("dtstart", "2026-10-13T12:00:00"),
        dtend=paris("dtend", "2026-10-13T13:00:00"),
    )
    series[2][1] = moved[2][0]
    return series


@pytest.mark.parametrize(
    ("event", "title", "when"),
    [
        pytest.param(
            first_moved_later(),
            "Point Twake Space E2E, décalé",
            "mardi 13 octobre 2026 de 18 h à 19 h",
            id="the first occurrence moved later",
        ),
        # Neither the series' RRULE nor its RDATE is read: the earliest DTSTART the copy writes
        pytest.param(
            first_moved_after_the_second(),
            "Point Twake Space E2E, reporté",
            "jeudi 22 octobre 2026 de 17 h à 18 h",
            id="the first occurrence moved after the second",
        ),
        pytest.param(
            invitation_a(
                "NEEDS-ACTION",
                WEEKLY,
                ["rdate", {"tzid": "Europe/Paris"}, "date-time", "2026-10-10T17:00:00"],
            ),
            "Point Twake Space E2E",
            "mardi 13 octobre 2026 de 17 h à 18 h",
            id="a date its RDATE adds before its start",
        ),
        # The second occurrence, which the organizer moved to 18:00, is the first that takes place
        pytest.param(
            first_excluded(weekly_series("NEEDS-ACTION", "NEEDS-ACTION")),
            "Point Twake Space E2E",
            "mardi 20 octobre 2026 de 18 h à 19 h",
            id="the first occurrence excluded",
        ),
        pytest.param(
            excluded(
                weekly_series("NEEDS-ACTION", "NEEDS-ACTION"),
                ["2026-10-27T17:00:00", "2026-10-13T17:00:00"],
            ),
            "Point Twake Space E2E",
            "mardi 20 octobre 2026 de 18 h à 19 h",
            id="the first occurrence excluded after another",
        ),
        pytest.param(
            excluded(
                weekly_series("NEEDS-ACTION", "NEEDS-ACTION"),
                ["2026-10-27T17:00:00"],
                ["2026-10-13T17:00:00"],
            ),
            "Point Twake Space E2E",
            "mardi 20 octobre 2026 de 18 h à 19 h",
            id="the first occurrence excluded in another EXDATE",
        ),
        pytest.param(
            first_cancelled(weekly_series("NEEDS-ACTION", "NEEDS-ACTION")),
            "Point Twake Space E2E",
            "mardi 20 octobre 2026 de 18 h à 19 h",
            id="the first occurrence cancelled alone",
        ),
        pytest.param(
            later_first(occurrences_alone()),
            "Point Twake Space E2E",
            "mardi 13 octobre 2026 de 17 h à 18 h",
            id="occurrences without their series, the later first",
        ),
        pytest.param(
            later_first(weekly_series("NEEDS-ACTION", "NEEDS-ACTION")),
            "Point Twake Space E2E",
            "mardi 13 octobre 2026 de 17 h à 18 h",
            id="a series after one of its occurrences",
        ),
        pytest.param(
            moved_before_it_starts(),
            "Point Twake Space E2E, avancé",
            "lundi 12 octobre 2026 de 17 h à 18 h",
            id="an occurrence moved before the series starts",
        ),
    ],
)
@pytest.mark.parametrize(
    ("operation", "answer_words"),
    [
        pytest.param("accept", "Accepter", id="accept"),
        pytest.param("decline", "Refuser", id="decline"),
    ],
)
async def test_a_preview_of_answering_the_whole_series_tells_of_its_earliest_occurrence(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    answer_words: str,
    event: list[Any],
    title: str,
    when: str,
) -> None:
    # Its title and when it takes place the first time, wherever the copy holds it
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, operation, UID, series=True))

    assert told.splitlines()[0] == (
        f"{answer_words} toute la série « {title} », {when} la première fois, invitation de"
        " « E2E » <e2e.organizer@twake.test>"
    )


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(
            first_excluded(invitation_a("NEEDS-ACTION", WEEKLY)), id="its first occurrence excluded"
        ),
        pytest.param(
            first_cancelled(invitation_a("NEEDS-ACTION", WEEKLY)),
            id="its first occurrence cancelled alone",
        ),
        # Told by the series' title, not by that of the occurrence the copy writes first
        pytest.param(
            later_first(first_cancelled(invitation_a("NEEDS-ACTION", WEEKLY))),
            id="its first occurrence cancelled alone, written before it",
        ),
        pytest.param(
            first_excluded(
                invitation_a(
                    "NEEDS-ACTION",
                    ["rdate", {"tzid": "Europe/Paris"}, "date-time", "2026-10-20T17:00:00"],
                )
            ),
            id="the dates its RDATE adds alone taking place",
        ),
    ],
)
@pytest.mark.parametrize(
    ("operation", "answer_words"),
    [
        pytest.param("accept", "Accepter", id="accept"),
        pytest.param("decline", "Refuser", id="decline"),
    ],
)
async def test_a_preview_tells_a_series_writing_no_start_that_takes_place_by_its_title_alone(
    client: AsyncClient, boundary: FakeBoundary, operation: str, answer_words: str, event: list[Any]
) -> None:
    # Neither the series' RRULE nor its RDATE is read: the copy writes no other DTSTART than its
    # first
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, operation, UID, series=True))

    assert told.splitlines()[0].startswith(
        f"{answer_words} toute la série « Point Twake Space E2E », invitation de « E2E » <"
    )


@pytest.mark.parametrize(
    ("event", "zone", "title", "when"),
    [
        pytest.param(
            day_moved_to_a_time(),
            "Europe/Paris",
            "Point Twake Space E2E",
            "mardi 13 octobre 2026, toute la journée",
            id="a day, in Paris",
        ),
        pytest.param(
            day_moved_to_a_time(),
            "America/Los_Angeles",
            "Point Twake Space E2E, au bureau",
            "lundi 12 octobre 2026 de 15 h 30 à 16 h 30",
            id="a day, in Los Angeles",
        ),
        pytest.param(
            day_moved_to_a_time(),
            "Pacific/Kiritimati",
            "Point Twake Space E2E",
            "mardi 13 octobre 2026, toute la journée",
            id="a day, in Kiritimati",
        ),
        pytest.param(
            floating_moved_to_a_time(),
            "America/Los_Angeles",
            "Point Twake Space E2E",
            "mardi 13 octobre 2026 de 3 h à 4 h",
            id="a floating time, in Los Angeles",
        ),
        pytest.param(
            floating_moved_to_a_time(),
            "Pacific/Kiritimati",
            "Point Twake Space E2E",
            "mardi 13 octobre 2026 de 17 h à 18 h",
            id="a floating time, in Kiritimati",
        ),
    ],
)
@pytest.mark.parametrize(
    ("operation", "answer_words"),
    [
        pytest.param("accept", "Accepter", id="accept"),
        pytest.param("decline", "Refuser", id="decline"),
    ],
)
async def test_a_preview_starts_a_day_or_a_floating_time_in_the_users_zone(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    answer_words: str,
    event: list[Any],
    zone: str,
    title: str,
    when: str,
) -> None:
    # A day starts at its midnight in the user's zone, and a floating time is read in it: before
    # the time in Paris in some zones, after it in others
    in_zone(boundary, zone)
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, operation, UID, series=True))

    assert told.splitlines()[0].startswith(
        f"{answer_words} toute la série « {title} », {when} la première fois, invitation de"
        " « E2E » <"
    )


@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_the_owner_who_allowed_answering_the_whole_series_answers_it(
    client: AsyncClient, boundary: FakeBoundary, operation: str, partstat: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, weekly_series("NEEDS-ACTION", "TENTATIVE")
    )
    _, digest = preview_of(await preview(client, operation, UID, series=True))

    response = await answer(client, operation, UID, allowed_after(digest), series=True)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == weekly_series(partstat, partstat)


@pytest.mark.parametrize(("operation", "partstat", "answered_before"), ANSWERED_BEFORE)
async def test_an_occurrence_that_ends_after_the_preview_leaves_what_the_owner_allowed(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    operation: str,
    partstat: str,
    answered_before: str,
) -> None:
    # The moved occurrence ends at 17:00 in UTC, between the preview and the call: time passed,
    # and the organizer changed nothing
    clock.wall = datetime(2026, 10, 20, 16, 30, tzinfo=UTC)
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, weekly_series("NEEDS-ACTION", answered_before)
    )
    _, digest = preview_of(await preview(client, operation, UID, series=True))
    clock.wall = datetime(2026, 10, 20, 17, 30, tzinfo=UTC)

    response = await answer(client, operation, UID, allowed_after(digest), series=True)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == weekly_series(partstat, answered_before)


@pytest.mark.parametrize(
    ("previewed_in", "called_in"),
    [
        pytest.param(None, "America/Los_Angeles", id="given after failing for the preview"),
        pytest.param("America/Los_Angeles", None, id="failing after given for the preview"),
        pytest.param("Europe/Paris", "America/Los_Angeles", id="another, set since the preview"),
    ],
)
@pytest.mark.parametrize(
    ("operation", "answered_before"),
    [("accept", "DECLINED"), ("decline", "ACCEPTED")],
    ids=["accept", "decline"],
)
async def test_a_series_whose_user_zone_changed_since_the_preview_is_not_answered(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    operation: str,
    answered_before: str,
    previewed_in: str | None,
    called_in: str | None,
) -> None:
    # 18:00 in Los Angeles, 1:00 in UTC, 3:00 in Paris: the day the organizer retitled, which the
    # user answered already, is over in UTC and in Paris, not in Los Angeles. Read in another zone
    # than for the preview, the call would answer it otherwise than the owner allowed.
    clock.wall = datetime(2026, 10, 21, 1, tzinfo=UTC)
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, weekly_days("NEEDS-ACTION", answered_before)
    )
    in_zone(boundary, previewed_in)
    _, digest = preview_of(await preview(client, operation, UID, series=True))
    boundary.calendar.settings_down = False
    in_zone(boundary, called_in)

    response = await answer(client, operation, UID, allowed_after(digest), series=True)

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(
    ("previewed_in", "called_in"),
    [
        pytest.param(None, "America/Los_Angeles", id="given after failing for the preview"),
        pytest.param("America/Los_Angeles", None, id="failing after given for the preview"),
        pytest.param("Europe/Paris", "America/Los_Angeles", id="another, set since the preview"),
    ],
)
@pytest.mark.parametrize("series", [False, True], ids=["once", "for the whole series"])
@pytest.mark.parametrize(
    "event",
    [
        pytest.param(invitation_a, id="an invitation that does not repeat"),
        pytest.param(occurrence_alone, id="an occurrence alone"),
    ],
)
@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_an_invitation_that_does_not_repeat_is_answered_whatever_zone_since_the_preview(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    partstat: str,
    event: Callable[[str], list[Any]],
    series: bool,
    previewed_in: str | None,
    called_in: str | None,
) -> None:
    # Only the occurrences of a whole series end in the user's zone: the owner allowed an answer
    # to one event, which the zone does not change
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event("NEEDS-ACTION"))
    in_zone(boundary, previewed_in)
    _, digest = preview_of(await preview(client, operation, UID, series=series))
    boundary.calendar.settings_down = False
    in_zone(boundary, called_in)

    response = await answer(client, operation, UID, allowed_after(digest), series=series)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == event(partstat)


@pytest.mark.parametrize(
    ("previewed", "called"),
    [("accept", "decline"), ("decline", "accept")],
    ids=["accept", "decline"],
)
async def test_a_preview_of_one_answer_does_not_allow_the_other(
    client: AsyncClient, boundary: FakeBoundary, previewed: str, called: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())
    _, digest = preview_of(await preview(client, previewed, UID))

    response = await answer(client, called, UID, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_the_whole_series_of_an_invitation_that_does_not_repeat_is_the_invitation(
    client: AsyncClient, boundary: FakeBoundary, operation: str, partstat: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    once, _ = preview_of(await preview(client, operation, UID))
    told, digest = preview_of(await preview(client, operation, UID, series=True))
    response = await answer(client, operation, UID, allowed_after(digest), series=True)

    assert told == once
    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == invitation_a(partstat)


@pytest.mark.parametrize(("operation", "partstat"), ANSWERS)
async def test_the_owner_who_allowed_what_they_were_shown_answers_the_invitation(
    client: AsyncClient, boundary: FakeBoundary, operation: str, partstat: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())
    _, digest = preview_of(await preview(client, operation, UID))

    response = await answer(client, operation, UID, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == invitation_a(partstat)


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
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_an_invitation_changed_since_the_preview_is_not_answered(
    client: AsyncClient, boundary: FakeBoundary, operation: str, changed: list[Any]
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())
    _, digest = preview_of(await preview(client, operation, UID))
    # The organizer changes the event before the owner says so
    boundary.calendar.objects[HREF].jcal = changed

    response = await answer(client, operation, UID, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.calendar.writes == []
    assert boundary.calendar.objects[HREF].jcal == changed


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_series_changed_since_the_preview_is_not_answered(
    client: AsyncClient, boundary: FakeBoundary, operation: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, weekly_series("NEEDS-ACTION", "NEEDS-ACTION")
    )
    _, digest = preview_of(await preview(client, operation, UID, series=True))
    # The organizer moves one occurrence again before the owner says so for the series
    moved = weekly_series("NEEDS-ACTION", "NEEDS-ACTION", moved_hour=19)
    boundary.calendar.objects[HREF].jcal = moved

    response = await answer(client, operation, UID, allowed_after(digest), series=True)

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (invitation_a("NEEDS-ACTION", WEEKLY), "recurring_invitation"),
        (invitation_a("NEEDS-ACTION", CANCELLED), "invitation_cancelled"),
        (CANCELLED_SERIES, "invitation_cancelled"),
    ],
    ids=["recurring", "cancelled", "a cancelled series"],
)
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_preview_refuses_what_answering_would_refuse(
    client: AsyncClient, boundary: FakeBoundary, operation: str, event: list[Any], code: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    response = await preview(client, operation, UID)

    assert response.status_code == 409
    assert response.json()["code"] == code
    assert "x-twake-preview" not in response.headers
    assert boundary.calendar.writes == []


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_a_preview_asked_otherwise_than_with_true_does_nothing(
    client: AsyncClient, boundary: FakeBoundary, operation: str
) -> None:
    # A value the contract does not know could be meant as a preview: it never acts on it
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    response = await answer(client, operation, UID, {"x-twake-preview": "yes"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.calendar.writes == []
