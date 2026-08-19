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
"""

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.model import MAIN_SOURCE
from aiobserve.view import bounds, tree
from aiobserve.view.app import build_app
from aiobserve.view.enrichment import Descriptions
from aiobserve.view.nodes import Kind, Ref
from tests.conftest import MAIN, SPINE
from tests.view.conftest import SPAWNS, Planter, fields, inside, one, values


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
        bucket = moved.get(f"/session/{SPINE}/unattributed/{source}")
        assert bucket.status_code == 200
        keys = values(bucket.text, "data-tree")
        assert keys[keys.index(f"call:{call_id}") + 1] == f"run:{run_id}"
        # The unattached bucket, which is the other home, does not also hold it.
        loose = moved.get(f"/session/{SPINE}/unattached")
        assert f"run:{run_id}" not in values(loose.text, "data-tree")


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
    # The cap admits one child, and the path through the selection is kept beside it...
    shown = [key for key in values(html, "data-tree") if key in level]
    assert shown == [level[0], f"turn:{selection}"]
    # ...with a tail saying how many rows are off the tree, linking to the page that pages
    # them rather than capping them: the session's own, still under the size the reader typed.
    assert fields(html, "data-more", f"session:{SPINE}")["cut"] == str(len(level) - len(shown))
    assert inside(html, "data-more", f"session:{SPINE}", "href") == [f"/session/{SPINE}?kin=1"]
    # And the level under the selection takes the same cap, where no rescue is owed: one child
    # of the several the turn has, and a tail for the rest.
    assert [key for key in values(html, "data-tree") if key in under] == [under[0]]
    assert fields(html, "data-more", f"turn:{selection}")["cut"] == str(len(under) - 1)


def test_a_chain_deeper_than_the_page_is_priced_for_is_refused(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """Resolving a path past `bounds.DEPTH` raises rather than opening a tree nothing priced.

    The response's bound is arithmetic over the depth and the per-level cap, so a deeper chain
    is not a bigger page — it is a page whose size was never computed. The corpus reaches five
    levels and a run adds two, so the shape is built rather than recorded: a ladder of runs,
    each spawned from a turn of the one above it.
    """
    ladder = [
        {
            "run_id": f"a{step}",
            "spawn_source": f"a{step - 1}" if step else MAIN_SOURCE,
            "spawn_turn_id": f"t{step}",
        }
        for step in range(bounds.DEPTH)
    ]
    corpus = tree.Corpus(SPINE, whole=0.0, runs=ladder, described=Descriptions(), source=MAIN)
    # A short ladder resolves: every rung is a run and a turn, and the session caps it.
    shallow = tree.Corpus(SPINE, 0.0, ladder[:2], Descriptions(), MAIN)
    assert len(tree.ancestry(shallow, [Ref(Kind.RUN, "a1", "a1")])) == 5
    with pytest.raises(ValueError, match=str(bounds.DEPTH)):
        tree.ancestry(corpus, [Ref(Kind.RUN, ladder[-1]["run_id"], ladder[-1]["run_id"])])


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
