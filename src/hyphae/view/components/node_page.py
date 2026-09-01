"""One node of a session, whole: the NavTree it sits in, and the pane that reads it.

Both arrive in one response, and a click on a NavTree row re-fetches this same URL — htmx takes
`#reading-pane` out of the response and swaps `#nav-tree-rows` out of band, so a pasted link and
a click serve the same bytes.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import NamedTuple

import htpy

from hyphae.view.citation import Cited
from hyphae.view.columns import Shape
from hyphae.view.components import Html, citation, layout, logs, nav_tree, node_body, parts
from hyphae.view.components.logs import Logged
from hyphae.view.components.nav_tree import PANE_SWAP, NavTreeRow, PresetChoice
from hyphae.view.components.node_body import Facts
from hyphae.view.components.parts import Pager
from hyphae.view.detail import Detail, EnrichmentLines
from hyphae.view.enrichment import Enrichment
from hyphae.view.failures import Step as Failures
from hyphae.view.nodes import Node
from hyphae.view.text import cuts
from hyphae.view.text import format as fmt
from hyphae.view.walk import Step as Walked

# The two scripts only this page needs: what no stylesheet can reach — where the tree opens,
# where a popover stands, and the NavTree width a reader sets by dragging. The width is not a
# knob on the URL: a column width belongs to the screen and not to the node a link names.
_SCRIPTS = htpy.fragment[
    [
        htpy.script(src="/static/nav-tree-width.js", defer=True),
        htpy.script(src="/static/nav-tree.js", defer=True),
    ]
]


class Trail(NamedTuple):
    """The way out of the session, above the nodes inside it.

    Neither step is a node, so neither carries a node's marks or a knob suffix — a click on
    either leaves the session. `project_url` is nothing where the store holds no path to filter
    the list by, and the project then prints without a link.
    """

    list_url: str
    project_dir: str | None
    project_url: str | None


class Said(NamedTuple):
    """What a pass wrote about the node, and the way to the whole of each line it wrote.

    One value rather than two: a pane that had the words without the links, or the links
    without the words, is not a state `view/detail.py` can produce.
    """

    enrichment: Enrichment
    lines: EnrichmentLines


class Archived(NamedTuple):
    """The bytes behind the node: the thread's transcript, and the line it was read from."""

    thread_url: str
    line_no: int | None


class Nav(NamedTuple):
    """The NavTree side: the preset control, the one open path, and the thread it was read for.

    Not `nav_tree.NavTree`, which is what building the tree produced: this is the half of it a
    page draws, and the preset control and the thread are the page's own.
    """

    choices: Sequence[PresetChoice]
    rows: Sequence[NavTreeRow]
    thread: str


class Body(NamedTuple):
    """The node read whole: its own fields, what a pass said about it, its fat values, its bytes.

    What the pane would still show if the session around it were gone — everything else the
    page carries is about where the node sits.
    """

    facts: Facts
    said: Said | None
    details: Sequence[Detail]
    archived: Archived


class Steps(NamedTuple):
    """Where reading in order goes: the node before this one on its level, and the one after."""

    previous: Walked | None
    next: Walked | None


class Bearings(NamedTuple):
    """Where the node sits, and every way off it that is not a step down into a child.

    Three ways out, and going down is the NavTree's: back out of the session along the crumb
    chain, along the level with the walk, and across to another failure with the stepper.
    """

    trail: Trail
    chain: Sequence[Node]
    walked: Steps
    # How many tool calls the session failed, which is what the way into the errors page says.
    tool_errors: int | None
    # The failures either side of this node, where the pane is standing on one.
    failures: Failures | None


class Children(NamedTuple):
    """One page of the node's children, as the log under the body reads them.

    `total` is the level's own size rather than the page's: the heading counts the level, and
    the pager under it is cut from the same number.
    """

    shape: Shape
    rows: Sequence[Logged]
    total: int
    pager: Pager | None


