"""The trace viewer's app: routes, the store connection, and the sort composition.

`build_app(db_path)` returns a FastAPI app over one trace store; `serve` runs it. Nothing
here writes: every request opens its own read-only connection, checks the store's schema
version, renders, and closes. That is what lets an extract run while a page is open, and
what makes a locked store a 503 rather than a crash.

The pages are built from library queries (`analyze/queries/`) — the viewer composes sort and
filter *around* a query's SELECT and binds every user-supplied value as a parameter, so no
request text ever reaches SQL. `Page` names the queries whole pages read, which is also the
set the payload bound scans (`tests/view/test_bounds.py`).
"""

import datetime as dt
import socket
import webbrowser
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple, assert_never
from urllib.parse import urlencode

import duckdb
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from aiobserve.analyze import queries
from aiobserve.analyze.queries import ParamValue
from aiobserve.export.duckdb import SCHEMA_VERSION, held_schema_version
from aiobserve.model import MAIN_SOURCE
from aiobserve.view import format as fmt
from aiobserve.view import render

# Loopback only, and a port unlikely to be taken. Fixed rather than picked at startup so a
# link pasted into a note opens the same page tomorrow.
HOST = "127.0.0.1"
PORT = 8477

_PACKAGE = Path(__file__).parent
TEMPLATES = _PACKAGE / "templates"
STATIC = _PACKAGE / "static"

# Nothing loads from anywhere but this app: no CDN, no inline script, no remote font. The
# viewer renders text a transcript wrote, so the escaping is the first defence and this is
# the second.
CSP = "default-src 'self'"

# DuckDB's wording when another process holds the store's write lock. Matched on text
# because the exception type it arrives as covers every other I/O failure too.
_LOCKED = "Conflicting lock is held"


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


# Any of the three, for the fetch helper they share. Declaring a query in one of the enums is
# what puts it in reach of the payload scans, so the union is also the checklist.
Library = Page | Fragment | Value


# What the session list can be sorted by: a column of `view_sessions`, mapped to its header
# label. A closed dictionary, and the only place a request's `sort` value is ever looked up —
# an unknown key is a 400, never a fragment of SQL. `tests/view/test_app.py` checks every
# key against the columns the query returns.
SORTS: dict[str, str] = {
    "started_at": "Started",
    "title": "Session",
    "project_dir": "Project",
    "turns": "Turns",
    "api_calls": "Calls",
    "tool_calls": "Tools",
    "tool_errors": "Errors",
    "agent_runs": "Runs",
    "compactions": "Compactions",
    "cost_usd": "Cost",
    "output_tokens": "Output",
    "wall_ms": "Wall",
    "active_ms": "Active",
}


@dataclass(frozen=True)
class Filter:
    """One way the session list can be narrowed, as the two halves that make it safe."""

    # The predicate composed into the WHERE, naming its own bound parameter and nothing else.
    # It reads a column of `view_sessions`, which is what the composition wraps.
    predicate: str
    # What a request's value has to parse as before it can bind. A value that will not parse
    # is a 400, so the type is also the only vetting a filter value gets.
    type: queries.ParamType


# What the session list can be narrowed by, per query-string key. Closed, like `SORTS`: a key
# outside it is a 400, and a key inside it contributes a fixed predicate and a bound value —
# request text never becomes SQL. Composed in this order, so the WHERE and the citation read
# the same whatever order a URL happened to put them in.
FILTERS: dict[str, Filter] = {
    "project": Filter("project_dir = $project", queries.ParamType.TEXT),
    "since": Filter("started_at >= $since", queries.ParamType.DATE),
    # Inclusive of the day named: someone asking for sessions until the 7th means the 7th.
    "until": Filter("started_at < $until + INTERVAL 1 DAY", queries.ParamType.DATE),
    "skill": Filter("list_contains(skills, $skill)", queries.ParamType.TEXT),
    # A floor rather than a flag, so `errors=1` reads "any" and a larger number "at least".
    "errors": Filter("tool_errors >= $errors", queries.ParamType.INTEGER),
}

