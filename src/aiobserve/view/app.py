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

import socket
import webbrowser
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
from aiobserve.view import format as fmt
from aiobserve.view import render
from aiobserve.view.listing import (
    CONTROLS,
    DEFAULT_DIRECTION,
    DEFAULT_SORT,
    DIRECTIONS,
    FILTERS,
    MAX_PAGE_SESSIONS,
    PAGE_SESSIONS,
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
    fetch,
    open_store,
    page_rows,
    paged,
)
from aiobserve.view.threads import ancestry, children, session_threads, timeline

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

# The most a page or a fragment will serve at once, whatever a URL asks for — so these, not
# the manifest defaults, are the numbers the payload bound is arithmetic over. A size is
# something a reader types. The turn fragment's two sizes multiply, and 12 KB a call row
# spends the ceiling at the defaults themselves, so `?calls=` and `?tools=` only go down.
MAX_PAGE_CALLS = queries.PAGE_CALLS
MAX_PAGE_TOOLS = queries.PAGE_TOOLS
MAX_PAGE_RECORDS = 200
# The offload ceiling is the one set by escaping alone rather than by a row's markup: the
# content is a file some tool wrote, and a chunk of nothing but `&` weighs five bytes a
# character.
MAX_CHUNK_CHARS = 60_000


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
    templates.env.filters |= {
        "money": fmt.money,
        "count": fmt.count,
        "when": fmt.when,
        "clock": fmt.clock,
        "duration": fmt.duration,
        # The three filters that print what a transcript wrote. Each hands back escaped
        # markup; `view/render.py` is where that escaping lives, and nothing here may add
        # `|safe`.
        "markdown": render.markdown,
        "pretty": render.pretty,
        "link": render.link,
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
            lines = turn_lines(connection, session_id, MAIN_SOURCE)
        keyed: dict[str, ParamValue] = {"session_id": session_id}
        at_source = keyed | {"source": MAIN_SOURCE}
        threads = session_threads(turns, runs, markers)
        return templates.TemplateResponse(
            request,
            "session.html",
            {
                "header": header[0],
                "main": MAIN_SOURCE,
                "timeline": threads.entries,
                "unattached": threads.unattached,
                "lines": lines,
                "citations": {
                    page.value: queries.citation(
                        page, at_source if page in (Page.COMPACTIONS, Page.TURN_RECORDS) else keyed
                    )
                    for page in (
                        Page.SESSION_HEADER,
                        Page.TIMELINE,
                        Page.RUNS,
                        Page.COMPACTIONS,
                        Page.TURN_RECORDS,
                    )
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
            lines = turn_lines(connection, session_id, run_id)
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
                "lines": lines,
                "citations": {
                    Page.RUN_HEADER.value: queries.citation(
                        Page.RUN_HEADER, keyed | {"run_id": run_id}
                    ),
                    Page.RUN_TIMELINE.value: queries.citation(Page.RUN_TIMELINE, at_source),
                    Page.RUNS.value: queries.citation(Page.RUNS, keyed),
                    Page.COMPACTIONS.value: queries.citation(Page.COMPACTIONS, at_source),
                    Page.TURN_RECORDS.value: queries.citation(Page.TURN_RECORDS, at_source),
                },
            },
        )

    @app.get("/session/{session_id}/records/{source}")
    def records_page(
        request: Request,
        session_id: str,
        source: str,
        after: int = queries.FIRST_PAGE,
        size: int = queries.PAGE_RECORDS,
    ) -> Response:
        """One page of a thread's raw transcript — where a report's citation lands.

        A citation names `(session_id, source, line_no)`; the URL for it is this path with
        `?after={line_no - 1}#L{line_no}`, so the cited record is the first row on the page.
        """
        checked(size, MAX_PAGE_RECORDS)
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
        size: int = queries.CHUNK_CHARS,
    ) -> Response:
        """One chunk of a tool result Claude Code wrote to a file beside the transcript.

        `name` is the transcript's own file name, so it may hold anything a tool named a file
        — spaces, percent signs, something shaped like a path. It is a key into the store and
        never a path the server opens, which is what makes the shape of it uninteresting.
        """
        checked(size, MAX_CHUNK_CHARS)
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
