"""The applications whose contracts the service can publish, each declared once here: the domain
its contracts belong to and the routers of its contracts."""

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

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
class Application:
    domain: str
    """The first segment of its contracts' ids, such as calendar in calendar.freebusy.read.v1:
    what an owner allows their assistant to read, or to write, in."""
    routers: Callable[[Context], Sequence[APIRouter]]
    """The routers of its contracts, one per contract."""


def _calendar(context: Context) -> list[APIRouter]:
    calendar = Calendar(context.settings.calendar_url, context.http)
    return [
        freebusy.router(calendar, context.caller),
        invitations.router(context.pool, calendar, context.caller),
    ]


APPLICATIONS = (
    Application(
        domain="events",
        routers=lambda context: [events.router(context.pool, context.caller)],
    ),
    Application(domain="calendar", routers=_calendar),
)


def published(domains: Collection[str]) -> list[Application]:
    """The applications of these domains, in the order they are declared in."""
    unknown = ", ".join(sorted(set(domains) - {application.domain for application in APPLICATIONS}))
    if unknown:
        raise ValueError(f"PUBLISHED_APPS names applications the service does not have: {unknown}")
    return [application for application in APPLICATIONS if application.domain in domains]
