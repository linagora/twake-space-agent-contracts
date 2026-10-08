from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import ALICE_CALENDAR_ID, MMAUDET_CALENDAR_ID, CalendarObject, FakeBoundary
from tests.test_invitation_accept import (
    CANCELLED,
    HREF,
    ONE_OCCURRENCE,
    UID,
    WEEKLY,
    delivered_to,
    invitation_a,
    own_meeting,
    paris,
    weekly_series,
    with_props,
)

DECLINE = "/contracts/v1/calendar/invitations/decline"


async def decline(
    client: AsyncClient, uid: str, headers: dict[str, str] | None = None, **body: Any
) -> Response:
    return await client.post(
        DECLINE, json={"uid": uid, **body}, headers=AS_MMAUDET | (headers or {})
    )


async def preview(client: AsyncClient, uid: str, language: str = "fr", **body: Any) -> Response:
    """The harness asks what declining would do, before it asks the owner."""
    return await decline(client, uid, asking_preview(language), **body)


async def test_declining_by_the_events_uid_sets_only_the_users_participation(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    response = await decline(client, UID)

    assert response.status_code == 200, response.text
    assert response.json() == {"uid": UID, "partstat": "DECLINED"}
    assert boundary.calendar.objects[HREF].jcal == invitation_a("DECLINED")


@pytest.mark.parametrize(
    ("owner", "event"),
    [
        pytest.param(ALICE_CALENDAR_ID, invitation_a(None), id="in another user's calendar only"),
        pytest.param(MMAUDET_CALENDAR_ID, invitation_a(None), id="not listing them"),
        pytest.param(
            MMAUDET_CALENDAR_ID, invitation_a(None, WEEKLY), id="a series not listing them"
        ),
        pytest.param(MMAUDET_CALENDAR_ID, own_meeting(), id="their own meeting"),
        pytest.param(
            MMAUDET_CALENDAR_ID, with_props(own_meeting(), rrule=WEEKLY), id="their own series"
        ),
    ],
)
@pytest.mark.parametrize("asked", [{}, asking_preview("fr")], ids=["declining", "a preview"])
@pytest.mark.parametrize("series", [False, True], ids=["once", "for the whole series"])
async def test_a_user_not_invited_is_answered_as_for_an_unknown_invitation(
    client: AsyncClient,
    boundary: FakeBoundary,
    owner: str,
    event: list[Any],
    asked: dict[str, str],
    series: bool,
) -> None:
    # The contract never tells that an event exists to a user it does not invite, nor lets the
    # organizer's assistant decline the meeting, or the series, the organizer called
    unknown = await decline(client, UID, asked, series=series)
    boundary.calendar.objects[delivered_to(owner)] = CalendarObject(owner, event)

    response = await decline(client, UID, asked, series=series)

    assert (unknown.status_code, unknown.json()["code"]) == (404, "invitation_not_found")
    assert (response.status_code, response.json()) == (unknown.status_code, unknown.json())
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(
    "recurrence", [WEEKLY, ONE_OCCURRENCE], ids=["a weekly series", "one occurrence of a series"]
)
async def test_a_recurring_invitation_is_refused_unless_for_the_whole_series(
    client: AsyncClient, boundary: FakeBoundary, recurrence: list[Any]
) -> None:
    # The UID names the whole series, not which of its occurrences the user would decline
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, invitation_a("NEEDS-ACTION", recurrence)
    )

    response = await decline(client, UID)

    assert response.status_code == 409
    assert response.json()["code"] == "recurring_invitation"
    assert boundary.calendar.writes == []


async def test_declining_the_whole_series_declines_each_of_its_occurrences(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The user's answer to one occurrence included, once they said no to the whole series
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, weekly_series("NEEDS-ACTION", "ACCEPTED")
    )

    response = await decline(client, UID, series=True)

    assert response.status_code == 200, response.text
    assert response.json() == {"uid": UID, "partstat": "DECLINED"}
    assert boundary.calendar.objects[HREF].jcal == weekly_series("DECLINED", "DECLINED")


