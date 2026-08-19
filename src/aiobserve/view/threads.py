"""A session's threads as the pages lay them out: turns in order, with the runs they spawned.

A run's transcript is a thread like the session's own, so a session page and a run page are
the same shape over a different `source`. Two rules do the work and both are load-bearing: a
run hangs off the turn its spawning call was made in — never off a turn of its own timeline,
which is where a fork's un-replayed copy of that call would put it — and a run the store
names no parent for hangs off nothing rather than off a guess.

Pure functions over rows the queries returned. Nothing here reads a store or a request.
"""

import math
from collections.abc import Container, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import NamedTuple

from aiobserve.analyze import queries
from aiobserve.model import MAIN_SOURCE
from aiobserve.view.store import Row

# The digests' keyset column: where a turn's prompt sat in its thread, unique and ascending,
# which is what lets `after` mean "the last index already shown" instead of an offset.
TURN_CURSOR = "turn_index"


class EntryKind(StrEnum):
    """What one row of a session timeline is."""

    TURN = "turn"
    COMPACTION = "compaction"


@dataclass(frozen=True)
class Chip:
    """One agent run where it hangs: on the turn that spawned it, under the run that did."""

    run: Row
    children: tuple["Chip", ...]


class Capped[T](NamedTuple):
    """What one budget let through, and what it left behind.

    Every list of runs a page renders is bounded this way rather than by what the corpus
    happens to hold, and a page that cut something says so — `cut` is what the "+N more" link
    is minted from, `total` what a heading counts.
    """

    shown: tuple[T, ...]
    # Rows the budget left out: nodes of the forest, not top-level items.
    cut: int
    # Rows the whole list holds, cut or not.
    total: int


@dataclass(frozen=True)
class Entry:
    """One row of a thread's timeline: a turn with its runs, or a compaction marker."""

    kind: EntryKind
    row: Row
    chips: Capped[Chip] = Capped((), 0, 0)

    @property
    def continuation(self) -> bool:
        """The row carrying the calls that answer no turn of this thread.

        A resume answers turns that live in the session it resumed, and a by-reference fork
        opens mid-conversation; the digests give both a sentinel row rather than dropping
        their spend. The page says so, because "no prompt" and "an empty prompt" read alike.
        """
        return self.kind is EntryKind.TURN and self.row["turn_id"] == queries.UNATTRIBUTED


def chips(runs: Sequence[Row], source: str, turn_id: str | None) -> tuple[Chip, ...]:
    """The runs one turn of `source` spawned, each carrying the runs it spawned in turn."""
    return tuple(
        Chip(run, chips(runs, run["run_id"], None) + _under_turns(runs, run["run_id"]))
        for run in runs
        if run["spawn_source"] == source and run["spawn_turn_id"] == turn_id
    )


def _under_turns(runs: Sequence[Row], source: str) -> tuple[Chip, ...]:
    """Runs spawned from a turn of `source`'s own timeline, which the run page lays out."""
    turns = sorted({run["spawn_turn_id"] for run in runs if run["spawn_source"] == source} - {None})
    return tuple(chip for turn_id in turns for chip in chips(runs, source, turn_id))


def capped(forest: Sequence[Chip], budget: int) -> Capped[Chip]:
    """A run forest cut to `budget` nodes, counting what it cut.

    The cut takes the first `budget` nodes of a pre-order walk, so a rendered run's parent is
    always rendered — a top-level cap would bound nothing, since three chips can carry fifty
    nodes between them.
    """
    total = sum(1 for _ in _walk(forest))
    shown, _ = _take(forest, budget)
    return Capped(shown, max(total - budget, 0), total)


def _take(forest: Sequence[Chip], budget: int) -> tuple[tuple[Chip, ...], int]:
    """The first `budget` nodes of a pre-order walk, and what is left of the budget."""
    kept: list[Chip] = []
    for chip in forest:
        if budget <= 0:
            break
        children, budget = _take(chip.children, budget - 1)
        kept.append(Chip(chip.run, children))
    return tuple(kept), budget


def cut_to[T](items: Sequence[T], budget: int) -> Capped[T]:
    """A flat list cut to `budget` rows — the forest cap's case for rows that do not nest."""
    return Capped(tuple(items[:budget]), max(len(items) - budget, 0), len(items))


