"""IANA time zones: the names the contracts take, and how iCalendar describes one for the times of
an event."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ZONE = r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$"
"""An IANA time zone name, such as Europe/Paris or Etc/GMT+1: a pattern any OpenAPI validator
checks."""

JCAL_TIME = "%Y-%m-%dT%H:%M:%S"
"""How jCal writes a time of day in a time zone, and, with a Z after it, in UTC."""


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


def _utc_offset(offset: timedelta) -> str:
    """An offset from UTC as jCal writes it, such as +02:00, with its seconds when it has some."""
    sign = "-" if offset < timedelta(0) else "+"
    hours, rest = divmod(abs(int(offset.total_seconds())), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{sign}{hours:02d}:{minutes:02d}" + (f":{seconds:02d}" if seconds else "")


def _changes_of_time(zone: ZoneInfo, since: datetime, until: datetime) -> list[datetime]:
    """When the zone's offset from UTC changes between two times: found from one day to the next,
    then to the second, which the time zone database counts in."""

    def offset(seconds: int) -> timedelta | None:
        return (since + timedelta(seconds=seconds)).astimezone(zone).utcoffset()

    changes: list[datetime] = []
    day = 86_400
    probe, last = 0, int((until - since).total_seconds())
    while probe < last:
        later = min(probe + day, last)
        if offset(probe) != offset(later):
            before, after = probe, later
            while after - before > 1:
                middle = (before + after) // 2
                if offset(middle) == offset(probe):
                    before = middle
                else:
                    after = middle
            changes.append((since + timedelta(seconds=after)).astimezone(UTC))
        probe = later
    return changes


def _observance(
    zone: ZoneInfo, kind: str, onset: str, offset_from: timedelta, moment: datetime
) -> list[Any]:
    local = moment.astimezone(zone)
    return [
        kind,
        [
            ["dtstart", {}, "date-time", onset],
            ["tzoffsetfrom", {}, "utc-offset", _utc_offset(offset_from)],
            ["tzoffsetto", {}, "utc-offset", _utc_offset(local.utcoffset() or timedelta(0))],
            ["tzname", {}, "text", local.tzname() or zone.key],
        ],
        [],
    ]


def _rule_from(zone: ZoneInfo, change: datetime) -> list[Any]:
    """The rule of the zone from that change of offset on: summer time or not, and its onset
    written in the time it replaces."""
    offset_from = (change - timedelta(seconds=1)).astimezone(zone).utcoffset() or timedelta(0)
    kind = "daylight" if change.astimezone(zone).dst() else "standard"
    onset = (change + offset_from).strftime(JCAL_TIME)
    return _observance(zone, kind, onset, offset_from, change)


def vtimezone(zone: str, start: datetime, end: datetime) -> list[Any]:
    """The time zone as iCalendar describes it for an event between two times: the rule in force
    when the event starts, from the last change of offset within the year before, then the rule
    from each change until it ends. A zone that kept one offset all that year has one rule, from
    the year's start."""
    local = ZoneInfo(zone)
    since = start - timedelta(days=366)
    changes = _changes_of_time(local, since, end)
    in_force = [change for change in changes if change <= start][-1:]
    rules = [_rule_from(local, change) for change in in_force]
    if not rules:
        steady = since.astimezone(local)
        onset = steady.strftime(JCAL_TIME)
        rules = [_observance(local, "standard", onset, steady.utcoffset() or timedelta(0), since)]
    rules += [_rule_from(local, change) for change in changes if change > start]
    return ["vtimezone", [["tzid", {}, "text", zone]], rules]
