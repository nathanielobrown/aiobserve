"""Reading a rendered NavTree, and building the level the store says one should hold.

`data-nav-tree` carries a row's node key — `kind:id`, the key its URL is built from — so a NavTree
reads back as a list in document order, and `data-more` marks a row standing for children the
cap left out. The levels here are built out of the store the way the design orders one, in the
test's own SQL: turns with compactions dropped in by time, then the thread's unattributed
bucket, then — under the session alone — the runs nothing placed. A run hangs under the tool
call that spawned it, in every preset. Reading the order back out of the store rather than
pinning it means a re-recorded fixture moves the expectation instead of reddening the tier.

`cell` is the design's kind x preset table written out — every cell in full, including the ones
a preset passes through, so a table edit has to be an edit here before it can pass. A plain
module rather than the conftest, so the readers of a tree and the levels they are checked
against sit in one place.
"""

import datetime as dt
from collections.abc import Callable, Sequence
from html import unescape
from typing import NamedTuple

import duckdb

from hyphae.model import MAIN_SOURCE
from hyphae.view.format import money
from hyphae.view.nodes import (
    BODY_URL,
    KIN_URL,
    Kind,
    Preset,
    meter,
)
from tests.conftest import MAIN, SPINE
from tests.view.conftest import (
    SPAWNS,
    badges,
    inside,
    one,
    values,
)


def url(turn_id: str) -> str:
    """The node URL of one turn of `SPINE`'s main thread."""
    return f"/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"


def spawned(store: duckdb.DuckDBPyConnection, session_id: str) -> list[tuple[str | None, ...]]:
    """One session's runs beside the spawning edge that places each, in `view_runs`' order."""
    return store.execute(SPAWNS, [session_id]).fetchall()


def thread_level(store: duckdb.DuckDBPyConnection, session_id: str, source: str) -> list[str]:
    """One thread's children, as node keys, in the order the design puts them in.

    The session's own thread is `main`, and it is the one that also holds the unattached
    bucket: what makes a run unattached is that nothing says which thread spawned it, so the
    bucket spans every thread rather than sitting on one.
    """
    turns = store.execute(
        "SELECT id, started_at FROM live_turns WHERE session_id = ? AND source = ?"
        ' ORDER BY "index"',
        [session_id, source],
    ).fetchall()
    # A compaction lands before the first turn that started after it, which is when it
    # happened — but only the ones that happened between two turns: one that happened during
    # a turn is a child of that turn (`turn_marks`), not a sibling of it.
    placed = dropped_in(
        [(f"turn:{turn_id}", started) for turn_id, started in turns],
        [(mark, at) for mark, at, turn in marks(store, session_id, source) if turn is None],
    )
    # The thread's calls that answer no turn *of this thread*, as one bucket. A fork replays
    # calls whose `turn_id` names a turn of the thread it forked from, so the resolution is a
    # join and not a NULL check.
    (loose_calls,) = one(
        store,
        "SELECT count(*) FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE c.session_id = ? AND c.source = ? AND t.id IS NULL",
        [session_id, source],
    )
    if loose_calls:
        placed.append(f"unattributed:{source}")
    if source == MAIN_SOURCE and any(row[1] is None for row in spawned(store, session_id)):
        placed.append(f"unattached:{session_id}")
    return placed


def marks(
    store: duckdb.DuckDBPyConnection, session_id: str, source: str
) -> list[tuple[str, dt.datetime, str | None]]:
    """One thread's compactions in time order, each beside the turn it happened during.

    The placement rule in the test's own SQL: a turn holds a compaction its span covers, and
    where two spans cover one — the corpus records turns that overlap — the turn that started
    last holds it, because that is the one still running. Half-open at both ends: a compaction
    at the instant a turn starts is that turn's, one at the instant it ends is the next thing's.
    """
    return [
        (str(mark), at, turn)
        for mark, at, turn in store.execute(
            "SELECT k.id, k.timestamp,"
            "  (SELECT t.id FROM live_turns t"
            "     WHERE t.session_id = k.session_id AND t.source = k.source"
            "       AND k.timestamp >= t.started_at AND k.timestamp < t.ended_at"
            '     ORDER BY t.started_at DESC, t."index" DESC LIMIT 1)'
            " FROM live_compactions k WHERE k.session_id = ? AND k.source = ?"
            " ORDER BY k.timestamp",
            [session_id, source],
        ).fetchall()
    ]


def dropped_in(
    rows: Sequence[tuple[str, dt.datetime | None]], pending: Sequence[tuple[str, dt.datetime]]
) -> list[str]:
    """A level's own rows with `pending`'s compactions dropped in where they happened."""
    placed: list[str] = []
    waiting = list(pending)
    for key, started in rows:
        while waiting and started is not None and waiting[0][1] < started:
            placed.append(f"compaction:{waiting.pop(0)[0]}")
        placed.append(key)
    return placed + [f"compaction:{mark}" for mark, _ in waiting]


