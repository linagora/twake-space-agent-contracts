"""Where the service finds the issuer of the users' tokens and the applications it relays to, and
which of those applications it publishes."""

import os
from dataclasses import dataclass

# What the service published before PUBLISHED_APPS existed, and still publishes without it
PUBLISHED_BY_DEFAULT = frozenset({"events", "calendar"})


@dataclass(frozen=True)
class Settings:
    issuer: str
    """The LemonLDAP-NG issuer of the users' access tokens, exactly as in their iss claim."""
    audience: str
    """The client the token broker gets those tokens for: tokens of any other client are refused."""
    jwks_url: str
    """Where the issuer publishes its signing keys."""
    calendar_url: str
    """The Calendar side service, which free/busy goes through with the user's token."""
    published_apps: frozenset[str]
    """The applications the service publishes, by domain: it serves and describes their contracts
    only. The operator keeps it equal to the applications APISIX routes."""
    chat_url: str | None = None
    """The gateway's outbound route to Synapse, Twake Chat's homeserver, which adds the token of
    the contracts' application service: Chat goes through it as the user. Needed once Chat is
    published, and only then."""
    matrix_server_name: str | None = None
    """The homeserver's name, which ends the Matrix id of each of its users. Needed once Chat is
    published, and only then."""
    matrix_mail_domain: str | None = None
    """The mail domain of the homeserver's users, the server name unless set: alice@<domain> is
    @alice:<server name>."""

    @classmethod
    def from_env(cls) -> "Settings":
        issuer = os.environ["OIDC_ISSUER"]
        return cls(
            issuer=issuer,
            audience=os.environ.get("OIDC_AUDIENCE", "twake-space-agents"),
            # Where LemonLDAP-NG publishes them, unless told otherwise
            jwks_url=os.environ.get("OIDC_JWKS_URL", issuer.rstrip("/") + "/oauth2/jwks"),
            calendar_url=os.environ["CALENDAR_URL"].rstrip("/"),
            # Unset or empty, as a chart may render a value it lacks: what it published before
            published_apps=frozenset(
                domain.strip().lower()
                for domain in os.environ.get("PUBLISHED_APPS", "").split(",")
                if domain.strip()
            )
            or PUBLISHED_BY_DEFAULT,
            chat_url=os.environ.get("CHAT_URL", "").rstrip("/") or None,
            matrix_server_name=os.environ.get("MATRIX_SERVER_NAME") or None,
            matrix_mail_domain=os.environ.get("MATRIX_MAIL_DOMAIN") or None,
        )
