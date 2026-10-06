import httpx
import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, as_user, email_of, matrix_error, matrix_id

ROOMS = "/contracts/v1/chat/rooms"


def paths(boundary: FakeBoundary) -> list[str]:
    """The paths of Synapse's client API the service called, in order."""
    return [
        request.url.path.removeprefix("/synapse/_matrix/client/v3")
        for request in boundary.synapse.requests
    ]


async def test_the_user_acts_as_the_chat_account_of_their_email(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await client.get(ROOMS, headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    assert {request.url.params["user_id"] for request in boundary.synapse.requests} == {
        matrix_id("mmaudet")
    }


async def test_the_account_is_checked_once(client: AsyncClient, boundary: FakeBoundary) -> None:
    for _ in range(2):
        assert (await client.get(ROOMS, headers=AS_MMAUDET)).status_code == 200

    assert paths(boundary).count("/account/3pid") == 1


async def test_the_accounts_address_matches_whatever_its_case(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.synapse.accounts[matrix_id("mmaudet")] = ["MMaudet@Twake.test"]

    response = await client.get(ROOMS, headers=AS_MMAUDET)

    assert response.status_code == 200, response.text


async def test_an_account_that_does_not_list_the_users_email_is_not_used(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The account named after the email lists another address: it may be someone else's
    boundary.synapse.accounts[matrix_id("mmaudet")] = ["michel@elsewhere.test"]

    response = await client.get(ROOMS, headers=AS_MMAUDET)

    assert response.status_code == 409
    assert response.json()["code"] == "identity_ambiguous"
    assert paths(boundary) == ["/account/3pid"]


@pytest.mark.parametrize(
    "email",
    [email_of("nobody"), "mmaudet@elsewhere.test"],
    ids=["no account", "another mail domain"],
)
async def test_a_user_without_a_chat_account_is_not_found(client: AsyncClient, email: str) -> None:
    response = await client.get(ROOMS, headers=as_user(email))

    assert response.status_code == 404
    assert response.json()["code"] == "chat_account_not_found"


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        pytest.param(httpx.Response(503), "chat_unavailable", id="unavailable"),
        pytest.param(
            matrix_error(401, "M_UNKNOWN_TOKEN", "Unrecognised access token"),
            "chat_refused",
            id="application service token refused",
        ),
        pytest.param(
            httpx.Response(403, json={"message": "Your IP address is not allowed"}),
            "chat_refused",
            id="refused by the gateway",
        ),
        pytest.param(httpx.Response(200, text="<html>"), "chat_unavailable", id="not JSON"),
    ],
)
async def test_what_goes_wrong_in_chat_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary, answer: httpx.Response, code: str
) -> None:
    boundary.synapse.answer = answer

    response = await client.get(ROOMS, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == code


async def test_a_chat_that_does_not_answer_is_unavailable(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.synapse.down = True

    response = await client.get(ROOMS, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "chat_unavailable"


async def test_chat_limiting_the_user_says_when_to_try_again(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.synapse.answer = httpx.Response(
        429,
        json={"errcode": "M_LIMIT_EXCEEDED", "error": "Too Many Requests", "retry_after_ms": 2000},
    )

    response = await client.get(ROOMS, headers=AS_MMAUDET)

    assert response.status_code == 429
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "chat_rate_limited"
    assert response.json()["retry_after_ms"] == 2000