def turn_marks(
    store: duckdb.DuckDBPyConnection, session_id: str, source: str, turn_id: str | None
) -> list[tuple[str, dt.datetime]]:
    """The compactions that happened during one turn, in time order.

    None at a bucket: a bucket holds the calls that answer no turn, and a compaction that
    answers none stays beside the turns of its thread.
    """
    if turn_id is None:
        return []
    return [(mark, at) for mark, at, turn in marks(store, session_id, source) if turn == turn_id]


def turn_level(
    store: duckdb.DuckDBPyConnection, session_id: str, source: str, turn_id: str | None
) -> list[str]:
    """The api calls under one turn and the compactions among them.

    `turn_id` None is the unattributed bucket's own level, which reads the same way: the calls
    that answer no turn. No run is here — a run hangs off its own spawning tool call, which is
    two levels further down.
    """
    calls = store.execute(
        "SELECT c.id, c.started_at FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE c.session_id = ? AND c.source = ? AND t.id IS NOT DISTINCT FROM ?"
        ' ORDER BY c."index"',
        [session_id, source, turn_id],
    ).fetchall()
    return dropped_in(
        [(f"call:{call_id}", started) for call_id, started in calls],
        turn_marks(store, session_id, source, turn_id),
    )


def open_turn(store: duckdb.DuckDBPyConnection) -> str:
    """The turn these leaves select: one with more than one api call, and not its level's first.

    Both halves matter. Two calls under it give the level below the selection something for a
    cap to cut, and a turn that is not its level's first row is one a cap of a single child
    would drop — which is what the rescue rule exists to stop.
    """
    (turn_id,) = one(
        store,
        'SELECT t.id FROM live_turns t WHERE t.session_id = ? AND t.source = ? AND t."index" > 0'
        " AND (SELECT count(*) FROM live_api_calls c WHERE c.session_id = t.session_id"
        "   AND c.source = t.source AND c.turn_id = t.id) > 1"
        ' ORDER BY t."index" LIMIT 1',
        [SPINE, MAIN],
    )
    return str(turn_id)


# The runs of one session beside every edge a preset places them by: the spawning edge the
# full tree reads (`SPAWNS`), plus the tool call that edge resolved through and the run's own
# declared parent, which is the one edge `agents` reads that an unresolvable call cannot lose.
EDGES = SPAWNS.replace("c.id FROM", "c.id, tc.id, a.parent_agent_id FROM")


class Edge(NamedTuple):
    """One run and every way the design's table has of placing it."""

    run_id: str
    # The thread and the turn its spawning call answered, the turn NULL where the call
    # resolved to no turn of its thread and both NULL where nothing resolved at all.
    spawn_source: str | None
    spawn_turn_id: str | None
    spawn_call_id: str | None
    spawn_tool_id: str | None
    parent_agent_id: str | None


def edges(store: duckdb.DuckDBPyConnection, session_id: str) -> list[Edge]:
    """One session's runs with the edges that place them, in `view_runs`' order — by start."""
    return [Edge(*row) for row in store.execute(EDGES, [session_id]).fetchall()]


def call_tools(
    store: duckdb.DuckDBPyConnection, session_id: str, source: str, api_call_id: str
) -> list[str]:
    """The tool calls one api call made, in the order it made them."""
    return [
        f"tool:{row[0]}"
        for row in store.execute(
            "SELECT id FROM live_tool_calls WHERE session_id = ? AND source = ?"
            ' AND api_call_id = ? ORDER BY "index"',
            [session_id, source, api_call_id],
        ).fetchall()
    ]


def tool_level(
    store: duckdb.DuckDBPyConnection, session_id: str, source: str, turn_id: str | None
) -> list[str]:
    """`noapi`'s level under a turn: its calls' tool calls and its compactions.

    The api calls are hidden, so their tool calls rise to the turn in call-then-tool order. A
    compaction hangs off the turn whichever preset the reader is in, so it drops in by time
    here too. `turn_id` None is the unattributed bucket's level, which reads the same way. A
    run is not here either: it hangs under the tool call that spawned it, one level deeper.
    """
    tools = store.execute(
        "SELECT tc.id, tc.started_at FROM live_tool_calls tc"
        " JOIN live_api_calls c ON c.session_id = tc.session_id AND c.source = tc.source"
        "  AND c.id = tc.api_call_id"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE tc.session_id = ? AND tc.source = ? AND t.id IS NOT DISTINCT FROM ?"
        ' ORDER BY c."index", tc."index"',
        [session_id, source, turn_id],
    ).fetchall()
    return dropped_in(
        [(f"tool:{tool_id}", started) for tool_id, started in tools],
        turn_marks(store, session_id, source, turn_id),
    )