def timeline(
    turns: Sequence[Row],
    runs: Sequence[Row],
    compactions: Sequence[Row],
    source: str,
    budget: int,
) -> list[Entry]:
    """One thread's turns in order, with the runs it spawned chipped on and its compactions
    interleaved.

    `source` is `main` for a session page and a run id for a run page: the same shape either
    way, because a run's transcript is a thread like the session's own. `budget` is how many
    runs one turn may render before the rest become a "+N more".
    """
    entries: list[Entry] = []
    pending = list(compactions)
    for turn in turns:
        started = turn["started_at"]
        # A compaction that ran before this turn started belongs after the previous one.
        while pending and started is not None and pending[0]["timestamp"] <= started:
            entries.append(Entry(EntryKind.COMPACTION, pending.pop(0)))
        forest = capped(chips(runs, source, turn["turn_id"]), budget)
        entries.append(Entry(EntryKind.TURN, turn, forest))
    return entries + [Entry(EntryKind.COMPACTION, row) for row in pending]


def marks_on_page(
    compactions: Sequence[Row], outline: Sequence[Row], page: Sequence[Row]
) -> list[Row]:
    """The compactions one page of a thread renders: those `timeline` hangs on its own turns.

    A mark hangs off the turn it precedes — the first turn of the thread that had not started
    when it ran — so which page it belongs to is a property of the whole thread, computed
    here against `outline` and not against the page. Windowing by time instead would show a
    mark twice or lose it wherever a thread's clocks run backwards, which a resumed
    transcript's do.
    """
    if not outline:
        # Nothing to hang a mark on, which is one page: every mark trails the thread.
        return list(compactions)
    if not page:
        return []
    at = {row["turn_index"]: position for position, row in enumerate(outline)}
    start, stop = at[page[0]["turn_index"]], at[page[-1]["turn_index"]] + 1
    # The last page is open at its end, so the marks that trail every turn land on it.
    trailing = stop == len(outline)
    return [
        mark
        for mark in compactions
        if start <= (fell := _lands(mark, outline)) < stop or (trailing and fell == stop)
    ]


def _lands(mark: Row, outline: Sequence[Row]) -> int:
    """Where in a thread one compaction falls: the position of the turn it precedes.

    `len(outline)` when no turn of the thread started after it — the marks that trail the
    timeline, which is what `timeline` leaves to the end.
    """
    return next(
        (
            position
            for position, turn in enumerate(outline)
            if turn["started_at"] is not None and turn["started_at"] >= mark["timestamp"]
        ),
        len(outline),
    )


class Threads(NamedTuple):
    """A session page's two run-bearing parts, which together hold every run it recorded."""

    entries: list[Entry]
    unattached: Capped[Chip]
    # The page's compaction markers, already interleaved into `entries` — carried for the
    # count the page shows when the cap cut some.
    marks: Capped[Row]


def session_threads(
    turns: Sequence[Row],
    thread_turn_ids: Sequence[str],
    runs: Sequence[Row],
    compactions: Sequence[Row],
    chip_budget: int,
    mark_budget: int,
) -> Threads:
    """The main thread's timeline and the unattached list, checked to cover every run.

    Built together because the guarantee is about the pair: a run lands on a turn, under the
    run that spawned it, or in the unattached list. One this cannot place is a shape we have
    not seen, so it raises rather than vanishing from a page that still counts it.

    `turns` is the page; `thread_turn_ids` is every turn the session holds. The check is over
    the thread, because a run whose spawning turn is on another page is placed — just not
    here — and computing it from the page would raise for every one of them. The two budgets
    bound what renders, never what is checked: a run the cap cut is still a placed run.
    """
    marks = cut_to(compactions, mark_budget)
    entries = timeline(turns, runs, marks.shown, MAIN_SOURCE, chip_budget)
    loose = unattached(runs)
    placed = {
        chip.run["run_id"]
        for turn_id in thread_turn_ids
        for chip in _walk(chips(runs, MAIN_SOURCE, turn_id))
    }
    placed |= {chip.run["run_id"] for chip in _walk(loose)}
    missing = {run["run_id"] for run in runs} - placed
    if missing:
        raise ValueError(f"{len(missing)} run(s) hang off no turn and no run: {sorted(missing)}")
    return Threads(entries, capped(loose, chip_budget), marks)


def parent_of(run: Row) -> str | None:
    """The thread a run hangs under: the parent its transcript names, else the thread its
    spawning call was made from.

    The two-rule linkage `enrich/store.py:item_parents` applies, and in that order — a fork's
    spawning call is only in its own transcript, which the chip join deliberately excludes,
    so `parent_agent_id` is all a fork has. None when the store names neither.
    """
    return run["parent_agent_id"] or run["spawn_source"]


def ancestry(run_id: str, runs: Sequence[Row]) -> list[str]:
    """The threads above a run, outermost first — as far as the store names them.

    It stops where the naming stops rather than falling back to the session's main thread: a
    run whose parent nothing names is a run this store cannot place, and `main` would be a
    guess. So the trail is empty for such a run, and truncated for its descendants.
    """
    by_id = {run["run_id"]: run for run in runs}
    trail: list[str] = []
    current = run_id
    while (parent := parent_of(by_id[current])) is not None:
        if parent in trail:
            raise ValueError(f"{run_id} hangs under itself: {[*trail, parent]}")
        trail.append(parent)
        # `main`, or a run of some other session: either way the walk is over.
        if parent not in by_id:
            break
        current = parent
    return trail[::-1]


