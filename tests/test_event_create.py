from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import MMAUDET_CALENDAR_ID, FakeBoundary, email_of

# The user's default calendar, whose id esn-sabre makes the user's own
DEFAULT_CALENDAR = f"/calendars/{MMAUDET_CALENDAR_ID}/{MMAUDET_CALENDAR_ID}"
LUNCH = {
    "title": "Lunch with the team",
    "start": "2026-10-13T12:00:00+02:00",
    "end": "2026-10-13T14:00:00+02:00",
    "time_zone": "Europe/Paris",
}
# The same lunch, written in the user's own time zone
LUNCH_IN_THEIR_ZONE = {name: value for name, value in LUNCH.items() if name != "time_zone"}


async def create(client: AsyncClient, *headers: dict[str, str], **body: Any) -> Response:
    """The call as the harness sends it, with what it adds to ask for a preview, if anything."""
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.post("/contracts/v1/calendar/events", json=body, headers=sent)


def stored_components(boundary: FakeBoundary, uid: str, name: str) -> list[list[Any]]:
    """The components of that name, such as vevent, of the event of that UID in the user's
    default calendar."""
    jcal = boundary.calendar.objects[f"{DEFAULT_CALENDAR}/{uid}.ics"].jcal
    return [component for component in jcal[2] if component[0] == name]


def stored_event(boundary: FakeBoundary, uid: str) -> dict[str, list[Any]]:
    """The properties of the event of that UID in the user's default calendar, by name."""
    (vevent,) = stored_components(boundary, uid, "vevent")
    return {prop[0]: prop for prop in vevent[1]}


def observance(kind: str, onset: str, offset_from: str, offset_to: str, name: str) -> list[Any]:
    """A rule of a time zone, as jCal writes it: from its onset, in the time it replaces, the
    zone's offset changes to another."""
    return [
        kind,
        [
            ["dtstart", {}, "date-time", onset],
            ["tzoffsetfrom", {}, "utc-offset", offset_from],
            ["tzoffsetto", {}, "utc-offset", offset_to],
            ["tzname", {}, "text", name],
        ],
        [],
    ]


# Paris, in 2026: summer time from March 29 at 2:00, winter time again from October 25 at 3:00
PARIS_SUMMER_2026 = observance("daylight", "2026-03-29T02:00:00", "+01:00", "+02:00", "CEST")
PARIS_WINTER_2026 = observance("standard", "2026-10-25T03:00:00", "+02:00", "+01:00", "CET")


