"""The trace viewer's routes: what each URL serves, and what it reads to serve it.

`build_app(db_path)` returns a FastAPI app over one trace store; `serve` runs it. Nothing
here writes: every request opens its own read-only connection (`view/store.py`), checks the
store's schema version, renders, and closes. That is what lets an extract run while a page is
open, and what makes a locked store a 503 rather than a crash.

The pages are built from library queries (`analyze/queries/`) — the viewer composes sort and
filter *around* a query's SELECT (`view/listing.py`) and binds every user-supplied value as a
parameter, so no request text ever reaches SQL. How a session's runs and turns fit together
is `view/threads.py`; this module is the URLs and the templates they render.
"""

import datetime as dt
import socket
import webbrowser
from collections.abc import Mapping
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
from aiobserve.export.duckdb import SCHEMA_VERSION
from aiobserve.model import MAIN_SOURCE
from aiobserve.view import bounds, render
from aiobserve.view import format as fmt
from aiobserve.view.enrichment import described, enriched
from aiobserve.view.labels import label
from aiobserve.view.listing import (
    CONTROLS,
    DEFAULT_DIRECTION,
    DEFAULT_SORT,
    DIRECTIONS,
    FILTERS,
    LIST_URL,
    SORTS,
    Control,
    list_url,
    narrowing,
    sorted_sessions,
)
from aiobserve.view.store import (
    Fragment,
    Page,
    Paged,
    SchemaMoved,
    StoreLocked,
    Value,
    cursorless_rows,
    open_store,
    page_rows,
    paged,
    thread_outline,
    window,
)
from aiobserve.view.threads import (
    TURN_CURSOR,
    ancestry,
    capped,
    children,
    cut_to,
    marks_on_page,
    nav_tree,
    on_page,
    permalink,
    session_threads,
    timeline,
)

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


class ToolCalls(NamedTuple):
    """One page of tool calls and the query line that produced it.

    The citation travels with the rows because the list is rendered twice — nested under a
    call and served on its own — and a page cites what it ran.
    """

    page: Paged
    citation: str


def project_link(project_dir: str | None) -> str | None:
    """The session list narrowed to one project, or None when there is no list to open.

    The path is the whole one and not the head a row shows — the list's filter matches a path
    prefix, and a cut one matches nothing. A row the query left NULL is a row with no link:
    the sessions that named no directory, and a path longer than the head this page shows.
    """
    if project_dir is None:
        return None
    return list_url(
        DEFAULT_SORT, DEFAULT_DIRECTION, 1, bounds.SESSIONS.default, {"project": project_dir}
    )


