"""Where the service finds the issuer of the users' tokens."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    issuer: str
    """The LemonLDAP-NG issuer of the users' access tokens, exactly as in their iss claim."""
    audience: str
    """The client the token broker gets those tokens for: tokens of any other client are refused."""
    jwks_url: str
    """Where the issuer publishes its signing keys."""

    @classmethod
    def from_env(cls) -> "Settings":
        issuer = os.environ["OIDC_ISSUER"]
        return cls(
            issuer=issuer,
            audience=os.environ.get("OIDC_AUDIENCE", "twake-space-agents"),
            # Where LemonLDAP-NG publishes them, unless told otherwise
            jwks_url=os.environ.get("OIDC_JWKS_URL", issuer.rstrip("/") + "/oauth2/jwks"),
        )
