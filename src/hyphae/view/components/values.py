"""The whole of one value, arriving as the block that previewed it.

A pane prints the head of a fat value and offers the rest behind a link; these are what comes
back. Each replaces the block it was fetched from, so it arrives wearing that block's own
class and key — and carries its own citation, because a fragment is a page's answer too
(`docs/viewer.md`).
"""

from datetime import datetime
from typing import NamedTuple

import htpy

from hyphae.view import format as fmt
from hyphae.view import highlight
from hyphae.view.components import Html, parts


class Whole(NamedTuple):
    """One fat value fetched on its own: what it says, what it is filed under, and its query.

    `detail` is the name the pane filed the value under, and nothing for a value that is
    nobody's detail — the archived record. The styling that tells an ask from an answer reads
    it, which is why the fragment carries it back out.
    """

    value: str | None
    detail: str | None
    citation: str


class Record(NamedTuple):
    """One raw transcript record, whole: its own header line, and the JSON under it."""

    line_no: int
    type: str
    uuid: str | None
    timestamp: datetime | None
    raw_chars: int | None
    raw: str
    citation: str


def enrichment_line(*, node: Whole) -> Html:
    """The whole of one line an enrichment pass wrote — what it said, or the friction it saw.

    No link out, because there is no rest left to offer: the block it replaces held a head and
    the ask, and this is the whole of it.
    """
    return htpy.span(
        ".enrichment-line",
        data_enrichment_line=node.detail,
        data_value=len(node.value or ""),
        data_query=node.citation,
    )[htpy.span(data_field=node.detail)[node.value]]


def prose(*, node: Whole) -> Html:
    """The whole of one value as the markdown it was written in — an api call's text or thought.

    Through the same component the pane's own preview went through: one value, two mounts, and
    one escaping policy over both (`view/render.py`).
    """
    return _mount(node=node, classes=".value.detail.quoted")[
        parts.prose(field=node.detail or "value", value=node.value)
    ]


def code(*, node: Whole, syntax: highlight.Syntax) -> Html:
    """The whole of one value that was never prose — what a tool was passed, ran, or returned.

    Marked up as whatever the row said it was written in, and as JSON otherwise: a tool's
    arguments are JSON, and JSON put through a markdown renderer stops being the thing a reader
    came to re-read.
    """
    return _mount(node=node, classes=".value.detail")[
        parts.code(value=node.value or "", syntax=syntax, field="value")
    ]


def record(*, node: Record) -> Html:
    """One raw transcript record whole, as the browser's preview was cut from.

    Indented and marked up as the JSON it is rather than rendered as markdown: what a reader
    wants here is the shape Claude Code wrote, field by field.
    """
    return htpy.div(".value", data_record_value=node.line_no, data_query=node.citation)[
        [
            htpy.p(".numbers")[
                [
                    htpy.span(".type", data_field="type")[node.type],
                    # Not every record carries one: a summary record has no uuid, and a turn
                    # links to the record whose uuid is the turn's id.
                    htpy.span(data_field="uuid")[node.uuid] if node.uuid else None,
                    htpy.span(data_field="timestamp")[fmt.clock(node.timestamp)],
                    htpy.span[
                        [htpy.span(data_field="raw_chars")[fmt.count(node.raw_chars)], " chars"]
                    ],
                ]
            ],
            parts.code(value=node.raw, syntax=highlight.Syntax.JSON, field="raw"),
        ]
    ]


def _mount(*, node: Whole, classes: str) -> htpy.Element:
    """The block a fetched detail arrives as, keyed to the section it replaces."""
    return htpy.div(
        classes,
        data_detail=node.detail,
        data_value=len(node.value or ""),
        data_query=node.citation,
    )
