"""The process that reads a document, which the service runs for each:
python -m twake_space_agent_contracts.documents <kind> <budget> <seconds> <memory> <processor>,
the document on its standard input. It writes on its standard output, as JSON, the document's
text, {"text", "cut"}, or why it has none, {"refused"}."""

import contextlib
import json
import resource
import sys
import time
from collections.abc import Callable

from twake_space_agent_contracts.documents import opendocument, pdf, sheets, slides, word
from twake_space_agent_contracts.documents.reading import Full, OutOfTime, Output, Refusal

READERS: dict[str, Callable[[bytes, Output], None]] = {
    "docx": word.read,
    "pptx": slides.read,
    "xlsx": sheets.read,
    "pdf": pdf.read,
    "odt": opendocument.read_text,
    "ods": opendocument.read_spreadsheet,
    "odp": opendocument.read_presentation,
}


def read(kind: str, content: bytes, budget: int, seconds: float) -> dict[str, object]:
    """The answer for the document: its text, up to its budget and as far as it is read in these
    seconds and in the memory the process has, or why it has none."""
    output = Output(budget, time.monotonic() + seconds)
    # Why the reading stopped before the end of the document, if it did: the reason it is refused
    # for when it gave no text, and what the note that ends its text says otherwise
    stopped: tuple[str, str] | None = None
    try:
        READERS[kind](content, output)
    except Full:
        output.cut = True
    except OutOfTime:
        stopped = ("too_long", "reading it took too long")
    except Refusal as refusal:
        return {"refused": refusal.reason}
    except MemoryError:
        # The note is written past this clause: within it, the error still holds, through its
        # traceback, what the reading took, which may leave no memory for the note
        stopped = ("memory", "reading it took too much memory")
    except Exception:
        # Whatever a damaged document makes a parser raise, or one crafted against it
        return {"refused": "unreadable"}
    if stopped is not None:
        reason, why = stopped
        if output.empty:
            return {"refused": reason}
        output.stop(f"The rest of the document was not read: {why}.")
    return {"text": output.text(), "cut": output.cut}


def _bound(memory: int, processor: int) -> None:
    """Bounds what this process may take, whatever a document makes its parser do: its address
    space, past which an allocation fails, rather than the pod run out of memory; and its time on
    a processor, past which the kernel stops it. Only Linux, where the service runs, bounds an
    address space: macOS refuses to."""
    with contextlib.suppress(ValueError):
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    # Past the first bound, the kernel sends SIGXCPU, which stops the process; past the second,
    # SIGKILL
    resource.setrlimit(resource.RLIMIT_CPU, (processor, processor + 1))


def main() -> None:
    kind, budget, seconds = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
    _bound(memory=int(sys.argv[4]), processor=int(sys.argv[5]))
    try:
        answer = read(kind, sys.stdin.buffer.read(), budget, seconds)
        # In ASCII, which carries any text, a lone surrogate included
        sys.stdout.write(json.dumps(answer))
    except MemoryError:
        # The document itself takes more than the process may hold, or no memory is left to give
        # the answer: its text, its note or its JSON
        sys.stdout.write(json.dumps({"refused": "memory"}))


if __name__ == "__main__":
    main()