async def test_an_event_is_added_busy_to_the_users_default_calendar(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(client, **LUNCH)

    assert response.status_code == 201, response.text
    created = response.json()
    uid = created["uid"]
    assert created == {
        "uid": uid,
        "start": "2026-10-13T12:00:00+02:00",
        "end": "2026-10-13T14:00:00+02:00",
        "time_zone": "Europe/Paris",
        "all_day": False,
        "busy": True,
        "untrusted": {"title": "Lunch with the team", "location": None, "description": None},
    }
    assert list(boundary.calendar.objects) == [f"{DEFAULT_CALENDAR}/{uid}.ics"]
    event = stored_event(boundary, uid)
    assert event["uid"] == ["uid", {}, "text", uid]
    assert event["summary"] == ["summary", {}, "text", "Lunch with the team"]
    assert event["dtstart"] == [
        "dtstart",
        {"tzid": "Europe/Paris"},
        "date-time",
        "2026-10-13T12:00:00",
    ]
    assert event["dtend"] == ["dtend", {"tzid": "Europe/Paris"}, "date-time", "2026-10-13T14:00:00"]
    assert event["transp"] == ["transp", {}, "text", "OPAQUE"]
    # Nobody else is in it, so nobody is told of it
    assert not event.keys() & {"organizer", "attendee"}


async def test_the_time_zone_the_event_is_written_in_comes_with_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # iCalendar describes each time zone an event names: other calendar apps read its times by it
    response = await create(client, **LUNCH)

    assert response.status_code == 201, response.text
    assert stored_components(boundary, response.json()["uid"], "vtimezone") == [
        ["vtimezone", [["tzid", {}, "text", "Europe/Paris"]], [PARIS_SUMMER_2026]]
    ]


async def test_an_event_across_a_change_of_time_describes_both_times(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A night across the end of summer time in Paris
    night = LUNCH | {"start": "2026-10-24T22:00:00+02:00", "end": "2026-10-25T06:00:00+01:00"}

    response = await create(client, **night)

    assert response.status_code == 201, response.text
    assert response.json()["end"] == "2026-10-25T06:00:00+01:00"
    assert stored_components(boundary, response.json()["uid"], "vtimezone") == [
        [
            "vtimezone",
            [["tzid", {}, "text", "Europe/Paris"]],
            [PARIS_SUMMER_2026, PARIS_WINTER_2026],
        ]
    ]


async def test_without_a_time_zone_the_event_is_written_in_the_users_own(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The time zone of the user's Calendar settings, such as that of a colleague in Vietnam
    boundary.calendar.time_zones[email_of("mmaudet")] = "Asia/Ho_Chi_Minh"
    response = await create(client, **LUNCH_IN_THEIR_ZONE)

    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["start"], created["end"], created["time_zone"]) == (
        "2026-10-13T17:00:00+07:00",
        "2026-10-13T19:00:00+07:00",
        "Asia/Ho_Chi_Minh",
    )
    assert stored_event(boundary, created["uid"])["dtstart"] == [
        "dtstart",
        {"tzid": "Asia/Ho_Chi_Minh"},
        "date-time",
        "2026-10-13T17:00:00",
    ]


async def test_without_a_time_zone_calendar_knows_the_event_is_written_in_utc(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Calendar gives every user a time zone, theirs or the deployment's: one the IANA database
    # lacks serves nothing
    boundary.calendar.time_zones[email_of("mmaudet")] = "Mars/Olympus"
    response = await create(client, **LUNCH_IN_THEIR_ZONE)

    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["start"], created["end"], created["time_zone"]) == (
        "2026-10-13T10:00:00Z",
        "2026-10-13T12:00:00Z",
        "UTC",
    )
    event = stored_event(boundary, created["uid"])
    assert event["dtstart"] == ["dtstart", {}, "date-time", "2026-10-13T10:00:00Z"]
    assert event["dtend"] == ["dtend", {}, "date-time", "2026-10-13T12:00:00Z"]
    # A time in UTC names no time zone, so none is described
    assert stored_components(boundary, created["uid"], "vtimezone") == []


async def test_an_event_may_leave_the_user_free_and_say_where_and_what_it_is_for(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    where = "Le Bistrot, 12 rue de la Paix"
    what = "Bring the Q4 figures.\nTable for 8."

    response = await create(client, **LUNCH, busy=False, location=where, description=what)

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["busy"] is False
    assert created["untrusted"] == {
        "title": "Lunch with the team",
        "location": where,
        "description": what,
    }
    event = stored_event(boundary, created["uid"])
    # Free/busy leaves a transparent event out: the user stays free
    assert event["transp"] == ["transp", {}, "text", "TRANSPARENT"]
    assert event["location"] == ["location", {}, "text", where]
    assert event["description"] == ["description", {}, "text", what]


async def test_an_event_of_whole_days_lasts_from_the_first_to_the_last(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A week off, from Monday to Friday
    response = await create(client, title="Holidays", start="2026-10-19", end="2026-10-23")

    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["start"], created["end"], created["all_day"], created["time_zone"]) == (
        "2026-10-19",
        "2026-10-23",
        True,
        None,
    )
    event = stored_event(boundary, created["uid"])
    # iCalendar ends an event of whole days on the day after its last
    assert event["dtstart"] == ["dtstart", {}, "date", "2026-10-19"]
    assert event["dtend"] == ["dtend", {}, "date", "2026-10-24"]
    assert stored_components(boundary, created["uid"], "vtimezone") == []


# Whole days, from Monday to Friday
WEEK_OFF = {"title": "Holidays", "start": "2026-10-19", "end": "2026-10-23"}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(LUNCH | {"title": " "}, id="a blank title"),
        pytest.param(LUNCH | {"title": "L" * 501}, id="a title over 500 characters"),
        pytest.param(LUNCH | {"end": "2026-10-13T11:00:00+02:00"}, id="an end before the start"),
        pytest.param(LUNCH | {"end": LUNCH["start"]}, id="an end at the start"),
        pytest.param(WEEK_OFF | {"end": "2026-10-18"}, id="a last day before the first"),
        pytest.param(
            LUNCH | {"start": "2026-10-01T12:00:00+02:00", "end": "2026-11-01T12:00:01+01:00"},
            id="more than 31 days",
        ),
        pytest.param(WEEK_OFF | {"end": "2026-11-19"}, id="more than 31 whole days"),
        pytest.param(LUNCH | {"start": "2026-10-13T12:00:00"}, id="a time without its offset"),
        # Read as a day, it would make an event of whole days of what was meant as a time
        pytest.param(
            LUNCH_IN_THEIR_ZONE | {"start": "2026-10-13T00:00:00", "end": "2026-10-14T00:00:00"},
            id="midnight without its offset",
        ),
        pytest.param(
            LUNCH | {"start": "0001-01-01T00:00:00+00:00", "end": "0001-01-01T01:00:00+00:00"},
            id="a time before 1900",
        ),
        pytest.param(
            LUNCH | {"start": "9999-12-31T22:00:00+01:00", "end": "9999-12-31T23:00:00+01:00"},
            id="a time after 9998",
        ),
        pytest.param(
            WEEK_OFF | {"start": "9999-12-30", "end": "9999-12-31"}, id="a last day after 9998"
        ),
        pytest.param(LUNCH | {"end": "2026-10-13"}, id="a time and a day"),
        pytest.param(LUNCH | {"time_zone": "Europe/Atlantis"}, id="a zone no one knows"),
        pytest.param(WEEK_OFF | {"time_zone": "Europe/Paris"}, id="a zone for whole days"),
        # What it never does: invite, repeat or remind
        pytest.param(LUNCH | {"attendees": ["alice@twake.test"]}, id="attendees"),
        pytest.param(LUNCH | {"rrule": "FREQ=WEEKLY"}, id="a repetition"),
        pytest.param(LUNCH | {"alarm": "-PT15M"}, id="an alarm"),
    ],
)
async def test_an_invalid_event_is_refused_before_anything_is_written(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    response = await create(client, **body)

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "invalid_request"
    assert boundary.calendar.writes == []


async def test_the_same_call_made_again_adds_no_second_event(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Such as the call its owner allowed, run again after a time out
    first = await create(client, **LUNCH)
    again = await create(client, **LUNCH)

    assert (first.status_code, again.status_code) == (201, 200), again.text
    assert again.json() == first.json()
    assert len(boundary.calendar.objects) == 1
    assert len(boundary.calendar.writes) == 1


async def test_an_event_the_user_changed_since_is_left_as_they_made_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    first = await create(client, **LUNCH)
    uid = first.json()["uid"]
    # The user renames it in Calendar, which keeps its UID
    (vevent,) = stored_components(boundary, uid, "vevent")
    summary = next(prop for prop in vevent[1] if prop[0] == "summary")
    summary[3] = "Team lunch, at the canteen"

    again = await create(client, **LUNCH)

    assert again.status_code == 409, again.text
    assert again.json()["code"] == "event_exists"
    assert summary[3] == "Team lunch, at the canteen"
    assert boundary.calendar.writes == [f"{DEFAULT_CALENDAR}/{uid}.ics"]


async def test_an_event_asked_for_again_with_other_details_is_left_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # As a model would, to add a location to an event it added: no contract changes one yet
    first = await create(client, **LUNCH)

    again = await create(client, **LUNCH, location="Le Bistrot")

    assert again.status_code == 409, again.text
    assert again.json()["code"] == "event_exists"
    assert "location" not in stored_event(boundary, first.json()["uid"])
    assert len(boundary.calendar.writes) == 1


async def test_two_events_at_the_same_time_are_both_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    lunch = await create(client, **LUNCH)
    call = await create(client, **(LUNCH | {"title": "Call with the bank"}))

    assert (lunch.status_code, call.status_code) == (201, 201), call.text
    assert lunch.json()["uid"] != call.json()["uid"]
    assert len(boundary.calendar.objects) == 2


async def test_an_event_calendar_kept_but_answered_too_late_for_is_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The side service waits for esn-sabre longer than the service waits for it
    boundary.calendar.failing_writes = "landed"

    response = await create(client, **LUNCH)

    assert response.status_code == 201, response.text
    assert response.json()["untrusted"]["title"] == "Lunch with the team"
    assert len(boundary.calendar.objects) == 1


async def test_an_event_calendar_failed_to_keep_is_reported(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.failing_writes = "lost"

    response = await create(client, **LUNCH)

    assert response.status_code == 502, response.text
    assert response.json()["code"] == "calendar_unavailable"
    assert boundary.calendar.objects == {}


async def test_blanks_around_the_words_of_an_event_are_left_out(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(
        client, **(LUNCH | {"title": " Lunch with the team\n"}), location="  ", description=" Q4 "
    )

    assert response.status_code == 201, response.text
    assert response.json()["untrusted"] == {
        "title": "Lunch with the team",
        "location": None,
        "description": "Q4",
    }
    assert "location" not in stored_event(boundary, response.json()["uid"])


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Ajouter « Lunch with the team » à ton agenda, mardi 13 octobre 2026 de 12 h à 14 h\n"
            "Twake Agenda ne prévient personne.",
        ),
        (
            "en",
            "Add “Lunch with the team” to your calendar, Tuesday 13 October 2026 from 12:00 to"
            " 14:00\nTwake Calendar tells nobody.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_what_would_be_added_and_adds_nothing(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    response = await create(client, asking_preview(language), **LUNCH)

    told, _ = preview_of(response)
    assert told == summary
    assert boundary.calendar.writes == []
    assert boundary.calendar.objects == {}


async def test_a_preview_tells_where_the_event_is_what_it_is_for_and_that_it_leaves_free(
    client: AsyncClient,
) -> None:
    response = await create(
        client,
        asking_preview("fr"),
        **LUNCH,
        busy=False,
        location="Le Bistrot, 12 rue de la Paix",
        description="Bring the Q4 figures.\nTable for 8.",
    )

    told, _ = preview_of(response)
    assert told == (
        "Ajouter « Lunch with the team » à ton agenda, mardi 13 octobre 2026 de 12 h à 14 h, en"
        " te laissant libre\n"
        "Lieu : « Le Bistrot, 12 rue de la Paix »\n"
        "Description :\n"
        "\tBring the Q4 figures.\n"
        "\tTable for 8.\n"
        "Twake Agenda ne prévient personne."
    )


async def test_a_preview_tells_the_days_of_an_event_of_whole_days(client: AsyncClient) -> None:
    response = await create(client, asking_preview("fr"), **WEEK_OFF)

    told, _ = preview_of(response)
    assert told.splitlines()[0] == (
        "Ajouter « Holidays » à ton agenda, du lundi 19 octobre 2026 au vendredi 23 octobre 2026"
    )


async def test_a_preview_names_the_zone_of_the_event_without_the_owners(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The zone only says how to read the times: the event's serve, named
    boundary.calendar.settings_down = True

    response = await create(client, asking_preview("en"), **LUNCH)

    told, _ = preview_of(response)
    assert told.splitlines()[0] == (
        "Add “Lunch with the team” to your calendar, Tuesday 13 October 2026 from 12:00 to 14:00"
        " (time zone “Europe/Paris”)"
    )


async def test_a_preview_of_an_event_added_already_tells_nothing_would_be(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    await create(client, **LUNCH)

    response = await create(client, asking_preview("fr"), **LUNCH)

    told, _ = preview_of(response)
    assert told == (
        "« Lunch with the team » est déjà dans ton agenda, mardi 13 octobre 2026 de 12 h à 14 h :"
        " rien n'est ajouté."
    )
    assert len(boundary.calendar.writes) == 1


async def test_a_preview_refuses_what_adding_would_refuse(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(
        client, asking_preview("fr"), **(LUNCH | {"end": "2026-10-13T11:00:00+02:00"})
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.calendar.writes == []


async def test_the_owner_who_allowed_what_they_were_shown_gets_the_event(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(await create(client, asking_preview("fr"), **LUNCH))

    response = await create(client, allowed_after(digest), **LUNCH)

    assert response.status_code == 201, response.text
    assert len(boundary.calendar.objects) == 1


async def test_an_event_whose_time_zone_changed_since_the_preview_is_not_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(await create(client, asking_preview("fr"), **LUNCH_IN_THEIR_ZONE))
    # The owner moves to Vietnam before saying yes: the event would be written in another zone
    boundary.calendar.time_zones[email_of("mmaudet")] = "Asia/Ho_Chi_Minh"

    response = await create(client, allowed_after(digest), **LUNCH_IN_THEIR_ZONE)

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.calendar.writes == []


async def test_an_event_in_the_users_zone_waits_for_calendar_to_give_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Failing to give it is no zone to write the event in
    boundary.calendar.settings_down = True

    response = await create(client, **LUNCH_IN_THEIR_ZONE)

    assert response.status_code == 502, response.text
    assert response.json()["code"] == "calendar_unavailable"
    assert boundary.calendar.objects == {}


async def test_a_preview_refuses_an_event_there_with_other_details(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    await create(client, **LUNCH)

    response = await create(client, asking_preview("fr"), **LUNCH, busy=False)

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "event_exists"


async def test_an_event_removed_since_the_preview_said_it_was_there_is_not_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    first = await create(client, **LUNCH)
    _, digest = preview_of(await create(client, asking_preview("fr"), **LUNCH))
    # The user deletes it before saying yes to a call they were told would add nothing
    del boundary.calendar.objects[f"{DEFAULT_CALENDAR}/{first.json()['uid']}.ics"]

    response = await create(client, allowed_after(digest), **LUNCH)

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.calendar.objects == {}
