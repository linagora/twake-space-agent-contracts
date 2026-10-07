"""Where the service finds the issuer of the users' tokens and the applications it relays to, and
which of those applications it publishes."""

import os
from dataclasses import dataclass, field

# What the service published before PUBLISHED_APPS existed, but for the events it no longer has,
# and still publishes without it
PUBLISHED_BY_DEFAULT = frozenset({"calendar"})


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
    tasks_url: str | None
    """Twake Tasks, whose REST API the tasks contracts call with the user's token: needed only
    once tasks is published."""
    published_apps: frozenset[str]
    """The applications the service publishes, by domain: it serves and describes their contracts
    only. The operator keeps it equal to the applications APISIX routes."""
    chat_url: str | None = None
    """The gateway's outbound route to Synapse, Twake Chat's homeserver, which adds the token of
    the contracts' application service: Chat goes through it as the user. Needed once Chat is
    published, and only then."""
    chat_gateway_key: str | None = field(default=None, repr=False)
    """The key that route admits, so that only this service uses the application service's token:
    sent on each call to Chat, and to no other application. Needed once Chat is published, and
    only then. Out of the settings' representation, so that no trace or log shows it."""
    matrix_server_name: str | None = None
    """The homeserver's name, which ends the Matrix id of each of its users. Needed once Chat is
    published, and only then."""
    matrix_mail_domain: str | None = None
    """The mail domain of the homeserver's users, the server name unless set: alice@<domain> is
    @alice:<server name>."""
    mail_url: str | None = None
    """TMail, the Twake Mail backend, whose JMAP API mail goes through with the user's token:
    needed once Mail is published only."""
    drive_instance_domain: str | None = None
    """The domain of the users' cozy-stack instances, each one name under it, such as
    alice.<domain>: the service sends a Drive token to no other host. Needed once Drive is
    published, and only then."""
    drive_scheme: str = "https"
    """How the service reaches the users' cozy-stack instances, whose hosts the gateway gives."""
    drive_port: int | None = None
    """The port of those instances, when it is not the scheme's, such as a local stack's."""

    @classmethod
    def from_env(cls) -> "Settings":
        issuer = os.environ["OIDC_ISSUER"]
        drive_port = os.environ.get("DRIVE_PORT")
        return cls(
            issuer=issuer,
            audience=os.environ.get("OIDC_AUDIENCE", "twake-space-agents"),
            # Where LemonLDAP-NG publishes them, unless told otherwise
            jwks_url=os.environ.get("OIDC_JWKS_URL", issuer.rstrip("/") + "/oauth2/jwks"),
            calendar_url=os.environ["CALENDAR_URL"].rstrip("/"),
            tasks_url=os.environ.get("TASKS_URL", "").rstrip("/") or None,
            # Unset or empty, as a chart may render a value it lacks: what it published before
            published_apps=frozenset(
                domain.strip().lower()
                for domain in os.environ.get("PUBLISHED_APPS", "").split(",")
                if domain.strip()
            )
            or PUBLISHED_BY_DEFAULT,
            chat_url=os.environ.get("CHAT_URL", "").rstrip("/") or None,
            # No header carries the line break a secret's file may end with
            chat_gateway_key=os.environ.get("CHAT_GATEWAY_KEY", "").strip() or None,
            matrix_server_name=os.environ.get("MATRIX_SERVER_NAME") or None,
            matrix_mail_domain=os.environ.get("MATRIX_MAIL_DOMAIN") or None,
            mail_url=os.environ.get("MAIL_URL", "").rstrip("/") or None,
            drive_instance_domain=os.environ.get("DRIVE_INSTANCE_DOMAIN") or None,
            drive_scheme=os.environ.get("DRIVE_SCHEME", "https"),
            drive_port=int(drive_port) if drive_port else None,
        )
