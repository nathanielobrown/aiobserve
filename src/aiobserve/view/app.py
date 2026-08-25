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
from dataclasses import replace
from math import ceil
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
from aiobserve.view import bounds, errors, highlight, nodes, render, tree, walk
from aiobserve.view import format as fmt
from aiobserve.view.enrichment import (
    GLYPH,
    GLYPH_CLASS,
    Described,
    Descriptions,
    described,
    enriched,
)
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
    Listed,
    Page,
    Row,
    SchemaMoved,
    StoreLocked,
    Value,
    listed,
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
    # What the head is marked up as, where the record says what the value is written in — the
    # shell a `Bash` call ran, the file a `Read` returned. None is prose, which is most of a
    # transcript and which the pane prints as it was stored.
    syntax: highlight.Syntax | None


# Where the SQL behind a page is read. Every citation in a footer links here, so the path is
# written once and the route below takes the query's name from it.
QUERY_URL = "/query"


class Cited(NamedTuple):
    """One query a page ran, as the footer shows it: the line to re-run, and where to read it."""

    line: str
    url: str


def cited(name: str, bindings: Mapping[str, ParamValue]) -> Cited:
    """What produced a page, both ways a reader follows it.

    The line is what a report quotes and a shell re-runs; the URL is the same query as a page,
    bindings and all. Both spell a binding the one way `queries.shown` does, so the link a
    footer carries and the comment beside it cannot disagree about what was bound.
    """
    written = {key: queries.shown(value) for key, value in bindings.items()}
    return Cited(queries.citation(name, bindings), f"{QUERY_URL}/{name}?{urlencode(written)}")


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
    # How many children the level holds in all, which is more than the page shows whenever
    # the level runs past `?log=`: the heading counts the level, and the pager divides it.
    total: int
    details: list[Detail]
    # The transcript line the node was read from, where the store archived one. Only a turn
    # has one: `turns.id` is a record's `uuid`, which is the store's own join down to the
    # bytes Claude Code wrote.
    record: int | None
    ran: tree.Ran


# What one node route does beyond the reads every node page makes: its own header, its trail,
# and its children log. The session header is passed in because every page reads it already.
Reader = Callable[[duckdb.DuckDBPyConnection, tree.Corpus, Row], Seen]


class Body(NamedTuple):
    """How one kind answers an expansion: the header it reads, and what it says is under it.

    An expansion is the node's own body and nothing else, so `children` is the column counting
    what the full view would have listed — a count and a link stand in for the list, because an
    accordion of accordions is a page and the node already has one. `shape` names those
    children the way the full view's log heading does. A kind with neither ends the tree.
    """

    page: Page
    # The binding the header query takes the node's id as.
    keyed: str
    build: Callable[[str, str, Row, str | None], nodes.Node]
    shape: Shape
    children: str | None
    # Whether a pass can have described this kind, and so whether the label may be the model's.
    described: bool


# Every kind a children log lists, except the run: a run's URL carries its id where the others
# carry a thread, so it has a mount of its own.
BODIES: dict[str, Body] = {
    Kind.TURN: Body(
        Page.TURN_HEADER,
        "turn_id",
        lambda session_id, source, row, text: nodes.turn_node(session_id, source, row, 0, text),
        Shape.CALLS,
        "api_calls",
        described=True,
    ),
    Kind.CALL: Body(
        Page.CALL_HEADER,
        "api_call_id",
        lambda session_id, source, row, _: nodes.call_node(session_id, source, row, 0),
        Shape.TOOLS,
        "tool_calls",
        described=False,
    ),
    Kind.TOOL: Body(
        Page.TOOL_HEADER,
        "tool_call_id",
        lambda session_id, source, row, _: nodes.tool_node(session_id, source, row),
        Shape.NONE,
        None,
        described=False,
    ),
}


# How a pane names the node it is about, per kind, from the header its own route read. The
# tree built the row the pane stands on and cut its words where a tree row ends, which is a
# third of what a title has to spend (`nodes.Node.pane_title`) — so a page that took the tree's
# word for it would head a turn with the first line of the prompt and stop. The kinds absent
# are the ones no cut reaches: a session's node is read from its own header already, a
# compaction is named by its trigger, and a bucket is named by the viewer.
TITLED: dict[str, Callable[[str, str, Row, tree.Corpus], nodes.Node]] = {
    Kind.TURN: lambda session_id, source, row, corpus: nodes.turn_node(
        session_id, source, row, corpus.whole, corpus.turn_text(source, row["turn_id"])
    ),
    Kind.CALL: lambda session_id, source, row, corpus: nodes.call_node(
        session_id, source, row, corpus.whole
    ),
    Kind.TOOL: lambda session_id, source, row, _: nodes.tool_node(session_id, source, row),
    Kind.RUN: lambda session_id, _, row, corpus: nodes.run_node(
        session_id, row, corpus.whole, corpus.run_text(row["run_id"])
    ),
}


def knobs(nav: nodes.Preset, kin: int, log: int, detail: int) -> str:
    """The query string every link on a node page carries: whatever is not a default."""
    given = {
        name: value
        for name, value in (("nav", nav), ("kin", kin), ("log", log), ("detail", detail))
        if value != KNOB_DEFAULTS[name]
    }
    return f"?{urlencode(given)}" if given else ""


class Switch(NamedTuple):
    """One fold as the control above the tree offers it: where it goes, and whether we are in it."""

    preset: nodes.Preset
    url: str
    current: bool


