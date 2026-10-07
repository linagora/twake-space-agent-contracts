"""The process that reads a document, which the service runs for each:
python -m twake_space_agent_contracts.documents <kind> <budget> <seconds>, the document on its
standard input. It writes on its standard output, as JSON, the document's text, {"text", "cut"},
or why it has none, {"refused"}."""

import contextlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from twake_space_agent_contracts.documents import slides, word
from twake_space_agent_contracts.documents.reading import Full, OutOfTime, Output, Refusal

READERS: dict[str, Callable[[bytes, Output], None]] = {"docx": word.read, "pptx": slides.read}


def read(kind: str, content: bytes, budget: int, seconds: float) -> dict[str, object]:
    """The answer for the document: its text, up to its budget and as far as it is read in these
    seconds, or why it has none."""
    output = Output(budget, time.monotonic() + seconds)
    try:
        READERS[kind](content, output)
    except Full:
        output.cut = True
    except OutOfTime:
        if output.empty:
            return {"refused": "too_long"}
        output.stop("The rest of the document was not read: reading it took too long.")
    except Refusal as refusal:
        return {"refused": refusal.reason}
    except Exception:
        # Whatever a damaged document makes a parser raise, or one crafted against it
        return {"refused": "unreadable"}
    return {"text": output.text(), "cut": output.cut}


def _first_to_go() -> None:
    """Makes this process the first the kernel stops when the memory runs out, rather than the
    service: a document that takes all the memory stops its own reading only."""
    with contextlib.suppress(OSError):
        Path("/proc/self/oom_score_adj").write_text("1000")


def main() -> None:
    kind, budget, seconds = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
    _first_to_go()
    answer = read(kind, sys.stdin.buffer.read(), budget, seconds)
    # In ASCII, which carries any text, a lone surrogate included
    sys.stdout.write(json.dumps(answer))


if __name__ == "__main__":
    main()
