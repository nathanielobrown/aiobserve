"""Reading the trace store for one request: the connection, the queries, and a page of rows.

Every request opens its own read-only connection, checks the schema version, reads, and
closes — that is what lets an extract run while a page is open, and what makes a store under
someone else's write lock a 503 rather than a crash.

The three enums are the viewer's whole query catalog, split by what a query is allowed to
select: a page or a fragment truncates every fat column in SQL, and a per-value query is the
declared exception. Naming a query in one of them is what puts it in reach of the payload
scans (`tests/view/test_bounds.py`), so the union is also the checklist.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

import duckdb

from aiobserve.analyze import queries
from aiobserve.analyze.queries import ParamValue
from aiobserve.export.duckdb import SCHEMA_VERSION, held_schema_version

# DuckDB's wording when another process holds the store's write lock. Matched on text
# because the exception type it arrives as covers every other I/O failure too.
_LOCKED = "Conflicting lock is held"

Row = dict[str, Any]


class Page(StrEnum):
    """The library queries the pages are built from, by the part each one fills."""

    SESSIONS = "view_sessions"
    # The names the list's project filter offers, which is a column of the store rather than
    # of the page: the projects on one page of sessions are not the projects to filter by.
    PROJECTS = "view_projects"
    SESSION_HEADER = "view_session_header"
    RUN_HEADER = "view_run_header"
    # The two turn timelines, shared with `aiobserve query` — the same rows a report cites.
    # One query per thread kind: `session_digest` reads `main`, `run_digest` a bound source.
    TIMELINE = "session_digest"
    RUN_TIMELINE = "run_digest"
    RUNS = "view_runs"
    COMPACTIONS = "view_compactions"
    # One page of a thread's raw transcript, previewed a record per row.
    RECORDS = "view_records"


class Fragment(StrEnum):
    """The library queries htmx fetches a page of at a time, on expanding something."""

    # One page of the api calls under a turn, and one page of the tool calls under a call.
    TURN_CALLS = "view_turn_calls"
    CALL_TOOLS = "view_call_tools"


class Value(StrEnum):
    """The library queries that fetch one whole value: the exception to the page bound.

    Every other query truncates in SQL. These return a fat column untruncated because the
    unit *is* one value — the bound is the largest single value in the store, not a page's
    worth of them, and it is only reached when a reader opens that one value.
    """

    CALL_TEXT = "view_call_text"
    CALL_THINKING = "view_call_thinking"
    TOOL = "view_tool_value"
    RECORD = "view_record"


# Any of the three, for the fetch helper they share.
Library = Page | Fragment | Value


class StoreLocked(Exception):
    """Another process holds the store's write lock, so this request cannot read it."""


class SchemaMoved(Exception):
    """The store's schema version is not the one this build reads."""


@contextmanager
def open_store(db_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A read-only connection for one request, checked and closed.

    Raises `StoreLocked` when a writer holds the file and `SchemaMoved` when the store was
    re-created under the running viewer; both are checked per request rather than at startup
    because an extract can land between two page loads.
    """
    try:
        connection = duckdb.connect(str(db_path), read_only=True)
    except duckdb.IOException as error:
        if _LOCKED in str(error):
            raise StoreLocked(str(db_path)) from error
        raise
    try:
        # Timestamps went in as UTC; a page rendered in the machine's local zone would print
        # times that no citation of the same rows reproduces.
        connection.execute("SET TimeZone='UTC'")
        held = held_schema_version(connection)
        if held != SCHEMA_VERSION:
            raise SchemaMoved(f"{held or 'nothing'}")
        yield connection
    finally:
        connection.close()


def fetch(
    connection: duckdb.DuckDBPyConnection, sql: str, bindings: Mapping[str, ParamValue]
) -> list[Row]:
    """Run one statement and hand back its rows as dicts, keyed by column name."""
    cursor = connection.execute(sql, dict(bindings))
    columns = tuple(column[0] for column in cursor.description or ())
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def page_rows(
    connection: duckdb.DuckDBPyConnection, page: Library, **bindings: ParamValue
) -> list[Row]:
    """The rows of one library query, bound as given."""
    return fetch(connection, queries.load(page), bindings)


class Paged(NamedTuple):
    """One keyset page of a fragment: the rows, what is behind them, and where to resume."""

    rows: list[Row]
    # How many rows the cap cut, for the "+N more" the page shows instead of losing them.
    more: int
    # The `$after` cursor the next fetch binds, or None when this page is the last.
    after: int | None


def paged(rows: list[Row], matched: str, cursor: str) -> Paged:
    """A page of rows and its continuation, from a query's own pre-LIMIT match count.

    `matched` names the column carrying how many rows the cursor had ahead of it, which the
    paging queries compute with a window function — so a page knows what it cut without a
    second query, and cannot report "+0 more" for rows it silently dropped.
    """
    if not rows:
        return Paged(rows, 0, None)
    behind = rows[0][matched] - len(rows)
    return Paged(rows, behind, rows[-1][cursor] if behind else None)
