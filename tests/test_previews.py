"""What every preview shares, whatever its contract."""

import json

import pytest

from tests.conftest import HARNESS_LIMIT, harness_size
from twake_space_agent_contracts.previews import Preview, digest_of


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


def test_a_digest_takes_no_value_without_one_order() -> None:
    # Two replicas must find the same digest for the same thing: a set has no order they share,
    # and is refused rather than written in whichever its replica holds
    with pytest.raises(TypeError):
        digest_of({"WEB-1", "WEB-2"})
