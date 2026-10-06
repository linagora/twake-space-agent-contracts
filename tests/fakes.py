"""What the service reaches over HTTP, faked at that boundary: the signing keys of LemonLDAP-NG
and the Calendar side service. Also the clock the token checks read."""

import json
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from twake_space_agent_contracts.settings import Settings

# LemonLDAP-NG as the token broker's tokens come from it: issuer, audiences and signing key
ISSUER = "https://sign-up.test/"
AUDIENCE = "twake-space-agents"
SETTINGS = Settings(
    issuer=ISSUER,
    audience=AUDIENCE,
    jwks_url="https://sign-up.test/oauth2/jwks",
    calendar_url="https://calendar.test",
)
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_ID = "sig-1"

MMAUDET_CALENDAR_ID = "6650a1b2c3d4e5f6a7b8c9d0"


def email_of(uid: str) -> str:
    return f"{uid}@twake.test"


def token_for(
    email: str,
    *,
    key: rsa.RSAPrivateKey = SIGNING_KEY,
    key_id: str = KEY_ID,
    token_type: str = "at+JWT",
    **claims: Any,
) -> str:
    """An access token of the twake-space-agents client, as APISIX gets it from the broker.

    LemonLDAP-NG types its access tokens at+JWT, and names the client in client_id.
    """
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": [AUDIENCE, "tcalendar"],
        "client_id": AUDIENCE,
        "sub": email,
        "iat": now,
        "exp": now + 3600,
    } | claims
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": key_id, "typ": token_type})


def as_user(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(email)}"}


class FakeClock:
    """Seconds, as time.monotonic counts them, moved forward by the tests."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class FakeIssuer:
    """The signing keys LemonLDAP-NG publishes: the test key, unless a test rotates it away."""

    def __init__(self) -> None:
        self.keys = {KEY_ID: SIGNING_KEY}
        self.down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        published = [
            jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
            | {"kid": key_id, "use": "sig"}
            for key_id, key in self.keys.items()
        ]
        return httpx.Response(200, json={"keys": published})


class FakeCalendar:
    """The Calendar side service, as free/busy goes through it with the bearer's token.

    The user lookup by email, then the JSON free/busy of esn-sabre, which leaves out the events
    whose UIDs it is given. Times are written as esn-sabre writes them, 20261006T150000Z.
    """

    def __init__(self) -> None:
        self.users: dict[str, str] = {email_of("mmaudet"): MMAUDET_CALENDAR_ID}
        self.busy: dict[str, list[dict[str, str]]] = {}
        """Busy slots by user id: uid, start, end."""
        self.down = False
        self.refused_tokens = False
        self.free_busy_requests: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            caller = jwt.decode(bearer, options={"verify_signature": False})["sub"]
        except jwt.InvalidTokenError:
            return httpx.Response(401)
        if self.refused_tokens:
            return httpx.Response(401)
        if request.method == "GET" and request.url.path == "/api/users":
            email = request.url.params.get("email")
            if email != caller:
                return httpx.Response(403)
            user = self.users.get(caller)
            return httpx.Response(
                200, json=[{"_id": user, "preferredEmail": email}] if user else []
            )
        if request.method == "POST" and request.url.path == "/dav/calendars/freebusy":
            return self._free_busy(request, self.users.get(caller))
        return httpx.Response(404)

    def _free_busy(self, request: httpx.Request, user: str | None) -> httpx.Response:
        # esn-sabre answers JSON for this exact Accept only
        if request.headers.get("accept") != "application/json":
            return httpx.Response(406)
        body = json.loads(request.content)
        self.free_busy_requests.append(body)
        if user is None or body.get("users") != [user]:
            return httpx.Response(403)
        busy = [
            slot
            for slot in self.busy.get(user, [])
            if slot["uid"] not in body.get("uids", [])
            and slot["start"] < body["end"]
            and slot["end"] > body["start"]
        ]
        free_busy = {"id": user, "calendars": [{"id": user, "busy": busy}]}
        return httpx.Response(
            200, json={"start": body["start"], "end": body["end"], "users": [free_busy]}
        )


class FakeBoundary:
    """Routes the service's HTTP calls to the fakes."""

    def __init__(self) -> None:
        self.issuer = FakeIssuer()
        self.calendar = FakeCalendar()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url == SETTINGS.jwks_url:
            return self.issuer.handle(request)
        if request.url.host == "calendar.test":
            return self.calendar.handle(request)
        return httpx.Response(404)