def checked(size: int, ceiling: int) -> int:
    """A page size from a query string, or a 400 — every route's sizes go through here."""
    if not 1 <= size <= ceiling:
        raise HTTPException(400, f"Ask for a page size between 1 and {ceiling}.")
    return size


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

    def ago(value: dt.datetime | None) -> str:
        """How long ago, against the clock at render rather than one captured at startup.

        A viewer left open is a long-lived process, so the clock is read here per render:
        one captured when the app was built would freeze every row's freshness at boot.
        """
        return fmt.ago(value, fmt.utcnow())

    templates.env.filters |= {
        "money": fmt.money,
        "count": fmt.count,
        "share": fmt.share,
        "percent": fmt.percent,
        "when": fmt.when,
        "clock": fmt.clock,
        "duration": fmt.duration,
        "text": fmt.text,
        "ago": ago,
        # The three filters that print what a transcript wrote. Each hands back escaped
        # markup; `view/render.py` is where that escaping lives, and nothing here may add
        # `|safe`.
        "markdown": render.markdown,
        "pretty": render.pretty,
        "link": render.link,
    }

    # The one link two surfaces mint — the timeline's permalink and the map's out-of-window
    # node — so the cursor rule lives in one place and both go to the same page.
    # The namespace is typed by what Jinja seeds it with, which is why the assignment needs a
    # word: a global is any callable a template can name.
    templates.env.globals["permalink"] = permalink  # pyrefly: ignore
    templates.env.globals["label"] = label  # pyrefly: ignore

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
    def projects_page(request: Request) -> Response:
        # The clock both trailing windows are measured back from, read here and bound like
        # any other parameter. The query reads no clock of its own: a page counting "the last
        # 7 days" from SQL's `now()` would cite a line that answers something else tomorrow,
        # and the footer's whole promise is that a reader can re-run what the page ran.
        bound: dict[str, ParamValue] = {
            "as_of": fmt.utcnow().date(),
            "recent_days": queries.PAGE_RECENT_DAYS,
            "window_days": queries.PAGE_WINDOW_DAYS,
            "head_chars": queries.LIST_CHARS,
            "projects": bounds.PROJECTS.default,
        }
        with open_store(resolved) as connection:
            rows = page_rows(connection, Page.PROJECT_ROLLUPS, **bound)
        return templates.TemplateResponse(
            request,
            "projects.html",
            {
                # Each row beside the list of its own sessions, minted through the list's own
                # link builder so a project opens the list the way the list links to itself.
                "projects": [row | {"link": project_link(row["project_filter"])} for row in rows],
                # What the page cut, which the query counted before its LIMIT: a landing page
                # that silently dropped projects would be a corpus a reader cannot see.
                "cut": rows[0]["matched_projects"] - len(rows) if rows else 0,
                # The bindings, for the two window headings — a heading and its column read
                # the same numbers, and the citation below carries them too.
                "bound": bound,
                "citations": {
                    Page.PROJECT_ROLLUPS.value: queries.citation(Page.PROJECT_ROLLUPS, bound)
                },
            },
        )

    @app.get(LIST_URL)
    def session_list(
        request: Request,
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIRECTION,
        page: int = 1,
        size: int = bounds.SESSIONS.default,
    ) -> Response:
        if sort not in SORTS or direction not in DIRECTIONS:
            raise HTTPException(
                400,
                f"Sort by one of {', '.join(SORTS)}, in direction {' or '.join(DIRECTIONS)}.",
            )
        if page < 1 or not 1 <= size <= bounds.SESSIONS.ceiling:
            raise HTTPException(
                400, f"Ask for page 1 or later, at a size between 1 and {bounds.SESSIONS.ceiling}."
            )
        filters = narrowing(request.query_params)
        # What the URL said, kept as text: the links have to reproduce the request, and the
        # form has to come back filled in with what was typed into it.
        given = {key: request.query_params.get(key, "") for key in FILTERS}
        with open_store(resolved) as connection:
            # Whether the store holds the enrichment tables at all, which decides both what
            # the list joins and what it cites: a page cites what it ran.
            describes = enriched(connection)
            rows, more = sorted_sessions(
                connection, sort, direction, page, size, filters, described=describes
            )
            projects = page_rows(
                connection,
                Page.PROJECTS,
                head_chars=queries.LIST_CHARS,
                head_projects=queries.LIST_PROJECTS,
            )
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
                # Whether the store holds an enrichment pass's answers at all, which decides
                # whether the list carries a work column: an empty one over a store no pass
                # has touched is a claim the store cannot support.
                "described": describes,
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
                            # What the page shows of each row, which is composed around the
                            # query like the paging is: re-running the file alone answers
                            # with whole titles, paths and skill lists.
                            "head_chars": queries.LIST_CHARS,
                            "item_chars": queries.LIST_ITEM_CHARS,
                            "head_items": queries.LIST_ITEMS,
                            **filters,
                        },
                    ),
                    # Joined to that page rather than run against it, so it is cited on its
                    # own — and only over a store whose enrichment tables exist to join.
                    **(
                        {
                            Page.DESCRIBED_SESSIONS.value: queries.citation(
                                Page.DESCRIBED_SESSIONS,
                                {
                                    "head_chars": queries.LIST_CHARS,
                                    "tag_chars": queries.TAG_CHARS,
                                    "kind_chars": queries.TAG_CHARS,
                                    "head_kinds": queries.LIST_CATEGORIES,
                                },
                            )
                        }
                        if describes
                        else {}
                    ),
                },
            },
        )

    def turn_lines(
        connection: duckdb.DuckDBPyConnection, session_id: str, source: str
    ) -> dict[str, int]:
        """Which transcript line each of a thread's turns was read from, keyed by turn id.

        A turn whose record the store does not hold is absent rather than mapped to nothing,
        so a template can ask `lines.get(turn_id)` and get a link or no link.
        """
        return {
            row["turn_id"]: row["line_no"]
            for row in page_rows(
                connection, Page.TURN_RECORDS, session_id=session_id, source=source
            )
        }

    @app.get("/session/{session_id}")
    def session_page(
        request: Request,
        session_id: str,
        after: int = queries.FIRST_PAGE,
        turns: int = bounds.TURNS.default,
        chips: int = bounds.CHIPS.default,
    ) -> Response:
        """One page of a session's timeline: its turns in order, with the runs they spawned.

        `after` is *the last turn index already shown*, the records browser's semantics — so
        the turn at index N opens on `?after={N - 1}`, first row on the page. The two sizes
        multiply, which is why their product is checked as well as each of them.
        """
        checked(turns, bounds.TURNS.ceiling)
        checked(chips, bounds.CHIPS.ceiling)
        # The unattached list is a list of `chips` like a turn's, and it rides every page.
        if (turns + 1) * chips > bounds.CHIP_BUDGET:
            raise HTTPException(
                400, f"Ask for at most {bounds.CHIP_BUDGET} run rows a page: (turns + 1) × chips."
            )
        with open_store(resolved) as connection:
            header = page_rows(
                connection,
                Page.SESSION_HEADER,
                session_id=session_id,
                head_chars=queries.HEADER_CHARS,
                item_chars=queries.HEADER_ITEM_CHARS,
                head_items=queries.HEADER_ITEMS,
            )
            if not header:
                raise HTTPException(404, "No session with that id is in this store.")
            page = window(
                connection, Page.TIMELINE, TURN_CURSOR, after, turns, session_id=session_id
            )
            outline = thread_outline(connection, Page.TIMELINE, TURN_CURSOR, session_id=session_id)
            # The row for calls under no turn has no index to window on, so it rides the last
            # page — where the unpaged digest puts it.
            tail = (
                cursorless_rows(
                    connection,
                    Page.TIMELINE,
                    TURN_CURSOR,
                    bounds.CURSORLESS_TURNS,
                    session_id=session_id,
                )
                if page.after is None
                else []
            )
            runs = page_rows(
                connection, Page.RUNS, session_id=session_id, chip_chars=queries.CHIP_CHARS
            )
            markers = page_rows(
                connection,
                Page.COMPACTIONS,
                session_id=session_id,
                source=MAIN_SOURCE,
                chip_chars=queries.CHIP_CHARS,
            )
            lines = turn_lines(connection, session_id, MAIN_SOURCE)
            enrichment = described(connection, session_id, MAIN_SOURCE)
        # A cursor past the last turn and a thread that was never there are the same answer.
        # Page one is not: a session with no turns of its own still has its header and spend.
        if after != queries.FIRST_PAGE and not page.rows:
            raise HTTPException(404, "This session has no turns after that one.")
        rows = page.rows + tail
        keyed: dict[str, ParamValue] = {"session_id": session_id}
        at_source = keyed | {"source": MAIN_SOURCE}
        # What each query on this page was bound with, defaulting to the session key. The
        # digest's entry carries what the viewer composed around it, which is as much a part
        # of what produced the page as a bound parameter is.
        bound: dict[Page, dict[str, ParamValue]] = {
            Page.COMPACTIONS: at_source,
            Page.TURN_RECORDS: at_source,
            Page.ENRICHMENT: at_source,
            Page.TIMELINE: keyed | {"after": after, "limit": turns},
        }
        threads = session_threads(
            rows,
            [row["turn_id"] for row in outline],
            runs,
            marks_on_page(markers, outline, rows),
            chips,
            bounds.MARKS,
        )
        return templates.TemplateResponse(
            request,
            "session.html",
            {
                "header": header[0],
                "main": MAIN_SOURCE,
                # What the sidebar asks the map for: the window this page rendered.
                "after": after,
                "timeline": threads.entries,
                "unattached": threads.unattached,
                "marks": threads.marks,
                "lines": lines,
                "enrichment": enrichment,
                "page": page,
                # What the page's own links carry: the sizes it was served at, and the widest
                # one list may be — the size a "+N more" link asks for.
                "sizes": {"turns": turns, "chips": chips, "widest": bounds.CHIPS.ceiling},
                "citations": {
                    named.value: queries.citation(named, bound.get(named, keyed))
                    for named in (
                        Page.SESSION_HEADER,
                        Page.TIMELINE,
                        Page.RUNS,
                        Page.COMPACTIONS,
                        Page.TURN_RECORDS,
                        # Only when the store held the tables to ask: a page cites what it
                        # ran, and over an un-enriched store this query is not one of them.
                        *((Page.ENRICHMENT,) if enrichment.queried else ()),
                    )
                },
            },
        )

    @app.get("/session/{session_id}/run/{run_id}")
    def run_page(request: Request, session_id: str, run_id: str) -> Response:
        with open_store(resolved) as connection:
            header = page_rows(
                connection,
                Page.RUN_HEADER,
                session_id=session_id,
                run_id=run_id,
                head_chars=queries.HEADER_CHARS,
            )
            if not header:
                raise HTTPException(404, "No run with that id is in this session.")
            turns = page_rows(connection, Page.RUN_TIMELINE, session_id=session_id, source=run_id)
            # The session's runs, not this one's: the trail above the run and the runs under
            # it are both read off the same set of links.
            runs = page_rows(
                connection, Page.RUNS, session_id=session_id, chip_chars=queries.CHIP_CHARS
            )
            markers = page_rows(
                connection,
                Page.COMPACTIONS,
                session_id=session_id,
                source=run_id,
                chip_chars=queries.CHIP_CHARS,
            )
            lines = turn_lines(connection, session_id, run_id)
            enrichment = described(connection, session_id, run_id)
        marks = cut_to(markers, bounds.MARKS)
        keyed: dict[str, ParamValue] = {"session_id": session_id}
        at_source = keyed | {"source": run_id}
        return templates.TemplateResponse(
            request,
            "run.html",
            {
                "header": header[0],
                "main": MAIN_SOURCE,
                "trail": ancestry(run_id, runs),
                # A run's own thread is short — 8 turns at the corpus maximum — so it is
                # unpaged; its run lists are the same multiplicand a session page has, and
                # they take the same caps.
                "timeline": timeline(turns, runs, marks.shown, run_id, bounds.CHIPS.default),
                "marks": marks,
                "children": capped(children(run_id, runs), bounds.CHIPS.default),
                "lines": lines,
                "enrichment": enrichment,
                "citations": {
                    Page.RUN_HEADER.value: queries.citation(
                        Page.RUN_HEADER, keyed | {"run_id": run_id}
                    ),
                    Page.RUN_TIMELINE.value: queries.citation(Page.RUN_TIMELINE, at_source),
                    Page.RUNS.value: queries.citation(Page.RUNS, keyed),
                    Page.COMPACTIONS.value: queries.citation(Page.COMPACTIONS, at_source),
                    Page.TURN_RECORDS.value: queries.citation(Page.TURN_RECORDS, at_source),
                    # Absent over a store no enrichment pass has written to, where the query
                    # never ran (`view/enrichment.py`).
                    **(
                        {Page.ENRICHMENT.value: queries.citation(Page.ENRICHMENT, at_source)}
                        if enrichment.queried
                        else {}
                    ),
                },
            },
        )

    @app.get("/session/{session_id}/records/{source}")
    def records_page(
        request: Request,
        session_id: str,
        source: str,
        after: int = queries.FIRST_PAGE,
        size: int = bounds.RECORDS.default,
    ) -> Response:
        """One page of a thread's raw transcript — where a report's citation lands.

        A citation names `(session_id, source, line_no)`; the URL for it is this path with
        `?after={line_no - 1}#L{line_no}`, so the cited record is the first row on the page.
        """
        checked(size, bounds.RECORDS.ceiling)
        keyed: dict[str, ParamValue] = {"session_id": session_id, "source": source}
        bound = keyed | {"after": after, "page_records": size}
        with open_store(resolved) as connection:
            page = paged(
                page_rows(connection, Page.RECORDS, **bound, preview_chars=queries.RECORD_PREVIEW),
                "matched_records",
                "line_no",
            )
        # A thread the store never held and a cursor past the end of one it does are the same
        # answer — nothing at this URL. Neither is a page worth rendering empty.
        if not page.rows:
            raise HTTPException(404, "This store holds no records for that thread at that line.")
        return templates.TemplateResponse(
            request,
            "records.html",
            {
                "session_id": session_id,
                "source": source,
                "page": page,
                "size": size,
                "citations": {Page.RECORDS.value: queries.citation(Page.RECORDS, bound)},
            },
        )

    @app.get("/session/{session_id}/offload/{name:path}")
    def offload_page(
        request: Request,
        session_id: str,
        name: str,
        after: int = 0,
        size: int = bounds.CHUNK.default,
    ) -> Response:
        """One chunk of a tool result Claude Code wrote to a file beside the transcript.

        `name` is the transcript's own file name, so it may hold anything a tool named a file
        — spaces, percent signs, something shaped like a path. It is a key into the store and
        never a path the server opens, which is what makes the shape of it uninteresting.
        """
        checked(size, bounds.CHUNK.ceiling)
        if after < 0:
            raise HTTPException(400, "Ask for an offset of 0 or more.")
        bound: dict[str, ParamValue] = {
            "session_id": session_id,
            "name": name,
            "after_chars": after,
            "chunk_chars": size,
        }
        with open_store(resolved) as connection:
            rows = page_rows(connection, Page.OFFLOAD, **bound)
        if not rows:
            raise HTTPException(404, "No offloaded result of that name is in this session.")
        row = rows[0]
        served = after + len(row["chunk"])
        return templates.TemplateResponse(
            request,
            "offload.html",
            {
                "session_id": session_id,
                "row": row,
                "size": size,
                # Where the next chunk starts, or None when this one reached the end.
                "after": served if served < row["content_chars"] else None,
                "citations": {Page.OFFLOAD.value: queries.citation(Page.OFFLOAD, bound)},
            },
        )

    def tools_under(
        connection: duckdb.DuckDBPyConnection,
        keyed: Mapping[str, ParamValue],
        after: int,
        size: int,
    ) -> ToolCalls:
        """One page of the tool calls under one api call — the nested list and its "+N more".

        The same query, the same page shape and the same citation whether the list arrives
        inline under a call or as the continuation the indicator fetches, so the two can
        never disagree about the set or about what produced it.
        """
        bound = dict(keyed) | {"after": after, "page_tools": size}
        return ToolCalls(
            paged(
                page_rows(connection, Fragment.CALL_TOOLS, **bound),
                "matched_tool_calls",
                "tool_index",
            ),
            queries.citation(Fragment.CALL_TOOLS, bound),
        )

    @app.get("/fragment/nav/{session_id}")
    def session_nav(
        request: Request,
        session_id: str,
        after: int = queries.FIRST_PAGE,
        turns: int = bounds.TURNS.default,
        nodes: int = bounds.NAV.default,
    ) -> Response:
        """The map beside a session page: every main-thread turn, with the runs under it.

        Its own response rather than part of the page, because the ceiling is per response and
        the page has no room left (`view/bounds.py`). `after` and `turns` are the page's window
        — all the map takes from it — and decide which nodes are marked as on screen.
        """
        checked(turns, bounds.TURNS.ceiling)
        checked(nodes, bounds.NAV.ceiling)
        with open_store(resolved) as connection:
            # The session's own spend, which every share on the map is a share of, and the
            # 404: a map of a session nothing recorded is not an empty sidebar.
            header = page_rows(
                connection,
                Page.SESSION_HEADER,
                session_id=session_id,
                head_chars=queries.HEADER_CHARS,
                item_chars=queries.HEADER_ITEM_CHARS,
                head_items=queries.HEADER_ITEMS,
            )
            if not header:
                raise HTTPException(404, "No session with that id is in this store.")
            outline = page_rows(
                connection, Page.SESSION_NAV, session_id=session_id, nav_chars=queries.NAV_CHARS
            )
            runs = page_rows(
                connection, Page.RUNS, session_id=session_id, chip_chars=queries.NAV_CHARS
            )
            enrichment = described(connection, session_id, MAIN_SOURCE)
        keyed: dict[str, ParamValue] = {"session_id": session_id}
        # What the fragment ran, in the order it ran it. Keyed alone everywhere the rest of a
        # query's bindings are its manifest defaults, and `view_runs` cites its head too: the
        # map reads a run's strings narrower than a session page chips them, so a line without
        # it re-runs at the default and answers text the map never showed. The enrichment query
        # is here only when the store held the tables to ask: a fragment cites what it ran, and
        # over an un-enriched store that query is not one of them.
        ran = [
            (Page.SESSION_NAV, keyed),
            (Page.RUNS, keyed | {"chip_chars": queries.NAV_CHARS}),
            (Page.SESSION_HEADER, keyed),
        ]
        if enrichment.queried:
            ran.append((Page.ENRICHMENT, keyed | {"source": MAIN_SOURCE}))
        return templates.TemplateResponse(
            request,
            "fragments/nav.html",
            {
                "nav": nav_tree(
                    session_id,
                    outline,
                    runs,
                    {item: row.description for item, row in enrichment.turns.items()},
                    header[0]["cost_usd"],
                    queries.NAV_CHARS,
                    on_page(outline, after, turns),
                    nodes,
                ),
                "citations": [queries.citation(named, bound) for named, bound in ran],
            },
        )

    @app.get("/fragment/turn/{session_id}/{source}/{turn_id}")
    def turn_calls(
        request: Request,
        session_id: str,
        source: str,
        turn_id: str,
        after: int = queries.FIRST_PAGE,
        calls: int = bounds.CALLS.default,
        tools: int = bounds.TOOLS.default,
    ) -> Response:
        """One page of the api calls a turn made, each carrying a page of its tool calls."""
        checked(calls, bounds.CALLS.ceiling)
        checked(tools, bounds.TOOLS.ceiling)
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
                    chip_chars=queries.CHIP_CHARS,
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
        tools: int = bounds.TOOLS.default,
    ) -> Response:
        """One page of the tool calls under one api call."""
        checked(tools, bounds.TOOLS.ceiling)
        keyed: dict[str, ParamValue] = {
            "session_id": session_id,
            "source": source,
            "api_call_id": api_call_id,
        }
        with open_store(resolved) as connection:
            under = tools_under(connection, keyed, after, tools)
        return templates.TemplateResponse(
            request,
            "fragments/tools.html",
            {
                "session_id": session_id,
                "source": source,
                "api_call_id": api_call_id,
                "page": under.page,
                "tools": tools,
                "citation": under.citation,
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
        # The keys travel into the context as well: a fragment that links anywhere needs the
        # session and thread it was fetched for, and they are exactly what keyed it.
        return templates.TemplateResponse(
            request,
            f"fragments/{template}.html",
            dict(keyed) | {"row": rows[0], "citation": queries.citation(value, keyed)},
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

    @app.get("/fragment/record/{session_id}/{source}/{line_no}")
    def record_value(request: Request, session_id: str, source: str, line_no: int) -> Response:
        """One raw transcript record whole, as the browser's preview was cut from."""
        return whole(
            request,
            Value.RECORD,
            "record",
            {"session_id": session_id, "source": source, "line_no": line_no},
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
