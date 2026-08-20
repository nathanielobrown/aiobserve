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
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    Preset,
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
        # A compaction reaches here only where it happened between two turns: one that
        # happened during a turn is that turn's child, and its page seeds the turn.
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
    """One thread's own children: its turns and the compactions between them, then its buckets.

    A session and a run read alike — the difference is the source, and that only the session
    holds the unattached bucket, which spans every thread rather than sitting on one. Only the
    compactions that happened between two turns are here; one that happened *during* a turn is
    a child of that turn (`_marks`).
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
        [
            (compaction_node(corpus.session_id, source, row), row["timestamp"])
            for row in marks
            if row["turn_id"] is None
        ],
    )
    if standing is not None:
        placed.append(unattributed_node(corpus.session_id, source, standing.row, corpus.whole))
    if unattached:
        loose_runs = [run for run in corpus.runs if run["spawn_source"] is None]
        if loose_runs:
            placed.append(unattached_node(corpus.session_id, loose_runs, corpus.whole))
    return Level(placed, [(Page.TREE_TURNS, keyed), (Page.COMPACTIONS, keyed), (digest, bound)])


def _interleave[T](
    ordered: Sequence[tuple[T, dt.datetime | None]], marks: Sequence[tuple[T, dt.datetime]]
) -> list[T]:
    """A level in its own order with the compactions of the same thread dropped in by time.

    A compaction lands before the first row that started after it, which is where it happened.
    A row the store has no start for does not move one, and whatever is left over trails the
    level — a compaction after the last row is a compaction after the last row.

    Generic in what it places because two levels want it: a thread's turns, and the calls or
    tool calls under one turn.
    """
    placed: list[T] = []
    pending = list(marks)
    for item, started in ordered:
        while pending and started is not None and pending[0][1] < started:
            placed.append(pending.pop(0)[0])
        placed.append(item)
    placed.extend(item for item, _ in pending)
    return placed


def _marks(
    connection: duckdb.DuckDBPyConnection, corpus: Corpus, source: str, turn_id: str | None
) -> tuple[list[tuple[tuple[str, Node], dt.datetime]], Ran]:
    """One turn's compactions, paired with their ids the way a level's own rows are.

    A compaction is a child of the turn it happened during, so a turn's level holds its own —
    interleaved with the calls or tool calls by time. Nothing at `turn_id` NULL: a bucket
    holds calls that answer no turn, and a compaction that answers none is the thread's.
    """
    if turn_id is None:
        return [], []
    keyed: dict[str, ParamValue] = {"session_id": corpus.session_id, "source": source}
    rows = page_rows(connection, Page.COMPACTIONS, **keyed, chip_chars=queries.NAV_CHARS)
    return [
        (
            (row["compaction_id"], compaction_node(corpus.session_id, source, row)),
            row["timestamp"],
        )
        for row in rows
        if row["turn_id"] == turn_id
    ], [(Page.COMPACTIONS, keyed)]


def _runs(corpus: Corpus, rows: Iterable[Row]) -> list[Node]:
    """A run row per node, described by whatever the enrichment pass called it."""
    return [
        run_node(corpus.session_id, row, corpus.whole, corpus.run_text(row["run_id"]))
        for row in rows
    ]


def _spawned(corpus: Corpus, source: str, turn_id: str | None) -> list[Row]:
    """The runs whose spawning call resolved to one node — a turn, or a thread's bucket."""
    return [
        run
        for run in corpus.runs
        if run["spawn_source"] == source and run["spawn_turn_id"] == turn_id
    ]


def _hoisted(
    corpus: Corpus, placed: Sequence[tuple[str, Node]], spawned: Sequence[Row], edge: str
) -> list[Node]:
    """One level's rows with each run dropped in after the row its spawning edge names.

    `placed` pairs each row's id with its node, and `edge` is the run column naming one — the
    api call under `full`, the tool call under `noapi`, which is what the preset leaves
    showing. A run whose row this level does not hold still belongs to the node the level
    hangs under, so it trails the level: dropping it would lose a run the tree is the only
    way to.
    """
    waiting: dict[str, list[Row]] = {}
    for run in spawned:
        waiting.setdefault(run[edge], []).append(run)
    level: list[Node] = []
    for row_id, node in placed:
        level.append(node)
        level.extend(_runs(corpus, waiting.pop(row_id, [])))
    for leftover in waiting.values():
        level.extend(_runs(corpus, leftover))
    return level