def page(
    *,
    selection: Node,
    nav: Nav,
    body: Body,
    bearings: Bearings,
    children: Children,
    citations: Mapping[str, Cited],
    suffix: str,
    dev: bool,
) -> Html:
    """The whole document: the NavTree, the grip between the columns, and the reading pane.

    `selection` is the one input every part reads — it names the tab, heads the crumb chain,
    and titles the body — so it stands apart from the four groups around it.
    """
    return layout.page(
        tab_title=f"{selection.icon} {selection.tab_title} · hyphae",
        scripts=_SCRIPTS,
        main=htpy.div(id="browser")[
            [
                htpy.nav(id="nav-tree", aria_label="Session NavTree")[
                    # What every row of the NavTree does on a click, written once: fetch the
                    # row's own URL, take `#reading-pane` out of the response and put it where
                    # the pane is, and swap these rows in out of band. htmx reads each of these
                    # off the closest ancestor carrying it, so the rows below carry only the URL
                    # that differs between them — 3,217 rows is four fifths of a node page's
                    # budget (`.claude/rules/viewer-ui.md`).
                    htpy.div(PANE_SWAP, id="nav-tree-rows")[
                        [
                            nav_tree.presets(choices=nav.choices),
                            htpy.ul(".rows")[
                                nav_tree.lines(rows=nav.rows, suffix=suffix, thread=nav.thread)
                            ],
                        ]
                    ]
                ],
                # What the reader drags to widen the NavTree. A separator rather than a button:
                # it divides the two columns rather than doing anything, and arrow keys move it
                # for a reader who is not dragging. `static/nav-tree-width.js` is what it moves.
                htpy.div(
                    id="nav-tree-grip",
                    role="separator",
                    aria_orientation="vertical",
                    aria_label="NavTree width",
                    tabindex="0",
                ),
                htpy.article(id="reading-pane")[
                    [
                        _crumbs(
                            selection=selection,
                            trail=bearings.trail,
                            chain=bearings.chain,
                            suffix=suffix,
                        ),
                        node_body.body(node=selection, facts=body.facts, suffix=suffix),
                        # What an enrichment pass said about this node, where a pass reached it.
                        parts.summary(enrichment=body.said.enrichment, lines=body.said.lines)
                        if body.said
                        else None,
                        # The node's own values, cut to the pane's width, each with the way to
                        # the whole of it.
                        [parts.detail(item=item) for item in body.details],
                        _raw(archived=body.archived),
                        logs.log(
                            shape=children.shape,
                            rows=children.rows,
                            total=children.total,
                            suffix=suffix,
                            pager=children.pager,
                            opens=True,
                        ),
                        _walk(
                            previous=bearings.walked.previous,
                            following=bearings.walked.next,
                            suffix=suffix,
                        ),
                        _stepper(
                            session_id=selection.session_id,
                            tool_errors=bearings.tool_errors,
                            failures=bearings.failures,
                            suffix=suffix,
                        ),
                        # What produced the page, last in the pane rather than under the
                        # document. This page fills the viewport and the pane is what scrolls, so
                        # a footer outside it would sit below a fold nobody can reach — and the
                        # swap takes `#reading-pane` out of the response, so standing it here is
                        # also what keeps a clicked node's citations current.
                        citation.footer(citations=citations),
                    ]
                ],
            ]
        ],
        # Emptied: this page renders its citations inside the pane above.
        footer=None,
        dev=dev,
    )


def _crumbs(*, selection: Node, trail: Trail, chain: Sequence[Node], suffix: str) -> Html:
    """Where the node sits, outermost first: the same chain the NavTree has open."""
    return htpy.nav(".crumbs", data_crumbs=selection.kind, aria_label="Where this node sits")[
        [
            htpy.a(data_crumb_head="home", href=trail.list_url)[parts.mark(character="🏠")],
            " ",
            _project(trail=trail),
            [
                htpy.fragment[
                    [
                        htpy.a(data_crumb=step.key, href=f"{step.url}{suffix}")[
                            [
                                parts.mark(character=step.icon),
                                " ",
                                parts.glyph(enriched=step.enriched),
                                htpy.span(data_field=step.kind)[step.crumb_title],
                            ]
                        ],
                        " ",
                    ]
                ]
                for step in chain
            ],
        ]
    ]


def _project(*, trail: Trail) -> Html | None:
    """The session's project, linked where the list can be filtered down to it."""
    if trail.project_dir is None:
        return None
    named = htpy.span(data_field="project_dir")[cuts.project_path(trail.project_dir)]
    if trail.project_url:
        return htpy.fragment[
            [htpy.a(data_crumb_head="project", href=trail.project_url)[named], " "]
        ]
    return htpy.fragment[[htpy.span(data_crumb_head="project")[named], " "]]