def children(run_id: str, runs: Sequence[Row]) -> tuple[Chip, ...]:
    """The runs under this one that no turn of its own timeline claims.

    The complement of its chips: together they hold every run whose parent is this one, each
    exactly once, so a run page cannot lose a child the way the chip join alone would lose a
    fork.
    """
    return tuple(
        Chip(run, chips(runs, run["run_id"], None) + _under_turns(runs, run["run_id"]))
        for run in runs
        if parent_of(run) == run_id
        and not (run["spawn_source"] == run_id and run["spawn_turn_id"] is not None)
    )


def unattached(runs: Sequence[Row]) -> tuple[Chip, ...]:
    """The runs the chip join could not place — its complement, whatever the cause.

    No `tool_use_id`, a spawning call naming a tool call the store lacks, or a fork's own
    un-replayed copy of that call: the page says the run is unattached and does not guess.

    A missing turn is not on its own enough to land here. A run whose spawning call names a
    run of this session but no turn of it already hangs under that run — `chips` asks for
    exactly that pair — so listing it here as well would show it twice.
    """
    spawners = {run["run_id"] for run in runs}
    return tuple(
        Chip(run, chips(runs, run["run_id"], None) + _under_turns(runs, run["run_id"]))
        for run in runs
        if run["spawn_turn_id"] is None and run["spawn_source"] not in spawners
    )


class NavKind(StrEnum):
    """What one node of a session's map is."""

    TURN = "turn"
    RUN = "run"


@dataclass(frozen=True)
class NavNode:
    """One node of the map beside a session page: where it goes and what it took."""

    kind: NavKind
    # The turn or the run the node stands for — what the page's own rows are keyed by.
    node_id: str
    # Down the page when the turn is on it, to the page that starts with it when it is not,
    # and to a run's own page for a run.
    href: str
    label: str
    cost_usd: float
    # Calls under this node our price table could not price. A cost carries this or it is not
    # the node's cost, the same rule the session list's total is printed under.
    unpriced_api_calls: int
    # The node's share of what the session spent, or None when it spent nothing: a share of
    # nothing is a gap rather than 0%.
    share: float | None
    # Whether the page this map sits beside rendered the turn. A run takes its turn's answer,
    # so a run under a turn on the page is not dimmed along with the rest.
    in_window: bool
    children: tuple["NavNode", ...]

    @property
    def meter(self) -> str:
        """The decile class this node's spend bar is drawn with."""
        return meter(self.share)


# Steps in the spend meter. `static/style.css` draws one bar width per step, `s1` through
# `s10`, and nothing for `s0` — so this is how many classes there are to name, not a rounding
# choice, and the meter clamps to it rather than naming a class the stylesheet has no rule for.
DECILES = 10


def meter(share: float | None) -> str:
    """The decile class a node's spend bar is drawn with, `s0` through `s10`.

    A class and not a width, because the policy blocks the inline style a width needs. Any
    nonzero share rounds *up* into `s1`, or a session one turn dominates renders every other
    node with no bar at all.
    """
    return "s0" if not share else f"s{min(math.ceil(share * DECILES), DECILES)}"


class Nav(NamedTuple):
    """A session's whole map, cut to the size one response is bounded at."""

    # One node per main-thread turn, each carrying the runs that turn spawned.
    nodes: tuple[NavNode, ...]
    # The runs no turn claims, which hang here rather than nowhere.
    unattached: tuple[NavNode, ...]
    # How many runs that tail holds, cut or not — what its heading counts.
    loose: int
    # Nodes the budget left out, across both lists — what the tail says it dropped.
    cut: int


def permalink(session_id: str, turn_index: int, turn_id: str) -> str:
    """The link to one turn on a page that starts with it.

    The cursor is one before the turn's own index, because `after` is the last index already
    shown — so a link a reader saved opens the same turn tomorrow, whatever the session grew
    in between. Minted here because two surfaces mint it: the timeline and the map.
    """
    return f"/session/{session_id}?after={turn_index - 1}#turn-{turn_id}"


def on_page(turns: Sequence[Row], after: int, size: int) -> set[str]:
    """Which turns a page at `after` rendered, by id — `store.window`'s keyset rule in Python.

    The map covers the whole session while the page beside it holds one window of it, so the
    map has to know that window. Derived from the rows it already has rather than fetched
    again: a second query would be a second answer to what the page has settled.
    """
    ahead = [row for row in turns if row[TURN_CURSOR] > after]
    return {row["turn_id"] for row in ahead[:size]}


