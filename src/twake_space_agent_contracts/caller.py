"""The user an agent acts for, from the access token APISIX attaches through the token broker."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Header

from twake_space_agent_contracts.problems import Problem
from twake_space_agent_contracts.settings import Settings


@dataclass(frozen=True)
class User:
    email: str
    """The user, by the email that is their subject on Twake's LemonLDAP-NG, lowercased."""


def _refused(*, code: str, title: str, detail: str) -> Problem:
    return Problem(status=401, code=code, title=title, detail=detail)


def _keys_unavailable() -> Problem:
    return Problem(
        status=503,
        code="keys_unavailable",
        title="Signing keys unavailable",
        detail="The signing keys of the token issuer could not be fetched.",
    )


class TokenVerifier:
    """Checks access tokens against the signing keys of the issuer, fetched and kept in memory."""

    KEYS_MAX_AGE = 3600.0
    """Seconds the keys are kept before they are fetched again, so that a key the issuer
    withdraws stops being trusted within the hour."""
    UNKNOWN_KEY_INTERVAL = 60.0
    """Seconds between two fetches for an unknown key id, however many arrive."""
    RETRY_INTERVAL = 30.0
    """Seconds between two attempts while the issuer does not answer."""

    def __init__(
        self, settings: Settings, http: httpx.AsyncClient, clock: Callable[[], float]
    ) -> None:
        self._settings = settings
        self._http = http
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._failed_at: float | None = None
        # One fetch at a time: the requests that arrive meanwhile wait for its keys
        self._fetching = asyncio.Lock()

    async def _fetch_keys(self) -> None:
        try:
            response = await self._http.get(self._settings.jwks_url)
            response.raise_for_status()
            keys = response.json()["keys"]
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise _keys_unavailable() from error
        self._keys = {
            key["kid"]: jwt.PyJWK(key, algorithm="RS256").key
            for key in keys
            if key.get("kty") == "RSA" and key.get("use", "sig") == "sig" and "kid" in key
        }
        self._fetched_at = self._clock()
        self._failed_at = None

    def _due(self, key_id: str) -> bool:
        now = self._clock()
        if self._failed_at is not None and now - self._failed_at < self.RETRY_INTERVAL:
            return False
        if self._fetched_at is None:
            return True
        age = now - self._fetched_at
        return age > self.KEYS_MAX_AGE or (
            key_id not in self._keys and age > self.UNKNOWN_KEY_INTERVAL
        )

    async def _key(self, key_id: str) -> Any:
        async with self._fetching:
            if self._due(key_id):
                try:
                    await self._fetch_keys()
                except Problem:
                    self._failed_at = self._clock()
                    # The issuer is away: the keys already fetched still serve, if any
                    if not self._keys:
                        raise
            elif not self._keys:
                raise _keys_unavailable()
            return self._keys.get(key_id)

    async def user(self, token: str) -> User:
        invalid = _refused(
            code="invalid_token",
            title="Invalid token",
            detail="The access token is not one the token broker got for this service.",
        )
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as error:
            raise invalid from error
        key_id = header.get("kid")
        # LemonLDAP-NG types its access tokens at+JWT (RFC 9068): an ID token is not one
        if str(header.get("typ", "")).lower() != "at+jwt" or not isinstance(key_id, str):
            raise invalid
        key = await self._key(key_id)
        if key is None:
            raise invalid
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "client_id"]},
            )
        except jwt.InvalidTokenError as error:
            raise invalid from error
        # Another client may list this audience too: only the broker's own tokens serve
        if claims["client_id"] != self._settings.audience:
            raise invalid
        return User(email=str(claims["sub"]).lower())


CallerDependency = Callable[..., Awaitable[User]]


def caller_dependency(verifier: TokenVerifier) -> CallerDependency:
    """The dependency that gives a route the user whose token APISIX attached."""

    async def caller(
        authorization: Annotated[str | None, Header(include_in_schema=False)] = None,
    ) -> User:
        """The user of the bearer token.

        Left out of the OpenAPI document: an agent never holds a user's token, nor chooses whom
        it acts for.
        """
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _refused(
                code="missing_token",
                title="Missing token",
                detail="The request must carry the user's access token as a bearer token.",
            )
        return await verifier.user(token.strip())

    return caller