def _calls_level(
    connection: duckdb.DuckDBPyConnection, corpus: Corpus, source: str, turn_id: str | None
) -> Level:
    """The api calls under one turn, its compactions among them, each run hoisted after the
    call that spawned it.

    `turn_id` NULL is the unattributed bucket's level — the calls that answer no turn, and the
    runs those calls spawned. One function for both because the two differ by that binding.
    """
    keyed: dict[str, ParamValue] = {"session_id": corpus.session_id, "source": source}
    bound = keyed | {"turn_id": turn_id}
    calls = page_rows(connection, Page.TREE_CALLS, **bound, nav_chars=queries.NAV_CHARS)
    marks, mark_ran = _marks(connection, corpus, source, turn_id)
    # Each row keyed by its own id, which is what `_hoisted` reads to place a run after the
    # call that spawned it. A compaction's id answers no spawning edge, so it just passes.
    placed = _interleave(
        [
            (
                (row["api_call_id"], call_node(corpus.session_id, source, row, corpus.whole)),
                row["started_at"],
            )
            for row in calls
        ],
        marks,
    )
    level = _hoisted(corpus, placed, _spawned(corpus, source, turn_id), "spawn_call_id")
    return Level(level, [(Page.TREE_CALLS, bound), *mark_ran])


def _tools_level(
    connection: duckdb.DuckDBPyConnection,
    corpus: Corpus,
    source: str,
    api_call_id: str | None,
    turn_id: str | None,
) -> Level:
    """The tool calls under one api call, or — at `api_call_id` NULL — under one turn.

    The second is `noapi`'s level: the api calls are folded away, so their tool calls stand
    under the turn in call-then-tool order with each run hoisted after the tool call that
    spawned it, and the turn's compactions interleave by time. A call's own level hoists
    nothing and holds no compaction, because both hang off the turn.
    """
    bound: dict[str, ParamValue] = {
        "session_id": corpus.session_id,
        "source": source,
        "api_call_id": api_call_id,
        "turn_id": turn_id,
    }
    rows = page_rows(connection, Page.TREE_TOOLS, **bound, nav_chars=queries.NAV_CHARS)
    under = None if api_call_id is not None else turn_id
    marks, mark_ran = _marks(connection, corpus, source, under)
    placed = _interleave(
        [
            ((row["tool_call_id"], tool_node(corpus.session_id, source, row)), row["started_at"])
            for row in rows
        ],
        marks,
    )
    spawned = [] if api_call_id is not None else _spawned(corpus, source, turn_id)
    return Level(
        _hoisted(corpus, placed, spawned, "tool_use_id"), [(Page.TREE_TOOLS, bound), *mark_ran]
    )


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


