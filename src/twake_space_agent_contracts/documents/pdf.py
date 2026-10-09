"""PDF documents: page by page, each under a heading that numbers it, the text of their text layer,
as pypdf extracts it. A page that only shows images, as a scan's do, gives none. Only the first
MOST_PAGES pages are read, as a note says first."""

import io

import pypdf
from pypdf.errors import FileNotDecryptedError

from twake_space_agent_contracts.documents import MOST_PAGES
from twake_space_agent_contracts.documents.reading import (
    Encrypted,
    NoText,
    Output,
    Unreadable,
)


def read(content: bytes, output: Output) -> None:
    # pypdf runs no program of its own, such as the decoder of JBIG2 images it may look for
    pypdf.overwrite_configuration(jbig2dec_binary=None)
    try:
        # A PDF its owner only restricts opens with an empty password, which pypdf tries
        pages = pypdf.PdfReader(io.BytesIO(content)).pages
        count = len(pages)
    except FileNotDecryptedError as error:
        raise Encrypted("the PDF is protected by a password") from error
    if count > MOST_PAGES:
        output.note(f"Only its first {MOST_PAGES} pages, of {count}, are given.")
    # The pages without text, said once a page with text comes
    untold: list[int] = []
    found = damaged = False
    for number in range(1, min(count, MOST_PAGES) + 1):
        try:
            lines = _lines(pages[number - 1])
        except MemoryError:
            # Not a damaged page: its reading takes more memory than the process is given, which
            # stops the reading there, as when its time runs out
            raise
        except Exception:
            # Whatever a damaged page makes pypdf raise: the other pages may still be read
            damaged = True
            lines = []
        untold.append(number)
        output.tick()
        if lines:
            for page in untold:
                output.gap()
                output.add(f"# Page {page}")
            untold.clear()
            output.add("\n".join(lines))
            found = True
    if not found:
        if damaged:
            raise Unreadable("no page could be read")
        raise NoText("no page holds text")
    for page in untold:
        output.gap()
        output.add(f"# Page {page}")


def _lines(page: pypdf.PageObject) -> list[str]:
    """The lines of the page's text, without the blank lines around them."""
    lines = [line.rstrip() for line in page.extract_text().splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines
