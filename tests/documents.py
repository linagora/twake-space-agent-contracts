"""Documents as the user's Drive may hold them, built in the tests: by the library that writes their
kind, or by hand for what no library writes, such as a part a test replaces."""

import io
import struct
import zipfile
from collections.abc import Callable
from typing import Any

from docx import Document as new_word_document
from docx.document import Document as WordDocument
from docx.oxml import parse_xml
from httpx import AsyncClient, Response
from openpyxl import Workbook
from pptx import Presentation as new_presentation
from pptx.presentation import Presentation
from pypdf import PdfReader, PdfWriter

from tests.fakes import as_drive_owner

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"

# The prefixes the WordprocessingML a test adds may use
WORD_NAMESPACES = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "v": "urn:schemas-microsoft-com:vml",
}
# The styles of a document that knows only Normal, where Word's template knows hundreds
FEW_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{WORD_NAMESPACES["w"]}">'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    "</w:style></w:styles>"
).encode()


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


def presentation(build: Callable[[Presentation], object]) -> bytes:
    """A presentation as python-pptx writes it, from its default template, which PowerPoint
    made."""
    deck = new_presentation()
    build(deck)
    written = io.BytesIO()
    deck.save(written)
    return written.getvalue()


def workbook(build: Callable[[Workbook], object]) -> bytes:
    """A workbook as openpyxl writes it: its text in each cell, inline, and no value for its
    formulas, which only a spreadsheet application computes."""
    book = Workbook()
    build(book)
    written = io.BytesIO()
    book.save(written)
    return written.getvalue()


_SPREADSHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PACKAGE = "http://schemas.openxmlformats.org/package/2006"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def excel_workbook(rows: str, strings: list[str], *, date1904: bool = False) -> bytes:
    """A workbook of one sheet, Sheet1, written as Excel writes one: the text of its cells in a
    table of shared strings, of which each string is the content of an si element, the value of
    each formula as last computed beside it, and its rows those of sheetData. A cell of style 1
    shows a day, in Excel's built-in format 14, one of style 2 a day and a time."""
    shared = "".join(f"<si>{string}</si>" for string in strings)
    links = [
        ("rId1", "worksheet", "worksheets/sheet1.xml"),
        ("rId2", "sharedStrings", "sharedStrings.xml"),
        ("rId3", "styles", "styles.xml"),
    ]
    parts = {
        "[Content_Types].xml": f'<Types xmlns="{_PACKAGE}/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="{_PACKAGE}/relationships">'
        f'<Relationship Id="rId1" Type="{_OFFICE}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>",
        "xl/workbook.xml": f'<workbook xmlns="{_SPREADSHEET}" xmlns:r="{_OFFICE}">'
        f'<workbookPr date1904="{int(date1904)}"/>'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{_PACKAGE}/relationships">'
        + "".join(
            f'<Relationship Id="{id}" Type="{_OFFICE}/{kind}" Target="{target}"/>'
            for id, kind, target in links
        )
        + "</Relationships>",
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{_SPREADSHEET}">'
        f"<sheetData>{rows}</sheetData></worksheet>",
        "xl/sharedStrings.xml": f'<sst xmlns="{_SPREADSHEET}">{shared}</sst>',
        "xl/styles.xml": f'<styleSheet xmlns="{_SPREADSHEET}">'
        r'<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy\ hh:mm"/></numFmts>'
        '<cellStyleXfs count="1"><xf numFmtId="0"/></cellStyleXfs>'
        '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/></cellXfs>'
        "</styleSheet>",
    }
    written = io.BytesIO()
    with zipfile.ZipFile(written, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, part in parts.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8"?>' + part)
    return written.getvalue()


def pdf(*pages: str | None) -> bytes:
    """A PDF of these pages, each showing its text in Helvetica, a line under the other, or None
    for a page that shows no text, as a scan's image does, here a grey rectangle. Its objects:
    the catalog, the page tree, the font, then each page and its content stream."""
    kids = " ".join(f"{4 + 2 * number} 0 R" for number in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for number, text in enumerate(pages):
        if text is None:
            drawn = b"0.5 g 72 72 468 648 re f"
        else:
            shown = (_pdf_string(line) + b" Tj T*" for line in text.split("\n"))
            drawn = b"BT /F1 12 Tf 72 720 Td 14 TL " + b" ".join(shown) + b" ET"
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + f"{5 + 2 * number} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(drawn)} >>\nstream\n".encode() + drawn + b"\nendstream")
    written = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(written))
        written += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    table = len(written)
    written += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    written += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    written += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    written += f"startxref\n{table}\n%%EOF\n".encode()
    return bytes(written)


