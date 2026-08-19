"""The trace viewer's routes: what each URL serves, and what it reads to serve it.

`build_app(db_path)` returns a FastAPI app over one trace store; `serve` runs it. Nothing
here writes: every request opens its own read-only connection (`view/store.py`), checks the
store's schema version, renders, and closes. That is what lets an extract run while a page is
open, and what makes a locked store a 503 rather than a crash.

The pages are built from library queries (`analyze/queries/`) — the viewer composes sort and
filter *around* a query's SELECT (`view/listing.py`) and binds every user-supplied value as a
parameter, so no request text ever reaches SQL.

Every node of a session — the session, a turn, a run, an api call, a tool call, a compaction,
and each of the two buckets — has a URL, and all eight serve the same response: the tree with
the path to that node open, beside the pane that reads it. `browse` is that response; a route
supplies only what its own kind needs (`view/nodes.py` for the vocabulary, `view/tree.py` for
where a node sits and what hangs under it).
"""

import datetime as dt
import socket
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple
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
from aiobserve.export.duckdb import SCHEMA_VERSION
from aiobserve.model import MAIN_SOURCE
from aiobserve.view import bounds, nodes, render, tree, walk
from aiobserve.view import format as fmt
from aiobserve.view.enrichment import Described, Descriptions, described, enriched
from aiobserve.view.labels import label
from aiobserve.view.listing import (
    ARIA_SORT,
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
from aiobserve.view.nodes import Kind, Ref, Shape
from aiobserve.view.store import (
    TURN_CURSOR,
    Fragment,
    Page,
    Paged,
    Row,
    SchemaMoved,
    StoreLocked,
    Value,
    open_store,
    page_rows,
    paged,
    window,
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


# What a node URL can name, at the value a link that names none is served at: the view, and
# the three sizes. Every href a node page mints carries whatever is *not* one of these
# (`knobs`), so a reader who picked a view or narrowed the tree keeps it as they walk, and an
# ordinary link stays short.
KNOB_DEFAULTS: dict[str, int | str] = {
    "nav": nodes.Preset.FULL,
    "kin": bounds.KIN.default,
    "log": bounds.LOG.default,
    "detail": bounds.DETAIL.default,
}


class Detail(NamedTuple):
    """One fat column of a node as its pane shows it: the head, and the way to the rest.

    A pane never decides how much of a value it shows — the head is cut in SQL at the
    `?detail=` the request asked for, and `cut` is what that left for the link to offer.
    """

    name: str
    head: str
    cut: int
    url: str


class LogRow(NamedTuple):
    """One row of a pane's children log: the node it links to, beside the row it reads."""

    node: nodes.Node
    row: Row


class Seen(NamedTuple):
    """What one node's own reads answered, whatever kind of node it is.

    `trail` is what the node already knows about where it sits, innermost last — a call and a
    tool name their turn in their own header, so neither costs a read to place; `tree.ancestry`
    resolves the rest. A kind that reads no children answers `Shape.NONE` and no rows.
    """

    header: Row
    trail: list[Ref]
    shape: Shape
    rows: list[LogRow]
    # How many children the log's page left behind, and the cursor the next one resumes at.
    more: int
    after: int | None
    details: list[Detail]
    # The transcript line the node was read from, where the store archived one. Only a turn
    # has one: `turns.id` is a record's `uuid`, which is the store's own join down to the
    # bytes Claude Code wrote.
    record: int | None
    ran: tree.Ran


# What one node route does beyond the reads every node page makes: its own header, its trail,
# and its children log. The session header is passed in because every page reads it already.
Reader = Callable[[duckdb.DuckDBPyConnection, tree.Corpus, Row], Seen]


def knobs(nav: nodes.Preset, kin: int, log: int, detail: int) -> str:
    """The query string every link on a node page carries: whatever is not a default."""
    given = {
        name: value
        for name, value in (("nav", nav), ("kin", kin), ("log", log), ("detail", detail))
        if value != KNOB_DEFAULTS[name]
    }
    return f"?{urlencode(given)}" if given else ""


def continued(url: str, marks: str, after: int) -> str:
    """Where a children log's "+N more" goes: this same node, one page further on."""
    return f"{url}{marks}{'&' if marks else '?'}after={after}"


def detail_of(name: str, head: str | None, chars: int | None, url: str, size: int) -> Detail | None:
    """One fat column as a pane shows it, or None where the store holds nothing under it.

    `head` arrives one character past `size`, which is how a value with more behind it is told
    from one that ends where the pane does; `chars` is the whole length the link offers.
    """
    if not head:
        return None
    return Detail(name, fmt.cut(head, size), (chars or 0) - size if len(head) > size else 0, url)


def sliced(items: Sequence[Row], after: int, size: int) -> Paged:
    """A page of rows already in memory, cut the way a query's keyset cuts one.

    The unattached runs are the case: they arrive with the session's runs, which every level of
    the tree needs anyway, so paging them is slicing rather than a second read. `after` is the
    position of the last row already shown, which is what every other `?after=` means too.
    """
    start = max(after + 1, 0)
    rows = list(items[start : start + size])
    behind = max(len(items) - start - len(rows), 0)
    return Paged(rows, behind, start + len(rows) - 1 if behind else None)


def described_node(descriptions: Descriptions, node: nodes.Node) -> Described | None:
    """What an enrichment pass said about the node a pane is about, when it said anything.

    Three of the eight kinds are describable, and the pass keys turns by thread — which is the
    thread the page was read for, so the selection is always in reach of its own description.
    """
    if node.kind is Kind.SESSION:
        return descriptions.session
    if node.kind is Kind.TURN:
        return descriptions.turns.get(node.node_id)
    if node.kind is Kind.RUN:
        return descriptions.runs.get(node.node_id)
    return None


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


def viewed(nav: str) -> nodes.Preset:
    """The filter preset from a query string, or a 400 — every node route's `?nav=` comes here.

    A 400 rather than a fallback to the full tree: a reader who typed a view the viewer does
    not have should be told, not served a different one under the URL they asked for.
    """
    if nav not in set(nodes.Preset):
        raise HTTPException(400, f"Filter the tree by one of: {', '.join(nodes.Preset)}.")
    return nodes.Preset(nav)


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

    def project_path(value: str | None) -> str:
        """A project directory, with the home of whoever is reading the page folded to `~`.

        Read per render like the clock above, and for the same reason: a test says who is
        reading, and the next page moves with it.
        """
        return fmt.path(value, fmt.home())

    templates.env.filters |= {
        "money": fmt.money,
        "count": fmt.count,
        "share": fmt.share,
        "percent": fmt.percent,
        "when": fmt.when,
        "clock": fmt.clock,
        "duration": fmt.duration,
        "text": fmt.text,
        "path": project_path,
        "ago": ago,
        # The three filters that print what a transcript wrote. Each hands back escaped
        # markup; `view/render.py` is where that escaping lives, and nothing here may add
        # `|safe`.
        "markdown": render.markdown,
        "pretty": render.pretty,
        "link": render.link,
    }

    # What a page calls each field it prints. The namespace is typed by what Jinja seeds it
    # with, which is why the assignment needs a word: a global is any callable a template names.
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
                "cut": (rows[0]["matched_projects"] - len(rows)) if rows else 0,
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
                # The same ordering in ARIA's vocabulary, for the heading that marks it: the
                # form and the links carry the query string's word, the mark carries ARIA's.
                "aria_direction": ARIA_SORT[direction],
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

    def browse(
        request: Request,
        session_id: str,
        source: str,
        nav: str,
        kin: int,
        log: int,
        detail: int,
        after: int,
        read: Reader,
    ) -> Response:
        """One node page: the tree with the path to the node open, beside the pane reading it.

        Every kind serves through here, because a node page is one response whatever the node
        is. `source` is the thread the enrichment is read for — `view_enrichment` keys turns by
        thread, and the tree spans the session, so a turn on another thread falls back to its
        prompt. What differs per kind is `read`, which answers the node's own header, where it
        sits, and what its children log lists, and 404s when the node is not in the store.
        """
        preset = viewed(nav)
        checked(kin, bounds.KIN.ceiling)
        checked(log, bounds.LOG.ceiling)
        checked(detail, bounds.DETAIL.ceiling)
        keyed: dict[str, ParamValue] = {"session_id": session_id}
        header_bound = keyed | {
            "head_chars": queries.HEADER_CHARS,
            "item_chars": queries.HEADER_ITEM_CHARS,
            "head_items": queries.HEADER_ITEMS,
        }
        runs_bound = keyed | {"chip_chars": queries.NAV_CHARS}
        with open_store(resolved) as connection:
            head = page_rows(connection, Page.SESSION_HEADER, **header_bound)
            if not head:
                raise HTTPException(404, "No session with that id is in this store.")
            # The session's runs whole, once: a run is placed by the call that spawned it
            # rather than by the thread it ran on, so any level of the tree may need any of
            # them, and both buckets are defined against the same set.
            corpus = tree.Corpus(
                session_id=session_id,
                whole=head[0]["cost_usd"] or 0,
                runs=page_rows(connection, Page.RUNS, **runs_bound),
                described=described(connection, session_id, source),
                source=source,
            )
            seen = read(connection, corpus, head[0])
            built = tree.tree(
                connection,
                corpus,
                nodes.session_node(head[0], corpus.described),
                tree.ancestry(corpus, seen.trail),
                preset,
                kin,
            )
            # What the reader reads before and after this node, off the same open path. Read
            # inside the request's own connection because it asks the store for levels the
            # tree did not open.
            walked = walk.neighbours(connection, corpus, built.chain)
        # A cursor past the last child and a node that never had one are the same answer. The
        # first page is not: a node with no children still has its own facts to show.
        if after != queries.FIRST_PAGE and not seen.rows:
            raise HTTPException(404, "This node has no children after that one.")
        selection = built.chain[-1]
        ran: tree.Ran = [
            (Page.SESSION_HEADER, header_bound),
            (Page.RUNS, runs_bound),
            *seen.ran,
            *built.ran,
            *walked.ran,
        ]
        # Only when the store held the tables to ask: a page cites what it ran, and over an
        # un-enriched store this query is not one of them.
        if corpus.described.queried:
            ran.append((Page.ENRICHMENT, keyed | {"source": source}))
        marks = knobs(preset, kin, log, detail)
        return templates.TemplateResponse(
            request,
            "node.html",
            {
                "selection": selection,
                "chain": built.chain,
                "rows": built.rows,
                "header": seen.header,
                "enrichment": described_node(corpus.described, selection),
                "details": seen.details,
                # The bytes behind the node: the thread's transcript, and — for a turn — the
                # one line it was read from.
                "source": source,
                "record": seen.record,
                # Where the reading order goes from here, in both directions.
                "previous": walked.previous,
                "following": walked.following,
                "shape": seen.shape,
                "log": seen.rows,
                "more": seen.more,
                "next": (
                    continued(selection.url, marks, seen.after) if seen.after is not None else None
                ),
                # What every href on the page carries, so a click serves the URL it displays.
                "suffix": marks,
                "citations": {named.value: queries.citation(named, bound) for named, bound in ran},
            },
        )

    def turn_log(corpus: tree.Corpus, source: str, rows: list[Row]) -> list[LogRow]:
        """A page of one thread's digest as a children log reads it: a row per turn."""
        return [
            LogRow(
                nodes.turn_node(
                    corpus.session_id,
                    source,
                    row,
                    corpus.whole,
                    corpus.turn_text(source, row["turn_id"]),
                ),
                row,
            )
            for row in rows
        ]

    def call_log(
        connection: duckdb.DuckDBPyConnection,
        corpus: tree.Corpus,
        source: str,
        turn_id: str | None,
        after: int,
        log: int,
    ) -> tuple[Paged, list[LogRow], tree.Ran]:
        """One page of the api calls under a turn — or, at `turn_id` NULL, under a bucket.

        One function for both because the two differ by that binding alone, which is the same
        rule the tree's level reads by: a call answering no turn sits in its thread's bucket.
        """
        bound: dict[str, ParamValue] = {
            "session_id": corpus.session_id,
            "source": source,
            "turn_id": turn_id,
            "after": after,
            "page_calls": log,
            "log_chars": queries.LOG_CHARS,
        }
        page = paged(
            page_rows(connection, Fragment.TURN_CALLS, **bound), "matched_api_calls", "call_index"
        )
        rows = [
            LogRow(nodes.call_node(corpus.session_id, source, row, corpus.whole), row)
            for row in page.rows
        ]
        return page, rows, [(Fragment.TURN_CALLS, bound)]

    def run_log(corpus: tree.Corpus, rows: list[Row]) -> list[LogRow]:
        """A list of agent runs as a children log reads it: a row per run."""
        return [
            LogRow(
                nodes.run_node(
                    corpus.session_id, row, corpus.whole, corpus.run_text(row["run_id"])
                ),
                row,
            )
            for row in rows
        ]

    @app.get("/session/{session_id}")
    def session_page(
        request: Request,
        session_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        after: int = queries.FIRST_PAGE,
    ) -> Response:
        """A session's own node: what it was, and its main thread as the tree's first level.

        `after` is *the last turn index already shown*, the records browser's semantics — so
        the turn at index N is the first row of `?after={N - 1}`.
        """

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            page = window(connection, Page.TIMELINE, TURN_CURSOR, after, log, session_id=session_id)
            return Seen(
                header=head,
                trail=[Ref(Kind.SESSION, None, session_id)],
                shape=Shape.TURNS,
                rows=turn_log(corpus, MAIN_SOURCE, page.rows),
                more=page.more,
                after=page.after,
                details=[],
                record=None,
                ran=[(Page.TIMELINE, {"session_id": session_id, "after": after, "limit": log})],
            )

        return browse(request, session_id, MAIN_SOURCE, nav, kin, log, detail, after, read)

    @app.get("/session/{session_id}/turn/{source}/{turn_id}")
    def turn_page(
        request: Request,
        session_id: str,
        source: str,
        turn_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        after: int = queries.FIRST_PAGE,
    ) -> Response:
        """One turn: what it was asked, and the api calls that answered it."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "turn_id": turn_id,
                "head_chars": queries.HEADER_CHARS,
                "detail_chars": detail,
            }
            rows = page_rows(connection, Page.TURN_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No turn with that id is in this thread.")
            # Which line of the transcript each turn of this thread came from. Read for the
            # whole thread because that is what the query answers; two identifier columns per
            # turn, and the pane keeps the one row it is about.
            thread: dict[str, ParamValue] = {"session_id": session_id, "source": source}
            archived = {
                row["turn_id"]: row["line_no"]
                for row in page_rows(connection, Page.TURN_RECORDS, **thread)
            }
            page, log_rows, ran = call_log(connection, corpus, source, turn_id, after, log)
            return Seen(
                header=rows[0],
                trail=[Ref(Kind.TURN, source, turn_id)],
                shape=Shape.CALLS,
                rows=log_rows,
                more=page.more,
                after=page.after,
                details=[
                    item
                    for item in (
                        detail_of(
                            "prompt",
                            rows[0]["prompt"],
                            rows[0]["prompt_chars"],
                            f"/fragment/prompt/{session_id}/{source}/{turn_id}",
                            detail,
                        ),
                    )
                    if item is not None
                ],
                record=archived.get(turn_id),
                ran=[(Page.TURN_HEADER, bound), *ran, (Page.TURN_RECORDS, thread)],
            )

        return browse(request, session_id, source, nav, kin, log, detail, after, read)

    @app.get("/session/{session_id}/run/{run_id}")
    def run_page(
        request: Request,
        session_id: str,
        run_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        after: int = queries.FIRST_PAGE,
    ) -> Response:
        """One agent run: the brief it was given, and its own thread of turns.

        A run's id is also the `source` its rows carry, which is why the URL needs no thread
        segment and why the enrichment is read at the run.
        """

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "run_id": run_id,
                "head_chars": queries.HEADER_CHARS,
                "detail_chars": detail,
            }
            rows = page_rows(connection, Page.RUN_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No run with that id is in this session.")
            page = window(
                connection,
                Page.RUN_TIMELINE,
                TURN_CURSOR,
                after,
                log,
                session_id=session_id,
                source=run_id,
            )
            return Seen(
                header=rows[0],
                trail=[Ref(Kind.RUN, run_id, run_id)],
                shape=Shape.TURNS,
                rows=turn_log(corpus, run_id, page.rows),
                more=page.more,
                after=page.after,
                details=[
                    item
                    for item in (
                        detail_of(
                            "description",
                            rows[0]["description"],
                            rows[0]["description_chars"],
                            f"/fragment/brief/{session_id}/{run_id}",
                            detail,
                        ),
                    )
                    if item is not None
                ],
                record=None,
                ran=[
                    (Page.RUN_HEADER, bound),
                    (
                        Page.RUN_TIMELINE,
                        {
                            "session_id": session_id,
                            "source": run_id,
                            "after": after,
                            "limit": log,
                        },
                    ),
                ],
            )

        return browse(request, session_id, run_id, nav, kin, log, detail, after, read)

    @app.get("/session/{session_id}/call/{source}/{api_call_id}")
    def call_page(
        request: Request,
        session_id: str,
        source: str,
        api_call_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        after: int = queries.FIRST_PAGE,
    ) -> Response:
        """One api call: what it answered, what it thought, and the tools it called."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "api_call_id": api_call_id,
                "head_chars": queries.HEADER_CHARS,
                "detail_chars": detail,
            }
            rows = page_rows(connection, Page.CALL_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No api call with that id is in this thread.")
            row = rows[0]
            tools: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "api_call_id": api_call_id,
                "after": after,
                "page_tools": log,
                "log_chars": queries.LOG_CHARS,
            }
            page = paged(
                page_rows(connection, Fragment.CALL_TOOLS, **tools),
                "matched_tool_calls",
                "tool_index",
            )
            return Seen(
                header=row,
                # The call's own header says which turn it answers, so its place costs no
                # read: a NULL turn puts it in its thread's unattributed bucket instead.
                trail=[tree.home(source, row["turn_id"]), Ref(Kind.CALL, source, api_call_id)],
                shape=Shape.TOOLS,
                rows=[
                    LogRow(nodes.tool_node(session_id, source, item), item) for item in page.rows
                ],
                more=page.more,
                after=page.after,
                details=[
                    item
                    for item in (
                        detail_of(
                            "text",
                            row["text_head"],
                            row["text_chars"],
                            f"/fragment/text/{session_id}/{source}/{api_call_id}",
                            detail,
                        ),
                        detail_of(
                            "thinking",
                            row["thinking_head"],
                            row["thinking_chars"],
                            f"/fragment/thinking/{session_id}/{source}/{api_call_id}",
                            detail,
                        ),
                    )
                    if item is not None
                ],
                record=None,
                ran=[(Page.CALL_HEADER, bound), (Fragment.CALL_TOOLS, tools)],
            )

        return browse(request, session_id, source, nav, kin, log, detail, after, read)

    @app.get("/session/{session_id}/tool/{source}/{tool_call_id}")
    def tool_page(
        request: Request,
        session_id: str,
        source: str,
        tool_call_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        after: int = queries.FIRST_PAGE,
    ) -> Response:
        """One tool call: what it was passed, and what it returned. Nothing hangs under it."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "tool_call_id": tool_call_id,
                "head_chars": queries.HEADER_CHARS,
                "detail_chars": detail,
            }
            rows = page_rows(connection, Page.TOOL_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No tool call with that id is in this thread.")
            row = rows[0]
            return Seen(
                header=row,
                # The whole path down, out of one read: the call that made it, and the turn
                # that call answers — else that thread's bucket, by the same rule.
                trail=[
                    tree.home(source, row["turn_id"]),
                    Ref(Kind.CALL, source, row["api_call_id"]),
                    Ref(Kind.TOOL, source, tool_call_id),
                ],
                shape=Shape.NONE,
                rows=[],
                more=0,
                after=None,
                details=[
                    item
                    for item in (
                        detail_of(
                            "input",
                            row["input_head"],
                            row["input_chars"],
                            f"/fragment/input/{session_id}/{source}/{tool_call_id}",
                            detail,
                        ),
                        detail_of(
                            "result",
                            row["result_head"],
                            row["result_chars"],
                            f"/fragment/result/{session_id}/{source}/{tool_call_id}",
                            detail,
                        ),
                    )
                    if item is not None
                ],
                record=None,
                ran=[(Page.TOOL_HEADER, bound)],
            )

        return browse(request, session_id, source, nav, kin, log, detail, after, read)

    @app.get("/session/{session_id}/compaction/{source}/{compaction_id}")
    def compaction_page(
        request: Request,
        session_id: str,
        source: str,
        compaction_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        after: int = queries.FIRST_PAGE,
    ) -> Response:
        """One compaction: where a thread's context was rewritten, and what that cost it.

        Read out of the thread's markers rather than by id — a compaction has no query of its
        own because the thread's whole set is what the tree beside it renders anyway.
        """

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "chip_chars": queries.HEADER_CHARS,
            }
            found = [
                row
                for row in page_rows(connection, Page.COMPACTIONS, **bound)
                if row["compaction_id"] == compaction_id
            ]
            if not found:
                raise HTTPException(404, "No compaction with that id is in this thread.")
            return Seen(
                header=found[0],
                trail=[Ref(Kind.COMPACTION, source, compaction_id)],
                shape=Shape.NONE,
                rows=[],
                more=0,
                after=None,
                details=[],
                record=None,
                ran=[(Page.COMPACTIONS, bound)],
            )

        return browse(request, session_id, source, nav, kin, log, detail, after, read)

    @app.get("/session/{session_id}/unattributed/{source}")
    def unattributed_page(
        request: Request,
        session_id: str,
        source: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        after: int = queries.FIRST_PAGE,
    ) -> Response:
        """One thread's api calls that answer no turn — a resume's calls answer turns that
        live in the session it resumed, and this is where they are read."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            standing = tree.unattributed(connection, corpus, source)
            if standing is None:
                raise HTTPException(404, "Every api call on this thread answers a turn.")
            page, log_rows, ran = call_log(connection, corpus, source, None, after, log)
            return Seen(
                header=standing.row,
                trail=[Ref(Kind.UNATTRIBUTED, source, source)],
                shape=Shape.CALLS,
                rows=log_rows,
                more=page.more,
                after=page.after,
                details=[],
                record=None,
                ran=[standing.ran, *ran],
            )

        return browse(request, session_id, source, nav, kin, log, detail, after, read)

    @app.get("/session/{session_id}/unattached")
    def unattached_page(
        request: Request,
        session_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        after: int = queries.FIRST_PAGE,
    ) -> Response:
        """The session's agent runs no spawning call resolved.

        Session-scoped rather than per thread: what makes a run unattached is that nothing says
        which thread spawned it, so the bucket hangs off the session itself.
        """

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            loose = [run for run in corpus.runs if run["spawn_source"] is None]
            if not loose:
                raise HTTPException(404, "Every agent run in this session was placed.")
            page = sliced(loose, after, log)
            return Seen(
                header=head,
                trail=[Ref(Kind.UNATTACHED, None, session_id)],
                shape=Shape.RUNS,
                rows=run_log(corpus, page.rows),
                more=page.more,
                after=page.after,
                details=[],
                record=None,
                ran=[],
            )

        return browse(request, session_id, MAIN_SOURCE, nav, kin, log, detail, after, read)

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

    @app.get("/fragment/input/{session_id}/{source}/{tool_call_id}")
    def tool_input(request: Request, session_id: str, source: str, tool_call_id: str) -> Response:
        """What one tool call was passed, whole."""
        return whole(
            request,
            Value.TOOL_INPUT,
            "raw",
            {"session_id": session_id, "source": source, "tool_call_id": tool_call_id},
        )

    @app.get("/fragment/result/{session_id}/{source}/{tool_call_id}")
    def tool_result(request: Request, session_id: str, source: str, tool_call_id: str) -> Response:
        """What one tool call returned, whole — the largest single fetch the viewer makes."""
        return whole(
            request,
            Value.TOOL_RESULT,
            "raw",
            {"session_id": session_id, "source": source, "tool_call_id": tool_call_id},
        )

    @app.get("/fragment/prompt/{session_id}/{source}/{turn_id}")
    def turn_prompt(request: Request, session_id: str, source: str, turn_id: str) -> Response:
        """What one turn was asked, whole."""
        return whole(
            request,
            Value.TURN_PROMPT,
            "value",
            {"session_id": session_id, "source": source, "turn_id": turn_id},
        )

    @app.get("/fragment/brief/{session_id}/{run_id}")
    def run_brief(request: Request, session_id: str, run_id: str) -> Response:
        """The whole brief one agent run was given."""
        return whole(
            request, Value.RUN_BRIEF, "value", {"session_id": session_id, "run_id": run_id}
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
