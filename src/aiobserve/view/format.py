"""How the pages print numbers and times. Registered as Jinja filters by `app.build_app`.

Every one of these takes None, because a store column that can be NULL reaches the template
as None and an empty cell says less than a dash.
"""

import datetime as dt

# What a page prints where the store holds nothing. One character, so a column of them reads
# as a gap rather than as a value.
ABSENT = "—"

_MINUTE = 60_000
_HOUR = 60 * _MINUTE


def money(value: float | None) -> str:
    """A cost in dollars, at cent precision — the scale a session is read at."""
    return ABSENT if value is None else f"${value:.2f}"


def count(value: int | None) -> str:
    """A count, with thousands separated."""
    return ABSENT if value is None else f"{value:,}"


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
