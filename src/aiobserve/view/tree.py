"""The tree beside a node page: the path down to the selection, and only that path opened.

Every node of a session has a URL of its own, and the tree is how a reader walks between
them. What renders is one open path — the selection's ancestors, the selection, and the
selection's children — so a session's whole shape is never on the page at once. The rows come
back flat, in document order, because a click swaps the list out of band and a nested list
would swap only the part of itself the click happened to land in.

Reads the store for one level at a time and says which query it ran, so the page can cite it.
Everything else here is arithmetic over the rows those queries returned.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

import duckdb

from aiobserve.analyze import queries
from aiobserve.analyze.queries import ParamValue
from aiobserve.model import MAIN_SOURCE
from aiobserve.view.store import Library, Page, Row, page_rows
from aiobserve.view.threads import meter


class Kind(StrEnum):
    """What a node is: the segment its URL carries, and the query its children come from."""

    SESSION = "session"
    TURN = "turn"
    CALL = "call"


@dataclass(frozen=True)
class Node:
    """One node of a session, wherever it is read — a tree row, a crumb, or the pane itself."""

    kind: Kind
    session_id: str
    # The thread it was recorded on, `main` or a run's id. None for the session, which is
    # every thread it holds rather than one of them.
    source: str | None
    node_id: str
    label: str
    # What it cost, and how many calls under it our price table could not price: a total
    # missing calls is not what the node cost, so the two always travel together.
    cost_usd: float
    unpriced_api_calls: int
    # Its share of what the session spent, or None when the session spent nothing — a share
    # of nothing is a gap rather than 0%.
    share: float | None

    @property
    def key(self) -> str:
        """`kind:id` — what a row is marked with, and how a test names the row it means."""
        return f"{self.kind}:{self.node_id}"

    @property
    def url(self) -> str:
        """Where the node reads: the link a row carries, and the URL a click fetches."""
        if self.kind is Kind.SESSION:
            return f"/session/{self.session_id}"
        return f"/session/{self.session_id}/{self.kind}/{self.source}/{self.node_id}"

    @property
    def meter(self) -> str:
        """The step class this node's spend bar is drawn with."""
        return meter(self.share)


@dataclass(frozen=True)
class TreeRow:
    """One line of the tree: a node at its depth, or the tail standing for what a cap cut."""

    node: Node
    depth: int
    selected: bool
    # On a tail row, how many of `node`'s children the cap left out. Zero on a node's own row,
    # which is what tells the two apart.
    cut: int = 0


class Level(NamedTuple):
    """The children of one open node, and the query that read them."""

    nodes: list[Node]
    query: Library
    bindings: Mapping[str, ParamValue]


class Tree(NamedTuple):
    """A whole tree: its rows in document order, and every query it ran to build them."""

    rows: list[TreeRow]
    ran: list[tuple[Library, Mapping[str, ParamValue]]]


def session_node(header: Row) -> Node:
    """The root of every tree: the session everything under it was recorded in."""
    cost = header["cost_usd"] or 0
    return Node(
        kind=Kind.SESSION,
        session_id=header["session_id"],
        source=None,
        node_id=header["session_id"],
        # The title Claude Code gave the session, else the id — which is what a reader
        # pasted to arrive here, so it names the row even for a session with no title.
        label=(header["title"] or header["session_id"])[: queries.NAV_CHARS],
        cost_usd=cost,
        unpriced_api_calls=header["unpriced_api_calls"],
        share=1.0 if cost else None,
    )


def turn_node(session_id: str, source: str, row: Row, whole: float) -> Node:
    """One turn as a node, from a tree row or from the turn's own header.

    Both carry the same three label columns, cut here rather than in SQL so that a turn on
    the tree and the same turn in the crumbs above the pane read alike.
    """
    cost = row["cost_usd"]
    return Node(
        kind=Kind.TURN,
        session_id=session_id,
        source=source,
        node_id=row["turn_id"],
        label=_turn_label(row)[: queries.NAV_CHARS],
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=cost / whole if whole else None,
    )


