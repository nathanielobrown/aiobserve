"""The tree beside a node page: the path down to the selection, and only that path opened.

Every node of a session has a URL of its own, and the tree is how a reader walks between
them. What renders is one open path — the selection's ancestors, the selection, and the
selection's children — so a session's whole shape is never on the page at once. The rows come
back flat, in document order, because a click swaps the list out of band and a nested list
would swap only the part of itself the click happened to land in.

The path is resolved bottom-up in refs, which are ids and nothing else, and then expanded
top-down: a node renders out of its parent's level, so every visible node has a visible parent
by construction rather than by a check afterwards.

Reads the store one level at a time and says which query it ran, so the page can cite it.
Everything else here is arithmetic over the rows those queries returned.
"""

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

import duckdb

from aiobserve.analyze import queries
from aiobserve.analyze.queries import ParamValue
from aiobserve.model import MAIN_SOURCE
from aiobserve.view import bounds
from aiobserve.view.enrichment import Descriptions
from aiobserve.view.nodes import (
    Kind,
    Node,
    Ref,
    call_node,
    compaction_node,
    run_node,
    tool_node,
    turn_node,
    unattached_node,
    unattributed_node,
)
from aiobserve.view.store import (
    TURN_CURSOR,
    Library,
    Page,
    Row,
    cursorless_rows,
    page_rows,
)

# What one level's queries were bound with, in the order the level ran them.
Ran = list[tuple[Library, Mapping[str, ParamValue]]]


@dataclass(frozen=True)
class Corpus:
    """What every level of one session's tree is built against, read once for the request.

    The runs are the session's whole set because a run is placed by the call that spawned it
    rather than by the thread it ran on, so any level may need any of them. The enrichment is
    read for one thread — `view_enrichment` keys turns by source — so a turn on another thread
    reads by its prompt while runs and the session, which are keyed by the session, do not.
    """

    session_id: str
    # What the session spent, the basis every share on the tree is a share of.
    whole: float
    runs: list[Row]
    described: Descriptions
    # The thread the enrichment was read for.
    source: str

    def turn_text(self, source: str, turn_id: str) -> str | None:
        """What the pass called one turn, or None when it said nothing about it."""
        if source != self.source:
            return None
        row = self.described.turns.get(turn_id)
        return row.description if row else None

    def run_text(self, run_id: str) -> str | None:
        """What the pass called one run, or None when it said nothing about it."""
        row = self.described.runs.get(run_id)
        return row.description if row else None


class Level(NamedTuple):
    """The children of one open node, and the queries that read them."""

    nodes: list[Node]
    ran: Ran


@dataclass(frozen=True)
class TreeRow:
    """One line of the tree: a node at its depth, or the tail standing for what a cap cut."""

    node: Node
    depth: int
    selected: bool
    # On a tail row, how many of `node`'s children the cap left out. Zero on a node's own row,
    # which is what tells the two apart.
    cut: int = 0


class Tree(NamedTuple):
    """A whole tree: its rows in document order, the open path, and every query it ran."""

    rows: list[TreeRow]
    # The open path as rendered nodes, outermost first — what the crumbs above the pane show.
    chain: list[Node]
    ran: Ran


def ancestry(corpus: Corpus, trail: Sequence[Ref]) -> list[Ref]:
    """The whole path down to `trail[-1]`, session first.

    `trail` is what the selection's own header already answered: a call and a tool know which
    turn they sit under, and nothing else needs a read to place itself. Raises past
    `bounds.DEPTH` rather than opening a chain the response was never priced for.
    """
    whole = list(trail)
    while (parent := _parent(corpus, whole[0])) is not None:
        whole.insert(0, parent)
        if len(whole) > bounds.DEPTH:
            raise ValueError(f"a chain deeper than {bounds.DEPTH} is not a page this serves")
    return whole


def _parent(corpus: Corpus, ref: Ref) -> Ref | None:
    """Where one node hangs, or None for the session, which hangs nowhere."""
    match ref.kind:
        case Kind.SESSION:
            return None
        case Kind.UNATTACHED:
            return Ref(Kind.SESSION, None, corpus.session_id)
        case Kind.TURN | Kind.COMPACTION | Kind.UNATTRIBUTED:
            return _thread_parent(corpus, str(ref.source))
        case Kind.RUN:
            return _run_parent(corpus, ref.node_id)
        case Kind.CALL | Kind.TOOL:
            raise ValueError(f"a {ref.kind} node's header names its parent; seed the trail")


def _thread_parent(corpus: Corpus, source: str) -> Ref:
    """What a thread hangs off: the session for `main`, else the run that thread belongs to."""
    if source == MAIN_SOURCE:
        return Ref(Kind.SESSION, None, corpus.session_id)
    return Ref(Kind.RUN, source, source)