def _pdf_string(text: str) -> bytes:
    """Text as a PDF string between parentheses, in the font's encoding, close to Latin-1."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"(" + escaped.encode("latin-1") + b")"


def encrypted_pdf(content: bytes, *, user_password: str, owner_password: str) -> bytes:
    """The PDF encrypted with AES-256 as pypdf encrypts it: opened with the user's password,
    which may be empty, its owner's password then only limiting what readers may do."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(content)))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="AES-256")
    written = io.BytesIO()
    writer.write(written)
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


def declaring(content: bytes, name: str, *, size: int, compressed: int | None = None) -> bytes:
    """The zip with the part of that name declaring these sizes in the zip's directory, whatever
    it holds: the sizes a zip is unpacked by."""
    data = bytearray(content)
    # The end of a zip without a comment says where its directory starts
    (offset,) = struct.unpack_from("<I", data, len(data) - 22 + 16)
    while data[offset : offset + 4] == b"PK\x01\x02":
        name_size, extra_size, comment_size = struct.unpack_from("<3H", data, offset + 28)
        if data[offset + 46 : offset + 46 + name_size].decode() == name:
            if compressed is not None:
                struct.pack_into("<I", data, offset + 20, compressed)
            struct.pack_into("<I", data, offset + 24, size)
            return bytes(data)
        offset += 46 + name_size + extra_size + comment_size
    raise AssertionError(f"No part {name} in the zip")


# What a compound file, the container of Office's older documents, says of its sectors
_SECTOR = 512
_FREE = 0xFFFFFFFF
_END_OF_CHAIN = 0xFFFFFFFE
_FAT_SECTOR = 0xFFFFFFFD
_NO_STREAM = 0xFFFFFFFF


def compound_file(*streams: tuple[str, bytes]) -> bytes:
    """A compound file (MS-CFB, version 3), the container of Office's older documents, holding
    these streams, of 4 KiB each at most: its allocation table in its first sector, its directory
    in the next, then each stream in sectors of its own."""
    table = [_FAT_SECTOR, _END_OF_CHAIN]
    starts = []
    for _, data in streams:
        starts.append(len(table))
        sectors = -(-len(data) // _SECTOR)
        table += [len(table) + n + 1 for n in range(sectors - 1)] + [_END_OF_CHAIN]
    table += [_FREE] * (_SECTOR // 4 - len(table))
    header = bytearray(_SECTOR)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<5H", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    # One sector of allocation table, the directory in sector 1, no mini stream, no DIFAT sector
    struct.pack_into("<3I", header, 0x2C, 1, 1, 0)
    struct.pack_into("<5I", header, 0x38, 4096, _END_OF_CHAIN, 0, _END_OF_CHAIN, 0)
    struct.pack_into("<109I", header, 0x4C, 0, *[_FREE] * 108)
    directory = _entry("Root Entry", 5, child=1, start=_END_OF_CHAIN, size=0)
    # Siblings in a tree by name, shorter names first, each the next one's left
    for number, (name, data) in enumerate(streams, start=1):
        right = number + 1 if number < len(streams) else _NO_STREAM
        directory += _entry(name, 2, right=right, start=starts[number - 1], size=len(data))
    directory += bytes(-len(directory) % _SECTOR)
    padded = [data + bytes(-len(data) % _SECTOR) for _, data in streams]
    return bytes(header + struct.pack(f"<{_SECTOR // 4}I", *table) + directory + b"".join(padded))


def encrypted_office_document() -> bytes:
    """An Office Open XML document protected by a password, as Office writes it: no zip, but a
    compound file that holds how it is encrypted, in EncryptionInfo, and the zip encrypted, in
    EncryptedPackage."""
    return compound_file(
        ("EncryptionInfo", bytes(4096)), ("EncryptedPackage", bytes(range(256)) * 16)
    )


def encrypting(content: bytes, name: str) -> bytes:
    """The zip with the file of that name saying it is encrypted, as a zip encrypted with a
    password does: in its own header, and in the zip's directory."""
    data = bytearray(content)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        header = archive.getinfo(name).header_offset
    # The flags of a file's header, then of its entry in the directory
    data[header + 6] |= 1
    (offset,) = struct.unpack_from("<I", data, len(data) - 22 + 16)
    while data[offset : offset + 4] == b"PK\x01\x02":
        name_size, extra_size, comment_size = struct.unpack_from("<3H", data, offset + 28)
        if data[offset + 46 : offset + 46 + name_size].decode() == name:
            data[offset + 8] |= 1
        offset += 46 + name_size + extra_size + comment_size
    return bytes(data)


def _entry(
    name: str,
    kind: int,
    *,
    start: int,
    size: int,
    child: int = _NO_STREAM,
    right: int = _NO_STREAM,
) -> bytes:
    """An entry of a compound file's directory, black in its tree of siblings."""
    entry = bytearray(128)
    named = (name + "\0").encode("utf-16-le")
    entry[: len(named)] = named
    struct.pack_into("<HBB3I", entry, 0x40, len(named), kind, 1, _NO_STREAM, right, child)
    struct.pack_into("<IQ", entry, 0x74, start, size)
    return bytes(entry)