def call_node(session_id: str, source: str, row: Row, whole: float) -> Node:
    """One api call as a node: what it said, else the model that said it."""
    cost = row["cost_usd"] or 0
    return Node(
        kind=Kind.CALL,
        session_id=session_id,
        source=source,
        node_id=row["api_call_id"],
        # A call that answered with tool calls and no text has nothing to quote, so the
        # model names the row rather than leaving it blank.
        label=(row["text_head"] or row["model"] or "")[: queries.NAV_CHARS],
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=cost / whole if whole else None,
    )


def _turn_label(row: Row) -> str:
    """What to call a turn: the command it ran and what followed, else the prompt as typed.

    The prompt is last because a slash command's prompt is the `<command-…>` wrapper Claude
    Code put around it, which says nothing in the width of a tree.
    """
    if row["command_name"] is not None:
        return f"{row['command_name']} {row['command_args'] or ''}".strip()
    # The store declares a turn's prompt NOT NULL (`export/duckdb.py`), so this arm always
    # has something to say, even when what it says is the empty string.
    return row["prompt"]


def _turns_under(connection: duckdb.DuckDBPyConnection, node: Node, whole: float) -> Level:
    """A session's main thread, one row per turn."""
    bindings: Mapping[str, ParamValue] = {"session_id": node.session_id, "source": MAIN_SOURCE}
    rows = page_rows(connection, Page.TREE_TURNS, **bindings, nav_chars=queries.NAV_CHARS)
    nodes = [turn_node(node.session_id, MAIN_SOURCE, row, whole) for row in rows]
    return Level(nodes, Page.TREE_TURNS, bindings)


def _calls_under(connection: duckdb.DuckDBPyConnection, node: Node, whole: float) -> Level:
    """The api calls one turn made, in the order it made them."""
    bindings: Mapping[str, ParamValue] = {
        "session_id": node.session_id,
        "source": node.source,
        "turn_id": node.node_id,
    }
    rows = page_rows(connection, Page.TREE_CALLS, **bindings, nav_chars=queries.NAV_CHARS)
    nodes = [call_node(node.session_id, str(node.source), row, whole) for row in rows]
    return Level(nodes, Page.TREE_CALLS, bindings)


# What one kind of node holds. Closed rather than defaulted: a kind with no entry is a kind
# nothing can be opened to yet, and the tree says so by raising instead of rendering a leaf.
CHILDREN = {Kind.SESSION: _turns_under, Kind.TURN: _calls_under}


def turn_chain(session: Row, source: str, header: Row, whole: float) -> list[Node]:
    """The open path down to one turn: the session it was recorded in, then the turn."""
    return [session_node(session), turn_node(session["session_id"], source, header, whole)]


def tree(
    connection: duckdb.DuckDBPyConnection, chain: Sequence[Node], whole: float, cap: int
) -> Tree:
    """The session's tree with `chain` open — its steps, their siblings, and its children.

    `chain` runs outermost first and ends at the selection. Every node on it is expanded and
    nothing else is, so the reader sees one path and what sits beside each step of it. `cap`
    bounds a level and a tail row says what it left out, except that the row the path goes
    through is always kept: a cut that hid the selection would leave the pane describing a
    node the tree does not show.
    """
    selection = chain[-1].key
    open_keys = [node.key for node in chain]
    rows: list[TreeRow] = []
    ran: list[tuple[Library, Mapping[str, ParamValue]]] = []

    def expand(node: Node, depth: int) -> None:
        rows.append(TreeRow(node, depth, selected=node.key == selection))
        if node.key not in open_keys:
            return
        level = CHILDREN[node.kind](connection, node, whole)
        ran.append((level.query, level.bindings))
        kept, cut = _kin(level.nodes, cap, open_keys)
        for child in kept:
            expand(child, depth + 1)
        if cut:
            rows.append(TreeRow(node, depth + 1, selected=False, cut=cut))

    expand(chain[0], 0)
    return Tree(rows, ran)


def _kin(under: Sequence[Node], cap: int, open_keys: Sequence[str]) -> tuple[list[Node], int]:
    """The first `cap` children plus the one the path descends through, and what was cut."""
    kept = list(under[:cap])
    rescued = [node for node in under[cap:] if node.key in open_keys]
    return kept + rescued, len(under) - len(kept) - len(rescued)
