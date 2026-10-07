"""What every preview shares, whatever its contract."""

import json

from tests.conftest import HARNESS_LIMIT, harness_size
from twake_space_agent_contracts.previews import Preview


def test_no_summary_takes_more_than_the_harness_shows() -> None:
    # Each contract keeps its summary within bounds: should one fail to, the harness would still
    # show it, cut, and say so
    preview = Preview(asked=True, language="en", shown=None)

    answer = json.loads(bytes(preview.answer("<&>" * 4_000, "sha256:0").body))

    summary = answer["summary"]
    shown, cut = summary.rsplit("\n", 1)
    assert harness_size(summary) <= HARNESS_LIMIT
    assert shown == ("<&>" * 4_000)[: len(shown)]
    assert len(shown) > 2_000
    assert cut == f"(cut here: {12_000 - len(shown):,} more characters are not shown)"