def runs_where(
    store: duckdb.DuckDBPyConnection, session_id: str, holds: Callable[[Edge], bool]
) -> list[str]:
    """The session's runs one edge places under one node, in the order they started."""
    return [f"run:{edge.run_id}" for edge in edges(store, session_id) if holds(edge)]


def hanging(store: duckdb.DuckDBPyConnection, session_id: str, source: str, key: str) -> list[str]:
    """The runs one shut row stands, and the runs under those, as the NavTree draws them.

    A run is always visible: where the rows between it and its spawning call are shut, it
    renders under the deepest one showing. So the expectation for any row a page draws closed
    is the row and then this, by the spawning edge — the same edge the cells place a run by.
    """
    kind, _, node_id = key.partition(":")
    match kind:
        case "turn":
            spawned = runs_where(
                store,
                session_id,
                lambda edge: edge.spawn_source == source and edge.spawn_turn_id == node_id,
            )
        case "unattributed":
            spawned = runs_where(
                store,
                session_id,
                lambda edge: edge.spawn_source == node_id and edge.spawn_turn_id is None,
            )
        case "call":
            spawned = runs_where(
                store,
                session_id,
                lambda edge: edge.spawn_source == source and edge.spawn_call_id == node_id,
            )
        case "tool":
            spawned = runs_where(
                store,
                session_id,
                lambda edge: edge.spawn_source == source and edge.spawn_tool_id == node_id,
            )
        # A run stands whatever its own thread spawned, which is what its turns would show.
        case "run":
            spawned = runs_where(store, session_id, lambda edge: edge.spawn_source == node_id)
        case "unattached":
            spawned = runs_where(store, session_id, lambda edge: edge.spawn_source is None)
        case _:
            return []
    return [
        key
        for run in spawned
        for key in [run, *hanging(store, session_id, run.removeprefix("run:"), run)]
    ]


def shut(
    store: duckdb.DuckDBPyConnection, session_id: str, source: str, level: Sequence[str]
) -> list[str]:
    """One level as a page draws it with every row of it closed: each row, then what it hides."""
    return [key for row in level for key in [row, *hanging(store, session_id, source, row)]]


def cell(
    store: duckdb.DuckDBPyConnection,
    preset: Preset,
    kind: Kind,
    session_id: str,
    source: str,
    node_id: str,
) -> list[str]:
    """One cell of the design's kind × preset table, read out of the store.

    Every cell is spelled out rather than folded into "same as full", so a table edit is an
    edit here: a preset that started filtering a cell it used to pass through would have to be
    written down before it could pass.
    """
    match kind, preset:
        # A thread's own children, or — under `agents` — the runs it spawned instead, with the
        # session keeping the unattached bucket that no thread holds.
        case Kind.SESSION, Preset.AGENTS:
            placed = runs_where(store, session_id, lambda edge: edge.spawn_source == MAIN)
            if runs_where(store, session_id, lambda edge: edge.spawn_source is None):
                placed.append(f"unattached:{session_id}")
            return placed
        case Kind.SESSION, _:
            return thread_level(store, session_id, MAIN)
        case Kind.RUN, Preset.AGENTS:
            return runs_where(store, session_id, lambda edge: edge.parent_agent_id == node_id)
        case Kind.RUN, _:
            return thread_level(store, session_id, node_id)
        # A turn and its thread's bucket hold the same three levels, one at a bound turn and
        # one at none: the api calls, those calls' tool calls, or the runs they spawned.
        case (Kind.TURN | Kind.UNATTRIBUTED) as under, _:
            turn_id = None if under is Kind.UNATTRIBUTED else node_id
            if preset is Preset.AGENTS:
                return runs_where(
                    store,
                    session_id,
                    lambda edge: edge.spawn_source == source and edge.spawn_turn_id == turn_id,
                )
            if preset is Preset.NO_API:
                return tool_level(store, session_id, source, turn_id)
            return turn_level(store, session_id, source, turn_id)
        case Kind.CALL, Preset.AGENTS:
            return runs_where(store, session_id, lambda edge: edge.spawn_call_id == node_id)
        case Kind.CALL, _:
            return call_tools(store, session_id, source, node_id)
        # A tool call holds the run it spawned, in every preset: that is where a run lives now,
        # and the preset only decides how many rows stand between the two.
        case Kind.TOOL, _:
            return runs_where(store, session_id, lambda edge: edge.spawn_tool_id == node_id)
        case Kind.COMPACTION, _:
            return []
        case Kind.UNATTACHED, _:
            return runs_where(store, session_id, lambda edge: edge.spawn_source is None)
    raise ValueError(f"the design's table has no {kind} x {preset} cell")