def switcher(node: nodes.Node, nav: nodes.Preset, kin: int, log: int, detail: int) -> list[Switch]:
    """The node the reader is on under each fold, so switching never costs them their place."""
    return [
        Switch(choice, f"{node.url}{knobs(choice, kin, log, detail)}", choice is nav)
        for choice in nodes.Preset
    ]


def numbered(url: str, marks: str, page: int) -> str:
    """One page of a node's children log as a URL: the node, its knobs, and the page number.

    Page one is the node's own URL. A reader who pages back to the start has to land on the
    document a link to the node serves, and it is the one the payload sweep prices.
    """
    if page == 1:
        return f"{url}{marks}"
    return f"{url}{marks}{'&' if marks else '?'}page={page}"


class Pager(NamedTuple):
    """A children log's place in its level, and the way to either side of it."""

    # Which page of how many, in words — the label the control is read and heard by.
    place: str
    previous: str | None
    following: str | None


def pager(url: str, marks: str, page: int, pages: int) -> Pager | None:
    """The control under a children log, or None where the level is one page long."""
    if pages < 2:
        return None
    return Pager(
        place=f"Page {page} of {pages}",
        previous=numbered(url, marks, page - 1) if page > 1 else None,
        following=numbered(url, marks, page + 1) if page < pages else None,
    )


def skipped(page: int, size: int) -> int:
    """How many children the pages before this one held — what a numbered page binds to skip."""
    return (page - 1) * size


def detail_of(
    name: str,
    head: str | None,
    chars: int | None,
    url: str,
    size: int,
    syntax: highlight.Syntax | None = None,
) -> Detail | None:
    """One fat column as a pane shows it, or None where the store holds nothing under it.

    `head` arrives one character past `size`, which is how a value with more behind it is told
    from one that ends where the pane does; `chars` is the whole length the link offers.
    `syntax` is what the record says the value is written in, and the default is prose:
    everything a session wrote is prose until something in the row says otherwise.
    """
    if not head:
        return None
    cut = (chars or 0) - size if len(head) > size else 0
    return Detail(name, fmt.cut(head, size), cut, url, syntax)


def sliced(items: Sequence[Row], page: int, size: int) -> Listed:
    """One numbered page of rows already in memory, cut the way a query's OFFSET cuts one.

    The unattached runs are the case: they arrive with the session's runs, which every level of
    the tree needs anyway, so paging them is slicing rather than a second read.
    """
    start = skipped(page, size)
    return Listed(list(items[start : start + size]), len(items))


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


