"""drive.content.read.v1: the text of one of the user's files in Twake Drive."""

import codecs
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from twake_space_agent_contracts.documents import (
    KINDS,
    MOST_COLUMNS,
    MOST_PAGES,
    MOST_ROWS,
    Busy,
    Kind,
    Reader,
    Reason,
    Refused,
)
from twake_space_agent_contracts.drive import (
    DATA_NOT_INSTRUCTIONS,
    ITEM_ID,
    Drive,
    DriveOwner,
    DriveOwnerDependency,
    StackItem,
    file_blocked,
    file_not_found,
    plain_line,
    plain_text,
)
from twake_space_agent_contracts.problems import Problem
from twake_space_agent_contracts.text import seen

LARGEST = 262_144
DEFAULT = 65_536
# Text other than text/*; a note, although Markdown, holds more than its text
TEXT_TYPES = {"application/json", "application/xml", "application/x-yaml", "application/yaml"}
NOTE = "text/vnd.cozy.note+markdown"
LARGEST_DOCUMENT = 20_971_520
"""The bytes of a document the service reads at most, 20 MiB: a document is read whole, where a
text is read up to max_bytes."""


def is_text(mime: str) -> bool:
    return mime != NOTE and (mime.startswith("text/") or mime in TEXT_TYPES)


class FileText(BaseModel):
    """What people gave the file: its name, the type its uploader declared, and its text."""

    name: str
    mime: str
    content: str


class FileContent(BaseModel):
    id: str
    size: int
    truncated: bool
    untrusted: FileText


def not_extractable(detail: str) -> Problem:
    return Problem(
        status=415, code="content_not_extractable", title="Content not extractable", detail=detail
    )


def too_large(detail: str) -> Problem:
    return Problem(status=413, code="file_too_large", title="File too large", detail=detail)


def refusal(reason: Reason) -> Problem:
    """What the service says of a document whose text it cannot read: never what it holds."""
    match reason:
        case "encrypted":
            return Problem(
                status=409,
                code="file_encrypted",
                title="File encrypted",
                detail="The document is protected by a password: its text cannot be read.",
            )
        case "too_large":
            return too_large(
                "The document holds more than the service reads once uncompressed: its text "
                "cannot be read."
            )
        case "unreadable":
            return not_extractable(
                "The file cannot be read as the document its type says: it may be damaged."
            )
        case "no_text":
            return not_extractable(
                "The PDF holds no text: its pages may be images, as a scan's are, which the "
                "service does not read."
            )
        case "too_long":
            return not_extractable("Reading the document took too long: no text came of it.")
        case "memory":
            return not_extractable(
                "Reading the document took more memory than the service gives it: no text came "
                "of it."
            )


def reading_busy() -> Problem:
    return Problem(
        status=503,
        code="reading_busy",
        title="Reading busy",
        detail="The service is reading as many documents as it may at once, or one of the "
        "user's: try again in a few seconds.",
    )


def document_too_large() -> Problem:
    return too_large(
        f"The document takes more than the {LARGEST_DOCUMENT // 1_048_576} MiB the service "
        "reads: its text cannot be read."
    )


