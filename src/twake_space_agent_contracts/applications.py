"""The applications whose contracts the service can publish, each declared once here: the domain
its contracts belong to, the words the harness names it with, and the routers of its contracts."""

from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass

import httpx
from fastapi import APIRouter
from psycopg_pool import AsyncConnectionPool

from twake_space_agent_contracts import (
    drive_contents,
    drive_create,
    drive_files,
    events,
    freebusy,
    invitations,
    task_reads,
)
from twake_space_agent_contracts.calendar import Calendar
from twake_space_agent_contracts.caller import CallerDependency
from twake_space_agent_contracts.chat import members, messages, rooms
from twake_space_agent_contracts.chat.synapse import Synapse
from twake_space_agent_contracts.drive import Drive, drive_owner_dependency
from twake_space_agent_contracts.mail import drafts, emails, mailboxes, move, threads, trash
from twake_space_agent_contracts.mail.tmail import TMail
from twake_space_agent_contracts.settings import Settings
from twake_space_agent_contracts.tasks import Tasks

# An application's entry in x-twake-domains: by level, name included, its words in each language
Description = dict[str, dict[str, str]]


@dataclass(frozen=True)
class Context:
    """What the routers of an application are built from."""

    settings: Settings
    pool: AsyncConnectionPool
    """The events database."""
    http: httpx.AsyncClient
    caller: CallerDependency
    clock: Callable[[], float]
    """Seconds, as time.monotonic counts them, by which what an application keeps for a while
    expires."""


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

    def described(self) -> Description:
        """Its entry in x-twake-domains, as the harness reads it."""
        levels = {"name": self.name, "read": self.read, "write": self.write}
        return {level: asdict(words) for level, words in levels.items() if words is not None}


def _calendar(context: Context) -> list[APIRouter]:
    calendar = Calendar(context.settings.calendar_url, context.http)
    return [
        freebusy.router(calendar, context.caller),
        invitations.router(context.pool, calendar, context.caller),
    ]


def _chat(context: Context) -> list[APIRouter]:
    url, server_name = context.settings.chat_url, context.settings.matrix_server_name
    # Required once Chat is published, and only then: the service runs before Chat goes live
    if url is None or server_name is None:
        missing = [
            name
            for name, value in (("CHAT_URL", url), ("MATRIX_SERVER_NAME", server_name))
            if value is None
        ]
        raise ValueError(f"PUBLISHED_APPS names chat, which needs {' and '.join(missing)}")
    # The users' mail domain is the server name unless told otherwise, as the harness maps them
    mail_domain = context.settings.matrix_mail_domain or server_name
    synapse = Synapse(url, server_name, mail_domain, context.http)
    return [
        rooms.router(synapse, context.caller),
        members.router(synapse, context.caller),
        messages.router(synapse, context.caller),
    ]


def _mail(context: Context) -> list[APIRouter]:
    # Needed once Mail is published only, so that the service starts without it otherwise
    if context.settings.mail_url is None:
        raise ValueError("PUBLISHED_APPS names mail, but MAIL_URL is not set")
    tmail = TMail(context.settings.mail_url, context.http, context.clock)
    return [
        mailboxes.router(tmail, context.caller),
        emails.router(tmail, context.caller),
        threads.router(tmail, context.caller),
        drafts.router(tmail, context.caller),
        move.router(tmail, context.caller),
        trash.router(tmail, context.caller),
    ]


def _drive(context: Context) -> list[APIRouter]:
    domain = context.settings.drive_instance_domain
    # Required once Drive is published, and only then: the service runs before Drive goes live
    if domain is None:
        raise ValueError("PUBLISHED_APPS names drive, which needs DRIVE_INSTANCE_DOMAIN")
    drive = Drive(context.settings, context.http)
    drive_owner = drive_owner_dependency(context.caller, domain)
    return [
        drive_files.router(drive, drive_owner),
        drive_contents.router(drive, drive_owner),
        drive_create.router(drive, drive_owner),
    ]


def _tasks(context: Context) -> list[APIRouter]:
    # A deployment that does not publish Tasks has no need to know where it is
    if context.settings.tasks_url is None:
        raise ValueError("PUBLISHED_APPS names tasks, which needs TASKS_URL")
    tasks = Tasks(context.settings.tasks_url, context.http)
    return [task_reads.router(tasks, context.caller)]


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
    Application(
        domain="chat",
        name=Words(en="Twake Chat", fr="Twake Chat"),
        read=Words(
            en="list your rooms and their members, and read the messages of your unencrypted rooms",
            fr="lister tes salons et leurs membres, et lire les messages de tes salons non"
            " chiffrés",
        ),
        write=None,
        routers=_chat,
    ),
    Application(
        domain="mail",
        name=Words(en="Twake Mail", fr="Twake Mail"),
        read=Words(en="list, search and read your mail", fr="lister, chercher et lire tes mails"),
        write=Words(
            en="prepare replies to your mail as drafts, which you send yourself, and move your"
            " mail between your folders, archive it or put it in the trash",
            fr="préparer des réponses à tes mails en brouillons, que tu envoies toi-même, et"
            " déplacer tes mails d'un dossier à l'autre, les archiver ou les mettre à la corbeille",
        ),
        routers=_mail,
    ),
    Application(
        domain="drive",
        name=Words(en="Twake Drive", fr="Twake Drive"),
        read=Words(
            en="list, search and read your files",
            fr="lister, chercher et lire tes fichiers",
        ),
        write=Words(
            en="create text files in your Drive, never in a folder shared with others",
            fr="créer des fichiers texte dans ton Drive, jamais dans un dossier partagé avec"
            " d'autres",
        ),
        routers=_drive,
    ),
    Application(
        domain="tasks",
        name=Words(en="Twake Tasks", fr="Twake Tasks"),
        read=Words(
            en="list, search and read your tasks",
            fr="lister, chercher et lire tes tâches",
        ),
        write=None,
        routers=_tasks,
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