def carried(nav: str, kin: int, log: int, detail: int) -> str:
    """The knobs a request asked for, checked and minted back into the suffix its links carry."""
    return knobs(
        viewed(nav),
        checked(kin, bounds.KIN.ceiling),
        checked(log, bounds.LOG.ceiling),
        checked(detail, bounds.DETAIL.ceiling),
    )


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

    def line(value: str | None) -> str:
        """A row's string at the width a children log prints it, marked where it was cut.

        The template's half of the one-extra-character protocol: every string a log row prints
        comes back from its query one character past this width, so a value that arrives longer
        than the cut is a value with more behind it. What `nodes.Node.log_title` does for a
        node's title, for the columns a row prints straight off the row.
        """
        return fmt.ABSENT if value is None else fmt.cut(value, queries.LOG_CHARS)

    def head(value: object) -> object:
        """A header's value as a pane prints it: a string cut and marked, anything else as is.

        The same half of the protocol `line` holds, at the pane's width — every string a
        header query previews comes back one character past this cut. Applied by
        `_parts.html:fact` to every value that reaches it rather than at the rows that need
        it, so a fact added beside them inherits the bound instead of printing a value whole.
        A header's other facts are flags and already-formatted numbers, and only a string the
        store holds can be longer than the pane: those go through as `text` leaves them.
        """
        if value is None:
            return fmt.ABSENT
        return fmt.cut(value, queries.HEADER_CHARS) if isinstance(value, str) else value

    templates.env.filters |= {
        "money": fmt.money,
        "count": fmt.count,
        "share": fmt.share,
        "when": fmt.when,
        "clock": fmt.clock,
        "duration": fmt.duration,
        "text": fmt.text,
        "line": line,
        "head": head,
        "path": project_path,
        "ago": ago,
        # The three filters that print what a transcript wrote. Each hands back escaped
        # markup; `view/render.py` and `view/highlight.py` are where that escaping lives, and
        # nothing here may add `|safe`.
        "markdown": render.markdown,
        "lit": highlight.lit,
        "link": render.link,
    }

    # What a page calls each field it prints. The namespace is typed by what Jinja seeds it
    # with, which is why the assignment needs a word: a global is any callable a template names.
    templates.env.globals["label"] = label  # pyrefly: ignore
    # And the mark every model-written string carries, beside the class that styles it.
    templates.env.globals["GLYPH"] = GLYPH  # pyrefly: ignore
    templates.env.globals["GLYPH_CLASS"] = GLYPH_CLASS  # pyrefly: ignore
    # And how long a value may be before a page prints it plain rather than marked up, which
    # is what the line beside a plain value says.
    templates.env.globals["HIGHLIGHT_CHARS"] = bounds.HIGHLIGHT_CHARS  # pyrefly: ignore
    # The syntaxes a template may ask for, so that asking for one it does not mark up raises
    # here rather than rendering a value as a line of error tokens.
    templates.env.globals["SYNTAX"] = highlight.Syntax  # pyrefly: ignore
    # And where an agent run reads, for the one link a template mints from a column rather than
    # from a node: the `Task` call that started the run.
    templates.env.globals["run_url"] = nodes.run_url  # pyrefly: ignore
    # And the thread a page is reading, which heads every path a template writes that no node
    # stands behind: the raw transcript, and the fetch of one archived record.
    templates.env.globals["thread_url"] = nodes.thread_url  # pyrefly: ignore
    # The columns each children log heads and fills, so the head and the rows cannot drift
    # apart, and how many of them an expansion opened under a row has to span.
    templates.env.globals["COLUMNS"] = nodes.COLUMNS  # pyrefly: ignore
    templates.env.globals["spanned"] = nodes.spanned  # pyrefly: ignore

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
                "citations": {Page.PROJECT_ROLLUPS.value: cited(Page.PROJECT_ROLLUPS, bound)},
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
                    Page.SESSIONS.value: cited(
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
                            Page.DESCRIBED_SESSIONS.value: cited(
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

    def header_bound(session_id: str) -> dict[str, ParamValue]:
        """What `Page.SESSION_HEADER` binds for one session, named once for every reader of it.

        A node page reads the row whole; `errors_page` reads it only to word a 404, but both
        have to bind the same params or a change to one silently stops answering for the other.
        """
        return {
            "session_id": session_id,
            "head_chars": queries.HEADER_CHARS,
            "item_chars": queries.HEADER_ITEM_CHARS,
            "head_items": queries.HEADER_ITEMS,
        }

    def browse(
        request: Request,
        session_id: str,
        source: str,
        nav: str,
        kin: int,
        log: int,
        detail: int,
        page: int,
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
        # A page number below the first is a bad ask like a size outside its bounds, and is
        # answered the same way: no level has such a page, so what is wrong is the number and
        # not the node the URL names. Asked before anything is read — it would otherwise bind
        # a negative offset. A number past a level's *last* page is a 404 further down: that
        # one is a question about the node, and only the level can answer it.
        if page < 1:
            raise HTTPException(400, "Ask for a children log page from one upwards.")
        bound = header_bound(session_id)
        # The session's runs are read once and printed twice: as a tree row at a label's width
        # and as a children log row at a line's. Cut to the wider of the two here, and cut
        # again at each — a row cut to the narrower would print a line already stopped.
        runs_bound = {"session_id": session_id, "chip_chars": queries.LOG_CHARS}
        with open_store(resolved) as connection:
            head = page_rows(connection, Page.SESSION_HEADER, **bound)
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
            # The failures either side of this one, read only where the pane is standing on a
            # failure. A session-wide list is a query per page load and the step it answers
            # does not exist anywhere else, so every other node page asks the store nothing.
            failed = (
                errors.failures(connection, session_id)
                if built.chain[-1].kind is Kind.TOOL and built.chain[-1].is_error
                else None
            )
        # A page past the last of a level and a node that never had one are the same answer.
        # The first page is not: a node with no children still has its own facts to show.
        if page > 1 and not seen.rows:
            raise HTTPException(404, "This node's children do not run to that page.")
        selection = built.chain[-1]
        # Named from its own header rather than from the tree row it stands on (`TITLED`).
        # The words alone: what the node cost and what share of the session that is are the
        # tree's to work out, against the whole session rather than against one header.
        if (titled := TITLED.get(selection.kind)) is not None:
            selection = replace(
                selection, words=titled(session_id, source, seen.header, corpus).words
            )
        ran: tree.Ran = [
            (Page.SESSION_HEADER, bound),
            (Page.RUNS, runs_bound),
            *seen.ran,
            *built.ran,
            *walked.ran,
        ]
        # Only when the store held the tables to ask: a page cites what it ran, and over an
        # un-enriched store this query is not one of them.
        if corpus.described.queried:
            ran.append((Page.ENRICHMENT, {"session_id": session_id, "source": source}))
        # The same rule for the stepper's own read: a page cites what it ran, and most node
        # pages do not run this one.
        if failed is not None:
            ran.extend(failed.ran)
        marks = knobs(preset, kin, log, detail)
        return templates.TemplateResponse(
            request,
            "node.html",
            {
                "selection": selection,
                "presets": switcher(selection, preset, kin, log, detail),
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
                # And where the session failed: how many failures it holds, which is what the
                # way into the list says, beside the step to the next one where there is one.
                "session_tool_errors": head[0]["tool_errors"],
                "step": errors.stepped(failed.listed, selection) if failed else None,
                "shape": seen.shape,
                "log": seen.rows,
                # The level's own size, and where in it this page sits — the heading counts
                # the first, the control under the log reads the second.
                "total": seen.total,
                "pager": pager(selection.url, marks, page, ceil(seen.total / log)),
                # What every href on the page carries, so a click serves the URL it displays.
                "suffix": marks,
                "citations": {named.value: cited(named, bound) for named, bound in ran},
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
        page: int,
        log: int,
    ) -> tuple[Listed, list[LogRow], tree.Ran]:
        """One page of the api calls under a turn — or, at `turn_id` NULL, under a bucket.

        One function for both because the two differ by that binding alone, which is the same
        rule the tree's level reads by: a call answering no turn sits in its thread's bucket.
        """
        bound: dict[str, ParamValue] = {
            "session_id": corpus.session_id,
            "source": source,
            "turn_id": turn_id,
            "skipped": skipped(page, log),
            "page_calls": log,
            "log_chars": queries.LOG_CHARS,
        }
        calls = listed(page_rows(connection, Fragment.TURN_CALLS, **bound), "matched_api_calls")
        rows = [
            LogRow(nodes.call_node(corpus.session_id, source, row, corpus.whole), row)
            for row in calls.rows
        ]
        return calls, rows, [(Fragment.TURN_CALLS, bound)]

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
        page: int = 1,
    ) -> Response:
        """A session's own node: what it was, and its main thread as the tree's first level."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            offset = skipped(page, log)
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "log_chars": queries.LOG_CHARS,
            }
            turns = window(connection, Page.TIMELINE, TURN_CURSOR, offset, log, **bound)
            return Seen(
                header=head,
                trail=[Ref(Kind.SESSION, None, session_id)],
                shape=Shape.TURNS,
                rows=turn_log(corpus, MAIN_SOURCE, turns.rows),
                total=turns.total,
                details=[],
                record=None,
                ran=[(Page.TIMELINE, bound | {"offset": offset, "limit": log})],
            )

        return browse(request, session_id, MAIN_SOURCE, nav, kin, log, detail, page, read)

    @app.get("/session/{session_id}/thread/{source}/turn/{turn_id}")
    def turn_page(
        request: Request,
        session_id: str,
        source: str,
        turn_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
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
            at = f"{nodes.thread_url(session_id, source)}/turn/{turn_id}"
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
            calls, log_rows, ran = call_log(connection, corpus, source, turn_id, page, log)
            return Seen(
                header=rows[0],
                trail=[Ref(Kind.TURN, source, turn_id)],
                shape=Shape.CALLS,
                rows=log_rows,
                total=calls.total,
                details=[
                    item
                    for item in (
                        detail_of(
                            "prompt",
                            rows[0]["prompt"],
                            rows[0]["prompt_chars"],
                            f"/fragment/prompt{at}",
                            detail,
                        ),
                        detail_of(
                            "command_args",
                            rows[0]["command_args"],
                            rows[0]["command_args_chars"],
                            f"/fragment/args{at}",
                            detail,
                        ),
                    )
                    if item is not None
                ],
                record=archived.get(turn_id),
                ran=[(Page.TURN_HEADER, bound), *ran, (Page.TURN_RECORDS, thread)],
            )

        return browse(request, session_id, source, nav, kin, log, detail, page, read)

    @app.get("/session/{session_id}/run/{run_id}")
    def run_page(
        request: Request,
        session_id: str,
        run_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
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
            offset = skipped(page, log)
            timeline: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": run_id,
                "log_chars": queries.LOG_CHARS,
            }
            turns = window(connection, Page.RUN_TIMELINE, TURN_CURSOR, offset, log, **timeline)
            return Seen(
                header=rows[0],
                trail=[Ref(Kind.RUN, run_id, run_id)],
                shape=Shape.TURNS,
                rows=turn_log(corpus, run_id, turns.rows),
                total=turns.total,
                details=[
                    item
                    for item in (
                        detail_of(
                            "description",
                            rows[0]["description"],
                            rows[0]["description_chars"],
                            f"/fragment/brief{nodes.run_url(session_id, run_id)}",
                            detail,
                        ),
                    )
                    if item is not None
                ],
                record=None,
                ran=[
                    (Page.RUN_HEADER, bound),
                    (Page.RUN_TIMELINE, timeline | {"offset": offset, "limit": log}),
                ],
            )

        return browse(request, session_id, run_id, nav, kin, log, detail, page, read)

    @app.get("/session/{session_id}/thread/{source}/call/{api_call_id}")
    def call_page(
        request: Request,
        session_id: str,
        source: str,
        api_call_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
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
            at = f"{nodes.thread_url(session_id, source)}/call/{api_call_id}"
            rows = page_rows(connection, Page.CALL_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No api call with that id is in this thread.")
            row = rows[0]
            tools: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "api_call_id": api_call_id,
                "skipped": skipped(page, log),
                "page_tools": log,
                "log_chars": queries.LOG_CHARS,
            }
            called = listed(
                page_rows(connection, Fragment.CALL_TOOLS, **tools), "matched_tool_calls"
            )
            return Seen(
                header=row,
                # The call's own header says which turn it answers, so its place costs no
                # read: a NULL turn puts it in its thread's unattributed bucket instead.
                trail=[tree.home(source, row["turn_id"]), Ref(Kind.CALL, source, api_call_id)],
                shape=Shape.TOOLS,
                rows=[
                    LogRow(nodes.tool_node(session_id, source, item), item) for item in called.rows
                ],
                total=called.total,
                details=[
                    item
                    for item in (
                        detail_of(
                            "text",
                            row["text_head"],
                            row["text_chars"],
                            f"/fragment/text{at}",
                            detail,
                        ),
                        detail_of(
                            "thinking",
                            row["thinking_head"],
                            row["thinking_chars"],
                            f"/fragment/thinking{at}",
                            detail,
                        ),
                    )
                    if item is not None
                ],
                record=None,
                ran=[(Page.CALL_HEADER, bound), (Fragment.CALL_TOOLS, tools)],
            )

        return browse(request, session_id, source, nav, kin, log, detail, page, read)

    @app.get("/session/{session_id}/thread/{source}/tool/{tool_call_id}")
    def tool_page(
        request: Request,
        session_id: str,
        source: str,
        tool_call_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
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
            at = f"{nodes.thread_url(session_id, source)}/tool/{tool_call_id}"
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
                total=0,
                details=[
                    item
                    for item in (
                        # The command first, where the call ran one: it is what the input is
                        # about, and the input below it is the record it was read out of.
                        detail_of(
                            "command",
                            row["command"],
                            row["command_chars"],
                            f"/fragment/command{at}",
                            detail,
                            highlight.Syntax.BASH,
                        ),
                        detail_of(
                            "input",
                            row["input"],
                            row["input_chars"],
                            f"/fragment/input{at}",
                            detail,
                        ),
                        detail_of(
                            "result",
                            row["result_head"],
                            row["result_chars"],
                            f"/fragment/result{at}",
                            detail,
                            highlight.by_suffix(row["result_type"]),
                        ),
                    )
                    if item is not None
                ],
                record=None,
                ran=[(Page.TOOL_HEADER, bound)],
            )

        return browse(request, session_id, source, nav, kin, log, detail, page, read)

    @app.get("/session/{session_id}/thread/{source}/compaction/{compaction_id}")
    def compaction_page(
        request: Request,
        session_id: str,
        source: str,
        compaction_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
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
            # Where it hangs is what the query already answered: under the turn it happened
            # during, else beside the turns of its thread. Seeded rather than resolved,
            # because a turn a timestamp lands in is a read this row has made.
            turn_id = found[0]["turn_id"]
            return Seen(
                header=found[0],
                trail=[
                    *([Ref(Kind.TURN, source, turn_id)] if turn_id is not None else []),
                    Ref(Kind.COMPACTION, source, compaction_id),
                ],
                shape=Shape.NONE,
                rows=[],
                total=0,
                details=[],
                record=None,
                ran=[(Page.COMPACTIONS, bound)],
            )

        return browse(request, session_id, source, nav, kin, log, detail, page, read)

    @app.get("/session/{session_id}/thread/{source}/unattributed")
    def unattributed_page(
        request: Request,
        session_id: str,
        source: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """One thread's api calls that answer no turn — a resume's calls answer turns that
        live in the session it resumed, and this is where they are read."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            standing = tree.unattributed(connection, corpus, source)
            if standing is None:
                raise HTTPException(404, "Every api call on this thread answers a turn.")
            calls, log_rows, ran = call_log(connection, corpus, source, None, page, log)
            return Seen(
                header=standing.row,
                trail=[Ref(Kind.UNATTRIBUTED, source, source)],
                shape=Shape.CALLS,
                rows=log_rows,
                total=calls.total,
                details=[],
                record=None,
                ran=[standing.ran, *ran],
            )

        return browse(request, session_id, source, nav, kin, log, detail, page, read)

    @app.get("/session/{session_id}/unattached")
    def unattached_page(
        request: Request,
        session_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """The session's agent runs no spawning call resolved.

        Session-scoped rather than per thread: what makes a run unattached is that nothing says
        which thread spawned it, so the bucket hangs off the session itself.
        """

        def read(connection: duckdb.DuckDBPyConnection, corpus: tree.Corpus, head: Row) -> Seen:
            loose = [run for run in corpus.runs if run["spawn_source"] is None]
            if not loose:
                raise HTTPException(404, "Every agent run in this session was placed.")
            runs = sliced(loose, page, log)
            return Seen(
                header=head,
                trail=[Ref(Kind.UNATTACHED, None, session_id)],
                shape=Shape.RUNS,
                rows=run_log(corpus, runs.rows),
                total=runs.total,
                details=[],
                record=None,
                ran=[],
            )

        return browse(request, session_id, MAIN_SOURCE, nav, kin, log, detail, page, read)

    @app.get("/session/{session_id}/errors")
    def errors_page(request: Request, session_id: str) -> Response:
        """Every failed tool call of one session, in the order they happened.

        Not a node page: a failure is a property of a tool call rather than a place in the
        tree, and a session's failures are scattered across every thread it ran. So this is a
        list, and each row leads to the tool call's own page — which opens the tree at it and
        carries the crumbs that place it.
        """
        with open_store(resolved) as connection:
            failed = errors.failures(connection, session_id)
            # A session the store never held and one whose calls all succeeded are both
            # nothing at this URL, and not the same nothing. The header is read only when
            # there is a 404 to word, so the page a reader actually opens runs one query.
            held = bool(failed.listed) or bool(
                page_rows(connection, Page.SESSION_HEADER, **header_bound(session_id))
            )
        if not failed.listed:
            raise HTTPException(
                404,
                "This session's tool calls all succeeded."
                if held
                else "No session with that id is in this store.",
            )
        return templates.TemplateResponse(
            request,
            "errors.html",
            {
                "session_id": session_id,
                "listed": failed.listed,
                "cut": failed.cut,
                "citations": {named.value: cited(named, bound) for named, bound in failed.ran},
            },
        )

    @app.get(f"{QUERY_URL}/{{query_name}}")
    def query_page(request: Request, query_name: str) -> Response:
        """One library query's SQL, under the bindings a page cited it with.

        Where every citation in a footer goes. The name is a key of the query manifest and
        never a path: a name the manifest does not declare is a 404 before anything is read,
        which is what makes a request for `../../secret` a miss rather than a file.
        """
        if query_name not in queries.QUERIES:
            raise HTTPException(404, "No query by that name ships with this build.")
        return templates.TemplateResponse(
            request,
            "query.html",
            # Whatever the citation carried, printed back rather than bound to anything: this
            # page runs no query, so a binding here is a fact about the page that sent you.
            {
                "name": query_name,
                "sql": queries.load(query_name),
                "bindings": dict(request.query_params),
            },
        )

    @app.get("/session/{session_id}/thread/{source}/records")
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
        bound = keyed | {
            "after": after,
            "page_records": size,
            "preview_chars": queries.RECORD_PREVIEW,
        }
        with open_store(resolved) as connection:
            page = paged(
                page_rows(connection, Page.RECORDS, **bound),
                "matched_records",
                "line_no",
            )
        # A thread the store never held and a cursor past the end of one it does are the same
        # answer — nothing at this URL. Neither is a page worth rendering empty.
        if not page.rows:
            raise HTTPException(404, "This store holds no records for that thread at that line.")
        # The one record the page fetches unasked: the first row, which is the one a citation
        # named — but only where a record that wide stays inside a page's budget
        # (`bounds.OPENED_RECORD_CHARS`). Past it the row is where every other row is, one
        # click from its own fetch, because a reader who paged here asked for no such thing.
        first = page.rows[0]
        opened = first["line_no"] if first["raw_chars"] <= bounds.OPENED_RECORD_CHARS else None
        return templates.TemplateResponse(
            request,
            "records.html",
            {
                "session_id": session_id,
                "source": source,
                "page": page,
                "size": size,
                "opened": opened,
                "citations": {Page.RECORDS.value: cited(Page.RECORDS, bound)},
            },
        )

    @app.get("/session/{session_id}/offload/{offload_name:path}")
    def offload_page(
        request: Request,
        session_id: str,
        offload_name: str,
        after: int = 0,
        size: int = bounds.CHUNK.default,
    ) -> Response:
        """One chunk of a tool result Claude Code wrote to a file beside the transcript.

        The name is the transcript's own file name, so it may hold anything a tool named a
        file — spaces, percent signs, something shaped like a path. It is a key into the store
        and never a path the server opens, which is what makes the shape of it uninteresting.
        """
        checked(size, bounds.CHUNK.ceiling)
        if after < 0:
            raise HTTPException(400, "Ask for an offset of 0 or more.")
        bound: dict[str, ParamValue] = {
            "session_id": session_id,
            "name": offload_name,
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
                "citations": {Page.OFFLOAD.value: cited(Page.OFFLOAD, bound)},
            },
        )

    def expanded(
        request: Request,
        node: nodes.Node,
        row: Row,
        shape: Shape,
        children: int | None,
        marks: str,
        ran: tree.Ran,
    ) -> Response:
        """One node's body alone, the way an expansion in someone else's log mounts it.

        The same macro the full view's pane renders through, so the two cannot drift apart;
        where the page has the crumbs, the log and prev/next, this has how many children the
        node holds and the way to its own page. `marks` is the knobs the page around the
        expansion was read under, which every link out of here carries on.
        """
        return templates.TemplateResponse(
            request,
            "fragments/body.html",
            {
                "node": node,
                "row": row,
                "shape": shape,
                "children": children,
                "suffix": marks,
                "citations": {named.value: cited(named, bound) for named, bound in ran},
            },
        )

    @app.get(f"{nodes.BODY_URL}/session/{{session_id}}/thread/{{source}}/{{kind}}/{{node_id}}")
    def node_body(
        request: Request,
        kind: str,
        session_id: str,
        source: str,
        node_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
    ) -> Response:
        """The body of a turn, an api call, or a tool call, for an expansion in its parent.

        The knobs come along for the links this serves, not for what it reads: the mount
        carries the page's own query string so a reader who opens an expansion and clicks
        through it keeps the fold and the sizes they were reading under.
        """
        shaped = BODIES.get(kind)
        if shaped is None:
            raise HTTPException(404, "No expansion is served for that kind of node.")
        bound: dict[str, ParamValue] = {
            "session_id": session_id,
            "source": source,
            shaped.keyed: node_id,
            "head_chars": queries.HEADER_CHARS,
            # A body renders facts and no fat value, so the columns a pane would preview are
            # read at the width the label is cut from rather than at the reader's `?detail=`.
            "detail_chars": queries.HEADER_CHARS,
        }
        keyed: dict[str, ParamValue] = {"session_id": session_id, "source": source}
        with open_store(resolved) as connection:
            rows = page_rows(connection, shaped.page, **bound)
            if not rows:
                raise HTTPException(404, "No node with that id is in this thread.")
            # The label is the model's words wherever a pass reached the node, exactly as the
            # log row that opened this expansion has it.
            describes = described(connection, session_id, source) if shaped.described else None
        told = describes.turns.get(node_id) if describes else None
        ran: tree.Ran = [(shaped.page, bound)]
        if describes is not None and describes.queried:
            ran.append((Page.ENRICHMENT, keyed))
        return expanded(
            request,
            shaped.build(session_id, source, rows[0], told.description if told else None),
            rows[0],
            shaped.shape,
            rows[0][shaped.children] if shaped.children else None,
            carried(nav, kin, log, detail),
            ran,
        )

    @app.get(f"{nodes.BODY_URL}/session/{{session_id}}/{Kind.RUN}/{{run_id}}")
    def run_body(
        request: Request,
        session_id: str,
        run_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
    ) -> Response:
        """One agent run's body. Its own mount: a run's URL carries its id where a thread goes."""
        bound: dict[str, ParamValue] = {
            "session_id": session_id,
            "run_id": run_id,
            "head_chars": queries.HEADER_CHARS,
            "detail_chars": queries.HEADER_CHARS,
        }
        keyed: dict[str, ParamValue] = {"session_id": session_id, "source": run_id}
        with open_store(resolved) as connection:
            rows = page_rows(connection, Page.RUN_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No agent run with that id is in this session.")
            # A run's id is the thread its own rows carry, so it is what the pass keyed on too.
            describes = described(connection, session_id, run_id)
        row = describes.runs.get(run_id)
        ran: tree.Ran = [(Page.RUN_HEADER, bound)]
        if describes.queried:
            ran.append((Page.ENRICHMENT, keyed))
        return expanded(
            request,
            nodes.run_node(session_id, rows[0], 0, row.description if row else None),
            rows[0],
            Shape.TURNS,
            rows[0]["turns"],
            carried(nav, kin, log, detail),
            ran,
        )

    def spilled(
        request: Request,
        session_id: str,
        at: Ref,
        thread: str,
        depth: int,
        opened: str,
        nav: str,
        kin: int,
        log: int,
        detail: int,
    ) -> Response:
        """The children one level's window left out: the rows a `+N more` row stands in for.

        The tree draws a window on a level and a tail row saying how many it left out; this
        serves the rest of that level, at the depth the tree had reached, so a click can stand
        them where the tail row stood. `opened` is the key of the child the open path descends
        through, which the window keeps wherever in the level it sits — the page sent it so
        that the two halves of one split agree, and this is the half that must not repeat it.

        `thread` is the reader's, not the level's: the enrichment is keyed by thread, so a page
        draws a turn of any other thread by its prompt, and a row served here has to read the
        way the page beside it would have drawn it.

        Unbounded on purpose: what comes back is a level less a window, so a node with ten
        thousand children answers with ten thousand rows.
        """
        preset = viewed(nav)
        cap = checked(kin, bounds.KIN.ceiling)
        checked(log, bounds.LOG.ceiling)
        checked(detail, bounds.DETAIL.ceiling)
        if not 0 < depth <= bounds.DEPTH:
            raise HTTPException(400, f"A tree row sits between depth 1 and {bounds.DEPTH}.")
        keyed: dict[str, ParamValue] = {"session_id": session_id}
        with open_store(resolved) as connection:
            head = page_rows(
                connection,
                Page.SESSION_HEADER,
                **keyed,
                head_chars=queries.HEADER_CHARS,
                item_chars=queries.HEADER_ITEM_CHARS,
                head_items=queries.HEADER_ITEMS,
            )
            if not head:
                raise HTTPException(404, "No session with that id is in this store.")
            corpus = tree.Corpus(
                session_id=session_id,
                whole=head[0]["cost_usd"] or 0,
                runs=page_rows(connection, Page.RUNS, **keyed, chip_chars=queries.NAV_CHARS),
                described=described(connection, session_id, thread),
                source=thread,
            )
            level = tree.children(connection, corpus, at, preset, opened or None)
        return templates.TemplateResponse(
            request,
            "fragments/kin.html",
            {
                "rows": [
                    tree.TreeRow(node, depth, selected=False)
                    for node in tree.windowed(level.nodes, cap, [opened]).cut
                ],
                "thread": thread,
                "suffix": carried(nav, kin, log, detail),
            },
        )

    @app.get(f"{nodes.KIN_URL}/session/{{session_id}}/thread/{{source}}/{{kind}}/{{node_id}}")
    def node_kin(
        request: Request,
        kind: str,
        session_id: str,
        source: str,
        node_id: str,
        thread: str,
        depth: int,
        opened: str = "",
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
    ) -> Response:
        """The rest of one level, under a node recorded on a thread.

        Neither `thread` nor `depth` has a default: these rows are going somewhere in a tree
        that already exists, and only the row that asked for them knows where they land and
        which thread's descriptions the tree around them was drawn by.
        """
        if kind not in set(Kind):
            raise HTTPException(404, "No level is served for that kind of node.")
        at = Ref(kind=Kind(kind), source=source, node_id=node_id)
        return spilled(request, session_id, at, thread, depth, opened, nav, kin, log, detail)

    @app.get(f"{nodes.KIN_URL}/session/{{session_id}}/{{kind}}/{{node_id}}")
    def loose_kin(
        request: Request,
        kind: str,
        session_id: str,
        node_id: str,
        thread: str,
        depth: int,
        opened: str = "",
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
    ) -> Response:
        """The rest of one level, under a node that carries no thread of its own.

        The session, an agent run, and the unattached bucket: their URLs have no room for the
        thread the node was recorded on, and the level does not need it — each builder reads
        the thread out of the node it hangs under.
        """
        if kind not in set(Kind):
            raise HTTPException(404, "No level is served for that kind of node.")
        at = Ref(kind=Kind(kind), source=None, node_id=node_id)
        return spilled(request, session_id, at, thread, depth, opened, nav, kin, log, detail)

    def whole(
        request: Request,
        value: Value,
        template: str,
        keyed: Mapping[str, ParamValue],
        column: str,
        detail: str | None,
        syntax: highlight.Syntax | None = None,
    ) -> Response:
        """One per-value fragment: the whole value, or a 404 when nothing is stored under it.

        `column` is where the query puts the value this fragment is for. A row can exist with
        nothing under it — a `Read` has no command, a turn no prompt — and that is a 404 and
        not an empty page: nothing on a pane links here unless there is a value to fetch, so a
        request for one that is not there is a URL somebody typed or a link somebody kept.

        `detail` is the name the pane files this value under, and the fragment replaces that
        whole section, so it carries the name out with it — the styling that tells an ask from
        an answer reads it. A fragment that is nobody's detail — the archived record — has none.

        `syntax` is what the route knows the value is written in. A value whose language is a
        property of the row instead — the file a `Read` returned — carries it in the query's
        own `result_type`, so the fetch is marked up the way its preview on the pane was.
        """
        with open_store(resolved) as connection:
            rows = page_rows(connection, value, **keyed)
        if not rows or rows[0][column] is None:
            raise HTTPException(404, "Nothing in this store is stored under that id.")
        row = rows[0]
        # The keys travel into the context as well: a fragment that links anywhere needs the
        # session and thread it was fetched for, and they are exactly what keyed it.
        return templates.TemplateResponse(
            request,
            f"fragments/{template}.html",
            dict(keyed)
            | {
                "row": row,
                "detail": detail,
                "citation": queries.citation(value, keyed),
                "syntax": syntax or highlight.by_suffix(row.get("result_type")),
            },
        )

    @app.get("/fragment/text/session/{session_id}/thread/{source}/call/{api_call_id}")
    def call_text(request: Request, session_id: str, source: str, api_call_id: str) -> Response:
        """What one api call said, whole."""
        return whole(
            request,
            Value.CALL_TEXT,
            "value",
            {"session_id": session_id, "source": source, "api_call_id": api_call_id},
            "value",
            "text",
        )

    @app.get("/fragment/thinking/session/{session_id}/thread/{source}/call/{api_call_id}")
    def call_thinking(request: Request, session_id: str, source: str, api_call_id: str) -> Response:
        """What one api call thought, whole."""
        return whole(
            request,
            Value.CALL_THINKING,
            "value",
            {"session_id": session_id, "source": source, "api_call_id": api_call_id},
            "value",
            "thinking",
        )

    @app.get("/fragment/record/session/{session_id}/thread/{source}/line/{line_no}")
    def record_value(request: Request, session_id: str, source: str, line_no: int) -> Response:
        """One raw transcript record whole, as the browser's preview was cut from."""
        return whole(
            request,
            Value.RECORD,
            "record",
            {"session_id": session_id, "source": source, "line_no": line_no},
            # The record itself, which the store holds NOT NULL.
            "raw",
            # The line a node was read from, not one of the node's own values: nothing on a
            # pane files it under a name, and nothing swaps it into a detail.
            None,
        )

    @app.get("/fragment/input/session/{session_id}/thread/{source}/tool/{tool_call_id}")
    def tool_input(request: Request, session_id: str, source: str, tool_call_id: str) -> Response:
        """What one tool call was passed, whole."""
        return whole(
            request,
            Value.TOOL_INPUT,
            "raw",
            {"session_id": session_id, "source": source, "tool_call_id": tool_call_id},
            "value",
            "input",
        )

    @app.get("/fragment/result/session/{session_id}/thread/{source}/tool/{tool_call_id}")
    def tool_result(request: Request, session_id: str, source: str, tool_call_id: str) -> Response:
        """What one tool call returned, whole — the largest single fetch the viewer makes."""
        return whole(
            request,
            Value.TOOL_RESULT,
            "raw",
            {
                "session_id": session_id,
                "source": source,
                "tool_call_id": tool_call_id,
                # Not a cut of the answer, which rides whole: the bound on the file suffix
                # beside it, which is what says how the answer is marked up.
                "head_chars": queries.HEADER_CHARS,
            },
            "value",
            "result",
        )

    @app.get("/fragment/command/session/{session_id}/thread/{source}/tool/{tool_call_id}")
    def tool_command(request: Request, session_id: str, source: str, tool_call_id: str) -> Response:
        """What one `Bash` call ran, whole — read as the shell reads it."""
        return whole(
            request,
            Value.TOOL_COMMAND,
            "raw",
            {"session_id": session_id, "source": source, "tool_call_id": tool_call_id},
            "value",
            "command",
            highlight.Syntax.BASH,
        )

    @app.get("/fragment/prompt/session/{session_id}/thread/{source}/turn/{turn_id}")
    def turn_prompt(request: Request, session_id: str, source: str, turn_id: str) -> Response:
        """What one turn was asked, whole."""
        return whole(
            request,
            Value.TURN_PROMPT,
            "value",
            {"session_id": session_id, "source": source, "turn_id": turn_id},
            "value",
            "prompt",
        )

    @app.get("/fragment/args/session/{session_id}/thread/{source}/turn/{turn_id}")
    def turn_command_args(request: Request, session_id: str, source: str, turn_id: str) -> Response:
        """What followed the slash command one turn ran, whole."""
        return whole(
            request,
            Value.TURN_COMMAND_ARGS,
            "value",
            {"session_id": session_id, "source": source, "turn_id": turn_id},
            "value",
            "command_args",
        )

    @app.get("/fragment/brief/session/{session_id}/run/{run_id}")
    def run_brief(request: Request, session_id: str, run_id: str) -> Response:
        """The whole brief one agent run was given."""
        return whole(
            request,
            Value.RUN_BRIEF,
            "value",
            {"session_id": session_id, "run_id": run_id},
            "value",
            "description",
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