# The `agents` preset's levels, which read nothing: a run is placed by an edge `view_runs`
# already answered, so the whole spawn tree is arithmetic over the runs read for the request.
def _agent_session(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    """The runs the main thread spawned, then the runs nothing placed."""
    placed = _runs(corpus, [run for run in corpus.runs if run["spawn_source"] == MAIN_SOURCE])
    loose = [run for run in corpus.runs if run["spawn_source"] is None]
    if loose:
        placed.append(unattached_node(corpus.session_id, loose, corpus.whole))
    return Level(placed, [])


def _agent_children(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    """The runs a run spawned, by what the transcript says their parent was.

    `parent_agent_id` rather than the spawning call, because this is the one level the preset
    is about: a run whose spawning call resolved to nothing still names the run it came from.
    """
    return Level(
        _runs(corpus, [r for r in corpus.runs if r["parent_agent_id"] == node.node_id]), []
    )


def _agent_thread(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    """The runs one turn — or, at a bucket, one thread's turnless calls — spawned."""
    turn_id = None if node.kind is Kind.UNATTRIBUTED else node.node_id
    return Level(_runs(corpus, _spawned(corpus, str(node.source), turn_id)), [])


def _agent_call(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    """The runs one api call spawned. Matched on the thread too: a fork's transcript replays
    its parent's calls, so an id alone would hang the run under the replayed copy as well."""
    placed = [
        run
        for run in corpus.runs
        if run["spawn_call_id"] == node.node_id and run["spawn_source"] == node.source
    ]
    return Level(_runs(corpus, placed), [])


def _agent_tool(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    """The run one tool call spawned, matched on the thread for the same reason as the call."""
    placed = [
        run
        for run in corpus.runs
        if run["tool_use_id"] == node.node_id and run["spawn_source"] == node.source
    ]
    return Level(_runs(corpus, placed), [])


def _session_level(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    return _thread_level(connection, corpus, MAIN_SOURCE, unattached=True)


def _run_level(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    return _thread_level(connection, corpus, node.node_id, unattached=False)


def _turn_calls(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    return _calls_level(connection, corpus, str(node.source), node.node_id)


def _bucket_calls(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    return _calls_level(connection, corpus, str(node.source), None)


def _turn_tools(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    return _tools_level(connection, corpus, str(node.source), None, node.node_id)


def _bucket_tools(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    return _tools_level(connection, corpus, str(node.source), None, None)


def _call_tools(connection: duckdb.DuckDBPyConnection, corpus: Corpus, node: Node) -> Level:
    return _tools_level(connection, corpus, str(node.source), node.node_id, None)


Builder = Callable[[duckdb.DuckDBPyConnection, Corpus, Node], Level]

# What one kind of node holds under one filter preset — the design's kind × preset table, one
# entry per cell. Total over `Kind × Preset` on purpose, and spelled out rather than defaulted:
# the tree opens whatever the path reaches, so a missing cell would be a page that renders and
# then raises halfway down, and a cell a preset passes through is a decision either way.
CHILDREN: dict[tuple[Kind, Preset], Builder] = {
    (Kind.SESSION, Preset.FULL): _session_level,
    (Kind.SESSION, Preset.NO_API): _session_level,
    (Kind.SESSION, Preset.AGENTS): _agent_session,
    (Kind.RUN, Preset.FULL): _run_level,
    (Kind.RUN, Preset.NO_API): _run_level,
    (Kind.RUN, Preset.AGENTS): _agent_children,
    (Kind.TURN, Preset.FULL): _turn_calls,
    (Kind.TURN, Preset.NO_API): _turn_tools,
    (Kind.TURN, Preset.AGENTS): _agent_thread,
    (Kind.UNATTRIBUTED, Preset.FULL): _bucket_calls,
    (Kind.UNATTRIBUTED, Preset.NO_API): _bucket_tools,
    (Kind.UNATTRIBUTED, Preset.AGENTS): _agent_thread,
    (Kind.CALL, Preset.FULL): _call_tools,
    (Kind.CALL, Preset.NO_API): _call_tools,
    (Kind.CALL, Preset.AGENTS): _agent_call,
    (Kind.TOOL, Preset.FULL): _leaf,
    (Kind.TOOL, Preset.NO_API): _leaf,
    (Kind.TOOL, Preset.AGENTS): _agent_tool,
    (Kind.COMPACTION, Preset.FULL): _leaf,
    (Kind.COMPACTION, Preset.NO_API): _leaf,
    (Kind.COMPACTION, Preset.AGENTS): _leaf,
    (Kind.UNATTACHED, Preset.FULL): _unattached_level,
    (Kind.UNATTACHED, Preset.NO_API): _unattached_level,
    (Kind.UNATTACHED, Preset.AGENTS): _unattached_level,
}


def tree(
    connection: duckdb.DuckDBPyConnection,
    corpus: Corpus,
    root: Node,
    trail: Sequence[Ref],
    preset: Preset,
    cap: int,
) -> Tree:
    """The session's tree with `trail` open — its steps, their siblings, and its children.

    `trail` runs outermost first and ends at the selection; `root` is the node `trail[0]` names,
    which the page read for its own header. Every step is expanded and nothing else is, so a
    reader sees one path and what sits beside each step of it. `preset` picks which children
    each level shows, except that a level whose cell hides the path's own next step renders in
    full: a reader standing on a folded-away kind still sees where it sits. `cap` bounds a
    level and a tail row says what it left out, except that the row the path goes through is
    always kept: a cut that hid the selection would leave the pane describing a node the tree
    does not show.
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
        level = CHILDREN[(node.kind, preset)](connection, corpus, node)
        at = open_keys.index(node.key) + 1
        if at < len(open_keys) and all(child.key != open_keys[at] for child in level.nodes):
            # A preset filters children and never the expanded chain: where this level's cell
            # hides the kind the path goes through, the level renders in full instead. Adding
            # the step to the filtered level would draw part of the tree twice — `noapi`
            # hoists a tool call to its turn, so an api call spliced back in would render its
            # own copy of a row already sitting a level higher.
            level = CHILDREN[(node.kind, Preset.FULL)](connection, corpus, node)
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
    """The first `cap` children, the one the path descends through among them, and what was cut.

    The path's child takes a slot rather than an extra row: `cap` is what the page's byte
    arithmetic is priced on, so a level that renders `cap + 1` children is a page over the
    bound. Only one child of a level can be on the path, so the rescue costs at most the level's
    last shown sibling — a row the tail still counts and the parent's own page still lists.
    """
    rescued = [node for node in under[cap:] if node.key in open_keys]
    kept = list(under[: max(cap - len(rescued), 0)])
    return kept + rescued, len(under) - len(kept) - len(rescued)
