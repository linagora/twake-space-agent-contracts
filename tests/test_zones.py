"""How the contracts write the times of a calendar, whatever the platform."""

from datetime import UTC, datetime

import pytest

from twake_space_agent_contracts.calendar import SABRE_TIME
from twake_space_agent_contracts.zones import JCAL_TIME, formatted


@pytest.mark.parametrize(
    ("moment", "pattern", "written"),
    [
        pytest.param(
            datetime(1, 1, 1, tzinfo=UTC),
            f"{JCAL_TIME}Z",
            "0001-01-01T00:00:00Z",
            id="jCal, year 1",
        ),
        pytest.param(
            datetime(999, 1, 1, tzinfo=UTC),
            SABRE_TIME,
            "09990101T000000Z",
            id="esn-sabre, year 999",
        ),
        pytest.param(
            datetime(2026, 10, 9, 8, 30, 15, tzinfo=UTC),
            SABRE_TIME,
            "20261009T083015Z",
            id="esn-sabre, year 2026",
        ),
    ],
)
def test_a_time_is_written_with_a_year_of_four_digits(
    moment: datetime, pattern: str, written: str
) -> None:
    # glibc's strftime writes a year before 1000 in fewer digits, as 1-01-01, which jCal and
    # esn-sabre do not read; macOS writes four
    assert formatted(moment, pattern) == written
