"""What the service reaches over HTTP, faked at that boundary: the signing keys of LemonLDAP-NG.
Also the clock the token checks read."""

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
)
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_ID = "sig-1"


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


class FakeBoundary:
    """Routes the service's HTTP calls to the fakes."""

    def __init__(self) -> None:
        self.issuer = FakeIssuer()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url == SETTINGS.jwks_url:
            return self.issuer.handle(request)
        return httpx.Response(404)
