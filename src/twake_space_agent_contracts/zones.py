"""IANA time zones: the names the contracts take, when a day starts in one, and how iCalendar
describes one for the times of an event."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

ZONE = r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$"
"""An IANA time zone name, such as Europe/Paris or Etc/GMT+1: a pattern any OpenAPI validator
checks."""

JCAL_TIME = "%Y-%m-%dT%H:%M:%S"
"""How jCal writes a time of day in a time zone, and, with a Z after it, in UTC."""

FIRST_TIME = datetime(1, 1, 2, tzinfo=UTC)
"""The first time every time zone can show, a day after the first datetime holds, an offset from
UTC being less than a day: some zones are more than 14 hours from UTC in year 1."""
LAST_TIME = datetime(9999, 12, 31, tzinfo=UTC)
"""The last time every time zone can show, a day before the last datetime holds."""


def formatted(moment: datetime, pattern: str) -> str:
    """A time written in a strftime pattern, such as JCAL_TIME, its year in four digits on every
    platform: glibc's strftime writes a year before 1000 in fewer, as 1-01-01, which jCal and
    esn-sabre do not read."""
    return moment.strftime(pattern.replace("%Y", f"{moment.year:04d}"))


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


def bounded(moment: datetime) -> datetime:
    """An aware time, or for one before or after those every time zone can show, the first or last
    of them, which any zone can then show."""
    return min(max(moment, FIRST_TIME), LAST_TIME)


def exact(moment: datetime) -> datetime:
    """An aware time at an offset RFC 3339 writes, and pydantic, to the minute: as it is; in UTC
    when its offset counts seconds, as zones did before they kept to whole minutes, such as Paris,
    9 minutes 21 seconds ahead of UTC until 1911; or, for one before or after the times UTC holds,
    at its offset rounded away from UTC to the minute, the time moved as much."""
    offset = moment.utcoffset() or timedelta(0)
    seconds = offset % timedelta(minutes=1)
    if not seconds:
        return moment
    try:
        return moment.astimezone(UTC)
    except OverflowError:
        # Only an offset ahead of UTC puts a time before the first it holds, and only one behind
        # it after the last: rounded away from UTC, it moves the time back among them
        rounded = offset - seconds
        if offset > timedelta(0):
            rounded += timedelta(minutes=1)
        return (moment.replace(tzinfo=None) + (rounded - offset)).replace(tzinfo=timezone(rounded))


def midnight(day: date, zone: ZoneInfo) -> datetime:
    """When the day starts in the zone: at midnight, or, on a day whose midnight the clocks skip,
    when they go forward; on the first day datetime holds, whose midnight is before its first time
    in a zone ahead of UTC, the first time every zone can show."""
    # A midnight is less than a day from that of UTC: only that of the first day can fall out
    if day == date.min:
        return FIRST_TIME.astimezone(zone)
    return datetime.combine(day, time(), zone).astimezone(UTC).astimezone(zone)


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
    onset = formatted(change + offset_from, JCAL_TIME)
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
        onset = formatted(steady, JCAL_TIME)
        rules = [_observance(local, "standard", onset, steady.utcoffset() or timedelta(0), since)]
    rules += [_rule_from(local, change) for change in changes if change > start]
    return ["vtimezone", [["tzid", {}, "text", zone]], rules]
