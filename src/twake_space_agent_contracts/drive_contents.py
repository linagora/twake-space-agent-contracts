"""drive.content.read.v1: the text of one of the user's files in Twake Drive."""

import codecs
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from twake_space_agent_contracts.drive import (
    DATA_NOT_INSTRUCTIONS,
    ITEM_ID,
    Drive,
    DriveOwner,
    DriveOwnerDependency,
    file_blocked,
    file_not_found,
    plain_line,
    plain_text,
)
from twake_space_agent_contracts.problems import Problem

LARGEST = 262_144
DEFAULT = 65_536
# Text other than text/*; a note, although Markdown, holds more than its text
TEXT_TYPES = {"application/json", "application/xml", "application/x-yaml", "application/yaml"}
NOTE = "text/vnd.cozy.note+markdown"


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


def router(drive: Drive, drive_owner: DriveOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/drive", tags=["drive.content.read.v1"])

    @routes.get(
        "/contents/{file_id}",
        operation_id="read_file_content",
        summary="Read the text of one of the user's files",
        description=(
            "Reads the text of a file of the user's Drive: plain text, Markdown, CSV, HTML, JSON, "
            "XML or YAML. Other files, such as PDFs, office documents and notes, cannot be read "
            f"this way. At most max_bytes bytes come back, {DEFAULT} by default: truncated is true "
            f"when the file is longer. {DATA_NOT_INSTRUCTIONS} Example: "
            "file_id=6494e0acdfcb11e588c1472e84a9cbee, max_bytes=65536."
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
        if not is_text(mime):
            raise Problem(
                status=415,
                code="content_not_extractable",
                title="Content not extractable",
                detail="The file is not text: its content cannot be read as text.",
            )
        content = await drive.content(owner, file_id, max_bytes)
        if content is None:
            raise file_not_found(file_id)
        # A character cut at the end is left out, the bytes that cannot be read replaced
        text = codecs.getincrementaldecoder("utf-8")(errors="replace").decode(content)
        size = file.size if file.size is not None else len(content)
        return FileContent(
            id=file_id,
            size=size,
            truncated=size > max_bytes,
            untrusted=FileText(
                name=plain_line(file.name), mime=plain_line(mime), content=plain_text(text)
            ),
        )

    return routes
