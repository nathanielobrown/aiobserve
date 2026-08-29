"""The NavTree: the one open path through a session, as one flat list of rows.

Flat rather than nested because a click swaps the whole list out of band in one go, and because
a row's own attributes have to be readable without a descendant's answering for it. `data-depth`
is what the stylesheet indents by, so nesting would buy the markup nothing.

This is the page's byte budget: a node page draws thousands of these rows, and every attribute
written here is written that many times (`.claude/rules/viewer-ui.md`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

import htpy

from hyphae.view import format as fmt
from hyphae.view.components import Html, parts
from hyphae.view.nodes import Node, Preset


@dataclass(frozen=True)
class NavTreeRow:
    """One line of the NavTree: a node at its depth, or the tail standing for what a cap cut."""

    node: Node
    depth: int
    selected: bool
    # Whether this row is a step of the open path above the selection: the stylesheet clamps
    # those at the top of the scroller, so a reader deep in a level sees what they are inside.
    ancestor: bool
    # On a tail row, how many of `node`'s children the cap left out. Zero on a node's own row,
    # which is what tells the two apart.
    cut: int = 0
    # On a tail row, the key of the child the open path descends through, when this level holds
    # one. The row's own fetch carries it: the cap keeps that child whatever its place in the
    # level, so the fetch has to know it to leave it out of what it sends back.
    opened: str | None = None


class PresetChoice(NamedTuple):
    """One preset as the control above the NavTree offers it: where it goes, and whether we are
    in it."""

    preset: Preset
    url: str
    current: bool


# What a click on a NavTree row or a log row's wide column does: swap the reading pane, and the
# NavTree beside it, for the child's own. Written once here and read by every surface that links
# a node into the pane. `hx-get` is not in it: the URL is the row's.
PANE_SWAP = {
    "hx-target": "#reading-pane",
    "hx-swap": "outerHTML",
    "hx-select": "#reading-pane",
    "hx-select-oob": "#nav-tree-rows",
    "hx-push-url": "true",
}

# Every part of the swap a row inherits from `#nav-tree-rows`, undone. A tail row's fetch and a
# popover's both come back as something other than a pane — rows in one case, a popover in the
# other — so neither may take the pane's swap with it. The third swap vocabulary, named for the
# same reason as the other two (`components/logs.py`).
UNSET_SWAP = {"hx-select": "unset", "hx-select-oob": "unset", "hx-push-url": "false"}


def presets(*, choices: Sequence[PresetChoice]) -> Html:
    """The preset the NavTree is in, and the ones the reader can switch to.

    The same node under each `?nav=`, so a switch never costs them their place. Inside the
    swapped element rather than above it, which is what keeps the links pointing at the node a
    click just landed on.
    """
    return htpy.p(".presets", aria_label="NavTree presets")[
        [
            htpy.a(
                ".button",
                data_nav=choice.preset,
                href=choice.url,
                aria_current="true" if choice.current else None,
            )[choice.preset.label]
            for choice in choices
        ]
    ]


def lines(*, rows: Sequence[NavTreeRow], suffix: str, thread: str) -> Html:
    """Every row of one NavTree, in document order — a node's own, or a level's tail.

    `thread` is the thread the rows are described for, which is what a tail row's fetch has to
    carry: a level may hold nodes of another thread, and a row is described by the thread the
    reader is on rather than by the one its node ran on.
    """
    return htpy.fragment[
        [
            _tail(row=row, suffix=suffix, thread=thread)
            if row.cut
            else _row(row=row, suffix=suffix)
            for row in rows
        ]
    ]


def _tail(*, row: NavTreeRow, suffix: str, thread: str) -> Html:
    """What the window left out of this level, and the way to it.

    htmx fetches those rows and stands them where this one stands, so the level opens without
    the reader losing the pane. A button rather than a link — there is no page at the other end,
    only the rest of a level. The fetch carries the reader's knobs, the thread they are reading
    on, the depth these rows sit at, and the child the open path descends through, which the cap
    kept and this must not send twice.
    """
    fetch = f"{suffix}{'&' if suffix else '?'}thread={thread}&depth={row.depth}"
    if row.opened:
        fetch += f"&opened={row.opened}"
    return htpy.li(".row.more", data_depth=row.depth, data_more=row.node.key)[
        htpy.button(
            {"hx-get": f"{row.node.rest}{fetch}", **UNSET_SWAP},
            type="button",
            hx_target="closest li",
            hx_swap="outerHTML",
        )[["+", htpy.span(data_field="cut")[fmt.count(row.cut)], " more"]]
    ]


def _row(*, row: NavTreeRow, suffix: str) -> Html:
    """One node's row: what it is, what it is called, and what it cost.

    `ancestor` is what the stylesheet clamps: a step of the open path above the selection stays
    at the top of the scroller while the rows under it go by. A class rather than a key of its
    own, like the bar's — it is a thing the stylesheet paints and not a value the store holds —
    and it rides the rows of one path, never the level around them.
    """
    node = row.node
    url = f"{node.url}{suffix}"
    return htpy.li(
        class_=f"row node {node.kind} {node.bar}{' ancestor' if row.ancestor else ''}",
        data_depth=row.depth,
        data_nav_tree=node.key,
        data_selected=node.key if row.selected else None,
        aria_current="true" if row.selected else None,
    )[
        [
            _peek(numbers=node.numbers),
            # A row links where it fetches: one URL, whether the reader clicks it, pastes it, or
            # comes back to it from a bookmark. What the click does with the response is written
            # once, on `#nav-tree-rows`, and inherited from here.
            htpy.a(href=url, hx_get=url)[
                [
                    parts.mark(character=node.icon),
                    parts.glyph(enriched=node.enriched),
                    htpy.span(data_field="title")[node.nav_tree_title],
                    _error(node.is_error),
                    _compacted(node.compactions),
                    _cost(row=row),
                ]
            ],
        ]
    ]


def _peek(*, numbers: str) -> Html | None:
    """What the bar and the badge on this row stand for, fetched when a reader reaches the row.

    A sibling of the link rather than the row itself, because htmx attributes are inherited: the
    overrides a popover needs written on the `<li>` would be inherited by the link inside it, and
    the click would swap a pane's worth of markup into the row. The trigger listens on the row
    all the same — `from:` is what separates where an event is heard from what answers it — and
    on `focusin`, which bubbles where `focus` does not, so a reader tabbing to the link reaches
    what the pointer reaches. Once apiece: the popover is markup that stays.
    """
    if not numbers:
        return None
    return htpy.span(
        ".peek",
        {"hx-get": numbers, **UNSET_SWAP},
        hx_trigger="mouseenter from:closest li once delay:200ms, focusin from:closest li once",
        hx_target="this",
        hx_swap="beforeend",
    )


def _error(is_error: bool) -> Html | None:
    """The one thing the NavTree says about a node beyond what it is and what it cost.

    Spelled the way the children log spells it, so the stylesheet's one alarm rule paints both
    and a test reads either the same way.
    """
    return htpy.span(data_field="is_error")["error"] if is_error else None


def _compacted(compactions: int) -> Html | None:
    """How often this run's own thread ran its window out.

    Drawn on a run's row alone, because a compaction of the thread the reader is on is already a
    ⊟ row of the tree and this one is not: a subagent compacts unasked, on a transcript nobody
    opened. The count rides the labelled span and the word beside it does not, the way every
    other number on a row is carried — and the pill is absent rather than zero, since a run that
    never compacted has nothing to say.
    """
    if not compactions:
        return None
    return htpy.span(".compacted")[
        [
            htpy.span(data_field="compactions")[fmt.count(compactions)],
            f" compaction{'' if compactions == 1 else 's'}",
        ]
    ]


def _cost(*, row: NavTreeRow) -> Html | None:
    """What the row spent, and — where a run hangs under it — what its whole subtree did.

    Two badges rather than one number because the two answer different questions: a turn that
    spawned four agents cost little itself and drove a lot. Each wears its own step class, so the
    deeper ground is the bigger share whichever half carries it.
    """
    node = row.node
    if node.cost_usd is None:
        return None
    return htpy.span(".secondary")[
        [
            parts.badge(step=node.meter, field="cost_usd", value=node.cost_usd),
            ["/", parts.badge(step=node.total_meter, field="total_usd", value=node.total_usd)]
            if node.total_usd is not None
            else None,
            parts.unpriced(calls=node.unpriced_api_calls),
        ]
    ]
