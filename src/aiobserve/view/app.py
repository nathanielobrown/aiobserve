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

import socket
import webbrowser
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

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
    SESSION_HEADER = "view_session_header"
    # The turn timeline, shared with `aiobserve query` — the same rows a report cites.
    TIMELINE = "session_digest"
    RUNS = "view_runs"
    COMPACTIONS = "view_compactions"


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
    """One row of a session's timeline: a turn with its runs, or a compaction marker."""

    kind: EntryKind
    row: Row
    chips: tuple[Chip, ...] = ()


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
    connection: duckdb.DuckDBPyConnection, page: Page, **bindings: ParamValue
) -> list[Row]:
    """The rows of one library query, bound as given."""
    return fetch(connection, queries.load(page), bindings)


class Listing(NamedTuple):
    """One page of the session list, and whether the store holds another after it."""

    rows: list[Row]
    more: bool


def sorted_sessions(
    connection: duckdb.DuckDBPyConnection, sort: str, direction: str, page: int, size: int
) -> Listing:
    """One page of the session list, ordered by one of `SORTS` — the design's composition.

    The library query stays the citable core: it goes in a subquery untouched, and what is
    wrapped around it is an ORDER BY built from two dictionary lookups and a LIMIT bound as
    a parameter. `session_id` breaks ties in the same direction, which makes every sort a
    total order, its reverse exact, and the page boundaries stable between requests.
    """
    # A sort key *is* a column name, so membership is the whole guard — and it is checked
    # here as well as at the route, because this is the function that builds SQL.
    if sort not in SORTS:
        raise KeyError(sort)
    order = DIRECTIONS[direction]
    listing = queries.load(Page.SESSIONS).strip().rstrip(";")
    # One row past the page: cheaper than a second query, and all a pager needs to know.
    rows = fetch(
        connection,
        f"SELECT * FROM ({listing})"
        f" ORDER BY {sort} {order.keyword} {order.nulls}, session_id {order.keyword}"
        " LIMIT $limit OFFSET $offset",
        {"limit": size + 1, "offset": (page - 1) * size},
    )
    return Listing(rows[:size], len(rows) > size)


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


def timeline(turns: Sequence[Row], runs: Sequence[Row], compactions: Sequence[Row]) -> list[Entry]:
    """A session's turns in order, with its runs chipped on and its compactions interleaved.

    Every run of the session lands exactly once — on a turn, under the run that spawned it,
    or in the unattached list `unattached` returns. A run this cannot place is a shape we
    have not seen, so it raises rather than vanishing from a page that still counts it.
    """
    entries: list[Entry] = []
    pending = list(compactions)
    for turn in turns:
        started = turn["started_at"]
        # A compaction that ran before this turn started belongs after the previous one.
        while pending and started is not None and pending[0]["timestamp"] <= started:
            entries.append(Entry(EntryKind.COMPACTION, pending.pop(0)))
        entries.append(Entry(EntryKind.TURN, turn, chips(runs, MAIN_SOURCE, turn["turn_id"])))
    entries += [Entry(EntryKind.COMPACTION, row) for row in pending]
    placed = {chip.run["run_id"] for entry in entries for chip in _walk(entry.chips)}
    placed |= {chip.run["run_id"] for chip in _walk(unattached(runs))}
    missing = {run["run_id"] for run in runs} - placed
    if missing:
        raise ValueError(f"{len(missing)} run(s) hang off no turn and no run: {sorted(missing)}")
    return entries


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
        with open_store(resolved) as connection:
            rows, more = sorted_sessions(connection, sort, direction, page, size)
        # A header link flips the direction of the column already sorted by, and opens any
        # other column at the direction that puts its largest values first. Re-sorting starts
        # from the first page: page 4 of one order says nothing about page 4 of another.
        flipped = "asc" if direction == "desc" else "desc"
        held = f"&size={size}" if size != PAGE_SESSIONS else ""
        links = {
            key: f"/?sort={key}&direction={flipped if key == sort else DEFAULT_DIRECTION}{held}"
            for key in SORTS
        }
        step = f"/?sort={sort}&direction={direction}{held}&page="
        return templates.TemplateResponse(
            request,
            "sessions.html",
            {
                "sessions": rows,
                "sorts": SORTS,
                "sort": sort,
                "direction": direction,
                "links": links,
                "page": page,
                "first": (page - 1) * size + 1,
                "previous": f"{step}{page - 1}" if page > 1 else None,
                "next": f"{step}{page + 1}" if more else None,
                "citations": {
                    Page.SESSIONS.value: queries.citation(
                        Page.SESSIONS,
                        {
                            "sort": sort,
                            "direction": direction,
                            "limit": size,
                            "offset": (page - 1) * size,
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
        return templates.TemplateResponse(
            request,
            "session.html",
            {
                "header": header[0],
                "timeline": timeline(turns, runs, markers),
                "unattached": unattached(runs),
                "citations": {
                    page.value: queries.citation(
                        page, keyed | ({"source": MAIN_SOURCE} if page is Page.COMPACTIONS else {})
                    )
                    for page in (Page.SESSION_HEADER, Page.TIMELINE, Page.RUNS, Page.COMPACTIONS)
                },
            },
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