# The HTML input a filter's type gets on the form. One map rather than a field per filter.
CONTROLS: dict[queries.ParamType, str] = {
    queries.ParamType.TEXT: "text",
    queries.ParamType.DATE: "date",
    queries.ParamType.INTEGER: "number",
}


class Control(NamedTuple):
    """One filter as the form renders it."""

    key: str
    # The HTML input type, from `CONTROLS`.
    type: str
    # What this request asked for, so the form comes back holding what was typed into it.
    value: str


class Direction(NamedTuple):
    """One sort direction, as the two SQL fragments it puts in the ORDER BY."""

    keyword: str
    # Where the NULLs go. Opposite ends in the two directions, so that reversing a sort
    # reverses the whole list rather than pinning the empty rows to one end.
    nulls: str


DIRECTIONS: dict[str, Direction] = {
    "asc": Direction("ASC", "NULLS LAST"),
    "desc": Direction("DESC", "NULLS FIRST"),
}

# Newest first: the session someone is looking for is usually the one that just ran.
DEFAULT_SORT = "started_at"
DEFAULT_DIRECTION = "desc"

# How many sessions a page of the list holds, and the most it will hold on request. The list
# is the one page that grows with the corpus: 575 sessions rendered whole came to 587 KB,
# past the design's page ceiling, so the size is bound rather than assumed small.
PAGE_SESSIONS = 200
MAX_PAGE_SESSIONS = 500

# Every query-string key the session list reads: the filters, plus what orders and pages them.
LIST_KEYS = frozenset(FILTERS) | {"sort", "direction", "page", "size"}

# The most a fragment will page at once, whatever a URL asks for. The payload bound the design
# states is arithmetic over the *manifest defaults*; these ceilings are the coarser guard that
# keeps a hand-typed `?calls=100000` from trying to render a whole session in one response.
MAX_PAGE_CALLS = 100
MAX_PAGE_TOOLS = 200

Row = dict[str, Any]


class StoreLocked(Exception):
    """Another process holds the store's write lock, so this request cannot read it."""


class SchemaMoved(Exception):
    """The store's schema version is not the one this build reads."""


class EntryKind(StrEnum):
    """What one row of a session timeline is."""

    TURN = "turn"
    COMPACTION = "compaction"


@dataclass(frozen=True)
class Chip:
    """One agent run where it hangs: on the turn that spawned it, under the run that did."""

    run: Row
    children: tuple["Chip", ...]


@dataclass(frozen=True)
class Entry:
    """One row of a thread's timeline: a turn with its runs, or a compaction marker."""

    kind: EntryKind
    row: Row
    chips: tuple[Chip, ...] = ()

    @property
    def continuation(self) -> bool:
        """The row carrying the calls that answer no turn of this thread.

        A resume answers turns that live in the session it resumed, and a by-reference fork
        opens mid-conversation; the digests give both a sentinel row rather than dropping
        their spend. The page says so, because "no prompt" and "an empty prompt" read alike.
        """
        return self.kind is EntryKind.TURN and self.row["turn_id"] == queries.UNATTRIBUTED


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


class Listing(NamedTuple):
    """One page of the session list, and whether the store holds another after it."""

    rows: list[Row]
    more: bool


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


def sorted_sessions(
    connection: duckdb.DuckDBPyConnection,
    sort: str,
    direction: str,
    page: int,
    size: int,
    filters: Mapping[str, ParamValue],
) -> Listing:
    """One page of the session list, ordered by one of `SORTS` — the design's composition.

    The library query stays the citable core: it goes in a subquery untouched, and what is
    wrapped around it is a WHERE of `FILTERS` predicates, an ORDER BY built from two
    dictionary lookups, and a LIMIT — every value a request supplied bound as a parameter.
    `session_id` breaks ties in the same direction, which makes every sort a total order,
    its reverse exact, and the page boundaries stable between requests.
    """
    # A sort or filter key *is* part of a SQL fragment, so membership is the whole guard —
    # and it is checked here as well as at the route, because this builds the SQL.
    if sort not in SORTS or not filters.keys() <= FILTERS.keys():
        raise KeyError(sort)
    order = DIRECTIONS[direction]
    listing = queries.load(Page.SESSIONS).strip().rstrip(";")
    # `FILTERS` order, not the query string's: the SQL a citation stands for is the same
    # whichever way a URL was typed.
    applied = [FILTERS[key].predicate for key in FILTERS if key in filters]
    where = f" WHERE {' AND '.join(applied)}" if applied else ""
    # One row past the page: cheaper than a second query, and all a pager needs to know.
    rows = fetch(
        connection,
        f"SELECT * FROM ({listing}){where}"
        f" ORDER BY {sort} {order.keyword} {order.nulls}, session_id {order.keyword}"
        " LIMIT $limit OFFSET $offset",
        {"limit": size + 1, "offset": (page - 1) * size, **filters},
    )
    return Listing(rows[:size], len(rows) > size)