def nav_tree(
    session_id: str,
    turns: Sequence[Row],
    runs: Sequence[Row],
    described: Mapping[str, str],
    cost_usd: float | None,
    label_chars: int,
    here: Container[str],
    budget: int,
) -> Nav:
    """A session's whole main thread as a map, with every run it holds hanging off it.

    The map is the session and not the page: `here` — which turns the page beside it rendered
    — is all it takes from the window, and it decides emphasis and where a node's link goes.
    `described` is what a pass said each turn did, keyed by turn id and empty over a store no
    pass has touched; `cost_usd` is the session's own, which every share is a share of.

    Every run appears exactly once: under the turn that spawned it, under the run that did, or
    in the unattached tail. One this cannot place raises, as `session_threads` does, rather
    than vanishing from a map that counts it.
    """
    whole = cost_usd or 0
    nodes = tuple(
        _turn_node(session_id, row, runs, described, whole, label_chars, row["turn_id"] in here)
        for row in turns
    )
    loose = tuple(
        _run_node(session_id, chip, whole, label_chars, False) for chip in unattached(runs)
    )
    placed = {node.node_id for node in _nodes(nodes) if node.kind is NavKind.RUN}
    placed |= {node.node_id for node in _nodes(loose)}
    missing = {run["run_id"] for run in runs} - placed
    if missing:
        raise ValueError(f"{len(missing)} run(s) are on no node of the map: {sorted(missing)}")
    total = sum(1 for _ in _nodes(nodes)) + sum(1 for _ in _nodes(loose))
    kept, left = _take_nodes(nodes, budget)
    tail, _ = _take_nodes(loose, left)
    return Nav(kept, tail, sum(1 for _ in _nodes(loose)), max(total - budget, 0))


def _turn_node(
    session_id: str,
    row: Row,
    runs: Sequence[Row],
    described: Mapping[str, str],
    whole: float,
    label_chars: int,
    here: bool,
) -> NavNode:
    turn_id = row["turn_id"]
    cost = row["cost_usd"]
    return NavNode(
        kind=NavKind.TURN,
        node_id=turn_id,
        href=f"#turn-{turn_id}" if here else permalink(session_id, row[TURN_CURSOR], turn_id),
        label=_label(row, described.get(turn_id))[:label_chars],
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=cost / whole if whole else None,
        in_window=here,
        children=tuple(
            _run_node(session_id, chip, whole, label_chars, here)
            for chip in chips(runs, MAIN_SOURCE, turn_id)
        ),
    )


def _label(row: Row, description: str | None) -> str:
    """What to call one turn: what a pass said it did, else the command it ran and what
    followed, else the prompt as typed.

    The prompt is last because a slash command's prompt is the `<command-…>` wrapper Claude
    Code put around it, which says nothing in the width of a sidebar.
    """
    if description:
        return description
    if row["command_name"] is not None:
        return f"{row['command_name']} {row['command_args'] or ''}".strip()
    # The store declares a turn's prompt NOT NULL (`export/duckdb.py`), so this is the one
    # branch that always has something to say, even when what it says is the empty string.
    return row["prompt"]


def _run_node(
    session_id: str, chip: Chip, whole: float, label_chars: int, in_window: bool
) -> NavNode:
    run = chip.run
    cost = run["cost_usd"]
    return NavNode(
        kind=NavKind.RUN,
        node_id=run["run_id"],
        href=f"/session/{session_id}/run/{run['run_id']}",
        # A run whose transcript recorded no description is named by its agent alone.
        label=f"{run['agent_type']} {run['description'] or ''}".strip()[:label_chars],
        cost_usd=cost,
        unpriced_api_calls=run["unpriced_api_calls"],
        share=cost / whole if whole else None,
        in_window=in_window,
        children=tuple(
            _run_node(session_id, child, whole, label_chars, in_window) for child in chip.children
        ),
    )


def _take_nodes(nodes: Sequence[NavNode], budget: int) -> tuple[tuple[NavNode, ...], int]:
    """The first `budget` nodes of a pre-order walk, and what is left of the budget.

    `_take`'s rule over the map's own node: a rendered run's parent is always rendered, so the
    cut is a shorter map rather than a map with holes in it.
    """
    kept: list[NavNode] = []
    for node in nodes:
        if budget <= 0:
            break
        children, budget = _take_nodes(node.children, budget - 1)
        kept.append(replace(node, children=children))
    return tuple(kept), budget


def _nodes(nodes: Sequence[NavNode]) -> Iterator[NavNode]:
    for node in nodes:
        yield node
        yield from _nodes(node.children)


def _walk(items: Sequence[Chip]) -> Iterator[Chip]:
    for chip in items:
        yield chip
        yield from _walk(chip.children)
