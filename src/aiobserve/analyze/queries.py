"""The query library: one `.sql` file per question, and the manifest that says how to run it.

A finding cites the query behind it, so a question lives in a versioned file a report can
name and anyone can re-run — not in a Python string. Two consumers share this library: the
`aiobserve query` runner and the trace viewer (`plans/trace-viewer/design.md`).

The manifest is production code. It gives every parameter a query declares either a
production default — the value a bare invocation runs, and the value a committed report
quotes — or `REQUIRED`, for a choice the caller has to make: a defaulted line range on
`records_slice` would quietly hand back a window of raw transcript instead of an error.

Adding a query means adding its file *and* its manifest entry; the smoke tier fails on
either half alone.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path

QUERY_DIR = Path(__file__).parent / "queries"

# The trailing window every corpus count is reported in beside the full corpus. Fixed, not
# decayed: a window is a filter a reader can re-run and argue with.
WINDOW_DAYS = 28


class Scope(StrEnum):
    """What a query is asking about, which decides what the runner has to give it."""

    # Counts across sessions: takes `--project`, and reads the corpus predicate and the
    # trailing window through the runner's `project_sessions` table.
    CORPUS = "corpus"
    # A fetch keyed by `session_id`/`source`. Exempt from both — a corpus predicate on
    # `WHERE session_id = $session_id` is noise.
    KEYED = "keyed"


class ParamType(StrEnum):
    """How a `--param` string becomes the value DuckDB binds."""

    TEXT = "text"
    INTEGER = "integer"
    DATE = "date"


class NoDefault(Enum):
    """Marker for a parameter with no default: the caller must bind it or get an error."""

    REQUIRED = "required"


REQUIRED = NoDefault.REQUIRED

# What a bound parameter can be. NULL is a real default — `$since` unset means the whole
# corpus — so absence cannot stand in for "required", which is why `REQUIRED` is a marker.
ParamValue = str | int | dt.date | None


@dataclass(frozen=True)
class Param:
    """One DuckDB named parameter a query file declares."""

    type: ParamType
    # The production default, or REQUIRED when no sensible one exists.
    default: ParamValue | NoDefault


@dataclass(frozen=True)
class Query:
    """What the runner needs to know about one `.sql` file to bind and scope it."""

    scope: Scope
    params: Mapping[str, Param]


QUERIES: dict[str, Query] = {
    "session_counts": Query(scope=Scope.CORPUS, params={}),
    "sessions": Query(scope=Scope.CORPUS, params={}),
    "weekly_trend": Query(scope=Scope.CORPUS, params={}),
}


def load(name: str) -> str:
    """The SQL text of one library query, by file stem."""
    return (QUERY_DIR / f"{name}.sql").read_text()