def narrowing(params: Mapping[str, str]) -> dict[str, ParamValue]:
    """The filters one request asked for, each parsed as the type its predicate binds.

    A key outside `LIST_KEYS` is a 400 rather than a no-op: FastAPI ignores what it was not
    declared with, so a mistyped filter would otherwise show the whole corpus and look like
    an answer. An empty value is not a filter — the list's form submits every field, so a
    blank one has to mean "not filtering".
    """
    if not params.keys() <= LIST_KEYS:
        raise HTTPException(400, f"The list takes {', '.join(sorted(LIST_KEYS))}.")
    return {key: _as_bound(key, given) for key in FILTERS if (given := params.get(key))}


def _as_bound(key: str, text: str) -> ParamValue:
    """One filter's query-string text as the value DuckDB binds, or a 400."""
    kind = FILTERS[key].type
    try:
        match kind:
            case queries.ParamType.TEXT:
                return text
            case queries.ParamType.INTEGER:
                return int(text)
            case queries.ParamType.DATE:
                return dt.date.fromisoformat(text)
            case _:
                assert_never(kind)
    except ValueError as error:
        raise HTTPException(400, f"The list's {key} takes {kind} values.") from error


def list_url(sort: str, direction: str, page: int, size: int, filters: Mapping[str, str]) -> str:
    """A link back to the list, carrying everything that made this view of it.

    Every link the list writes goes through here. A filter that rode the sort headings but
    not the pager would widen the list halfway through reading it, which is the kind of thing
    a reader notices only after quoting the wrong count.
    """
    query: dict[str, str | int] = {"sort": sort, "direction": direction}
    if page > 1:
        query["page"] = page
    if size != PAGE_SESSIONS:
        query["size"] = size
    return "/?" + urlencode(query | {key: value for key, value in filters.items() if value})


def checked(size: int, ceiling: int) -> int:
    """A page size from a query string, or a 400 — every route's sizes go through here."""
    if not 1 <= size <= ceiling:
        raise HTTPException(400, f"Ask for a page size between 1 and {ceiling}.")
    return size


def chips(runs: Sequence[Row], source: str, turn_id: str | None) -> tuple[Chip, ...]:
    """The runs one turn of `source` spawned, each carrying the runs it spawned in turn."""
    return tuple(
        Chip(run, chips(runs, run["run_id"], None) + _under_turns(runs, run["run_id"]))
        for run in runs
        if run["spawn_source"] == source and run["spawn_turn_id"] == turn_id
    )


def _under_turns(runs: Sequence[Row], source: str) -> tuple[Chip, ...]:
    """Runs spawned from a turn of `source`'s own timeline, which the run page lays out."""
    turns = sorted({run["spawn_turn_id"] for run in runs if run["spawn_source"] == source} - {None})
    return tuple(chip for turn_id in turns for chip in chips(runs, source, turn_id))


def timeline(
    turns: Sequence[Row], runs: Sequence[Row], compactions: Sequence[Row], source: str
) -> list[Entry]:
    """One thread's turns in order, with the runs it spawned chipped on and its compactions
    interleaved.

    `source` is `main` for a session page and a run id for a run page: the same shape either
    way, because a run's transcript is a thread like the session's own.
    """
    entries: list[Entry] = []
    pending = list(compactions)
    for turn in turns:
        started = turn["started_at"]
        # A compaction that ran before this turn started belongs after the previous one.
        while pending and started is not None and pending[0]["timestamp"] <= started:
            entries.append(Entry(EntryKind.COMPACTION, pending.pop(0)))
        entries.append(Entry(EntryKind.TURN, turn, chips(runs, source, turn["turn_id"])))
    return entries + [Entry(EntryKind.COMPACTION, row) for row in pending]


