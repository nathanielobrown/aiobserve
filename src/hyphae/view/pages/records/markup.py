"""The records page's markup: the typed record row, the page of them, and one record."""

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import NamedTuple

import htpy

from hyphae.view.citation import Cited
from hyphae.view.components import Html, citation, layout
from hyphae.view.nodes import thread_url
from hyphae.view.text import format as fmt


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
