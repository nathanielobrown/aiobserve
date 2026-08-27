"""How the pages print numbers and times. Registered as Jinja filters by `app.build_app`.

Every one of these takes None, because a store column that can be NULL reaches the template
as None and an empty cell says less than a dash.
"""

import datetime as dt
from pathlib import Path, PurePosixPath

# What a page prints where the store holds nothing. One character, so a column of them reads
# as a gap rather than as a value.
ABSENT = "—"

# What a page prints where it cut a value short. One character, and the only thing that tells
# a value that ended from a value that was stopped.
ELLIPSIS = "…"

_MINUTE = 60_000
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR


def home() -> str:
    """The directory `path` folds to `~` — the one place the pages ask whose machine this is.

    A wrapper rather than a call at the point of use, so a test can say who is reading.
    """
    return str(Path.home())


def utcnow() -> dt.datetime:
    """The clock the pages read, in the store's zone — the one place that asks for it.

    A wrapper rather than a call at each point of use, so that a page's freshness is
    testable: a test moves this, and what the next render prints moves with it.
    """
    return dt.datetime.now(dt.UTC)


def money(value: float | None) -> str:
    """A cost in dollars, at cent precision — the scale a session is read at."""
    return ABSENT if value is None else f"${value:.2f}"


def count(value: int | None) -> str:
    """A count, with thousands separated."""
    return ABSENT if value is None else f"{value:,}"


def signed(value: int | None) -> str:
    """A change, with its sign kept: `+30,442`, `-80,900`.

    What a count prints as where the number is a difference. A delta printed bare reads as a
    total, and the negative one — a turn that compacted, and gave the window back — is the
    reading the bar beside it cannot draw (`docs/viewer.md`).
    """
    return ABSENT if value is None else f"{value:+,}"


def charge(value: float | None) -> str:
    """One category of a cost, at the precision a category is worth: `$0.0431`.

    Four digits rather than the two `money` prints, because the parts of a cost are routinely
    under a cent apiece — a legend in cents would be three zeroes and an answer.
    """
    return ABSENT if value is None else f"${value:.4f}"


def cut(value: str, size: int) -> str:
    """A string at the width a page reads it, marked where the rest was left behind.

    The one-extra-character protocol every cut query rides: a query selects `$n + 1`
    characters, so a value longer than `n` is a value with more behind it. How much more is a
    `length()` column's answer, beside the head, wherever a page offers the rest of a value.
    """
    return value[:size] + ELLIPSIS if len(value) > size else value


def text(value: str | None) -> str:
    """A string column as a cell: whatever the store holds, or the dash a NULL prints."""
    return ABSENT if value is None else value


def path(value: str | None, home: str) -> str:
    """A directory as a cell, with the reader's own home folded to `~`.

    Only the reader's home, and only whole segments of it: a directory under someone else's is
    theirs, and `~` over it would be a claim about this machine that the session cannot
    support. Display alone — every link, filter and datalist carries the path the store holds.
    """
    if value is None:
        return ABSENT
    here, root = PurePosixPath(value), PurePosixPath(home)
    if not here.is_relative_to(root):
        return value
    rest = here.relative_to(root)
    return "~" if rest == PurePosixPath(".") else f"~/{rest}"


def when(value: dt.datetime | None) -> str:
    """A timestamp in the store's zone (UTC), to the minute."""
    return ABSENT if value is None else value.strftime("%Y-%m-%d %H:%M")


def clock(value: dt.datetime | None) -> str:
    """A timestamp as time of day, for rows already under a dated heading."""
    return ABSENT if value is None else value.strftime("%H:%M:%S")


def duration(value: int | None) -> str:
    """Milliseconds as a span someone reads: `2h 05m`, `4m 12s`, `0.8s`."""
    if value is None:
        return ABSENT
    if value >= _HOUR:
        return f"{value // _HOUR}h {value % _HOUR // _MINUTE:02d}m"
    if value >= _MINUTE:
        return f"{value // _MINUTE}m {value % _MINUTE // 1000:02d}s"
    return f"{value / 1000:.1f}s"


def ago(value: dt.datetime | None, now: dt.datetime) -> str:
    """How long before `now` something happened, in the largest unit it fills: `3d ago`.

    One unit, not two: a list is scanned for which session is recent, and `3d 4h` answers a
    question nobody asked of it. `now` is passed in rather than read here so the caller —
    the filter `app.build_app` registers, or a test — decides which clock the page is against.
    """
    if value is None:
        return ABSENT
    elapsed = int((now - value).total_seconds() * 1000)
    # A timestamp ahead of the reader's clock is skew between the machine that wrote the
    # session and the one showing it, and the present is the honest reading of it.
    if elapsed < _MINUTE:
        return "just now"
    if elapsed < _HOUR:
        return f"{elapsed // _MINUTE}m ago"
    if elapsed < _DAY:
        return f"{elapsed // _HOUR}h ago"
    return f"{elapsed // _DAY}d ago"


def percent(value: float | None) -> str:
    """A fraction as a percentage, to one decimal: `0.022` prints `2.2%`.

    The printing half of `share`, for a rate something else already worked out. A page reaches
    it through `share`, which is the filter every rate on screen goes through.
    """
    return ABSENT if value is None else f"{100 * value:.1f}%"


def share(part: float | None, whole: float | None) -> str:
    """A part of a whole as a percentage, to one decimal: `2.2%`.

    A whole of zero or NULL is a gap rather than 0%: no errors in no tool calls, and no spend
    to take a share of, are things the store does not know rather than rates it recorded.
    """
    return ABSENT if part is None or not whole else percent(part / whole)