class Threads(NamedTuple):
    """A session page's two run-bearing parts, which together hold every run it recorded."""

    entries: list[Entry]
    unattached: tuple[Chip, ...]


def session_threads(
    turns: Sequence[Row], runs: Sequence[Row], compactions: Sequence[Row]
) -> Threads:
    """The main thread's timeline and the unattached list, checked to cover every run.

    Built together because the guarantee is about the pair: a run lands on a turn, under the
    run that spawned it, or in the unattached list. One this cannot place is a shape we have
    not seen, so it raises rather than vanishing from a page that still counts it.
    """
    entries = timeline(turns, runs, compactions, MAIN_SOURCE)
    loose = unattached(runs)
    placed = {chip.run["run_id"] for entry in entries for chip in _walk(entry.chips)}
    placed |= {chip.run["run_id"] for chip in _walk(loose)}
    missing = {run["run_id"] for run in runs} - placed
    if missing:
        raise ValueError(f"{len(missing)} run(s) hang off no turn and no run: {sorted(missing)}")
    return Threads(entries, loose)


def parent_of(run: Row) -> str | None:
    """The thread a run hangs under: the parent its transcript names, else the thread its
    spawning call was made from.

    The two-rule linkage `enrich/store.py:item_parents` applies, and in that order — a fork's
    spawning call is only in its own transcript, which the chip join deliberately excludes,
    so `parent_agent_id` is all a fork has. None when the store names neither.
    """
    return run["parent_agent_id"] or run["spawn_source"]


def ancestry(run_id: str, runs: Sequence[Row]) -> list[str]:
    """The threads above a run, outermost first — as far as the store names them.

    It stops where the naming stops rather than falling back to the session's main thread: a
    run whose parent nothing names is a run this store cannot place, and `main` would be a
    guess. So the trail is empty for such a run, and truncated for its descendants.
    """
    by_id = {run["run_id"]: run for run in runs}
    trail: list[str] = []
    current = run_id
    while (parent := parent_of(by_id[current])) is not None:
        if parent in trail:
            raise ValueError(f"{run_id} hangs under itself: {[*trail, parent]}")
        trail.append(parent)
        # `main`, or a run of some other session: either way the walk is over.
        if parent not in by_id:
            break
        current = parent
    return trail[::-1]


def children(run_id: str, runs: Sequence[Row]) -> tuple[Chip, ...]:
    """The runs under this one that no turn of its own timeline claims.

    The complement of its chips: together they hold every run whose parent is this one, each
    exactly once, so a run page cannot lose a child the way the chip join alone would lose a
    fork.
    """
    return tuple(
        Chip(run, chips(runs, run["run_id"], None) + _under_turns(runs, run["run_id"]))
        for run in runs
        if parent_of(run) == run_id
        and not (run["spawn_source"] == run_id and run["spawn_turn_id"] is not None)
    )


def unattached(runs: Sequence[Row]) -> tuple[Chip, ...]:
    """The runs the chip join could not place — its complement, whatever the cause.

    No `tool_use_id`, a spawning call naming a tool call the store lacks, or a fork's own
    un-replayed copy of that call: the page says the run is unattached and does not guess.
    """
    return tuple(
        Chip(run, chips(runs, run["run_id"], None) + _under_turns(runs, run["run_id"]))
        for run in runs
        if run["spawn_turn_id"] is None
    )


def _walk(items: Sequence[Chip]) -> Iterator[Chip]:
    for chip in items:
        yield chip
        yield from _walk(chip.children)


