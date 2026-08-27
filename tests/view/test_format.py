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
from functools import partial
from typing import Any

import pytest

from hyphae.view.format import (
    ABSENT,
    ELLIPSIS,
    ago,
    clock,
    count,
    cut,
    duration,
    money,
    path,
    share,
    text,
    when,
)

# Whose machine a page is being read on, for the filter that folds a home to `~`. Not this
# machine's: the fold has to be the same string wherever the suite runs.
HOME = "/Users/reader"

# A moment in the store's zone, chosen for its single digits: a formatter that dropped the
# zero padding would render this one differently.
MOMENT = dt.datetime(2026, 8, 15, 9, 5, 3, tzinfo=dt.UTC)

MINUTE = dt.timedelta(minutes=1)
HOUR = dt.timedelta(hours=1)
DAY = dt.timedelta(days=1)
SECOND = dt.timedelta(seconds=1)


@pytest.mark.parametrize(
    "render",
    [money, count, text, when, clock, duration, partial(ago, now=MOMENT), partial(path, home=HOME)],
)
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


@pytest.mark.parametrize(
    ("value", "printed"),
    [
        # A moment this second, and one a second before the first unit fills, read the same:
        # a list refreshed while a session runs should not flicker between "0m" and "1m".
        (MOMENT, "just now"),
        (MOMENT - SECOND, "just now"),
        (MOMENT - MINUTE + SECOND, "just now"),
        # A minute exactly is the first row of the next form up, and so is an hour and a day.
        (MOMENT - MINUTE, "1m ago"),
        (MOMENT - HOUR + SECOND, "59m ago"),
        (MOMENT - HOUR, "1h ago"),
        (MOMENT - DAY + SECOND, "23h ago"),
        (MOMENT - DAY, "1d ago"),
        (MOMENT - 3 * DAY, "3d ago"),
        # Days do not roll over into a larger unit: a corpus is read in days, and "2mo" hides
        # which two months.
        (MOMENT - 400 * DAY, "400d ago"),
        # A timestamp in the future is clock skew between the machine that wrote the session
        # and the one reading it. Invented — no recorded session carries one — and it reads as
        # the present rather than as a negative unit.
        (MOMENT + HOUR, "just now"),
    ],
)
def test_an_elapsed_time_prints_in_the_largest_unit_it_fills(
    value: dt.datetime, printed: str
) -> None:
    """How long ago something happened reads as one unit and the word "ago"."""
    assert ago(value, MOMENT) == printed


@pytest.mark.parametrize(
    ("part", "whole", "printed"),
    [
        (1, 45, "2.2%"),
        # A real zero numerator is a rate someone can act on — no errors in five calls — so it
        # prints rather than reading as a gap.
        (0, 5, "0.0%"),
        (1, 1, "100.0%"),
    ],
)
def test_a_rate_prints_one_decimal_of_a_percent(part: float, whole: float, printed: str) -> None:
    """A share cell is a percentage to one decimal place, zero included."""
    assert share(part, whole) == printed


@pytest.mark.parametrize(
    ("part", "whole"),
    [
        # Nothing to count...
        (None, 45),
        # ...nothing to count against...
        (1, None),
        # ...and the one the store actually produces: a session that cost nothing, whose
        # share of its own spend is a gap rather than 0%.
        (1, 0),
    ],
)
def test_a_rate_over_nothing_is_a_gap_rather_than_zero(
    part: float | None, whole: float | None
) -> None:
    """A rate with no numerator, no denominator or nothing to divide into reads as absent."""
    assert share(part, whole) == ABSENT


@pytest.mark.parametrize(
    ("value", "printed"),
    [
        # The reader's own home, folded: the list is scanned for which project a session was
        # in, and nine characters of a path every row repeats are not that.
        (f"{HOME}/repos/hyphae", "~/repos/hyphae"),
        # The home itself, which is a project directory like any other.
        (HOME, "~"),
        # Someone else's home under the same parent. A prefix match on the string would fold
        # this one too, and `~` on another user's directory is a claim about this machine that
        # the session does not support.
        ("/Users/readerly/repos/hyphae", "/Users/readerly/repos/hyphae"),
        # A path the reader's home has nothing to do with — a session recorded on another
        # machine, or one under a shared checkout — prints as the store holds it.
        ("/srv/checkouts/hyphae", "/srv/checkouts/hyphae"),
        ("/Users", "/Users"),
    ],
)
def test_a_project_under_the_readers_home_prints_with_a_tilde(value: str, printed: str) -> None:
    """A directory cell folds the reader's own home and leaves every other path alone."""
    assert path(value, HOME) == printed


# What a value looks like at a cut boundary. Invented rather than drawn from the corpus: the
# boundary is one character wide and no recorded prompt, tool name or task brief sits on it.
CUT_AT = 8


@pytest.mark.parametrize(
    ("value", "printed"),
    [
        # Shorter than the width, so the query that fetched `$n + 1` characters got the whole
        # value back and the page says so by saying nothing.
        ("&" * (CUT_AT - 1), "&" * (CUT_AT - 1)),
        # Exactly the width: still whole. A cut query selects one character past what a page
        # shows, so a value that fills the width and stops is a value with nothing behind it.
        ("&" * CUT_AT, "&" * CUT_AT),
        # One over, which is the only signal there is — the extra character the query fetched
        # came back, so something was left behind and the page shows the width plus a mark.
        ("&" * (CUT_AT + 1), "&" * CUT_AT + ELLIPSIS),
        # Far over, which reads the same: how much more there is is a `length()` column's
        # answer, in the one place a page offers the rest of a value.
        ("&" * 400, "&" * CUT_AT + ELLIPSIS),
        ("", ""),
    ],
)
def test_a_value_wider_than_its_column_prints_cut_with_an_ellipsis(
    value: str, printed: str
) -> None:
    """A cut string carries the mark that says it was cut, and an uncut one carries nothing."""
    assert cut(value, CUT_AT) == printed
