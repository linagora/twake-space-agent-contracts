"""Documents as the user's Drive may hold them, built in the tests: by the library that writes their
kind, or by hand for what no library writes, such as a part a test replaces."""

import io
import zipfile
from collections.abc import Callable
from typing import Any

from docx import Document as new_word_document
from docx.document import Document as WordDocument
from docx.oxml import parse_xml
from httpx import AsyncClient, Response

from tests.fakes import as_drive_owner

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# The prefixes the WordprocessingML a test adds may use
WORD_NAMESPACES = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "v": "urn:schemas-microsoft-com:vml",
}


async def read_content(client: AsyncClient, file_id: str, **params: Any) -> Response:
    """The text of the file, as the owner's assistant reads it."""
    return await client.get(
        f"/contracts/v1/drive/contents/{file_id}", params=params, headers=as_drive_owner()
    )


def word(build: Callable[[WordDocument], object]) -> bytes:
    """A Word document as python-docx writes it, from its default template, which Word made."""
    document = new_word_document()
    build(document)
    written = io.BytesIO()
    document.save(written)
    return written.getvalue()


def add_word_xml(document: WordDocument, xml: str) -> None:
    """Adds a block written in WordprocessingML, such as a paragraph with a tracked change, which
    python-docx cannot write, at the end of the body. Its first tag declares the prefixes of
    WORD_NAMESPACES."""
    declarations = " ".join(f'xmlns:{prefix}="{uri}"' for prefix, uri in WORD_NAMESPACES.items())
    tag_end = xml.index(">")
    # The section properties stay last, as Word writes them
    sections = document.element.body.sectPr
    assert sections is not None
    sections.addprevious(parse_xml(f"{xml[:tag_end]} {declarations}{xml[tag_end:]}"))


def rezipped(content: bytes, parts: dict[str, bytes]) -> bytes:
    """The zip with these parts, by name, in place of its own or added, the others as they were."""
    parts = dict(parts)
    written = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(content)) as original,
        zipfile.ZipFile(written, "w", zipfile.ZIP_DEFLATED) as copy,
    ):
        for entry in original.infolist():
            part = parts.pop(entry.filename) if entry.filename in parts else original.read(entry)
            copy.writestr(entry.filename, part)
        for name, part in parts.items():
            copy.writestr(name, part)
    return written.getvalue()


def part_of(content: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read(name)
