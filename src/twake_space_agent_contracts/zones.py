"""IANA time zones: the names the contracts take."""

from typing import Any
from zoneinfo import ZoneInfo

ZONE = r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$"
"""An IANA time zone name, such as Europe/Paris or Etc/GMT+1: a pattern any OpenAPI validator
checks."""


def zone_named(name: Any) -> ZoneInfo | None:
    """The time zone of that name in the IANA database, if it has one."""
    if not isinstance(name, str):
        return None
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        return None


def known_zone(name: str) -> str:
    """The name a call gives, if the IANA time zone database has a zone of that name."""
    if zone_named(name) is None:
        raise ValueError(f"{name} is not an IANA time zone, such as Europe/Paris")
    return name
