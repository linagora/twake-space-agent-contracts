from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import MMAUDET_CALENDAR_ID, FakeBoundary, email_of

DEFAULT_CALENDAR = f"/calendars/{MMAUDET_CALENDAR_ID}/{MMAUDET_CALENDAR_ID}"
ALICE, BOB = email_of("alice"), email_of("bob")
REVIEW = {
    "title": "Design review",
    "start": "2026-10-13T15:00:00+02:00",
    "end": "2026-10-13T16:00:00+02:00",
    "attendees": [ALICE, BOB],
    "time_zone": "Europe/Paris",
}


async def create(client: AsyncClient, *headers: dict[str, str], **body: Any) -> Response:
    """The call as the harness sends it, with what it adds to ask for a preview, if anything."""
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.post("/contracts/v1/calendar/meetings", json=body, headers=sent)


def stored_properties(boundary: FakeBoundary, uid: str) -> list[list[Any]]:
    """The properties of the meeting of that UID in the user's default calendar."""
    jcal = boundary.calendar.objects[f"{DEFAULT_CALENDAR}/{uid}.ics"].jcal
    (vevent,) = [component for component in jcal[2] if component[0] == "vevent"]
    properties: list[list[Any]] = vevent[1]
    return properties


async def test_a_meeting_is_added_with_the_user_as_organizer_and_the_people_invited(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(client, **REVIEW, location="Room 4", description="Agenda: the demo")

    assert response.status_code == 201, response.text
    created = response.json()
    uid = created["uid"]
    assert created == {
        "uid": uid,
        "start": "2026-10-13T15:00:00+02:00",
        "end": "2026-10-13T16:00:00+02:00",
        "time_zone": "Europe/Paris",
        "attendees": [ALICE, BOB],
        "untrusted": {
            "title": "Design review",
            "location": "Room 4",
            "description": "Agenda: the demo",
        },
    }
    assert list(boundary.calendar.objects) == [f"{DEFAULT_CALENDAR}/{uid}.ics"]
    properties = stored_properties(boundary, uid)
    me = email_of("mmaudet")
    assert ["organizer", {}, "cal-address", f"mailto:{me}"] in properties
    attendees = {prop[3]: prop[1] for prop in properties if prop[0] == "attendee"}
    assert attendees[f"mailto:{me}"]["partstat"] == "ACCEPTED"
    assert attendees[f"mailto:{me}"]["role"] == "CHAIR"
    for address in (ALICE, BOB):
        assert attendees[f"mailto:{address}"]["partstat"] == "NEEDS-ACTION"
        assert attendees[f"mailto:{address}"]["rsvp"] == "TRUE"


async def test_a_meeting_is_written_in_the_users_zone_by_default(client: AsyncClient) -> None:
    body = {name: value for name, value in REVIEW.items() if name != "time_zone"}

    response = await create(client, **body)

    assert response.status_code == 201, response.text
    assert response.json()["time_zone"] == "Europe/Paris"


async def test_addresses_are_lowercased_and_counted_once(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(client, **REVIEW | {"attendees": [ALICE.upper(), f" {ALICE} "]})

    assert response.status_code == 201, response.text
    assert response.json()["attendees"] == [ALICE]


async def test_the_same_call_made_again_adds_nothing_and_mails_nobody_again(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    first = await create(client, **REVIEW)

    again = await create(client, **REVIEW | {"attendees": [BOB, ALICE]})

    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert len(boundary.calendar.writes) == 1


async def test_the_same_meeting_with_other_details_is_refused(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    await create(client, **REVIEW)

    response = await create(client, **REVIEW, location="Room 4")

    assert response.status_code == 409
    assert response.json()["code"] == "event_exists"
    assert len(boundary.calendar.writes) == 1


async def test_other_people_make_another_meeting(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    await create(client, **REVIEW)

    response = await create(client, **REVIEW | {"attendees": [ALICE]})

    assert response.status_code == 201, response.text
    assert len(boundary.calendar.objects) == 2


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        pytest.param({"attendees": []}, "invalid_request", id="nobody"),
        pytest.param(
            {"attendees": [f"p{n}@twake.test" for n in range(21)]}, "invalid_request", id="21"
        ),
        pytest.param({"attendees": ["not an address"]}, "invalid_email", id="not an email"),
        pytest.param({"attendees": [email_of("mmaudet")]}, "invalid_request", id="the user"),
        pytest.param({"title": "   "}, "invalid_request", id="blank title"),
        pytest.param({"end": REVIEW["start"]}, "invalid_request", id="ends as it starts"),
        pytest.param({"end": "2026-12-13T16:00:00+02:00"}, "invalid_request", id="over 31 days"),
        pytest.param({"start": "2026-10-13T15:00:00"}, "invalid_request", id="without offset"),
        pytest.param({"start": "2026-10-13", "end": "2026-10-14"}, "invalid_request", id="days"),
        pytest.param({"time_zone": "Mars/Base"}, "invalid_request", id="unknown zone"),
        pytest.param({"organizer": ALICE}, "invalid_request", id="another field"),
    ],
)
async def test_a_meeting_that_cannot_be_called_is_refused_and_writes_nothing(
    client: AsyncClient, boundary: FakeBoundary, changes: dict[str, Any], code: str
) -> None:
    response = await create(client, **REVIEW | changes)

    assert response.status_code == 400
    assert response.json()["code"] == code
    assert boundary.calendar.objects == {}


async def test_an_unavailable_calendar_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.down = True

    response = await create(client, **REVIEW)

    assert response.status_code == 502
    assert response.json()["code"] == "calendar_unavailable"


async def test_a_meeting_needs_the_user_token(client: AsyncClient) -> None:
    response = await client.post("/contracts/v1/calendar/meetings", json=REVIEW)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Convoquer"
            f" <{ALICE}>, <{BOB}> à « Design review », mardi 13 octobre 2026 de 15 h à 16 h\n"
            "Twake Agenda envoie une invitation par mail à chacun, y compris aux personnes"
            " extérieures à ton organisation.",
        ),
        (
            "en",
            f"Invite <{ALICE}>, <{BOB}> to “Design review”, Tuesday 13 October 2026 from 15:00 to"
            " 16:00\nTwake Calendar emails an invitation to each of them, people outside your"
            " organization included.",
        ),
    ],
)
async def test_a_preview_tells_whom_the_meeting_invites_and_writes_nothing(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    response = await create(client, asking_preview(language), **REVIEW)

    told, _ = preview_of(response)
    assert told == summary
    assert boundary.calendar.writes == []
    assert boundary.calendar.objects == {}


async def test_a_preview_of_a_meeting_there_already_tells_nothing_is_sent(
    client: AsyncClient,
) -> None:
    await create(client, **REVIEW)

    response = await create(client, asking_preview("en"), **REVIEW)

    told, _ = preview_of(response)
    assert "is in your calendar already" in told
    assert told.endswith(": nothing is sent.")


async def test_a_preview_refuses_what_calling_the_meeting_would_refuse(
    client: AsyncClient,
) -> None:
    response = await create(client, asking_preview("fr"), **REVIEW | {"attendees": ["nope"]})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_email"


async def test_the_owner_who_allowed_what_they_were_shown_gets_the_meeting(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(await create(client, asking_preview("fr"), **REVIEW))

    response = await create(client, allowed_after(digest), **REVIEW)

    assert response.status_code == 201, response.text
    assert len(boundary.calendar.objects) == 1


async def test_a_meeting_added_since_the_preview_is_not_called_again(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(await create(client, asking_preview("fr"), **REVIEW))
    await create(client, **REVIEW)

    response = await create(client, allowed_after(digest), **REVIEW)

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert len(boundary.calendar.writes) == 1