def _raw(*, archived: Archived) -> Html:
    """What the extractor read, under what it made of it.

    The record arrives on open, one request: a raw line is the least filtered thing the viewer
    shows and the widest, so a pane that carried it unasked would be a pane priced for a
    transcript.
    """
    line = archived.line_no
    return htpy.fragment[
        [
            htpy.p(".raw")[
                [
                    htpy.a(data_field="records", href=f"{archived.thread_url}/records")[
                        "this thread's transcript"
                    ],
                    [
                        " ",
                        htpy.a(
                            data_field="record",
                            href=f"{archived.thread_url}/records?after={line - 1}#L{line}",
                        )[f"line {fmt.count(line)}"],
                    ]
                    if line
                    else None,
                ]
            ],
            htpy.details(
                ".raw",
                data_open_record=line,
                hx_get=f"/fragment/record{archived.thread_url}/line/{line}",
                hx_trigger="toggle once",
                hx_target="find .value",
            )[[htpy.summary["archived record"], htpy.div(".value")]]
            if line
            else None,
        ]
    ]


class Way(StrEnum):
    """Which of the two ways a control points, and the value it writes into the markup.

    The walk and the error stepper both put one control on each side of the pane, and both
    lean on which side it is: the arrow leads on the way back and trails on the way on, and
    the stylesheet pushes each to its own margin off this value.
    """

    PREVIOUS = "previous"
    NEXT = "next"


def _walk(*, previous: Walked | None, following: Walked | None, suffix: str) -> Html:
    """Reading in order, along the level the reader is standing on.

    Going down is what the NavTree is for, so these two go along the level and back out of it,
    never into a node. Buttons because they move the pane rather than leading anywhere new: the
    swap they carry is the one a NavTree row carries, written here because the pane is what the
    click replaces. A step that leaves the level says so with an up arrow.
    """
    return htpy.nav(".walk", PANE_SWAP, aria_label="Read in order")[
        [
            _step(
                step=previous,
                way=Way.PREVIOUS,
                arrow="↑" if previous.climbed else "←",
                suffix=suffix,
            )
            if previous
            else None,
            _step(
                step=following,
                way=Way.NEXT,
                arrow="↑" if following.climbed else "→",
                suffix=suffix,
            )
            if following
            else None,
        ]
    ]


def _step(*, step: Walked, way: Way, arrow: str, suffix: str) -> Html:
    """One control of the walk: where it goes, and whether taking it leaves the level."""
    named: list[Html | str | None] = [
        parts.glyph(enriched=step.node.enriched),
        htpy.span(data_field="kind")[step.node.kind],
        " ",
        htpy.span(data_field="title")[step.node.nav_tree_title],
    ]
    return htpy.button(
        ".button",
        type="button",
        data_walk=way,
        data_node=step.node.key,
        data_climb=way if step.climbed else None,
        hx_get=f"{step.node.url}{suffix}",
    )[[arrow, " ", *named] if way is Way.PREVIOUS else [*named, " ", arrow]]


def _stepper(
    *, session_id: str, tool_errors: int | None, failures: Failures | None, suffix: str
) -> Html | None:
    """Where this session failed.

    Written once here rather than per kind of node, because it is a fact about the session and
    every node page reads the session's header: the way to the whole list rides every page, and
    the step between two failures only appears where the pane is standing on one — the walk
    above reaches the next *node*, and a reader hunting failures wants the next *failure*, five
    spawns and two threads away.
    """
    if not tool_errors:
        return None
    return htpy.nav(".error-stepper", aria_label="Where this session failed")[
        [
            _failure(node=failures.previous, way=Way.PREVIOUS, suffix=suffix)
            if failures and failures.previous
            else None,
            htpy.a(data_step="all", href=f"/session/{session_id}/errors")[
                [htpy.span(data_field="tool_errors")[fmt.count(tool_errors)], " tool error(s)"]
            ],
            _failure(node=failures.next, way=Way.NEXT, suffix=suffix)
            if failures and failures.next
            else None,
        ]
    ]


def _failure(*, node: Node, way: Way, suffix: str) -> Html:
    """One step of the error stepper: the failure read before this one, or the one after."""
    named = htpy.span(data_field="title")[node.nav_tree_title]
    return htpy.a(data_step=way, data_node=node.key, href=f"{node.url}{suffix}")[
        ["← ", named] if way is Way.PREVIOUS else [named, " →"]
    ]
