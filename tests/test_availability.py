from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import ALICE_CALENDAR_ID, MMAUDET_CALENDAR_ID, FakeBoundary, FakeClock, email_of

SLOTS = "/contracts/v1/calendar/availability/slots"
ALICE = email_of("alice")
# Tuesday 13 October 2026, in Paris, which is the user's time zone
TUESDAY = {"start": "2026-10-13T00:00:00+02:00", "end": "2026-10-14T00:00:00+02:00"}
WEEK = {"start": "2026-10-12T00:00:00+02:00", "end": "2026-10-19T00:00:00+02:00"}


@pytest.fixture(autouse=True)
def alice(boundary: FakeBoundary) -> None:
    boundary.calendar.users[ALICE] = ALICE_CALENDAR_ID


def asked(period: dict[str, str] = TUESDAY, **params: Any) -> dict[str, Any]:
    """The query: Alice for an hour on Tuesday unless it says otherwise."""
    return {"email": [ALICE], "duration": 60} | period | params


async def find(
    client: AsyncClient, period: dict[str, str] = TUESDAY, **params: str | int | list[str]
) -> dict[str, Any]:
    response = await client.get(SLOTS, params=asked(period, **params), headers=AS_MMAUDET)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def starts(answer: dict[str, Any]) -> list[str]:
    slots = answer["slots"]
    assert isinstance(slots, list)
    return [slot["start"][11:16] for slot in slots]


async def test_everybody_free_gives_slots_within_business_hours_every_half_hour(
    client: AsyncClient,
) -> None:
    answer = await find(client)

    assert answer["time_zone"] == "Europe/Paris"
    assert answer["truncated"] is False
    assert answer["slots"][0] == {
        "start": "2026-10-13T09:00:00+02:00",
        "end": "2026-10-13T10:00:00+02:00",
    }
    assert starts(answer)[0] == "09:00"
    assert starts(answer)[-1] == "17:00"
    assert len(starts(answer)) == 17


