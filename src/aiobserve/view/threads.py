"""A session's threads as the pages lay them out: turns in order, with the runs they spawned.

A run's transcript is a thread like the session's own, so a session page and a run page are
the same shape over a different `source`. Two rules do the work and both are load-bearing: a
run hangs off the turn its spawning call was made in — never off a turn of its own timeline,
which is where a fork's un-replayed copy of that call would put it — and a run the store
names no parent for hangs off nothing rather than off a guess.

Pure functions over rows the queries returned. Nothing here reads a store or a request.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

from aiobserve.analyze import queries
from aiobserve.model import MAIN_SOURCE
from aiobserve.view.store import Row

# The session timeline's two page sizes, which multiply: `turns` rows, each carrying up to
# `chips` run rows. `session_digest` has no LIMIT of its own — a report quotes the whole digest
# — so this is the composed one, and `tests/view/test_bounds.py` pins it with the arithmetic.
PAGE_TURNS = 20
PAGE_CHIPS = 8
# The most chips one page renders however the two sizes are split. A reader can spend the whole
# budget on a single turn (`?turns=1&chips=160`) but not on twenty, which is what stops the
# largest recorded forest — 94 runs under one turn — from being a page nobody can open.
CHIP_BUDGET = 160
MAX_PAGE_CHIPS = CHIP_BUDGET
# What one page renders of a thread's compactions. Not a size a URL carries: the most any
# session's main thread holds is 18, so this is the arithmetic's backstop rather than a knob.
PAGE_MARKS = 25
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


@dataclass(frozen=True)
class Entry:
    """One row of a thread's timeline: a turn with its runs, or a compaction marker."""

    kind: EntryKind
    row: Row
    chips: tuple[Chip, ...] = ()

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


def timeline(
    turns: Sequence[Row], runs: Sequence[Row], compactions: Sequence[Row], source: str
) -> list[Entry]:
    """One thread's turns in order, with the runs it spawned chipped on and its compactions
    interleaved.

    `source` is `main` for a session page and a run id for a run page: the same shape either
    way, because a run's transcript is a thread like the session's own.
    """
    entries: list[Entry] = []
    pending = list(compactions)
    for turn in turns:
        started = turn["started_at"]
        # A compaction that ran before this turn started belongs after the previous one.
        while pending and started is not None and pending[0]["timestamp"] <= started:
            entries.append(Entry(EntryKind.COMPACTION, pending.pop(0)))
        entries.append(Entry(EntryKind.TURN, turn, chips(runs, source, turn["turn_id"])))
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
    unattached: tuple[Chip, ...]


def session_threads(
    turns: Sequence[Row],
    thread_turn_ids: Sequence[str],
    runs: Sequence[Row],
    compactions: Sequence[Row],
) -> Threads:
    """The main thread's timeline and the unattached list, checked to cover every run.

    Built together because the guarantee is about the pair: a run lands on a turn, under the
    run that spawned it, or in the unattached list. One this cannot place is a shape we have
    not seen, so it raises rather than vanishing from a page that still counts it.

    `turns` is the page; `thread_turn_ids` is every turn the session holds. The check is over
    the thread, because a run whose spawning turn is on another page is placed — just not
    here — and computing it from the page would raise for every one of them.
    """
    entries = timeline(turns, runs, compactions, MAIN_SOURCE)
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
    return Threads(entries, loose)


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


def _walk(items: Sequence[Chip]) -> Iterator[Chip]:
    for chip in items:
        yield chip
        yield from _walk(chip.children)
