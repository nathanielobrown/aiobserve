"""What a viewer cell actually says: the whole rendered string, at each unit boundary.

These functions exist to produce text, so every leaf compares the whole text. Anything
less — a length, a substring, a page that merely rendered — leaves the text itself
unclaimed, which is how a table of them survived a mutation run untouched.

The values are constructed rather than drawn from the corpus: the interesting ones are the
boundaries, a millisecond either side of a minute and of an hour, and no recorded row sits
on one.
"""

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest

from aiobserve.view.format import ABSENT, clock, count, duration, money, when

# A moment in the store's zone, chosen for its single digits: a formatter that dropped the
# zero padding would render this one differently.
MOMENT = dt.datetime(2026, 8, 15, 9, 5, 3, tzinfo=dt.UTC)


@pytest.mark.parametrize("render", [money, count, when, clock, duration])
def test_a_column_the_store_left_null_reads_as_one_dash(render: Callable[[Any], str]) -> None:
    """Every cell a NULL reaches prints the same single character, not an empty cell."""
    assert render(None) == ABSENT


@pytest.mark.parametrize(
    ("value", "printed"),
    [
        (0.0, "$0.00"),
        # A cost below half a cent still prints as a cost, since the cell is money either way.
        (0.004, "$0.00"),
        (1.5, "$1.50"),
        # Dollars carry no thousands separator, unlike a count.
        (1234.5, "$1234.50"),
    ],
)
def test_a_cost_prints_in_dollars_at_cent_precision(value: float, printed: str) -> None:
    """A cost cell is a dollar sign and exactly two decimal places, whatever the scale."""
    assert money(value) == printed


@pytest.mark.parametrize(
    ("value", "printed"), [(0, "0"), (999, "999"), (1000, "1,000"), (1234567, "1,234,567")]
)
def test_a_count_separates_thousands(value: int, printed: str) -> None:
    """A count cell groups digits from four figures up, and is bare below that."""
    assert count(value) == printed


def test_a_timestamp_prints_to_the_minute_and_the_time_of_day_to_the_second() -> None:
    """The two clock cells: a dated one down to the minute, a bare one down to the second."""
    # A cell under no date heading carries the date, zero-padded, seconds dropped...
    assert when(MOMENT) == "2026-08-15 09:05"
    # ...and one under a dated heading carries the time alone, down to the second.
    assert clock(MOMENT) == "09:05:03"


@pytest.mark.parametrize(
    ("value", "printed"),
    [
        (0, "0.0s"),
        (800, "0.8s"),
        # A millisecond under a minute is still seconds — and rounds up to a printed 60.0s,
        # which is the seam's only oddity and the reason this value is pinned.
        (59_999, "60.0s"),
        # A minute exactly: the branch is `>=`, so this is the first row of the middle form.
        (60_000, "1m 00s"),
        (252_000, "4m 12s"),
        (3_599_999, "59m 59s"),
        # An hour exactly, the same boundary one form up.
        (3_600_000, "1h 00m"),
        (7_500_000, "2h 05m"),
        # A whole day: hours do not roll over into a larger unit.
        (86_400_000, "24h 00m"),
    ],
)
def test_a_duration_prints_in_the_two_largest_units_it_fills(value: int, printed: str) -> None:
    """Milliseconds read as hours and minutes, minutes and seconds, or seconds alone."""
    assert duration(value) == printed
