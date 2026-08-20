"""The tree beside a node page: one open path through the session, and nothing else open.

The tree is served with the pane in one response, so these leaves fetch the same node URLs
`tests/view/test_node.py` does and read the rows instead. `data-tree` carries a row's node
key — `kind:id`, the key its URL is built from — so the whole tree reads as a list in
document order, and `data-more` marks a row standing for children the cap left out.

The expectations build a level out of the store the way the design orders one, in the test's
own SQL: turns with compactions dropped in by time, then the thread's unattributed bucket,
then — under the session alone — the runs nothing placed. A level of api calls hoists each run
after the call that spawned it. Reading the order back out of the store rather than pinning it
means a re-recorded fixture moves the expectation instead of reddening the tier.

`?nav=` picks which children a level shows, and `cell` below is the design's kind × preset
table written out — every cell in full, including the ones a preset passes through, so a
table edit has to be an edit here before it can pass.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from html import unescape
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.model import MAIN_SOURCE
from aiobserve.view import bounds, tree
from aiobserve.view.app import build_app
from aiobserve.view.enrichment import Descriptions
from aiobserve.view.nodes import Kind, Preset, Ref
from tests.conftest import MAIN, SPINE
from tests.view.conftest import SPAWNS, Planter, fields, inside, kin, one, rows, values


def url(turn_id: str) -> str:
    """The node URL of one turn of `SPINE`'s main thread."""
    return f"/session/{SPINE}/turn/{MAIN}/{turn_id}"


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
    marks = store.execute(
        "SELECT id, timestamp FROM live_compactions WHERE session_id = ? AND source = ?"
        " ORDER BY timestamp",
        [session_id, source],
    ).fetchall()
    placed: list[str] = []
    pending = list(marks)
    for turn_id, started in turns:
        # A compaction lands before the first turn that started after it, which is when it
        # happened; a turn the store has no start for cannot move one.
        while pending and started is not None and pending[0][1] < started:
            placed.append(f"compaction:{pending.pop(0)[0]}")
        placed.append(f"turn:{turn_id}")
    placed += [f"compaction:{mark}" for mark, _ in pending]
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


def turn_level(
    store: duckdb.DuckDBPyConnection, session_id: str, source: str, turn_id: str | None
) -> list[str]:
    """The api calls under one turn, each run hoisted after the call that spawned it.

    `turn_id` None is the unattributed bucket's own level, which reads the same way: the calls
    that answer no turn, and the runs those calls spawned.
    """
    calls = [
        row[0]
        for row in store.execute(
            "SELECT c.id FROM live_api_calls c"
            " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
            "  AND t.id = c.turn_id"
            " WHERE c.session_id = ? AND c.source = ? AND t.id IS NOT DISTINCT FROM ?"
            ' ORDER BY c."index"',
            [session_id, source, turn_id],
        ).fetchall()
    ]
    hoisted: dict[str, list[str]] = {}
    for run_id, spawn_source, spawn_turn, spawn_call, _ in spawned(store, session_id):
        if spawn_source == source and spawn_turn == turn_id:
            hoisted.setdefault(str(spawn_call), []).append(f"run:{run_id}")
    placed: list[str] = []
    for call_id in calls:
        placed.append(f"call:{call_id}")
        placed += hoisted.pop(call_id, [])
    # A run whose spawning call this level does not hold still belongs to the turn the edge
    # resolved to, so it trails the level rather than being dropped.
    for leftover in hoisted.values():
        placed += leftover
    return placed


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


