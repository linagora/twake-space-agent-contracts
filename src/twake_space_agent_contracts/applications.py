"""The applications whose contracts the service can publish, each declared once here: the domain
its contracts belong to, the words the harness names it with, and the routers of its contracts."""

from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass

import httpx
from fastapi import APIRouter
from psycopg_pool import AsyncConnectionPool

from twake_space_agent_contracts import events, freebusy, invitations
from twake_space_agent_contracts.calendar import Calendar
from twake_space_agent_contracts.caller import CallerDependency
from twake_space_agent_contracts.settings import Settings


@dataclass(frozen=True)
class Context:
    """What the routers of an application are built from."""

    settings: Settings
    pool: AsyncConnectionPool
    """The events database."""
    http: httpx.AsyncClient
    caller: CallerDependency


@dataclass(frozen=True)
class Words:
    """Words for the owner in each language the harness speaks: plain text on one line, without a
    final period."""

    en: str
    fr: str


@dataclass(frozen=True)
class Application:
    domain: str
    """The first segment of its contracts' ids, such as calendar in calendar.freebusy.read.v1:
    what an owner allows their assistant to read, or to write, in."""
    name: Words
    """How the harness names it to the owner, in 64 characters at most."""
    read: Words | None
    """What reading covers there, in 200 characters at most; None when no contract reads."""
    write: Words | None
    """What writing covers there, in 200 characters at most; None when no contract writes."""
    routers: Callable[[Context], Sequence[APIRouter]]
    """The routers of its contracts, one per contract."""

    def described(self) -> dict[str, dict[str, str]]:
        """Its entry in x-twake-domains, as the harness reads it."""
        levels = {"name": self.name, "read": self.read, "write": self.write}
        return {level: asdict(words) for level, words in levels.items() if words is not None}


def _calendar(context: Context) -> list[APIRouter]:
    calendar = Calendar(context.settings.calendar_url, context.http)
    return [
        freebusy.router(calendar, context.caller),
        invitations.router(context.pool, calendar, context.caller),
    ]


APPLICATIONS = (
    Application(
        domain="events",
        name=Words(en="Workplace events", fr="Événements de l'espace de travail"),
        read=Words(
            en="read the events of your workplace that concern you, such as your invitations",
            fr="lire les événements de ton espace de travail qui te concernent, comme tes"
            " invitations",
        ),
        write=None,
        routers=lambda context: [events.router(context.pool, context.caller)],
    ),
    Application(
        domain="calendar",
        name=Words(en="Twake Calendar", fr="Twake Agenda"),
        read=Words(
            en="see your free and busy times in your calendars",
            fr="voir tes créneaux libres et occupés dans tes agendas",
        ),
        write=Words(
            en="accept the invitations you received, which tells their organizer",
            fr="accepter les invitations que tu as reçues, ce qui prévient leur organisateur",
        ),
        routers=_calendar,
    ),
)


# The assistant's own feed of workplace events, which the harness reads without asking and checks
# the invitations it brings with: published whatever PUBLISHED_APPS says
ALWAYS_PUBLISHED = frozenset({"events"})


def published(domains: Collection[str]) -> list[Application]:
    """The applications of these domains and those always published, in the order they are
    declared in."""
    unknown = ", ".join(sorted(set(domains) - {application.domain for application in APPLICATIONS}))
    if unknown:
        raise ValueError(f"PUBLISHED_APPS names applications the service does not have: {unknown}")
    return [
        application
        for application in APPLICATIONS
        if application.domain in domains or application.domain in ALWAYS_PUBLISHED
    ]
