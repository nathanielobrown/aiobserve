"""The pages that are not a node's: an error, a query, the raw records, a file.

Each answers a question no single node holds, so each is a page of its own rather than a body
inside the node frame (`docs/viewer.md`).
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import NamedTuple
from urllib.parse import quote

import htpy

from hyphae.view import format as fmt
from hyphae.view.citation import Cited
from hyphae.view.components import Html, citation, layout, parts
from hyphae.view.errors import Failure
from hyphae.view.highlight import Syntax
from hyphae.view.nodes import thread_url


def error_page(*, status: int, message: str, dev: bool) -> Html:
    """What every failure the app catches is answered with — a status, a sentence, a way back.

    The message never repeats what was asked for: a request is untrusted text like any other.
    """
    return layout.page(
        tab_title=f"{status} — hyphae",
        scripts=None,
        main=htpy.section(id="error")[
            [
                htpy.h1(data_field="status")[status],
                htpy.p(data_field="message")[message],
                htpy.p[htpy.a(href="/")["Back to the projects"]],
            ]
        ],
        footer=None,
        dev=dev,
    )


def query_page(
    *, name: str, sql: str, macro_setup: str, bindings: dict[str, str], dev: bool
) -> Html:
    """One library query as the page that cited it links to.

    The SQL this build ships, under the bindings that page ran it with. Nothing is executed
    here — what a reader wants is what the numbers above meant, and the answer to that is the
    statement, not another result set. `macro_setup` is what a shell has to run first where the
    statement calls a library macro, and is empty where it calls none.
    """
    return layout.page(
        tab_title=f"{name}.sql · hyphae",
        scripts=None,
        main=htpy.article(id="query", data_sql=name)[
            [
                htpy.h1[f"{name}.sql"],
                _bindings(bindings),
                _setup(macro_setup),
                parts.code(value=sql, syntax=Syntax.SQL, field="sql"),
            ]
        ],
        footer=None,
        dev=dev,
    )


def _bindings(bindings: dict[str, str]) -> Html:
    """What the citing page bound the statement to, or a line saying it bound nothing."""
    if not bindings:
        return htpy.p(".plain")["Cited with no bindings."]
    return htpy.dl(".facts")[
        [
            htpy.div[[htpy.dt[key], htpy.dd(data_binding=key)[value]]]
            for key, value in bindings.items()
        ]
    ]


def _setup(macro_setup: str) -> Html | None:
    """The definitions the statement calls, above it — and nothing where it calls none.

    Both consumers install these before they run anything, so a reader who pastes the statement
    alone gets a catalog error and no way to find out why (`analyze/macros.py`).
    """
    if not macro_setup:
        return None
    return htpy.fragment[
        [
            htpy.p(".plain")[
                [
                    "Run these first: the definitions this statement calls, which ",
                    htpy.code["hp query"],
                    " and the viewer install before they run it.",
                ]
            ],
            parts.code(value=macro_setup, syntax=Syntax.SQL, field="macros"),
        ]
    ]


def errors_page(
    *,
    session_id: str,
    listed: Sequence[Failure],
    cut: int,
    citations: Mapping[str, Cited],
    dev: bool,
) -> Html:
    """Where one session failed, whichever thread it happened on.

    A list rather than a pane: a failure is not a place in the NavTree, so there is nothing to
    open a path to. Each row leads to the tool call's own page, which carries the crumbs that
    place it.
    """
    return layout.page(
        tab_title=f"{session_id} errors — hyphae",
        scripts=None,
        main=htpy.section(id="errors")[
            [
                htpy.h1["Failed tool calls"],
                htpy.p(".numbers")[
                    [
                        htpy.a(href=f"/session/{session_id}")[session_id],
                        htpy.span[
                            [
                                htpy.span(data_field="matched")[fmt.count(len(listed) + cut)],
                                " failed call(s)",
                            ]
                        ],
                    ]
                ],
                htpy.ol(".errors")[[_failure(item=item) for item in listed]],
                # What the page left out, said rather than dropped: the store keeps every
                # failure, and this page shows the first of them in the order they happened.
                htpy.p(".more", data_more_errors=cut)[
                    [
                        "+",
                        htpy.span(data_field="cut")[fmt.count(cut)],
                        " more failed call(s)",
                    ]
                ]
                if cut
                else None,
            ]
        ],
        footer=citation.footer(citations=citations),
        dev=dev,
    )


def _failure(*, item: Failure) -> Html:
    """One failed tool call as the list shows it: where it reads, whose thread, and when."""
    return htpy.li(data_error=item.node.key)[
        [
            htpy.a(href=item.node.url)[htpy.span(data_field="title")[item.node.nav_tree_title]],
            # The thread it ran on, because the list spans all of them: two failures of one
            # tool name are told apart by which agent hit them.
            htpy.span(".source", data_field="source")[item.node.source],
            htpy.span(data_field="started_at")[fmt.when(item.started_at)],
        ]
    ]


class RecordRow(NamedTuple):
    """One archived transcript line as the records page prints it, built from its store row."""

    line_no: int
    type: str
    timestamp: dt.datetime | None
    raw_chars: int
    raw_head: str


def records_page(
    *,
    session_id: str,
    source: str,
    rows: Sequence[RecordRow],
    matched: int,
    opened: int | None,
    after: int | None,
    more: int,
    size: int,
    citations: Mapping[str, Cited],
    dev: bool,
) -> Html:
    """One page of a thread's raw transcript — where a report's citation lands.

    `opened` names the one row that arrives open, or None where the row a citation named is too
    wide to open unasked (`bounds.OPENED_RECORD_CHARS`).
    """
    return layout.page(
        tab_title=f"{source} records — hyphae",
        scripts=None,
        main=htpy.section(id="records")[
            [
                htpy.h1["Raw records"],
                htpy.p(".numbers")[
                    [
                        htpy.a(href=f"/session/{session_id}")[session_id],
                        htpy.span(data_field="source")[source],
                        htpy.span[
                            [
                                htpy.span(data_field="matched")[fmt.count(matched)],
                                " record(s) from here",
                            ]
                        ],
                    ]
                ],
                htpy.ol(".records")[
                    [
                        _record(row=row, thread=thread_url(session_id, source), opened=opened)
                        for row in rows
                    ]
                ],
                htpy.p(".more", data_more_records=after)[
                    htpy.a(
                        href=f"{thread_url(session_id, source)}/records?after={after}&size={size}"
                    )[htpy.span(data_field="count")[f"+{fmt.count(more)} more"]]
                ]
                if after is not None
                else None,
            ]
        ],
        footer=citation.footer(citations=citations),
        dev=dev,
    )


def _record(*, row: RecordRow, thread: str, opened: int | None) -> Html:
    """One record's row, and the fetch that brings the whole of it.

    The whole record on first open, one request per record: a page of them whole is the one
    payload nothing here bounds. One row is the exception — the one the route picked as
    `opened`, the record a citation named — which arrives open and fetches itself as the page
    loads. Still a fetch and not inlined: the page stays bounded, and what is unbounded stays
    one record at a time.
    """
    # The anchor is the line number, which is what a citation carries: `#L42` lands here.
    return htpy.li(id=f"L{row.line_no}", data_record=row.line_no)[
        [
            htpy.span(".line")[row.line_no],
            # Spaces, one per gap: the row is no flex line and only `.line` carries a margin
            # (`view/static/style.css`), so these are what hold the five values apart.
            " ",
            htpy.span(".type", data_field="type")[row.type],
            " ",
            htpy.span(data_field="timestamp")[fmt.clock(row.timestamp)],
            " ",
            htpy.span[[htpy.span(data_field="raw_chars")[fmt.count(row.raw_chars)], " chars"]],
            " ",
            htpy.code(data_field="raw_head")[row.raw_head],
            htpy.details(
                ".whole",
                open=row.line_no == opened,
                data_open_record=row.line_no,
                hx_get=f"/fragment/record{thread}/line/{row.line_no}",
                hx_trigger="load" if row.line_no == opened else "toggle once",
                hx_target="find .value",
            )[[htpy.summary["whole record"], htpy.div(".value")]],
        ]
    ]


class OffloadFile(NamedTuple):
    """One offloaded tool result as its page prints it, built from its store row."""

    name: str
    size_bytes: int
    content_chars: int
    lossy_decode: bool
    chunk: str


def offload_page(
    *,
    session_id: str,
    file: OffloadFile,
    after: int | None,
    size: int,
    citations: Mapping[str, Cited],
    dev: bool,
) -> Html:
    """One chunk of a tool result Claude Code wrote to a file beside the transcript.

    `after` is where the next chunk starts, or None where this one reached the end.
    """
    return layout.page(
        tab_title=f"{file.name} — hyphae",
        scripts=None,
        main=htpy.section(id="offload", data_offload=file.name)[
            [
                htpy.h1(data_field="name")[file.name],
                htpy.p(".numbers")[
                    [
                        htpy.a(href=f"/session/{session_id}")[session_id],
                        htpy.span[
                            [
                                htpy.span(data_field="size_bytes")[fmt.count(file.size_bytes)],
                                " bytes on disk",
                            ]
                        ],
                        htpy.span[
                            [
                                htpy.span(data_field="content_chars")[
                                    fmt.count(file.content_chars)
                                ],
                                " chars stored",
                            ]
                        ],
                        # Only when it happened: the extractor could not decode the file as
                        # text and replaced what it could not read, so what is shown here is
                        # not what the tool wrote.
                        htpy.span(data_field="lossy_decode")["some bytes did not decode as text"]
                        if file.lossy_decode
                        else None,
                    ]
                ],
                htpy.pre(data_field="content")[file.chunk],
                htpy.p(".more", data_more_offload=after)[
                    htpy.a(
                        href=f"/session/{session_id}/offload/{quote(file.name, safe='/')}"
                        f"?after={after}&size={size}"
                    )[f"next {fmt.count(size)} chars"]
                ]
                if after is not None
                else None,
            ]
        ],
        footer=citation.footer(citations=citations),
        dev=dev,
    )