def test_every_sessions_own_page_opens_the_level_its_thread_holds(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A session page shows the session and its main thread's children, in the design's order.

    Swept over every session the corpus holds rather than over one, because the order has four
    rules and no single recorded session exercises them all: turns interleaved with
    compactions, then the thread's unattributed bucket, then the session's unattached runs.
    Comparing the whole list in order is what catches a rule applied in the wrong place.
    """
    sessions = [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]
    seen: set[str] = set()
    for session_id in sessions:
        html = client.get(f"/session/{session_id}").text
        expected = [f"session:{session_id}"] + thread_level(store, session_id, MAIN)
        assert values(html, "data-tree") == expected, session_id
        seen |= {key.split(":")[0] for key in expected}
    # And the sweep really did reach every rule above, so a corpus that lost its compactions
    # or its buckets would redden here rather than passing on the turns alone.
    assert {"compaction", "unattributed", "unattached"} <= seen


def test_the_tree_opens_the_selections_path_and_leaves_the_rest_shut(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """One open path: the chain down to the selection, expanded, and no other subtree.

    The chain here is the session and the turn under it, so the rows are the session, its
    thread's whole level, and — under the selected turn alone — the calls it made with the run
    it spawned hoisted among them. A tree that expanded a sibling would show that sibling's
    calls too, which is the difference this reads: the rows are compared as a whole list, in
    order, not searched for.
    """
    selection = open_turn(store)
    html = client.get(url(selection)).text
    expected: list[str] = [f"session:{SPINE}"]
    for key in thread_level(store, SPINE, MAIN):
        expected.append(key)
        # The selection is the one node whose children render — every other row is a row.
        if key == f"turn:{selection}":
            expected += turn_level(store, SPINE, MAIN, selection)
    assert values(html, "data-tree") == expected
    # And the tree says which row the pane is about, once.
    assert values(html, "data-selected") == [f"turn:{selection}"]


def test_every_tree_row_swaps_the_pane_from_its_own_node_url(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Each row is an `hx-get` of the URL it links to, swapping the pane and the tree.

    The server-side half of a tree click: the same URL a reader can paste, fetched by htmx,
    with the pane taken out of the response and the rows swapped out of band. Both targets
    have to be unique in the document or the swap lands somewhere else.
    """
    html = client.get(url(open_turn(store))).text
    rows = values(html, "data-tree")
    assert len(rows) > 1
    for key in rows:
        wiring = {
            name: inside(html, "data-tree", key, name)
            for name in ("href", "hx-get", "hx-select", "hx-select-oob", "hx-push-url")
        }
        # A row fetches what it links to: one URL, however the reader gets there.
        assert wiring["hx-get"] == wiring["href"], key
        assert wiring["hx-select"] == ["#pane"], key
        assert wiring["hx-select-oob"] == ["#tree-rows"], key
        assert wiring["hx-push-url"] == ["true"], key
    # The two ids the swap aims at, each written exactly once.
    assert html.count('id="pane"') == 1
    assert html.count('id="tree-rows"') == 1


def test_a_run_hoists_after_the_call_that_spawned_it_and_says_which(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run renders inside the turn it belongs to, right after its spawning call, with a tie.

    A run is a child of the turn and not of the api call — it has a thread of its own, and the
    call is only where it was asked for — so the row that says where it came from is the tie
    rather than the nesting. Read here at the level, where the neighbour on either side is the
    whole point.
    """
    run_id, source, turn_id, call_id, index = one(store, SPAWNS + " LIMIT 1", [SPINE])
    assert turn_id is not None, "the recorded run this reads is placed under a turn"
    html = client.get(url(str(turn_id))).text
    level = turn_level(store, SPINE, str(source), str(turn_id))
    at = level.index(f"run:{run_id}")
    # The run sits immediately after the call that spawned it, and the level renders as read.
    assert level[at - 1] == f"call:{call_id}"
    assert [key for key in values(html, "data-tree") if key in level] == level
    # And the row names the call by its place in the thread, which is what the tie is for.
    assert str(index) in fields(html, "data-tree", f"run:{run_id}")["tie"]


def test_a_bucket_home_is_decided_by_the_spawning_edge(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """The two buckets are disjoint by one edge: which of the two is a run's home, and why.

    A spawning call that resolves but sits under no turn of its thread puts the run in that
    thread's *unattributed* bucket, hoisted after the call as usual. Only a run whose spawning
    call resolves to nothing at all is *unattached*. The corpus records the second and not the
    first, so the first is planted: one recorded run's spawning call loses its turn, and the
    run has to move one bucket and not the other.
    """
    run_id, source, turn_id, call_id, _ = one(store, SPAWNS + " LIMIT 1", [SPINE])
    assert turn_id is not None, "the run this moves starts out under a turn"
    path = plant(
        (
            "UPDATE api_calls SET turn_id = NULL WHERE session_id = ? AND source = ? AND id = ?",
            [SPINE, str(source), str(call_id)],
        ),
    )
    with TestClient(build_app(path)) as moved:
        # The run is gone from the turn it used to hang under...
        assert f"run:{run_id}" not in values(moved.get(url(str(turn_id))).text, "data-tree")
        # ...and is in the thread's unattributed bucket, still after its spawning call.
        bucket_url = f"/session/{SPINE}/unattributed/{source}"
        bucket = moved.get(bucket_url)
        assert bucket.status_code == 200
        keys = values(bucket.text, "data-tree")
        assert keys[keys.index(f"call:{call_id}") + 1] == f"run:{run_id}"
        # The unattached bucket, which is the other home, does not also hold it.
        loose = moved.get(f"/session/{SPINE}/unattached")
        assert f"run:{run_id}" not in values(loose.text, "data-tree")
        # The run's own page agrees with the bucket that holds it. Read here because the tree
        # above is drawn from the bucket down while a run page is drawn from the run up: the
        # two answers come from different code, and a page that disagreed would be a crash —
        # the trail would look for the run under a bucket the session's tree does not hold.
        own = moved.get(f"/session/{SPINE}/run/{run_id}")
        assert own.status_code == 200
        assert values(own.text, "data-crumb") == [
            f"session:{SPINE}",
            f"unattributed:{source}",
            f"run:{run_id}",
        ]
        # And the bucket's three cells all place it, which is the shape no recorded session
        # has: a run under a thread's bucket, read under each preset in turn.
        planted = duckdb.connect(str(path), read_only=True)
        for preset in Preset:
            html = moved.get(bucket_url, params={"nav": preset}).text
            expected = cell(planted, preset, Kind.UNATTRIBUTED, SPINE, str(source), str(source))
            assert f"run:{run_id}" in expected, preset
            assert kin(html) == expected, preset
        planted.close()


def test_the_kin_cap_cuts_the_children_but_never_the_open_path(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Children are capped per level, with a row saying how many the cap left out.

    Driven below the fixture corpus's fan-out rather than planted up to the production cap:
    no recorded session comes near 25 children, and the knob exists for exactly this. The cap
    bites twice here — once on the level beside the selection, once on the calls under it —
    and the selection survives it either way. A cut that hid the open path would leave the
    pane describing a node the tree does not show.
    """
    selection = open_turn(store)
    html = client.get(url(selection), params={"kin": 1}).text
    level, under = thread_level(store, SPINE, MAIN), turn_level(store, SPINE, MAIN, selection)
    assert len(level) > 2 and len(under) > 1, "the cap has to have something to cut"
    # The cap admits one child, and the path through the selection takes that slot rather than
    # being kept past it: the rescue rides inside the cap. A level of `kin + 1` children is a
    # page the byte arithmetic never priced, and the sibling the reader loses is one the tail
    # row still counts and the parent's own page still lists.
    shown = [key for key in values(html, "data-tree") if key in level]
    assert shown == [f"turn:{selection}"]
    # ...with a tail saying how many rows are off the tree, linking to the page that pages
    # them rather than capping them: the session's own, still under the size the reader typed.
    assert fields(html, "data-more", f"session:{SPINE}")["cut"] == str(len(level) - len(shown))
    assert inside(html, "data-more", f"session:{SPINE}", "href") == [f"/session/{SPINE}?kin=1"]
    # And the level under the selection takes the same cap, where no rescue is owed: one child
    # of the several the turn has, and a tail for the rest.
    assert [key for key in values(html, "data-tree") if key in under] == [under[0]]
    assert fields(html, "data-more", f"turn:{selection}")["cut"] == str(len(under) - 1)
    # And no level on the page exceeds the cap, anywhere. `worst_node_bytes()` prices
    # `DEPTH * (KIN + 1)` rows on exactly this, so it is pinned here rather than left to a
    # reading of `_kin`.
    assert max(Counter(depth for depth, _ in rows(html)).values()) <= 1


def test_a_chain_is_resolved_to_the_depth_the_page_prices_and_no_deeper(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """`bounds.DEPTH` is the last chain `ancestry` resolves; one level past it raises.

    The response's bound is arithmetic over the depth and the per-level cap, so a deeper chain
    is not a bigger page — it is a page whose size was never computed. Read at the boundary
    from both sides, because a bound that refused at `DEPTH` would silently cost the deepest
    page the arithmetic paid for. The corpus reaches five levels, so the shape is built rather
    than recorded: a ladder of runs, each spawned from a turn of the one above it.
    """
    assert bounds.DEPTH % 2 == 0, "a turn lands on an even depth; an odd one would select a run"
    # Every rung is two levels — a run and the turn that spawned it — so the ladder is sized to
    # straddle the bound with its last two rungs: a turn on the second-deepest thread stands
    # exactly `DEPTH` under the session, and the run that turn spawned stands one deeper.
    ladder = [
        {
            "run_id": f"a{step}",
            "spawn_source": f"a{step - 1}" if step else MAIN_SOURCE,
            "spawn_turn_id": f"t{step}",
        }
        for step in range(bounds.DEPTH // 2)
    ]
    corpus = tree.Corpus(SPINE, whole=0.0, runs=ladder, described=Descriptions(), source=MAIN)
    # A short ladder resolves, which is what says a rung is worth two levels and not some other
    # number: two runs and the turn selected on the second is four levels under the session.
    shallow = tree.Corpus(SPINE, 0.0, ladder[:2], Descriptions(), MAIN)
    assert len(tree.ancestry(shallow, [Ref(Kind.RUN, "a1", "a1")])) == 5
    # Exactly `DEPTH` is served...
    spawning, deepest = str(ladder[-2]["run_id"]), str(ladder[-1]["run_id"])
    assert len(tree.ancestry(corpus, [Ref(Kind.TURN, spawning, "t")])) == bounds.DEPTH
    # ...and the run that turn spawned, one level deeper, is refused.
    with pytest.raises(ValueError, match=str(bounds.DEPTH)):
        tree.ancestry(corpus, [Ref(Kind.RUN, deepest, deepest)])


def test_a_row_draws_a_spend_bar_only_where_it_has_a_share_to_draw(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The bar is a class on the row, stepped by the row's share of what the session spent.

    Rows that cost nothing of their own — a tool call, a compaction — carry no bar rather than
    an empty one, because a bar drawn at zero reads as a measurement.
    """
    html = client.get(f"/session/{SPINE}").text
    (whole,) = one(
        store,
        "SELECT sum(cost_usd) FROM live_api_calls WHERE session_id = ?",
        [SPINE],
    )
    # The session is the basis, so its own row is the full bar.
    assert "s10" in inside(html, "data-tree", f"session:{SPINE}", "class")[0].split()
    for key in values(html, "data-tree"):
        classes = set(inside(html, "data-tree", key, "class")[0].split())
        steps = {name for name in classes if name.startswith("s") and name[1:].isdigit()}
        # Every row either shows what it cost with a bar beside it, or shows neither.
        assert bool(steps) == ("cost_usd" in fields(html, "data-tree", key)), key
    assert whole, "the session this reads has a spend to take shares of"


def test_a_size_above_its_ceiling_is_refused(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The three sizes a node URL carries only go down — the ceiling is the production default."""
    at = url(open_turn(store))
    for knob, bound in (("kin", bounds.KIN), ("log", bounds.LOG), ("detail", bounds.DETAIL)):
        assert client.get(at, params={knob: bound.ceiling + 1}).status_code == 400, knob
        assert client.get(at, params={knob: bound.ceiling}).status_code == 200, knob


# The runs of one session beside every edge a preset places them by: the spawning edge the
# full tree reads (`SPAWNS`), plus the tool call that edge resolved through and the run's own
# declared parent, which is the one edge `agents` reads that an unresolvable call cannot lose.
EDGES = SPAWNS.replace('c."index"', "tc.id, a.parent_agent_id")


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
    """`noapi`'s level under a turn: its calls' tool calls, with the runs hoisted among them.

    The api calls are hidden, so their tool calls rise to the turn in call-then-tool order and
    a run follows the tool call that spawned it rather than the api call that held it.
    `turn_id` None is the unattributed bucket's level, which reads the same way.
    """
    tools = [
        row[0]
        for row in store.execute(
            "SELECT tc.id FROM live_tool_calls tc"
            " JOIN live_api_calls c ON c.session_id = tc.session_id AND c.source = tc.source"
            "  AND c.id = tc.api_call_id"
            " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
            "  AND t.id = c.turn_id"
            " WHERE tc.session_id = ? AND tc.source = ? AND t.id IS NOT DISTINCT FROM ?"
            ' ORDER BY c."index", tc."index"',
            [session_id, source, turn_id],
        ).fetchall()
    ]
    hoisted: dict[str | None, list[str]] = {}
    for edge in edges(store, session_id):
        if edge.spawn_source == source and edge.spawn_turn_id == turn_id:
            hoisted.setdefault(edge.spawn_tool_id, []).append(f"run:{edge.run_id}")
    placed: list[str] = []
    for tool_id in tools:
        placed.append(f"tool:{tool_id}")
        placed += hoisted.pop(tool_id, [])
    # A run whose spawning tool call this level does not hold still belongs to the turn the
    # edge resolved to, exactly as it does when the api calls are showing.
    for leftover in hoisted.values():
        placed += leftover
    return placed


def runs_where(
    store: duckdb.DuckDBPyConnection, session_id: str, holds: Callable[[Edge], bool]
) -> list[str]:
    """The session's runs one edge places under one node, in the order they started."""
    return [f"run:{edge.run_id}" for edge in edges(store, session_id) if holds(edge)]


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
        # A tool call is a leaf until `agents`, where the run it spawned is the one child the
        # preset has left to hang under it.
        case Kind.TOOL, Preset.AGENTS:
            return runs_where(store, session_id, lambda edge: edge.spawn_tool_id == node_id)
        case Kind.TOOL, _:
            return []
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


def richest(
    store: duckdb.DuckDBPyConnection, preset: Preset, kind: Kind, count: int
) -> list[tuple[str, str, str]]:
    """The nodes of one kind whose cell holds the most, fullest first.

    No recorded session holds every kind, and a cell read on an empty node passes by agreeing
    that nothing is there — so a cell is checked wherever the corpus fills it best.
    """
    ordered = sorted(
        candidates(store, kind), key=lambda at: (-len(cell(store, preset, kind, *at)), at)
    )
    return ordered[:count]


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
            return f"/session/{session_id}/unattributed/{source}"
        case _:
            return f"/session/{session_id}/{kind}/{source}/{node_id}"


def sited(session_id: str, chain: Sequence[str]) -> list[tuple[Kind, str, str, str]]:
    """Each step of an open path as the arguments its own cell is read with.

    A key carries a kind and an id but not a thread, and the path is what supplies it: a node
    sits on `main` until the path passes through a run, and on that run's thread after it.
    """
    placed: list[tuple[Kind, str, str, str]] = []
    source = MAIN
    for key in chain:
        kind, _, node_id = key.partition(":")
        placed.append((Kind(kind), session_id, source, node_id))
        if Kind(kind) is Kind.RUN:
            source = node_id
    return placed


def node_link(href: str) -> bool:
    """Whether a link goes to a node page — the records browser and an offload file do not."""
    path = href.partition("?")[0].strip("/").split("/")
    return path[0] == "session" and (len(path) < 3 or path[2] in set(Kind))


# The cells no recorded session fills, which is not the same claim as an empty cell. A tool
# call and a compaction are leaves by the table; the bucket's `agents` cell is one the corpus
# happens not to reach, and the planted leaf below is what reaches it.
UNFILLED = {
    (Kind.TOOL, Preset.FULL),
    (Kind.TOOL, Preset.NO_API),
    *((Kind.COMPACTION, preset) for preset in Preset),
    (Kind.UNATTRIBUTED, Preset.AGENTS),
}


@pytest.mark.parametrize("preset", list(Preset))
@pytest.mark.parametrize("kind", list(Kind))
def test_every_kind_under_every_preset_opens_the_children_its_cell_defines(
    client: TestClient, store: duckdb.DuckDBPyConnection, kind: Kind, preset: Preset
) -> None:
    """The 24 cells of the design's table, each read off the page that renders it.

    One case per cell, checked on the node the corpus fills that cell fullest at, so a wrong
    cell reddens under its own name. `UNFILLED` is the other half: a cell that renders nothing
    has to be one the design or the corpus says is empty.
    """
    (picked,) = richest(store, preset, kind, 1)
    expected = cell(store, preset, kind, *picked)
    html = client.get(node_url(kind, *picked), params={"nav": preset}).text
    assert kin(html) == expected, picked
    assert bool(expected) == ((kind, preset) not in UNFILLED), picked


@pytest.mark.parametrize("preset", list(Preset))
def test_every_open_level_is_its_own_cell_or_the_full_one_that_holds_the_path(
    client: TestClient, store: duckdb.DuckDBPyConnection, preset: Preset
) -> None:
    """Every visible node has a visible parent, level by level and not at the selection alone.

    A preset filters children and never the expanded chain, so a level whose cell would hide
    the step the path goes through renders in full instead — which is what lets a reader stand
    on a kind the preset folds away and still see where it sits. Swept over the nodes of every
    kind the corpus fills best, because a level built under the wrong parent is a shape one
    selection can hide.
    """
    for kind in Kind:
        for at in richest(store, preset, kind, 3):
            html = client.get(node_url(kind, *at), params={"nav": preset}).text
            chain, drawn = values(html, "data-crumb"), rows(html)
            assert drawn[0] == (0, chain[0]), at
            for depth, (step, *arguments) in enumerate(sited(at[0], chain)):
                below = [key for at_depth, key in drawn if at_depth == depth + 1]
                expected = cell(store, preset, step, *arguments)
                if depth + 1 < len(chain) and chain[depth + 1] not in expected:
                    expected = cell(store, Preset.FULL, step, *arguments)
                assert below == expected, f"{at}: under {chain[depth]}"


@pytest.mark.parametrize("preset", list(Preset))
def test_the_tree_offers_every_fold_at_the_node_the_reader_stands_on(
    client: TestClient, store: duckdb.DuckDBPyConnection, preset: Preset
) -> None:
    """A fold is a control above the tree, not a query string a reader has to know to type.

    One link per preset, each pointing at the *same* node under a different fold, and the fold
    in force marked. Read on a node of every kind because every kind's page carries the tree,
    and read with a knob turned down because a link that dropped `?kin=` would quietly serve a
    wider page than the one the reader is standing on.
    """
    for kind in Kind:
        (picked,) = richest(store, preset, kind, 1)
        at = node_url(kind, *picked)
        html = client.get(at, params={"nav": preset, "kin": 2}).text
        # Every fold is offered, in the order the enum declares them, and the control rides the
        # rows: it sits inside the element a tree click swaps out of band, so the links follow
        # the reader to the node they land on instead of pointing back at the one they left.
        assert inside(html, "id", "tree-rows", "data-nav") == [choice.value for choice in Preset]
        for choice in Preset:
            (href,) = inside(html, "data-nav", choice, "href")
            went = urlsplit(href)
            # The same node under a different fold, carrying the knobs the reader arrived with.
            assert went.path == at, (kind, choice)
            carried = {name: value for name, (value,) in parse_qs(went.query).items()}
            wanted = {"kin": "2"} | ({} if choice is Preset.FULL else {"nav": str(choice)})
            assert carried == wanted, (kind, choice)
            # And the fold in force is the marked one, so the control says where the reader is.
            marked = ["true"] if choice is preset else []
            assert inside(html, "data-nav", choice, "aria-current") == marked, (kind, choice)


def test_a_preset_hides_a_kind_without_hiding_the_path_down_to_one(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A node a preset filters out still renders when the reader is standing on it.

    `agents` hides turns and `noapi` hides api calls, and either one selected is a reading
    position rather than a contradiction: the node is on the tree, it is the row the pane is
    about, and its whole chain renders above it with nothing missing in between.
    """
    turn = open_turn(store)
    (call_id,) = one(
        store,
        "SELECT id FROM live_api_calls WHERE session_id = ? AND source = ? AND turn_id = ?"
        ' ORDER BY "index" LIMIT 1',
        [SPINE, MAIN, turn],
    )
    hidden = {
        "agents": (url(turn), f"turn:{turn}"),
        "noapi": (f"/session/{SPINE}/call/{MAIN}/{call_id}", f"call:{call_id}"),
    }
    for preset, (at, key) in hidden.items():
        served = client.get(at, params={"nav": preset})
        assert served.status_code == 200, preset
        assert key in values(served.text, "data-tree"), preset
        assert values(served.text, "data-selected") == [key], preset
        assert values(served.text, "data-crumb")[0] == f"session:{SPINE}", preset
        assert values(served.text, "data-crumb")[-1] == key, preset


def test_a_preset_rides_every_node_link_the_page_mints(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """`?nav=` travels with the reader: every node link on the page carries it.

    The tree's rows, the tail a cap left, the crumbs, the pane's children log and the two walk
    controls are all node URLs, and a reader who picked a view keeps it through any of them.
    The switcher above the tree is the one exception, and the only one: its whole job is to
    change the fold, so its three links are excluded here and checked on their own leaf. Read
    with `?kin=1` so the tail row is on the page to check too.
    """
    html = client.get(url(open_turn(store)), params={"nav": "agents", "kin": 1}).text
    switching = set(inside(html, "class", "switch", "href"))
    assert len(switching) == len(Preset), "the switcher's own links, which change the fold"
    # `values` reads the markup and `inside` reads it parsed, so an href with two knobs on it
    # arrives `&amp;`-escaped from one and bare from the other.
    links = [
        href for href in values(html, "href") if node_link(href) and unescape(href) not in switching
    ]
    assert len(links) > 5, "the page mints node links to check"
    for href in links:
        assert parse_qs(href.partition("?")[2]).get("nav") == ["agents"], href


def test_a_preset_the_viewer_does_not_have_is_refused(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """`?nav=` names one of the three views or the request is a 400, not a quiet full tree."""
    at = url(open_turn(store))
    assert client.get(at, params={"nav": "everything"}).status_code == 400
    for preset in Preset:
        assert client.get(at, params={"nav": preset}).status_code == 200, preset
