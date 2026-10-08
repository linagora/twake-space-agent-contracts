"""What the previews of Calendar's writes tell alike: when an event takes place, as the owner reads
it."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from twake_space_agent_contracts.calendar import CalendarEvent, EventTime
from twake_space_agent_contracts.previews import Language, day, one_line, quoted, time_of_day
from twake_space_agent_contracts.zones import bounded


@dataclass(frozen=True)
class _Words:
    """How a preview tells when an event takes place, in one language."""

    at: str
    between: str
    across: str
    all_day: str
    days: str
    zoned: str


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        at="{day} à {time}",
        between="{day} de {start} à {end}",
        across="du {first} à {start} au {last} à {end}",
        all_day="{day}, toute la journée",
        days="du {first} au {last}",
        zoned="{when} (fuseau {zone})",
    ),
    "en": _Words(
        at="{day} at {time}",
        between="{day} from {start} to {end}",
        across="from {first} at {start} to {last} at {end}",
        all_day="{day}, all day",
        days="from {first} to {last}",
        zoned="{when} (time zone {zone})",
    ),
}


def _shown_time(moment: EventTime, zone: ZoneInfo | None) -> tuple[datetime, str | None]:
    """A time as the owner reads it, in their zone, else in its own, with the zone to name beside
    it; the first or last time every zone can show for one before or after them, as the list of
    events gives it. A floating time as the event writes it, with the zone to name, if any."""
    assert isinstance(moment.value, datetime)
    if moment.value.tzinfo is None:
        return moment.value, moment.zone
    shown = bounded(moment.value)
    if zone is not None:
        return shown.astimezone(zone), None
    return shown.astimezone(moment.value.tzinfo), moment.zone


def _days(start: date, end: EventTime | None, language: Language) -> str:
    """When an event of whole days takes place: its end is the day after its last, and one that is
    not after its first, such as 0001-01-01, or that is a time, ends it on that day."""
    words = _WORDS[language]
    if end is None or isinstance(end.value, datetime) or end.value - start <= timedelta(days=1):
        return words.all_day.format(day=day(start, language))
    last = end.value - timedelta(days=1)
    return words.days.format(first=day(start, language), last=day(last, language))


def when_it_takes_place(
    event: CalendarEvent, zone: ZoneInfo | None, language: Language
) -> str | None:
    """When the event takes place, as the owner reads it, in their zone when Calendar gives it;
    None when the event does not say."""
    start, end = event.starts, event.ends
    if start is None:
        return None
    if not isinstance(start.value, datetime):
        return _days(start.value, end, language)
    words = _WORDS[language]
    begins, named = _shown_time(start, zone)
    first, at = day(begins.date(), language), time_of_day(begins.time(), language)
    ending = None
    if end is not None and isinstance(end.value, datetime):
        ending = _shown_time(end, zone)[0]
    # An end written otherwise than its start, floating against zoned, tells nothing sure
    if ending is not None and (ending.tzinfo is None) != (begins.tzinfo is None):
        ending = None
    # A zoned end in the zone of the start, which the preview names for both
    if ending is not None and ending.tzinfo is not None:
        ending = ending.astimezone(begins.tzinfo)
    if ending is None or ending <= begins:
        when = words.at.format(day=first, time=at)
    elif ending.date() == begins.date():
        when = words.between.format(day=first, start=at, end=time_of_day(ending.time(), language))
    else:
        when = words.across.format(
            first=first,
            start=at,
            last=day(ending.date(), language),
            end=time_of_day(ending.time(), language),
        )
    # The zone the event names, which whoever wrote it chose
    return words.zoned.format(when=when, zone=quoted(one_line(named), language)) if named else when