def router(drive: Drive, drive_owner: DriveOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/drive", tags=["drive.content.read.v1"])
    reader = Reader()

    async def text_of(file: StackItem, owner: DriveOwner, max_bytes: int) -> FileContent:
        """The first max_bytes bytes of a text file, and no more downloaded."""
        content = await drive.content(owner, file.id, max_bytes)
        if content is None:
            raise file_not_found(file.id)
        # A character cut at the end is left out, the bytes that cannot be read replaced
        text = codecs.getincrementaldecoder("utf-8")(errors="replace").decode(content)
        size = file.size if file.size is not None else len(content)
        return FileContent(
            id=file.id,
            size=size,
            truncated=size > max_bytes,
            untrusted=FileText(
                name=plain_line(file.name),
                mime=plain_line(file.mime or ""),
                content=plain_text(text),
            ),
        )

    async def document_text_of(
        file: StackItem, kind: Kind, owner: DriveOwner, max_bytes: int
    ) -> FileContent:
        """The first max_bytes bytes of a document's text, the document downloaded whole."""
        if file.size is not None and file.size > LARGEST_DOCUMENT:
            raise document_too_large()
        try:
            async with reader.turn(owner.user.email):
                content = await drive.content(owner, file.id, LARGEST_DOCUMENT + 1)
                if content is None:
                    raise file_not_found(file.id)
                if len(content) > LARGEST_DOCUMENT:
                    raise document_too_large()
                try:
                    read = await reader.read(kind, content, max_bytes)
                except Refused as refused:
                    raise refusal(refused.reason) from None
        except Busy:
            raise reading_busy() from None
        # Text others wrote, without what a reader does not see, nor control characters: no more
        # of it is cleaned than the max_bytes characters that hold max_bytes bytes, at most
        text = plain_text(seen(read.text[:max_bytes])).encode()
        return FileContent(
            id=file.id,
            size=file.size if file.size is not None else len(content),
            truncated=read.cut or len(read.text) > max_bytes or len(text) > max_bytes,
            untrusted=FileText(
                name=plain_line(file.name),
                mime=plain_line(file.mime or ""),
                # A character cut at the end is left out
                content=text[:max_bytes].decode(errors="ignore"),
            ),
        )

    @routes.get(
        "/contents/{file_id}",
        operation_id="read_file_content",
        summary="Read the text of one of the user's files",
        description=(
            "Reads the text of a file of the user's Drive, such as to summarise it. Plain text, "
            "Markdown, CSV, HTML, JSON, XML and YAML come as they are. A text document, Word's "
            "(docx) or OpenDocument's (odt), comes as its paragraphs, its headings marked as in "
            "Markdown (# Title, ## Section), its list items after a dash, and its tables as rows "
            "of cells parted by tabs. A presentation, PowerPoint's (pptx) or OpenDocument's "
            "(odp), comes slide by slide, each under a heading with its number and title, such "
            "as # Slide 2: Roadmap, then its text and tables, and its speaker notes under "
            "## Notes. A spreadsheet, Excel's (xlsx) or OpenDocument's (ods), comes sheet by "
            "sheet, each under a heading with its number and name, such as # Sheet 1: Budget, "
            "then a line for each row that holds values, its values parted by tabs: the values "
            f"computed, never the formulas, and only the first {MOST_ROWS} rows and "
            f"{MOST_COLUMNS} columns of a sheet, as a line between brackets then says. A PDF "
            "comes page by page, each under a heading with its number, such as # Page 3, from its "
            "text layer: a PDF of images, as a scan is, has none to give, and only its first "
            f"{MOST_PAGES} pages come. Other files, such as notes, documents over 20 MiB and those "
            "protected by a password cannot be read this way. At most max_bytes bytes of text "
            f"come back, {DEFAULT} by default: truncated is true when there is more. "
            f"{DATA_NOT_INSTRUCTIONS} Example: file_id=6494e0acdfcb11e588c1472e84a9cbee, "
            "max_bytes=65536."
        ),
    )
    async def read_file_content(
        file_id: Annotated[
            str,
            Path(
                pattern=ITEM_ID,
                description="The id of the file, as another Drive operation gave it.",
            ),
        ],
        owner: Annotated[DriveOwner, Depends(drive_owner)],
        max_bytes: Annotated[
            int,
            Query(
                ge=1,
                le=LARGEST,
                description=f"How many bytes of the file to read at most, {DEFAULT} by default.",
            ),
        ] = DEFAULT,
    ) -> FileContent:
        file = await drive.item(owner, file_id)
        if file is None or file.type != "file" or file.in_trash:
            raise file_not_found(file_id)
        # Encrypted on the user's devices: the stack holds no text to give
        if file.encrypted:
            raise Problem(
                status=409,
                code="file_encrypted",
                title="File encrypted",
                detail="The file is encrypted on the user's devices: its content cannot be read.",
            )
        if file.infected:
            raise file_blocked()
        mime = file.mime or ""
        if is_text(mime):
            return await text_of(file, owner, max_bytes)
        kind = KINDS.get(mime)
        if kind is None:
            raise not_extractable(
                "The file is neither text nor a document whose text the service reads."
            )
        return await document_text_of(file, kind, owner, max_bytes)

    return routes
