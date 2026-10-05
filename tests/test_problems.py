from httpx import AsyncClient


async def test_an_unknown_path_answers_a_problem(client: AsyncClient) -> None:
    response = await client.get("/contracts/v1/nothing", headers={"X-Twake-User": "mmaudet"})

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "urn:twake:problem:not_found"
    assert response.json()["code"] == "not_found"
