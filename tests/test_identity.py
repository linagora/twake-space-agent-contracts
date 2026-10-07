import asyncio

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET, SLOT
from tests.fakes import MMAUDET_CALENDAR_ID, FakeBoundary, FakeClock, email_of, token_for

OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
# A contract any token check stands before
FREEBUSY = "/contracts/v1/calendar/freebusy"


def assert_refused(response: httpx.Response, code: str) -> None:
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == code


async def test_the_token_names_the_user(client: AsyncClient, boundary: FakeBoundary) -> None:
    response = await client.get(FREEBUSY, params=SLOT, headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    assert [asked["users"] for asked in boundary.calendar.free_busy_requests] == [
        [MMAUDET_CALENDAR_ID]
    ]


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer "}, {"Authorization": "Basic abc"}, {"X-Twake-User": "mmaudet"}],
    ids=["missing", "empty", "not bearer", "former user header"],
)
async def test_a_request_without_a_token_is_refused(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get(FREEBUSY, params=SLOT, headers=headers)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:twake:problem:missing_token",
        "title": "Missing token",
        "status": 401,
        "detail": "The request must carry the user's access token as a bearer token.",
        "code": "missing_token",
    }


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(token_for(email_of("mmaudet"), key=OTHER_KEY), id="signed by another key"),
        pytest.param(token_for(email_of("mmaudet"), iss="https://elsewhere.test/"), id="issuer"),
        pytest.param(token_for(email_of("mmaudet"), aud=["tcalendar"]), id="audience"),
        pytest.param(token_for(email_of("mmaudet"), exp=1_700_000_000), id="expired"),
        pytest.param(token_for(email_of("mmaudet"), token_type="JWT"), id="an ID token"),
        pytest.param(
            token_for(email_of("mmaudet"), client_id="tcalendar"),
            id="another client with this audience",
        ),
        pytest.param("not-a-jwt", id="malformed"),
    ],
)
async def test_a_token_the_broker_did_not_get_is_refused(client: AsyncClient, token: str) -> None:
    response = await client.get(FREEBUSY, params=SLOT, headers={"Authorization": f"Bearer {token}"})

    assert_refused(response, "invalid_token")


async def test_the_first_requests_all_wait_for_the_keys(client: AsyncClient) -> None:
    responses = await asyncio.gather(
        *(client.get(FREEBUSY, params=SLOT, headers=AS_MMAUDET) for _ in range(5))
    )

    assert [response.status_code for response in responses] == [200] * 5


async def test_keys_the_issuer_does_not_give_are_an_unavailable_service(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.issuer.down = True

    response = await client.get(FREEBUSY, params=SLOT, headers=AS_MMAUDET)

    assert response.status_code == 503
    assert response.json()["code"] == "keys_unavailable"


async def test_a_key_the_issuer_withdrew_stops_being_trusted_within_the_hour(
    client: AsyncClient, boundary: FakeBoundary, clock: FakeClock
) -> None:
    assert (await client.get(FREEBUSY, params=SLOT, headers=AS_MMAUDET)).status_code == 200
    boundary.issuer.keys = {"sig-2": OTHER_KEY}

    clock.now += 3601
    response = await client.get(FREEBUSY, params=SLOT, headers=AS_MMAUDET)

    assert_refused(response, "invalid_token")
