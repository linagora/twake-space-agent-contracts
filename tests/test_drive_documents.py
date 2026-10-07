"""What keeps reading documents safe, whatever their kind: files other people may have written,
some of them crafted to take down whatever reads them."""

from httpx import AsyncClient

from tests.documents import DOCX, read_content, rezipped, word
from tests.fakes import FakeBoundary, text_file
from twake_space_agent_contracts.drive_contents import LARGEST_DOCUMENT

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _laughs() -> str:
    """Entities that expand ten times each into the next, a thousand million times in all."""
    entities = ['<!ENTITY lol0 "lol">']
    for level in range(1, 10):
        references = f"&lol{level - 1};" * 10
        entities.append(f'<!ENTITY lol{level} "{references}">')
    return "".join(entities)


BILLION_LAUGHS = (
    f'<?xml version="1.0"?><!DOCTYPE w:document [{_laughs()}]>'
    f'<w:document xmlns:w="{WORD_NAMESPACE}">'
    "<w:body><w:p><w:r><w:t>&lol9;</w:t></w:r></w:p></w:body></w:document>"
).encode()
# What an external entity would bring in from the service's own files
EXTERNAL_ENTITY = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE w:document [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
    f'<w:document xmlns:w="{WORD_NAMESPACE}">'
    "<w:body><w:p><w:r><w:t>&secret;</w:t></w:r></w:p></w:body></w:document>"
).encode()


async def test_a_part_that_declares_entities_is_not_read(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = word(lambda document: document.add_paragraph("Plans"))
    boundary.drive.add(
        text_file(
            "bomb",
            "Bomb.docx",
            content=rezipped(content, {"word/document.xml": BILLION_LAUGHS}),
            mime=DOCX,
        ),
        text_file(
            "entity",
            "Entity.docx",
            content=rezipped(content, {"word/document.xml": EXTERNAL_ENTITY}),
            mime=DOCX,
        ),
    )

    for file_id in ("bomb", "entity"):
        response = await read_content(client, file_id)

        # Refused at its declaration, before any entity is expanded or fetched
        assert response.status_code == 415, response.text
        assert response.json()["code"] == "content_not_extractable"
        assert "lol" not in response.text
        assert "root:" not in response.text


async def test_a_document_larger_than_the_service_reads_is_refused_unread(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = word(lambda document: document.add_paragraph("Plans"))
    boundary.drive.add(
        text_file("big", "Big.docx", content=content, mime=DOCX, size=LARGEST_DOCUMENT + 1)
    )

    response = await read_content(client, "big")

    assert response.status_code == 413, response.text
    assert response.json()["code"] == "file_too_large"
    assert boundary.drive.downloads == []


async def test_a_document_larger_than_its_size_says_is_refused(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    content = word(lambda document: document.add_paragraph("Plans"))
    padded = content + b"\0" * (LARGEST_DOCUMENT + 1 - len(content))
    boundary.drive.add(text_file("big", "Big.docx", content=padded, mime=DOCX, size=len(content)))

    response = await read_content(client, "big")

    # The stack's size aside, no more is downloaded than the service reads
    assert response.status_code == 413, response.text
    assert response.json()["code"] == "file_too_large"