async def test_a_slot_overlapping_anyones_busy_time_is_left_out(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The user is busy 09:00 to 10:00 in Paris, Alice 14:00 to 15:00
    boundary.calendar.busy[MMAUDET_CALENDAR_ID] = [
        {"uid": "a", "start": "20261013T070000Z", "end": "20261013T080000Z"}
    ]
    boundary.calendar.busy[ALICE_CALENDAR_ID] = [
        {"uid": "b", "start": "20261013T120000Z", "end": "20261013T130000Z"}
    ]

    answer = await find(client)

    found = starts(answer)
    assert found[0] == "10:00"
    assert not {"13:30", "14:00", "14:30"} & set(found)
    assert "15:00" in found
    assert boundary.calendar.free_busy_requests[-1]["users"] == [
        MMAUDET_CALENDAR_ID,
        ALICE_CALENDAR_ID,
    ]


async def test_the_user_asking_for_themselves_is_not_counted_twice(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    await find(client, email=[ALICE, email_of("mmaudet").upper(), ALICE.upper()])

    assert boundary.calendar.free_busy_requests[-1]["users"] == [
        MMAUDET_CALENDAR_ID,
        ALICE_CALENDAR_ID,
    ]


async def test_the_weekend_has_no_slot(client: AsyncClient) -> None:
    saturday = {"start": "2026-10-17T00:00:00+02:00", "end": "2026-10-19T00:00:00+02:00"}

    answer = await find(client, saturday)

    assert answer["slots"] == []
    assert answer["truncated"] is False


async def test_slots_are_twenty_at_most_and_say_there_are_more(client: AsyncClient) -> None:
    answer = await find(client, WEEK)

    assert len(starts(answer)) == 20
    assert answer["truncated"] is True


async def test_a_slot_cannot_start_before_the_period_or_end_after_it(client: AsyncClient) -> None:
    period = {"start": "2026-10-13T10:15:00+02:00", "end": "2026-10-13T11:45:00+02:00"}

    answer = await find(client, period, duration=30)

    assert starts(answer) == ["10:30", "11:00"]


@pytest.mark.parametrize(
    ("now", "first", "asked_from"),
    [
        pytest.param(
            datetime(2026, 10, 13, 8, 10, tzinfo=UTC), "10:30", "20261013T083000Z", id="10:10"
        ),
        pytest.param(
            datetime(2026, 10, 13, 8, tzinfo=UTC), "10:00", "20261013T080000Z", id="10:00"
        ),
        pytest.param(
            datetime(2026, 10, 13, 8, 30, 0, 1, tzinfo=UTC),
            "11:00",
            "20261013T090000Z",
            id="just after 10:30",
        ),
    ],
)
async def test_a_period_that_has_begun_is_searched_from_the_next_half_hour(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    now: datetime,
    first: str,
    asked_from: str,
) -> None:
    # During Tuesday in Paris, two hours ahead of UTC
    clock.wall = now

    answer = await find(client, duration=30)

    assert starts(answer)[0] == first
    assert answer["start"] == f"2026-10-13T{first}:00+02:00"
    assert answer["end"] == "2026-10-14T00:00:00+02:00"
    assert boundary.calendar.free_busy_requests[-1]["start"] == asked_from


@pytest.mark.parametrize(
    ("now", "period"),
    [
        pytest.param(datetime(2026, 10, 14, 8, tzinfo=UTC), TUESDAY, id="over"),
        pytest.param(
            datetime(2026, 10, 13, 8, 10, tzinfo=UTC),
            {"start": "2026-10-13T10:00:00+02:00", "end": "2026-10-13T10:20:00+02:00"},
            id="ends before the next half hour",
        ),
    ],
)
async def test_a_period_with_no_time_left_is_refused_without_asking_calendar(
    client: AsyncClient,
    boundary: FakeBoundary,
    clock: FakeClock,
    now: datetime,
    period: dict[str, str],
) -> None:
    clock.wall = now

    response = await client.get(SLOTS, params=asked(period), headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.calendar.free_busy_requests == []


async def test_a_person_calendar_does_not_know_is_not_found(client: AsyncClient) -> None:
    response = await client.get(
        SLOTS,
        params=asked(email=[ALICE, "stranger@example.com"], duration=30),
        headers=AS_MMAUDET,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "person_not_found"


@pytest.mark.parametrize(
    ("params", "code"),
    [
        pytest.param({"email": []}, "invalid_request", id="nobody"),
        pytest.param(
            {"email": [f"p{n}@twake.test" for n in range(11)]}, "invalid_request", id="11"
        ),
        pytest.param({"email": ["not an address"]}, "invalid_email", id="not an email"),
        pytest.param({"duration": 5}, "invalid_request", id="too short"),
        pytest.param({"duration": 600}, "invalid_request", id="too long"),
        pytest.param(
            {"start": TUESDAY["end"], "end": TUESDAY["start"]}, "invalid_request", id="reversed"
        ),
        pytest.param(
            {"start": "2026-10-01T00:00:00Z", "end": "2026-10-16T00:00:00Z"},
            "invalid_request",
            id="longer than 14 days",
        ),
        pytest.param(
            {"start": "2026-10-13T00:00:00", "end": "2026-10-14T00:00:00"},
            "invalid_request",
            id="without offset",
        ),
    ],
)
async def test_a_request_that_cannot_be_served_is_refused(
    client: AsyncClient, params: dict[str, Any], code: str
) -> None:
    response = await client.get(SLOTS, params=asked(**params), headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == code


async def test_an_unavailable_calendar_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.down = True

    response = await client.get(SLOTS, params=asked(), headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "calendar_unavailable"


async def test_slots_need_the_user_token(client: AsyncClient) -> None:
    response = await client.get(SLOTS, params=asked())

    assert response.status_code == 401
