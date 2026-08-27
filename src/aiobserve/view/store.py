"""Reading the trace store for one request: the connection, the queries, and a page of rows.

Every request opens its own read-only connection, checks the schema version, reads, and
closes — that is what lets an extract run while a page is open, and what makes a store under
someone else's write lock a 503 rather than a crash.

The three enums are the viewer's whole query catalog, split by what a query is allowed to
select: a page or a fragment truncates every fat column in SQL, and a per-value query is the
declared exception. Naming a query in one of them is what puts it in reach of the payload
scans (`tests/view/test_bounds.py`), so the union is also the checklist.
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

import duckdb

from aiobserve.analyze import macros, queries
from aiobserve.analyze.queries import ParamValue
from aiobserve.export.duckdb import SCHEMA_VERSION, held_schema_version

# DuckDB's wording when another process holds the store's write lock. Matched on text
# because the exception type it arrives as covers every other I/O failure too.
_LOCKED = "Conflicting lock is held"

Row = dict[str, Any]

# The column both turn timelines are ordered by: unique and ascending within one thread, and
# NULL on the row standing for the calls that answer no turn, which rides no page of them.
TURN_CURSOR = "turn_index"


class Page(StrEnum):
    """The library queries the pages are built from, by the part each one fills."""

    SESSIONS = "view_sessions"
    # Every project the store holds sessions for, which is the landing page: the counts a
    # reader lands on are a corpus's, so they come from the `corpus_*` views.
    PROJECT_ROLLUPS = "view_project_rollups"
    # The names the list's project filter offers, which is a column of the store rather than
    # of the page: the projects on one page of sessions are not the projects to filter by.
    PROJECTS = "view_projects"
    SESSION_HEADER = "view_session_header"
    # Every failed tool call of one session, across every thread — the one page the tree
    # cannot lead to, because a failure is scattered rather than nested (`view/errors.py`).
    SESSION_ERRORS = "view_session_errors"
    # One node read whole, the header of its own page. One per kind that has fields of its
    # own; a bucket has none, and a compaction reads out of `view_compactions`.
    RUN_HEADER = "view_run_header"
    TURN_HEADER = "view_turn_header"
    CALL_HEADER = "view_call_header"
    TOOL_HEADER = "view_tool_header"
    # The levels of the tree beside a node page: one thin row per child, whatever the level
    # holds. One query per kind of child rather than per kind of parent, so a turn's calls
    # are read the same way under a session, under a run, or under a bucket.
    TREE_TURNS = "view_tree_turns"
    TREE_CALLS = "view_tree_calls"
    TREE_TOOLS = "view_tree_tools"
    # The two turn timelines, shared with `aiobserve query` — the same rows a report cites.
    # One query per thread kind: `session_timeline` reads `main`, `run_timeline` a bound source.
    TIMELINE = "session_timeline"
    RUN_TIMELINE = "run_timeline"
    RUNS = "view_runs"
    COMPACTIONS = "view_compactions"
    # What an enrichment pass said about the session, its turns and its runs. Absent from a
    # store no pass has written to, which is why `view/enrichment.py` asks before it runs.
    ENRICHMENT = "view_enrichment"
    # The same for the list: what the pass said each session was, joined to the page of rows
    # the list just read. Absent from an un-enriched store for the same reason.
    DESCRIBED_SESSIONS = "view_described_sessions"
    # One page of a thread's raw transcript, previewed a record per row, and the line each of
    # the thread's turns was read from — what turns a timeline row into a link into it.
    RECORDS = "view_records"
    TURN_RECORDS = "view_turn_records"
    # One chunk of a tool result written to a file beside the transcript.
    OFFLOAD = "view_offload"


class Fragment(StrEnum):
    """The library queries htmx fetches a page of at a time, on expanding something."""

    # One page of the api calls under a turn, and one page of the tool calls under a call.
    TURN_CALLS = "view_turn_calls"
    CALL_TOOLS = "view_call_tools"
    # The numbers behind one tree row, fetched when a reader points at it: what the row draws
    # as a bar and a badge, written out. One query for every kind made of api calls, and one
    # for the tool call, which is made of none.
    NUMBERS = "view_numbers"
    TOOL_NUMBERS = "view_numbers_tool"


class Value(StrEnum):
    """The library queries that fetch one whole value: the exception to the page bound.

    Every other query truncates in SQL. These return a fat column untruncated because the
    unit *is* one value — the bound is the largest single value in the store, not a page's
    worth of them, and it is only reached when a reader opens that one value.
    """

    CALL_TEXT = "view_call_text"
    CALL_THINKING = "view_call_thinking"
    # What one tool call was asked and what it returned, one value each rather than the row
    # whole: a pane previews the two apart, so each has its own way to the rest of it.
    TOOL_INPUT = "view_tool_input"
    TOOL_RESULT = "view_tool_result"
    # And what a `Bash` call ran, which the input holds escaped onto one line: a value of its
    # own because a shell command is read as shell, not as a string inside JSON.
    TOOL_COMMAND = "view_tool_command"
    RECORD = "view_record"
    # What a turn was asked, what followed the command a slash turn ran, and what an agent
    # run was briefed with. Each is a value a pane previews, cut in the node's header query
    # and fetched whole here.
    TURN_PROMPT = "view_turn_prompt"
    TURN_COMMAND_ARGS = "view_turn_command_args"
    RUN_BRIEF = "view_run_brief"
    # And the two a run's page reads off the call that spawned it: what that call asked for,
    # and what it returned to the agent that made it.
    RUN_PROMPT = "view_run_prompt"
    RUN_RESULT = "view_run_result"
    # And the two lines an enrichment pass wrote about an item, one query per level: what the
    # model said the item did, and the friction it saw in it. Fat for the same reason the rest
    # are — a pass writes as much as it wants to — and previewed on the pane the same way.
    TURN_SAID = "view_turn_said"
    RUN_SAID = "view_run_said"
    SESSION_SAID = "view_session_said"


# Any of the three, for the fetch helper they share.
Library = Page | Fragment | Value


class StoreLocked(Exception):
    """Another process holds the store's write lock, so this request cannot read it."""