def build_app(db_path: Path) -> FastAPI:
    """The viewer over the store at `db_path`, which must exist and hold this schema."""
    resolved = db_path.resolve()
    # Fail at startup rather than on the first page: a typo in `--db` should not open a
    # browser onto an error page.
    with open_store(resolved):
        pass

    app = FastAPI(title="aiobserve", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    templates = Jinja2Templates(directory=TEMPLATES)
    templates.env.filters |= {
        "money": fmt.money,
        "count": fmt.count,
        "when": fmt.when,
        "clock": fmt.clock,
        "duration": fmt.duration,
        # The two filters that print what a transcript wrote. Both hand back escaped markup;
        # `view/render.py` is where that escaping lives, and nothing here may add `|safe`.
        "markdown": render.markdown,
        "pretty": render.pretty,
    }

    def error(request: Request, status: int, message: str) -> Response:
        return templates.TemplateResponse(
            request, "error.html", {"status": status, "message": message}, status_code=status
        )

    @app.middleware("http")
    async def _policy(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["content-security-policy"] = CSP
        return response

    @app.exception_handler(StoreLocked)
    def _locked(request: Request, exception: Exception) -> Response:
        return error(
            request,
            503,
            "Another process holds the trace store — an extract or an enrich is running. "
            "The page will load once it finishes.",
        )

    @app.exception_handler(SchemaMoved)
    def _moved(request: Request, exception: Exception) -> Response:
        return error(
            request,
            503,
            f"The store now holds schema version {exception}, and this build reads "
            f"{SCHEMA_VERSION}. Restart the viewer.",
        )

    @app.exception_handler(StarletteHTTPException)
    def _http(request: Request, exception: Exception) -> Response:
        assert isinstance(exception, StarletteHTTPException)
        return error(request, exception.status_code, str(exception.detail))

    @app.get("/")
    def session_list(
        request: Request,
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIRECTION,
        page: int = 1,
        size: int = PAGE_SESSIONS,
    ) -> Response:
        if sort not in SORTS or direction not in DIRECTIONS:
            raise HTTPException(
                400,
                f"Sort by one of {', '.join(SORTS)}, in direction {' or '.join(DIRECTIONS)}.",
            )
        if page < 1 or not 1 <= size <= MAX_PAGE_SESSIONS:
            raise HTTPException(
                400, f"Ask for page 1 or later, at a size between 1 and {MAX_PAGE_SESSIONS}."
            )
        filters = narrowing(request.query_params)
        # What the URL said, kept as text: the links have to reproduce the request, and the
        # form has to come back filled in with what was typed into it.
        given = {key: request.query_params.get(key, "") for key in FILTERS}
        with open_store(resolved) as connection:
            rows, more = sorted_sessions(connection, sort, direction, page, size, filters)
            projects = fetch(connection, queries.load(Page.PROJECTS), {})
        # A header link flips the direction of the column already sorted by, and opens any
        # other column at the direction that puts its largest values first. Re-sorting starts
        # from the first page: page 4 of one order says nothing about page 4 of another.
        flipped = "asc" if direction == "desc" else "desc"
        links = {
            key: list_url(key, flipped if key == sort else DEFAULT_DIRECTION, 1, size, given)
            for key in SORTS
        }
        return templates.TemplateResponse(
            request,
            "sessions.html",
            {
                "sessions": rows,
                "sorts": SORTS,
                "sort": sort,
                "direction": direction,
                "links": links,
                # One input per filter, in `FILTERS` order, carrying what this request asked.
                "controls": [
                    Control(key, CONTROLS[spec.type], given[key]) for key, spec in FILTERS.items()
                ],
                "projects": [row["project_dir"] for row in projects],
                "page": page,
                "first": (page - 1) * size + 1,
                "previous": list_url(sort, direction, page - 1, size, given) if page > 1 else None,
                "next": list_url(sort, direction, page + 1, size, given) if more else None,
                "citations": {
                    Page.SESSIONS.value: queries.citation(
                        Page.SESSIONS,
                        {
                            "sort": sort,
                            "direction": direction,
                            "limit": size,
                            "offset": (page - 1) * size,
                            **filters,
                        },
                    )
                },
            },
        )

    @app.get("/session/{session_id}")
    def session_page(request: Request, session_id: str) -> Response:
        with open_store(resolved) as connection:
            header = page_rows(connection, Page.SESSION_HEADER, session_id=session_id)
            if not header:
                raise HTTPException(404, "No session with that id is in this store.")
            turns = page_rows(connection, Page.TIMELINE, session_id=session_id)
            runs = page_rows(connection, Page.RUNS, session_id=session_id)
            markers = page_rows(
                connection, Page.COMPACTIONS, session_id=session_id, source=MAIN_SOURCE
            )
        keyed: dict[str, ParamValue] = {"session_id": session_id}
        threads = session_threads(turns, runs, markers)
        return templates.TemplateResponse(
            request,
            "session.html",
            {
                "header": header[0],
                "main": MAIN_SOURCE,
                "timeline": threads.entries,
                "unattached": threads.unattached,
                "citations": {
                    page.value: queries.citation(
                        page, keyed | ({"source": MAIN_SOURCE} if page is Page.COMPACTIONS else {})
                    )
                    for page in (Page.SESSION_HEADER, Page.TIMELINE, Page.RUNS, Page.COMPACTIONS)
                },
            },
        )

    @app.get("/session/{session_id}/run/{run_id}")
    def run_page(request: Request, session_id: str, run_id: str) -> Response:
        with open_store(resolved) as connection:
            header = page_rows(connection, Page.RUN_HEADER, session_id=session_id, run_id=run_id)
            if not header:
                raise HTTPException(404, "No run with that id is in this session.")
            turns = page_rows(connection, Page.RUN_TIMELINE, session_id=session_id, source=run_id)
            # The session's runs, not this one's: the trail above the run and the runs under
            # it are both read off the same set of links.
            runs = page_rows(connection, Page.RUNS, session_id=session_id)
            markers = page_rows(connection, Page.COMPACTIONS, session_id=session_id, source=run_id)
        keyed: dict[str, ParamValue] = {"session_id": session_id}
        at_source = keyed | {"source": run_id}
        return templates.TemplateResponse(
            request,
            "run.html",
            {
                "header": header[0],
                "main": MAIN_SOURCE,
                "trail": ancestry(run_id, runs),
                "timeline": timeline(turns, runs, markers, run_id),
                "children": children(run_id, runs),
                "citations": {
                    Page.RUN_HEADER.value: queries.citation(
                        Page.RUN_HEADER, keyed | {"run_id": run_id}
                    ),
                    Page.RUN_TIMELINE.value: queries.citation(Page.RUN_TIMELINE, at_source),
                    Page.RUNS.value: queries.citation(Page.RUNS, keyed),
                    Page.COMPACTIONS.value: queries.citation(Page.COMPACTIONS, at_source),
                },
            },
        )

    def tools_under(
        connection: duckdb.DuckDBPyConnection,
        keyed: Mapping[str, ParamValue],
        after: int,
        size: int,
    ) -> Paged:
        """One page of the tool calls under one api call — the nested list and its "+N more".

        The same query and the same page shape whether it arrives inline under a call or as
        the continuation the indicator fetches, so the two can never disagree about the set.
        """
        return paged(
            page_rows(connection, Fragment.CALL_TOOLS, **keyed, after=after, page_tools=size),
            "matched_tool_calls",
            "tool_index",
        )

    @app.get("/fragment/turn/{session_id}/{source}/{turn_id}")
    def turn_calls(
        request: Request,
        session_id: str,
        source: str,
        turn_id: str,
        after: int = queries.FIRST_PAGE,
        calls: int = queries.PAGE_CALLS,
        tools: int = queries.PAGE_TOOLS,
    ) -> Response:
        """One page of the api calls a turn made, each carrying a page of its tool calls."""
        checked(calls, MAX_PAGE_CALLS)
        checked(tools, MAX_PAGE_TOOLS)
        keyed: dict[str, ParamValue] = {"session_id": session_id, "source": source}
        # The digests name the calls that sit under no turn with a sentinel, because a URL
        # cannot carry a NULL. The query asks for those rows with one.
        turn: ParamValue = None if turn_id == queries.UNATTRIBUTED else turn_id
        with open_store(resolved) as connection:
            page = paged(
                page_rows(
                    connection,
                    Fragment.TURN_CALLS,
                    **keyed,
                    turn_id=turn,
                    after=after,
                    page_calls=calls,
                ),
                "matched_api_calls",
                "call_index",
            )
            # One fetch per call rather than a nested subquery: `view_call_tools` stays the
            # single definition of what a page of tool rows is, and a turn page is 25 small
            # keyed reads on a local file.
            under = {
                row["api_call_id"]: tools_under(
                    connection,
                    keyed | {"api_call_id": row["api_call_id"]},
                    queries.FIRST_PAGE,
                    tools,
                )
                for row in page.rows
            }
        return templates.TemplateResponse(
            request,
            "fragments/calls.html",
            {
                "session_id": session_id,
                "source": source,
                "turn_id": turn_id,
                "page": page,
                "under": under,
                "tools": tools,
                "calls": calls,
                "citation": queries.citation(
                    Fragment.TURN_CALLS,
                    keyed | {"turn_id": turn, "after": after, "page_calls": calls},
                ),
            },
        )

    @app.get("/fragment/tools/{session_id}/{source}/{api_call_id}")
    def call_tools(
        request: Request,
        session_id: str,
        source: str,
        api_call_id: str,
        after: int = queries.FIRST_PAGE,
        tools: int = queries.PAGE_TOOLS,
    ) -> Response:
        """One page of the tool calls under one api call."""
        checked(tools, MAX_PAGE_TOOLS)
        keyed: dict[str, ParamValue] = {
            "session_id": session_id,
            "source": source,
            "api_call_id": api_call_id,
        }
        with open_store(resolved) as connection:
            page = tools_under(connection, keyed, after, tools)
        return templates.TemplateResponse(
            request,
            "fragments/tools.html",
            {
                "session_id": session_id,
                "source": source,
                "api_call_id": api_call_id,
                "page": page,
                "tools": tools,
                "citation": queries.citation(
                    Fragment.CALL_TOOLS, keyed | {"after": after, "page_tools": tools}
                ),
            },
        )

    def whole(
        request: Request, value: Value, template: str, keyed: Mapping[str, ParamValue]
    ) -> Response:
        """One per-value fragment: the whole value, or a 404 when nothing is stored under it."""
        with open_store(resolved) as connection:
            rows = page_rows(connection, value, **keyed)
        if not rows:
            raise HTTPException(404, "Nothing in this store is stored under that id.")
        return templates.TemplateResponse(
            request,
            f"fragments/{template}.html",
            {"row": rows[0], "citation": queries.citation(value, keyed)},
        )

    @app.get("/fragment/text/{session_id}/{source}/{api_call_id}")
    def call_text(request: Request, session_id: str, source: str, api_call_id: str) -> Response:
        """What one api call said, whole."""
        return whole(
            request,
            Value.CALL_TEXT,
            "value",
            {"session_id": session_id, "source": source, "api_call_id": api_call_id},
        )

    @app.get("/fragment/thinking/{session_id}/{source}/{api_call_id}")
    def call_thinking(request: Request, session_id: str, source: str, api_call_id: str) -> Response:
        """What one api call thought, whole."""
        return whole(
            request,
            Value.CALL_THINKING,
            "value",
            {"session_id": session_id, "source": source, "api_call_id": api_call_id},
        )

    @app.get("/fragment/tool/{session_id}/{source}/{tool_call_id}")
    def tool_value(request: Request, session_id: str, source: str, tool_call_id: str) -> Response:
        """One tool call whole: the arguments it was given and what it returned."""
        return whole(
            request,
            Value.TOOL,
            "tool",
            {"session_id": session_id, "source": source, "tool_call_id": tool_call_id},
        )

    return app


def serve(db_path: Path, port: int, *, open_browser: bool) -> None:
    """Run the viewer until interrupted, refusing a port something else already holds."""
    app = build_app(db_path)
    with socket.socket() as probe:
        try:
            probe.bind((HOST, port))
        except OSError as error:
            raise SystemExit(
                f"port {port} is in use — a viewer may already be running at "
                f"http://{HOST}:{port}/. Pass --port to use another."
            ) from error
    url = f"http://{HOST}:{port}/"
    print(f"aiobserve view: {db_path} at {url}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=HOST, port=port, log_level="warning")
