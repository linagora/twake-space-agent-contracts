"""The process that reads a document, which the service runs for each:
python -m twake_space_agent_contracts.documents <kind> <budget>, the document on its standard
input. It writes on its standard output, as JSON, the document's text, {"text", "cut"}, or why it
has none, {"refused"}."""

import contextlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

from twake_space_agent_contracts.documents import word
from twake_space_agent_contracts.documents.reading import Full, Output, Refusal

READERS: dict[str, Callable[[bytes, Output], None]] = {"docx": word.read}


def read(kind: str, content: bytes, budget: int) -> dict[str, object]:
    """The answer for the document: its text, up to its budget, or why it has none."""
    output = Output(budget)
    try:
        READERS[kind](content, output)
    except Full:
        output.cut = True
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
    kind, budget = sys.argv[1], int(sys.argv[2])
    _first_to_go()
    answer = read(kind, sys.stdin.buffer.read(), budget)
    # In ASCII, which carries any text, a lone surrogate included
    sys.stdout.write(json.dumps(answer))


if __name__ == "__main__":
    main()
