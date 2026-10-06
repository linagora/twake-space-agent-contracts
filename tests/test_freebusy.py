import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import MMAUDET_CALENDAR_ID, FakeBoundary, as_user, email_of

# Tuesday 17:00 to 18:00 in Paris
SLOT: dict[str, str | list[str]] = {
    "start": "2026-10-06T17:00:00+02:00",
    "end": "2026-10-06T18:00:00+02:00",
}


async def free_busy(client: AsyncClient, **params: str | list[str]) -> dict[str, object]:
    response = await client.get(
        "/contracts/v1/calendar/freebusy", params=SLOT | params, headers=AS_MMAUDET
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def test_a_user_with_nothing_then_is_free(client: AsyncClient) -> None:
    assert await free_busy(client) == {
        "start": "2026-10-06T15:00:00Z",
        "end": "2026-10-06T16:00:00Z",
        "free": True,
        "busy": [],
    }


async def test_a_user_with_a_meeting_in_the_period_is_busy(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.busy[MMAUDET_CALENDAR_ID] = [
        {"uid": "standup", "start": "20261006T153000Z", "end": "20261006T163000Z"},
        {"uid": "lunch", "start": "20261006T100000Z", "end": "20261006T110000Z"},
    ]

    answer = await free_busy(client)

    assert answer["free"] is False
    assert answer["busy"] == [{"start": "2026-10-06T15:30:00Z", "end": "2026-10-06T16:30:00Z"}]


async def test_the_events_left_out_do_not_make_the_user_busy(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The invitation being considered is in the calendar too, waiting for an answer
    boundary.calendar.busy[MMAUDET_CALENDAR_ID] = [
        {"uid": "invitation-a", "start": "20261006T150000Z", "end": "20261006T160000Z"}
    ]

    answer = await free_busy(client, exclude=["invitation-a"])

    assert answer["free"] is True
    assert boundary.calendar.free_busy_requests == [
        {
            "start": "20261006T150000Z",
            "end": "20261006T160000Z",
            "users": [MMAUDET_CALENDAR_ID],
            "uids": ["invitation-a"],
        }
    ]


@pytest.mark.parametrize(
    "period",
    [
        pytest.param({"start": SLOT["end"], "end": SLOT["start"]}, id="ends before it starts"),
        pytest.param(
            {"start": "2026-10-01T00:00:00Z", "end": "2026-11-15T00:00:00Z"},
            id="longer than 31 days",
        ),
        pytest.param(
            {"start": "2026-10-06T17:00:00", "end": "2026-10-06T18:00:00"}, id="without offset"
        ),
    ],
)
async def test_a_period_that_cannot_be_read_is_an_invalid_request(
    client: AsyncClient, period: dict[str, str]
) -> None:
    response = await client.get(
        "/contracts/v1/calendar/freebusy", params=period, headers=AS_MMAUDET
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"


async def test_a_user_calendar_does_not_know_is_not_found(client: AsyncClient) -> None:
    response = await client.get(
        "/contracts/v1/calendar/freebusy", params=SLOT, headers=as_user(email_of("nobody"))
    )

    assert response.status_code == 404
    assert response.json()["code"] == "calendar_user_not_found"


async def test_an_unavailable_calendar_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.down = True

    response = await client.get("/contracts/v1/calendar/freebusy", params=SLOT, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "calendar_unavailable"


async def test_free_busy_needs_the_user_token(client: AsyncClient) -> None:
    response = await client.get("/contracts/v1/calendar/freebusy", params=SLOT)

    assert response.status_code == 401


async def test_a_token_calendar_refuses_is_named_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.calendar.refused_tokens = True

    response = await client.get("/contracts/v1/calendar/freebusy", params=SLOT, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "calendar_refused"