@pytest.mark.parametrize(
    ("event", "series"),
    [
        pytest.param(invitation_a("NEEDS-ACTION", CANCELLED), False, id="once"),
        pytest.param(
            invitation_a("NEEDS-ACTION", WEEKLY, CANCELLED), True, id="for the whole series"
        ),
        pytest.param(
            weekly_series("NEEDS-ACTION", "NEEDS-ACTION", CANCELLED),
            True,
            id="a series one occurrence of which is cancelled",
        ),
    ],
)
async def test_a_cancelled_invitation_is_not_declined(
    client: AsyncClient, boundary: FakeBoundary, event: list[Any], series: bool
) -> None:
    # Cancelling leaves the event in the user's calendar, and Calendar would not tell anyone
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    response = await decline(client, UID, series=series)

    assert response.status_code == 409
    assert response.json()["code"] == "invitation_cancelled"
    assert boundary.calendar.writes == []


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Refuser « Point Twake Space E2E », mardi 13 octobre 2026 de 17 h à 18 h, invitation"
            " de « E2E » <e2e.organizer@twake.test>\nTwake Agenda prévient l'organisateur.",
        ),
        (
            "en",
            "Decline “Point Twake Space E2E”, Tuesday 13 October 2026 from 17:00 to 18:00, an"
            " invitation from “E2E” <e2e.organizer@twake.test>\n"
            "Twake Calendar tells the organizer.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_what_declining_would_do_and_does_nothing(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    told, _ = preview_of(await preview(client, UID, language))

    assert told == summary
    assert boundary.calendar.writes == []
    assert boundary.calendar.objects[HREF].jcal == invitation_a()


@pytest.mark.parametrize(
    ("language", "event", "first_line"),
    [
        pytest.param(
            "fr",
            weekly_series("NEEDS-ACTION", "ACCEPTED"),
            "Refuser toute la série « Point Twake Space E2E », mardi 13 octobre 2026 de 17 h à 18 h"
            " la première fois, invitation de « E2E » <e2e.organizer@twake.test>",
            id="fr",
        ),
        pytest.param(
            "en",
            weekly_series("NEEDS-ACTION", "ACCEPTED"),
            "Decline the whole series “Point Twake Space E2E”, Tuesday 13 October 2026 from 17:00"
            " to 18:00 the first time, an invitation from “E2E” <e2e.organizer@twake.test>",
            id="en",
        ),
        pytest.param(
            "en",
            with_props(invitation_a("NEEDS-ACTION", WEEKLY), summary=None),
            "Decline the whole untitled series, Tuesday 13 October 2026 from 17:00 to 18:00 the"
            " first time, an invitation from “E2E” <e2e.organizer@twake.test>",
            id="untitled",
        ),
    ],
)
async def test_a_preview_of_declining_the_whole_series_says_so(
    client: AsyncClient,
    boundary: FakeBoundary,
    language: str,
    event: list[Any],
    first_line: str,
) -> None:
    # When the series starts, that the owner tells it from another
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, event)

    told, _ = preview_of(await preview(client, UID, language, series=True))

    assert told.splitlines()[0] == first_line
    assert boundary.calendar.writes == []


async def test_the_whole_series_of_an_invitation_that_does_not_repeat_is_the_invitation(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())

    told, digest = preview_of(await preview(client, UID, "en", series=True))
    response = await decline(client, UID, allowed_after(digest), series=True)

    assert told.splitlines()[0] == (
        "Decline “Point Twake Space E2E”, Tuesday 13 October 2026 from 17:00 to 18:00, an"
        " invitation from “E2E” <e2e.organizer@twake.test>"
    )
    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == invitation_a("DECLINED")


async def test_the_owner_who_allowed_what_they_were_shown_declines_the_invitation(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())
    _, digest = preview_of(await preview(client, UID))

    response = await decline(client, UID, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert boundary.calendar.objects[HREF].jcal == invitation_a("DECLINED")


async def test_an_invitation_changed_since_the_preview_is_not_declined(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(MMAUDET_CALENDAR_ID, invitation_a())
    _, digest = preview_of(await preview(client, UID))
    # The organizer moves the event before the owner says no
    moved = with_props(
        invitation_a(),
        dtstart=paris("dtstart", "2026-10-13T18:00:00"),
        dtend=paris("dtend", "2026-10-13T19:00:00"),
    )
    boundary.calendar.objects[HREF].jcal = moved

    response = await decline(client, UID, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.calendar.writes == []
    assert boundary.calendar.objects[HREF].jcal == moved


async def test_a_series_changed_since_the_preview_is_not_declined(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.objects[HREF] = CalendarObject(
        MMAUDET_CALENDAR_ID, weekly_series("NEEDS-ACTION", "NEEDS-ACTION")
    )
    _, digest = preview_of(await preview(client, UID, series=True))
    # The organizer moves one occurrence again before the owner says no to the series
    moved = weekly_series("NEEDS-ACTION", "NEEDS-ACTION", moved_hour=19)
    boundary.calendar.objects[HREF].jcal = moved

    response = await decline(client, UID, allowed_after(digest), series=True)

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.calendar.writes == []
