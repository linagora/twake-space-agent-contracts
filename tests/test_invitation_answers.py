from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import (
    ALICE_CALENDAR_ID,
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


def floating_series(mmaudet_partstat: str, moved_partstat: str) -> list[Any]:
    """Invitation A as a weekly series in the user's calendar, its times written in no zone, which
    iCalendar calls floating: its occurrences, then the second, which the organizer moved to 18:00;
    the user's participation in each."""
    series = weekly_series(mmaudet_partstat, moved_partstat)
    for vevent in series[2]:
        for prop in vevent[1]:
            if prop[0] in ("dtstart", "dtend", "recurrence-id"):
                prop[1].pop("tzid")
    return series


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
    ("series", "over_since"),
    [
        # The moved occurrence ends at 19:00 in Paris, 17:00 in UTC
        pytest.param(weekly_series, datetime(2026, 10, 20, 17, 0, 1, tzinfo=UTC), id="at a time"),
        # The day the organizer retitled ends where the next starts, in UTC
        pytest.param(weekly_days, datetime(2026, 10, 21, tzinfo=UTC), id="on a day"),
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
    over_since: datetime,
) -> None:
    # As Twake Calendar answers a series: the answer the user gave an occurrence over stays, and
    # its organizer is not told of it again
    clock.wall = over_since
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, series("NEEDS-ACTION", answered_before)
    )

    response = await answer(client, operation, UID, series=True)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == series(partstat, answered_before)


@pytest.mark.parametrize(
    ("series", "under_way"),
    [
        pytest.param(
            weekly_series, datetime(2026, 10, 20, 17, tzinfo=UTC), id="at a time, as it ends"
        ),
        pytest.param(weekly_days, datetime(2026, 10, 20, 23, 59, tzinfo=UTC), id="on a day"),
        pytest.param(
            floating_series, datetime(2026, 10, 21, tzinfo=UTC), id="at a time of no zone"
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
    under_way: datetime,
) -> None:
    # Under way, ending that very time, or at a floating time, which no zone places: not over
    clock.wall = under_way
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, series("NEEDS-ACTION", answered_before)
    )

    response = await answer(client, operation, UID, series=True)

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == series(partstat, partstat)


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


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(series_one_cancelled, id="in a series"),
        pytest.param(occurrences_one_cancelled, id="among occurrences without their series"),
    ],
)
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


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(series_one_cancelled, id="in a series"),
        pytest.param(occurrences_one_cancelled, id="among occurrences without their series"),
    ],
)
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