def candidates(store: duckdb.DuckDBPyConnection, kind: Kind) -> list[tuple[str, str, str]]:
    """Every node of one kind the corpus holds: its session, its thread, and its id.

    A bucket is not a row of the store, so each is enumerated by what makes one exist — a call
    that answers no turn of its own thread, and a run whose spawning call resolved to nothing.
    """
    sessions = [str(row[0]) for row in store.execute("SELECT id FROM sessions").fetchall()]
    match kind:
        case Kind.SESSION:
            return [(session_id, MAIN, session_id) for session_id in sessions]
        case Kind.UNATTACHED:
            return [
                (session_id, MAIN, session_id)
                for session_id in sessions
                if runs_where(store, session_id, lambda edge: edge.spawn_source is None)
            ]
        case Kind.RUN:
            sql = "SELECT session_id, id, id FROM live_agent_runs"
        case Kind.TURN:
            sql = "SELECT session_id, source, id FROM live_turns"
        case Kind.CALL:
            sql = "SELECT session_id, source, id FROM live_api_calls"
        case Kind.TOOL:
            sql = "SELECT session_id, source, id FROM live_tool_calls"
        case Kind.COMPACTION:
            sql = "SELECT session_id, source, id FROM live_compactions"
        case Kind.UNATTRIBUTED:
            sql = (
                "SELECT DISTINCT c.session_id, c.source, c.source FROM live_api_calls c"
                " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
                "  AND t.id = c.turn_id"
                " WHERE t.id IS NULL"
            )
    return [(str(a), str(b), str(c)) for a, b, c in store.execute(sql).fetchall()]


def node_url(kind: Kind, session_id: str, source: str, node_id: str) -> str:
    """Where one node's page is. A kind's value is its URL segment, so the kind is the shape."""
    match kind:
        case Kind.SESSION:
            return f"/session/{session_id}"
        case Kind.RUN:
            return f"/session/{session_id}/run/{node_id}"
        case Kind.UNATTACHED:
            return f"/session/{session_id}/unattached"
        case Kind.UNATTRIBUTED:
            return f"/session/{session_id}/thread/{source}/unattributed"
        case _:
            return f"/session/{session_id}/thread/{source}/{kind}/{node_id}"


def mounts(html: str) -> list[str]:
    """Every expansion a page's log rows mount, unescaped — the markup carries `&` as `&amp;`."""
    return [unescape(href) for href in values(html, "hx-get") if href.startswith(BODY_URL)]


def spilled(html: str) -> list[str]:
    """Every level a page's tail rows fetch the rest of, unescaped."""
    return [unescape(href) for href in values(html, "hx-get") if href.startswith(KIN_URL)]


def node_link(href: str) -> bool:
    """Whether a link goes to a node page — the records browser and an offload file do not."""
    path = href.partition("?")[0].strip("/").split("/")
    if path[0] != "session":
        return False
    # Past the session, and past the thread where the node was recorded on one, a node's path
    # says its kind. Everything else the session holds is named by something that is not one.
    rest = path[4:] if path[2:3] == ["thread"] else path[2:]
    return not rest or rest[0] in set(Kind)


# The calls of one thread that answer no turn of it — the rows the unattributed bucket stands
# for — summed the way a page of them would be read.
STANDING = (
    "SELECT coalesce(round(sum(c.cost_usd), 4), 0), count(*) FILTER (c.cost_usd IS NULL)"
    " FROM live_api_calls c"
    " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
    "  AND t.id = c.turn_id"
    " WHERE c.session_id = ? AND c.source = ? AND t.id IS NULL"
)


# One run's own thread, which is what an unattached run brings to the bucket that gathers it.
THREAD = (
    "SELECT coalesce(round(sum(cost_usd), 4), 0), count(*) FILTER (cost_usd IS NULL)"
    " FROM live_api_calls WHERE session_id = ? AND source = ?"
)


def weighed(
    page: str,
    key: str,
    store: duckdb.DuckDBPyConnection,
    session_id: str,
    cost: float,
    unpriced: int,
) -> None:
    """One row's own half read against what the store holds on its thread, and what went unpriced.

    The subtree half is the rollup's, and the leaves in `test_nav_tree__badges.py` weigh it: what
    this holds is the number a row has always printed first.
    """
    (whole,) = one(store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [session_id])
    own = badges(page, key)["cost_usd"]
    assert own.shown == money(cost), key
    # The wash is that spend against the session, not against the row's parent or its own
    # children — and a session with nothing to take a share of draws every row at nothing. It
    # rides on the value it washes rather than on the row, because the row draws two of them.
    share = cost / whole if whole else None
    assert meter(share) in own.step.split(), key
    # A `title` inside the row is the mark on a total our price table could not complete —
    # there where some call under the row went unpriced, and nowhere else.
    marks = inside(page, "data-nav-tree", key, "title")
    assert bool(marks) == bool(unpriced), key
    assert not unpriced or str(unpriced) in marks[0], key