def _run_parent(corpus: Corpus, run_id: str) -> Ref:
    """Where a run hangs, by the spawning edge alone — the two buckets are disjoint by it.

    A resolved spawning call under a turn puts the run under that turn; one under no turn puts
    it in that thread's unattributed bucket; no spawning call at all puts it in the session's
    unattached bucket. A run the session does not hold is unattached for the same reason.
    """
    row = next((run for run in corpus.runs if run["run_id"] == run_id), None)
    if row is None or row["spawn_source"] is None:
        return Ref(Kind.UNATTACHED, None, corpus.session_id)
    if row["spawn_turn_id"] is None:
        return Ref(Kind.UNATTRIBUTED, row["spawn_source"], row["spawn_source"])
    return Ref(Kind.TURN, row["spawn_source"], row["spawn_turn_id"])


class Standing(NamedTuple):
    """A bucket's own row, beside the query line that produced it."""

    row: Row
    ran: tuple[Library, Mapping[str, ParamValue]]


def home(source: str, turn_id: str | None) -> Ref:
    """Where an api call sits: under the turn it answers, else in its thread's bucket.

    The disjointness rule at the call, which `_run_parent` reads one edge further out for the
    run that call spawned. A NULL turn is a home rather than a missing one.
    """
    if turn_id is None:
        return Ref(Kind.UNATTRIBUTED, source, source)
    return Ref(Kind.TURN, source, turn_id)


def _digest(session_id: str, source: str) -> tuple[Library, dict[str, ParamValue]]:
    """Which digest answers for a thread, and what it binds: `main` has one of its own."""
    if source == MAIN_SOURCE:
        return Page.TIMELINE, {"session_id": session_id}
    return Page.RUN_TIMELINE, {"session_id": session_id, "source": source}


def unattributed(
    connection: duckdb.DuckDBPyConnection, corpus: Corpus, source: str
) -> Standing | None:
    """One thread's calls that answer no turn, as its digest's own cursorless row reads them.

    None where every call on the thread answers a turn — and where the thread is not one this
    session holds, which is the same answer: there is no bucket at that URL either way.
    """
    digest, bound = _digest(corpus.session_id, source)
    rows = cursorless_rows(connection, digest, TURN_CURSOR, bounds.CURSORLESS_TURNS, **bound)
    return Standing(rows[0], (digest, bound)) if rows else None


def _thread_level(
    connection: duckdb.DuckDBPyConnection, corpus: Corpus, source: str, *, unattached: bool
) -> Level:
    """One thread's own children: its turns and compactions in time order, then its buckets.

    A session and a run read alike — the difference is the source, and that only the session
    holds the unattached bucket, which spans every thread rather than sitting on one.
    """
    keyed: dict[str, ParamValue] = {"session_id": corpus.session_id, "source": source}
    turns = page_rows(connection, Page.TREE_TURNS, **keyed, nav_chars=queries.NAV_CHARS)
    marks = page_rows(connection, Page.COMPACTIONS, **keyed, chip_chars=queries.NAV_CHARS)
    # The thread's calls that answer no turn, as one group — the bucket's own row, read the
    # same way the bucket's own page reads it.
    standing = unattributed(connection, corpus, source)
    digest, bound = _digest(corpus.session_id, source)
    placed = _interleave(
        [
            (
                turn_node(
                    corpus.session_id,
                    source,
                    row,
                    corpus.whole,
                    corpus.turn_text(source, row["turn_id"]),
                ),
                row["started_at"],
            )
            for row in turns
        ],
        [(compaction_node(corpus.session_id, source, row), row["timestamp"]) for row in marks],
    )
    if standing is not None:
        placed.append(unattributed_node(corpus.session_id, source, standing.row, corpus.whole))
    if unattached:
        loose_runs = [run for run in corpus.runs if run["spawn_source"] is None]
        if loose_runs:
            placed.append(unattached_node(corpus.session_id, loose_runs, corpus.whole))
    return Level(placed, [(Page.TREE_TURNS, keyed), (Page.COMPACTIONS, keyed), (digest, bound)])


def _interleave(
    turns: list[tuple[Node, dt.datetime | None]], marks: list[tuple[Node, dt.datetime]]
) -> list[Node]:
    """A thread's turns in index order with its compactions dropped in by time.

    A compaction lands before the first turn that started after it, which is where it happened.
    A turn the store has no start for does not move one, and whatever is left over trails the
    thread — a compaction after the last turn is a compaction after the last turn.
    """
    placed: list[Node] = []
    pending = list(marks)
    for node, started in turns:
        while pending and started is not None and pending[0][1] < started:
            placed.append(pending.pop(0)[0])
        placed.append(node)
    placed.extend(node for node, _ in pending)
    return placed