class SchemaMoved(Exception):
    """The store's schema version is not the one this build reads."""


@contextmanager
def open_store(db_path: Path) -> Generator[duckdb.DuckDBPyConnection]:
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
        # The library's shared SQL functions, which several of the queries below call by name.
        macros.install(connection)
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
    """One keyset page: the rows, what is behind them, and where to resume.

    The records browser's way of paging, and the only one left: a citation names a line, so the
    page for it is the one that *starts* at that line rather than the nth page of the thread.
    """

    rows: list[Row]
    # How many rows the cap cut, for the "+N more" the page shows instead of losing them.
    more: int
    # The `$after` cursor the next fetch binds, or None when this page is the last.
    after: int | None


class Listed(NamedTuple):
    """One numbered page of a level: the rows, and how many the level holds in all.

    `total` is the count before the LIMIT bit, which is what lets a page say which of how many
    it is — and what lets a heading count the level rather than the rows in front of the reader.
    """

    rows: list[Row]
    total: int


# What the composed window counts its pre-LIMIT matches into. A name of the composition and
# not of any library query, which is what lets the query stay unlimited and citable.
MATCHED_ROWS = "matched_rows"


def _core(page: Library) -> str:
    """One library query as a subquery: its own text, unchanged, ready to be wrapped."""
    return queries.load(page).strip().rstrip(";")


def window(
    connection: duckdb.DuckDBPyConnection,
    page: Library,
    cursor: str,
    skipped: int,
    size: int,
    **bindings: ParamValue,
) -> Listed:
    """One numbered page of a library query that limits nothing itself.

    The session list's composition (`view/listing.py`) for the other case: a query whose
    whole result a report quotes cannot carry a viewer's LIMIT, so the viewer wraps it. Rows
    come back ordered by `cursor`, which is a column name this package supplies — never
    request text — while `skipped` and `size` bind. A row the query gives no cursor value is
    outside every page and outside the count (`cursorless_rows`).
    """
    rows = fetch(
        connection,
        f"SELECT *, count(*) OVER () AS {MATCHED_ROWS} FROM ({_core(page)})"
        f" WHERE {cursor} IS NOT NULL ORDER BY {cursor} LIMIT $size OFFSET $skipped",
        {"skipped": skipped, "size": size, **bindings},
    )
    return listed(rows, MATCHED_ROWS)


def thread_outline(
    connection: duckdb.DuckDBPyConnection, page: Library, cursor: str, **bindings: ParamValue
) -> list[Row]:
    """A whole thread in outline — a timeline's rows, id and cursor and clock only.

    Two questions need the thread and not the page: which runs the session could place, and
    which page each compaction falls on. Both are cheap here because the projection is three
    scalars; neither can be answered from a window without changing what the answer means.
    """
    return fetch(
        connection,
        f"SELECT turn_id, {cursor}, started_at FROM ({_core(page)}) ORDER BY {cursor} NULLS LAST",
        bindings,
    )


def cursorless_rows(
    connection: duckdb.DuckDBPyConnection,
    page: Library,
    cursor: str,
    limit: int,
    **bindings: ParamValue,
) -> list[Row]:
    """The rows a paged query gives no cursor value, which no window can reach.

    The timelines' unattributed row is the case: it stands for the calls that answer no turn,
    so it has no turn index and rides the last page instead. `limit` is what the page that
    renders them budgeted; a query answering with more raises, because these rows arrive
    outside the size the reader asked for and a page that serves them anyway is a page whose
    ceiling was computed against something else.
    """
    rows = fetch(
        connection,
        f"SELECT * FROM ({_core(page)}) WHERE {cursor} IS NULL LIMIT $cursorless",
        {"cursorless": limit + 1, **bindings},
    )
    if len(rows) > limit:
        raise ValueError(f"{page} gave more than {limit} row(s) with no {cursor}")
    return rows


def listed(rows: list[Row], matched: str) -> Listed:
    """A page of rows and the size of the level it came from, out of the query's own count.

    `matched` names the column carrying how many rows matched before the LIMIT, which the
    paging queries compute with a window function — so a page knows the whole level without a
    second query, and a level whose page is empty is one whose pages ran out.
    """
    return Listed(rows, rows[0][matched] if rows else 0)


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