def _calls_level(
    connection: duckdb.DuckDBPyConnection, corpus: Corpus, source: str, turn_id: str | None
) -> Level:
    """The api calls under one turn, each run hoisted after the call that spawned it.

    `turn_id` NULL is the unattributed bucket's level — the calls that answer no turn, and the
    runs those calls spawned. One function for both because the two differ by that binding.
    """
    keyed: dict[str, ParamValue] = {"session_id": corpus.session_id, "source": source}
    bound = keyed | {"turn_id": turn_id}
    calls = page_rows(connection, Page.TREE_CALLS, **bound, nav_chars=queries.NAV_CHARS)
    spawned = [
        run
        for run in corpus.runs
        if run["spawn_source"] == source and run["spawn_turn_id"] == turn_id
    ]
    hoisted: dict[str, list[Row]] = {}
    for run in spawned:
        hoisted.setdefault(run["spawn_call_id"], []).append(run)
    placed: list[Node] = []
    for row in calls:
        placed.append(call_node(corpus.session_id, source, row, corpus.whole))
        for run in hoisted.pop(row["api_call_id"], []):
            placed.append(
                run_node(corpus.session_id, run, corpus.whole, corpus.run_text(run["run_id"]))
            )
    # A run whose spawning call this level does not hold still belongs to this node: the edge
    # resolved to the turn, so dropping it would lose a run the tree is the only way to.
    for leftover in hoisted.values():
        placed.extend(
            run_node(corpus.session_id, run, corpus.whole, corpus.run_text(run["run_id"]))
            for run in leftover
        )
    return Level(placed, [(Page.TREE_CALLS, bound)])


def _tools_level(
    connection: duckdb.DuckDBPyConnection, corpus: Corpus, source: str, api_call_id: str
) -> Level:
    """The tool calls one api call made, in the order it made them."""
    bound: dict[str, ParamValue] = {
        "session_id": corpus.session_id,
        "source": source,
        "api_call_id": api_call_id,
    }
    rows = page_rows(connection, Page.TREE_TOOLS, **bound, nav_chars=queries.NAV_CHARS)
    nodes = [tool_node(corpus.session_id, source, row) for row in rows]
    return Level(nodes, [(Page.TREE_TOOLS, bound)])


def _unattached_level(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    """The runs nothing placed. Already read with the session's runs, so this reads nothing."""
    nodes = [
        run_node(corpus.session_id, run, corpus.whole, corpus.run_text(run["run_id"]))
        for run in corpus.runs
        if run["spawn_source"] is None
    ]
    return Level(nodes, [])


def _leaf(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    """A node nothing hangs under: a tool call, and a compaction."""
    return Level([], [])


# What one kind of node holds. Total over `Kind` on purpose: the tree opens whatever the path
# reaches, so a kind with no entry would be a page that renders and then raises halfway down.
CHILDREN: dict[Kind, Callable[[duckdb.DuckDBPyConnection, Corpus, Node], Level]] = {
    Kind.SESSION: lambda connection, corpus, node: _thread_level(
        connection, corpus, MAIN_SOURCE, unattached=True
    ),
    Kind.RUN: lambda connection, corpus, node: _thread_level(
        connection, corpus, node.node_id, unattached=False
    ),
    Kind.TURN: lambda connection, corpus, node: _calls_level(
        connection, corpus, str(node.source), node.node_id
    ),
    Kind.UNATTRIBUTED: lambda connection, corpus, node: _calls_level(
        connection, corpus, str(node.source), None
    ),
    Kind.CALL: lambda connection, corpus, node: _tools_level(
        connection, corpus, str(node.source), node.node_id
    ),
    Kind.UNATTACHED: _unattached_level,
    Kind.TOOL: _leaf,
    Kind.COMPACTION: _leaf,
}


def tree(
    connection: duckdb.DuckDBPyConnection,
    corpus: Corpus,
    root: Node,
    trail: Sequence[Ref],
    cap: int,
) -> Tree:
    """The session's tree with `trail` open — its steps, their siblings, and its children.

    `trail` runs outermost first and ends at the selection; `root` is the node `trail[0]` names,
    which the page read for its own header. Every step is expanded and nothing else is, so a
    reader sees one path and what sits beside each step of it. `cap` bounds a level and a tail
    row says what it left out, except that the row the path goes through is always kept: a cut
    that hid the selection would leave the pane describing a node the tree does not show.
    """
    open_keys = [ref.key for ref in trail]
    selection = open_keys[-1]
    rows: list[TreeRow] = []
    chain: list[Node] = []
    ran: Ran = []

    def expand(node: Node, depth: int) -> None:
        rows.append(TreeRow(node, depth, selected=node.key == selection))
        if node.key not in open_keys:
            return
        chain.append(node)
        level = CHILDREN[node.kind](connection, corpus, node)
        ran.extend(level.ran)
        kept, cut = _kin(level.nodes, cap, open_keys)
        for child in kept:
            expand(child, depth + 1)
        if cut:
            rows.append(TreeRow(node, depth + 1, selected=False, cut=cut))

    expand(root, 0)
    # The selection renders out of its parent's level, so a chain shorter than the trail means
    # a level did not hold the child the path named — a store shape, not a page to serve.
    if len(chain) != len(trail):
        raise ValueError(f"nothing under {chain[-1].key} holds {open_keys[len(chain)]}")
    return Tree(rows, chain, ran)


def _kin(under: Sequence[Node], cap: int, open_keys: Sequence[str]) -> tuple[list[Node], int]:
    """The first `cap` children plus the one the path descends through, and what was cut."""
    kept = list(under[:cap])
    rescued = [node for node in under[cap:] if node.key in open_keys]
    return kept + rescued, len(under) - len(kept) - len(rescued)
