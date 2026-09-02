"""The NavTree beside a node page: one open path through the session, and nothing else open.

The NavTree is served with the pane in one response, so these leaves fetch the same node URLs
`tests/view/test_node.py` does and read the rows instead, through the readers in
`tests/view/nav_trees.py`. What they hold to is where a node hangs: which level a page opens, which
turn a compaction lands under, where a run stands, and what a cap cuts when a level runs past
what the page prices.

What a row draws is `test_nav_tree__rows.py` and `test_nav_tree__meters.py`; what `?nav=` leaves
standing is `test_nav_tree__presets.py`.
"""

import datetime as dt
from collections import Counter
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.analyze import queries
from hyphae.model import MAIN_SOURCE
from hyphae.view import bounds
from hyphae.view.app import build_app
from hyphae.view.enrichment import Descriptions
from hyphae.view.nodes import (
    KIN_URL,
    NO_LEDGER,
    Kind,
    Preset,
    Ref,
)
from hyphae.view.pages.node import nav_tree
from tests.conftest import MAIN, SPINE, SPINE_RUN
from tests.view.conftest import (
    SPAWNS,
    Planter,
    fields,
    inside,
    kin,
    one,
    rows,
    under,
    values,
    wired,
)
from tests.view.nav_trees import (
    call_tools,
    cell,
    edges,
    hanging,
    mounts,
    open_turn,
    shut,
    spilled,
    standing,
    thread_level,
    turn_level,
    url,
)


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
        level = thread_level(store, session_id, MAIN)
        expected = [f"session:{session_id}", *shut(store, session_id, MAIN, level)]
        assert values(html, "data-nav-tree") == expected, session_id
        seen |= {key.split(":")[0] for key in expected}
    # And the sweep really did reach every rule above, so a corpus that lost its compactions
    # or its buckets would redden here rather than passing on the turns alone.
    assert {"compaction", "unattributed", "unattached"} <= seen


def test_a_compaction_hangs_off_the_turn_whose_span_covers_it(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """Where a compaction sits, read at each instant the placement rule turns on.

    A compaction that happened while a turn was running is a child of that turn; one that
    happened between two turns is a sibling of them, in time order. The corpus exercises both
    sides but neither edge: no recorded compaction has a turn of its own thread starting after
    it, and none lands on the instant a turn starts or the instant one ends, so the NavTree would
    read the same with the rule's boundaries deleted. A compaction is where the reader sees the
    context being dropped, so the edges are planted — the same compaction moved to each of the
    three instants, and read off the turn's own page, where both levels are open at once.

    The pair is picked so the plant has one answer: a turn whose start no sibling shares, and
    whose end no turn of the thread is still running through.
    """
    session_id, source, compaction_id, turn_id, started, ended = one(
        store,
        "SELECT k.session_id, k.source, k.id, t.id, t.started_at, t.ended_at"
        " FROM live_compactions k"
        " JOIN live_turns t ON t.session_id = k.session_id AND t.source = k.source"
        " WHERE t.started_at IS NOT NULL AND t.ended_at IS NOT NULL"
        "   AND NOT EXISTS (SELECT 1 FROM live_turns o WHERE o.session_id = t.session_id"
        "     AND o.source = t.source AND o.id <> t.id AND o.started_at = t.started_at)"
        "   AND NOT EXISTS (SELECT 1 FROM live_turns o WHERE o.session_id = t.session_id"
        "     AND o.source = t.source AND t.ended_at >= o.started_at AND t.ended_at < o.ended_at)"
        ' ORDER BY k.session_id, k.source, t."index" LIMIT 1',
    )
    for moment, hangs, above in (
        # The instant before the turn starts is nobody's turn, so the compaction stands beside
        # the turns and above the one that started after it...
        (started - dt.timedelta(seconds=1), False, True),
        # ...the instant the turn starts is the turn's own, which is the edge the span closes
        # on, so the same compaction hangs off it instead...
        (started, True, False),
        # ...and the instant the turn ends belongs to whatever comes next, so it drops back
        # beside the turns, below the one it just left.
        (ended, False, False),
    ):
        path = plant(
            (
                "UPDATE compactions SET timestamp = ?::TIMESTAMPTZ WHERE id = ?",
                [str(moment), compaction_id],
            )
        )
        with TestClient(build_app(path)) as moved:
            placed = rows(moved.get(f"/session/{session_id}/thread/{source}/turn/{turn_id}").text)
        keys = [key for _, key in placed]
        at, turn_at = keys.index(f"compaction:{compaction_id}"), keys.index(f"turn:{turn_id}")
        # A child of the turn is one level deeper than it; a sibling shares its depth, and its
        # side of the turn says which way the time comparison went.
        assert (placed[at][0] == placed[turn_at][0] + 1) is hangs, (moment, placed)
        assert (at < turn_at) is above, (moment, placed)


def moved(compaction_id: str, at: dt.datetime) -> tuple[str, list[str]]:
    """One recorded compaction, moved onto `SPINE`'s main thread at the instant named."""
    return (
        "UPDATE compactions SET session_id = ?, source = ?, timestamp = ?::TIMESTAMPTZ"
        " WHERE id = ?",
        [SPINE, MAIN, str(at), compaction_id],
    )


def test_a_turn_holds_its_own_compactions_and_an_overlapped_instant_goes_to_the_later_turn(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """Two turns running at once, one compaction inside both and one inside only the outer.

    A turn's level holds the compactions of that turn and no other's. Where two turns cover
    one instant — 44 of the canonical store's 1,269 compactions sit inside more than one span
    — the turn that started last holds it, because that is the one still running when the
    context was dropped.

    Both claims need a thread with two turns owning a compaction apiece, and no recorded
    session has one: every thread holding a compaction holds a single turn. The overlap is
    real, though, so only the compactions are planted — two of them moved onto a thread whose
    turns already overlap, one at an instant both cover and one at an instant only the outer
    turn does.
    """
    outer, outer_started, inner, inner_started, inner_ended = one(
        store,
        "SELECT a.id, a.started_at, b.id, b.started_at, b.ended_at FROM live_turns a"
        " JOIN live_turns b ON b.session_id = a.session_id AND b.source = a.source"
        "  AND b.id <> a.id AND b.started_at > a.started_at AND b.ended_at <= a.ended_at"
        "  AND b.started_at < b.ended_at"
        ' WHERE a.session_id = ? AND a.source = ? ORDER BY a."index", b."index" LIMIT 1',
        [SPINE, MAIN],
    )
    shared, alone = [
        row[0]
        for row in store.execute("SELECT id FROM live_compactions ORDER BY id LIMIT 2").fetchall()
    ]
    path = plant(
        # Inside both spans: the instant belongs to the turn that started last.
        moved(shared, inner_started + (inner_ended - inner_started) / 2),
        # And inside the outer turn alone, before the inner one started.
        moved(alone, outer_started + (inner_started - outer_started) / 2),
    )
    with TestClient(build_app(path)) as served:
        pages = {
            turn_id: served.get(f"/session/{SPINE}/thread/{MAIN}/turn/{turn_id}").text
            for turn_id in (outer, inner)
        }
    for turn_id, expected in ((outer, alone), (inner, shared)):
        held = [key for key in kin(pages[turn_id]) if key.startswith(f"{Kind.COMPACTION}:")]
        assert held == [f"{Kind.COMPACTION}:{expected}"], turn_id
    # And the page says what placed them: the query that answered which turn each compaction
    # happened during is cited, at the thread both levels of this page read it on.
    cited = fields(pages[inner], "id", "citation")
    assert cited["view_compactions"] == (
        f"-- queries/view_compactions.sql session_id={SPINE} source={MAIN}"
        f" chip_chars={queries.NAV_CHARS}"
    )


def test_the_nav_tree_opens_the_selections_path_and_leaves_the_rest_shut(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """One open path: the chain down to the selection, expanded, and no other subtree.

    The chain here is the session and the turn under it, so the rows are the session, its
    thread's whole level, and — under the selected turn alone — the calls it made. A tree that
    expanded a sibling would show that sibling's calls too, which is the difference this reads:
    the rows are compared as a whole list, in order, not searched for. Every row that is not
    the selection stands whatever runs it hides, which is the one thing a shut row still draws.
    """
    selection = open_turn(store)
    html = client.get(url(selection)).text
    expected: list[str] = [f"session:{SPINE}"]
    for key in thread_level(store, SPINE, MAIN):
        expected.append(key)
        # The selection is the one node whose children render — every other row is a row and
        # the runs standing under it.
        if key == f"turn:{selection}":
            expected += shut(store, SPINE, MAIN, turn_level(store, SPINE, MAIN, selection))
        else:
            expected += hanging(store, SPINE, MAIN, key)
    assert values(html, "data-nav-tree") == expected
    # And the NavTree says which row the pane is about, once.
    assert values(html, "data-selected") == [f"turn:{selection}"]


def test_a_run_renders_under_the_tool_call_that_spawned_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run hangs off the tool call that asked for it, wherever the tree is read.

    The spawning tool call is where the run came from, so it is the row the run renders under —
    a place a reader can point at, rather than a note on the row saying which call to look for.
    Read on the spawning api call's own page, where its tool calls are the level and the run
    stands one deeper than the one that spawned it.
    """
    spawn = next(edge for edge in edges(store, SPINE) if edge.spawn_tool_id)
    page = f"/session/{SPINE}/thread/{spawn.spawn_source}/call/{spawn.spawn_call_id}"
    html = client.get(page).text
    # The call's own level is its tool calls, and none of them is the run...
    assert kin(html) == call_tools(store, SPINE, str(spawn.spawn_source), str(spawn.spawn_call_id))
    # ...which stands under the one tool call that spawned it, and under nothing else.
    assert under(html, f"tool:{spawn.spawn_tool_id}") == [f"run:{spawn.run_id}"]
    drawn = [key for _, key in rows(html)]
    assert drawn.count(f"run:{spawn.run_id}") == 1
    # And the row carries the node's title and its two costs — its own thread and the subtree
    # under it — and nothing naming that call: the place is the whole of what says where the
    # run came from.
    assert set(fields(html, "data-nav-tree", f"run:{spawn.run_id}")) == {
        "title",
        "cost_usd",
        "total_usd",
    }


def test_a_run_whose_rows_above_are_shut_stands_under_the_nearest_one_showing(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run is always visible: the same run read at three shut ancestors, in three presets.

    This is the rule the hoisting machinery was replaced by. A run nests under its spawning
    tool call, and a nesting that only rendered when every row above it was open would hide a
    run behind a row a reader has no reason to click. So each shut row stands the runs it
    hides, and opening one moves a run's indent rather than bringing it into being.
    """
    spawn = next(edge for edge in edges(store, SPINE) if edge.spawn_turn_id)
    source, run = str(spawn.spawn_source), f"run:{spawn.run_id}"
    # A shut api call, on the turn's own page: the run stands under the call, with no tool row
    # between the two — the row it nests under is not on the page at all.
    turn = client.get(f"/session/{SPINE}/thread/{source}/turn/{spawn.spawn_turn_id}").text
    assert run in under(turn, f"call:{spawn.spawn_call_id}")
    assert f"tool:{spawn.spawn_tool_id}" not in [key for _, key in rows(turn)]
    # A shut turn, under `noapi`, where neither the call nor the tool call is a row: the run
    # stands under the turn, on the session's own page.
    folded = client.get(f"/session/{SPINE}", params={"nav": Preset.NO_API}).text
    assert run in under(folded, f"turn:{spawn.spawn_turn_id}")
    # And under `agents`, where the run is the session's own child.
    agents = client.get(f"/session/{SPINE}", params={"nav": Preset.AGENTS}).text
    assert run in under(agents, f"session:{SPINE}")


@pytest.mark.parametrize("preset", list(Preset))
def test_every_run_of_a_session_is_on_its_page_under_every_preset(
    client: TestClient, store: duckdb.DuckDBPyConnection, preset: Preset
) -> None:
    """No preset loses a run, over every session the corpus holds.

    The NavTree is the only way to a run's page, so a rule that placed one under a row the
    session page never draws would take that run out of the viewer. Swept rather than spotted:
    a run is placed by an edge that resolves to any of five kinds of row, and a session page is
    where a reader starts.
    """
    for (session_id,) in store.execute("SELECT id FROM sessions").fetchall():
        html = client.get(f"/session/{session_id}", params={"nav": preset}).text
        drawn = [key for _, key in rows(html) if key.startswith(f"{Kind.RUN}:")]
        held = {
            f"{Kind.RUN}:{run_id}"
            for (run_id,) in store.execute(
                "SELECT id FROM live_agent_runs WHERE session_id = ?", [session_id]
            ).fetchall()
        }
        assert set(drawn) == held, session_id
        # Each of them once: a run drawn twice is one placed by two edges at the same time.
        assert len(drawn) == len(held), session_id


def test_a_bucket_home_is_decided_by_the_spawning_edge(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """The two buckets are disjoint by one edge: which of the two is a run's home, and why.

    A spawning call that resolves but sits under no turn of its thread puts the run in that
    thread's *unattributed* bucket, under that call as usual. Only a run whose spawning call
    resolves to nothing at all is *unattached*. The corpus records the second and not the
    first, so the first is planted: one recorded run's spawning call loses its turn, and the
    run has to move one bucket and not the other.
    """
    run_id, source, turn_id, call_id = one(store, SPAWNS + " LIMIT 1", [SPINE])
    assert turn_id is not None, "the run this moves starts out under a turn"
    path = plant(
        (
            "UPDATE api_calls SET turn_id = NULL WHERE session_id = ? AND source = ? AND id = ?",
            [SPINE, str(source), str(call_id)],
        ),
    )
    with TestClient(build_app(path)) as moved:
        # The run is gone from the turn it used to hang under — it stands on that page under
        # the bucket instead, which is the row the moved call is now part of...
        home = moved.get(url(str(turn_id))).text
        assert f"run:{run_id}" not in under(home, f"turn:{turn_id}")
        assert f"run:{run_id}" in under(home, f"unattributed:{source}")
        # ...and is in the thread's unattributed bucket, standing under its spawning call.
        bucket_url = f"/session/{SPINE}/thread/{source}/unattributed"
        bucket = moved.get(bucket_url)
        assert bucket.status_code == 200
        assert f"run:{run_id}" in under(bucket.text, f"call:{call_id}")
        # The unattached bucket, which is the other home, does not also hold it.
        loose = moved.get(f"/session/{SPINE}/unattached")
        assert f"run:{run_id}" not in values(loose.text, "data-nav-tree")
        # The run's own page agrees with the bucket that holds it. Read here because the NavTree
        # above is drawn from the bucket down while a run page is drawn from the run up: the
        # two answers come from different code, and a page that disagreed would be a crash —
        # the trail would look for the run under a bucket the session's NavTree does not hold.
        own = moved.get(f"/session/{SPINE}/run/{run_id}")
        assert own.status_code == 200
        spawn_tool = one(
            store, "SELECT tool_use_id FROM live_agent_runs WHERE id = ?", [str(run_id)]
        )[0]
        assert values(own.text, "data-crumb") == [
            f"session:{SPINE}",
            f"unattributed:{source}",
            f"call:{call_id}",
            f"tool:{spawn_tool}",
            f"run:{run_id}",
        ]
        # And the bucket draws it under each preset, which is the shape no recorded session
        # has: a run under a thread's bucket, with the rows between them shut or folded away.
        planted = duckdb.connect(str(path), read_only=True)
        for preset in Preset:
            html = moved.get(bucket_url, params={"nav": preset}).text
            assert kin(html) == cell(
                planted, preset, Kind.UNATTRIBUTED, SPINE, str(source), str(source)
            ), preset
            assert f"run:{run_id}" in [key for _, key in rows(html)], preset
        planted.close()


def test_the_kin_cap_cuts_the_children_but_never_the_open_path(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Children are capped per level, with a row saying how many the cap left out.

    Driven below the fixture corpus's fan-out rather than planted up to the production
    window (`bounds.KIN`), which no recorded session comes near: the knob exists for exactly
    this. The cap bites twice here — once on the level beside the selection, once on the calls
    under it — and the selection survives it either way. A cut that hid the open path would
    leave the pane describing a node the NavTree does not show.
    """
    selection = open_turn(store)
    html = client.get(url(selection), params={"kin": 1}).text
    level, beneath = thread_level(store, SPINE, MAIN), turn_level(store, SPINE, MAIN, selection)
    assert len(level) > 2 and len(beneath) > 1, "the cap has to have something to cut"
    # The cap admits one child, and the path through the selection takes that slot rather than
    # being kept past it: the rescue rides inside the cap. A level of `kin + 1` children is a
    # page the byte arithmetic never priced, and the sibling the reader loses is one the tail
    # row still counts and the parent's own page still lists.
    shown = [key for key in values(html, "data-nav-tree") if key in level]
    assert shown == [f"turn:{selection}"]
    # ...with a tail saying how many rows are off the NavTree, and no way off the page at all:
    # what it offers is a fetch for the rest of its own level, which the leaf below follows.
    assert fields(html, "data-more", f"session:{SPINE}")["cut"] == str(len(level) - len(shown))
    assert inside(html, "data-more", f"session:{SPINE}", "href") == []
    # And the level under the selection takes the same cap, where no rescue is owed: one child
    # of the several the turn has, and a tail for the rest.
    assert [key for key in values(html, "data-nav-tree") if key in beneath] == [beneath[0]]
    assert fields(html, "data-more", f"turn:{selection}")["cut"] == str(len(beneath) - 1)
    # And no level on the page exceeds the cap, anywhere. `worst_node_bytes()` prices
    # `DEPTH * (KIN + 1)` rows on exactly this, so it is pinned here rather than left to a
    # reading of `_kin`.
    assert max(Counter(depth for depth, _ in rows(html)).values()) <= 1


def test_a_tail_row_stands_the_rest_of_its_level_where_it_stands(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A `+N more` row opens the rest of its level in place, without moving the reader.

    What comes back is rows and not a pane, so the row overrides every part of the swap the
    tree writes once above it: it swaps out the row it sits in — the row, not the button
    inside it — selects nothing, sends nothing out of band, and pushes no URL, because the
    reader has not gone anywhere. The rows arrive at the depth
    the row stood at and in the level's own order, which is what lets them stand in its place.

    Both halves of one split are read here — the level beside the selection, where the open
    path holds a child inside the window wherever in the level it sits, and the level under
    the selection, where nothing is held back. The fetch names the held child so the two
    halves cannot both send it.
    """
    selection = open_turn(store)
    page = client.get(url(selection), params={"kin": 1}).text
    level, beneath = thread_level(store, SPINE, MAIN), turn_level(store, SPINE, MAIN, selection)
    tails = dict(wired(page, "data-more"))
    fetch, below = tails[f"session:{SPINE}"], tails[f"turn:{selection}"]
    # The whole of what the row does, inheritance and all...
    assert fetch == {
        "hx-get": (
            f"{KIN_URL}/session/{SPINE}/session/{SPINE}?kin=1&thread={MAIN}&depth=1&opened=turn:{selection}"
        ),
        "hx-target": "closest li",
        "hx-swap": "outerHTML",
        "hx-select": "unset",
        "hx-select-oob": "unset",
        "hx-push-url": "false",
    }
    # ...and what it fetches is the level less the window, at the depth the row sits at: the
    # rows the reader could not see, ready to stand where the row that counted them stands.
    served = client.get(fetch["hx-get"])
    assert served.status_code == 200
    stood = rows(served.text)
    # One sequence read whole, not two projections of it: they arrive carrying whatever the
    # tree draws beneath them, since a row that stands in for another has to bring what that
    # row was holding — one of these turns spawned the session's agent runs. Where those runs
    # fall among the turns is the claim. Split the sequence by depth and a fragment that put a
    # run above the turn holding it would read as correct.
    assert stood == [
        (1 + below, key)
        for below, key in standing(
            store, SPINE, MAIN, [key for key in level if key != f"turn:{selection}"]
        )
    ]
    # Each of them reads on under the sizes the reader typed, like any row the page drew, and
    # by one URL whether it is clicked or pasted. The link, not the popover trigger beside it:
    # a row fetches twice, and only one of the two is somewhere a reader can go.
    links = [(key, at) for key, at in wired(served.text, "data-nav-tree") if "href" in at]
    assert len(links) == len(stood)
    for key, wiring in links:
        assert wiring["href"] == wiring["hx-get"], key
        assert parse_qs(urlsplit(wiring["hx-get"]).query) == {"kin": ["1"]}, key
    # The level under the selection has no open path through it, so its tail row holds nothing
    # back and asks for everything past the window.
    assert (
        below["hx-get"]
        == f"{KIN_URL}/session/{SPINE}/thread/{MAIN}/turn/{selection}?kin=1&thread={MAIN}&depth=2"
    )
    assert rows(client.get(below["hx-get"]).text) == [(2, key) for key in beneath[1:]]
    # The depth is the one thing a level cannot say for itself, and the NavTree's arithmetic
    # prices `DEPTH` of them: rows claiming to stand outside the NavTree a page draws are rows
    # no page ever asked for.
    for depth, answer in ((0, 400), (1, 200), (bounds.DEPTH, 200), (bounds.DEPTH + 1, 400)):
        asked = client.get(
            f"{KIN_URL}/session/{SPINE}/thread/{MAIN}/turn/{selection}",
            params={"kin": 1, "thread": MAIN, "depth": depth},
        )
        assert asked.status_code == answer, depth


def test_a_fetched_row_is_described_by_the_thread_the_reader_stands_on(
    enriched_client: TestClient,
) -> None:
    """The rest of a level arrives named the way the page names it, not the way its thread does.

    `view_enrichment` keys turns by thread, so a page reads the descriptions of the one thread
    the reader is on and every turn of another thread falls back to its prompt. A run page is
    one such reader: the session's own turns are drawn above it, undescribed. The rows a tail
    row fetches have to agree — a fetch that read the level's thread instead would serve the
    same turns under other names, which is the one thing a row standing in another's place
    cannot do.
    """
    page = f"/session/{SPINE}/run/{SPINE_RUN}"
    # The main thread's turns as this page draws them: the level the run hangs under, whole.
    whole = enriched_client.get(page).text
    drawn = {
        key: fields(whole, "data-nav-tree", key)["title"] for at, key in rows(whole) if at == 1
    }
    # The same level under a window of one, and the rows its tail row stands for.
    tail = dict(wired(enriched_client.get(page, params={"kin": 1}).text, "data-more"))
    served = enriched_client.get(tail[f"session:{SPINE}"]["hx-get"]).text
    fetched = {key: fields(served, "data-nav-tree", key)["title"] for _, key in rows(served)}
    assert fetched, "the window left nothing out: this page no longer proves the case"
    assert fetched == {key: drawn[key] for key in fetched}
    # And the claim has teeth: on its own page the main thread reads by its descriptions, so
    # a fragment that read the level's thread would have served those names instead.
    home = enriched_client.get(f"/session/{SPINE}").text
    described = {
        key: fields(home, "data-nav-tree", key)["title"] for at, key in rows(home) if at == 1
    }
    assert any(described[key] != title for key, title in fetched.items())


def test_a_chain_is_resolved_to_the_depth_the_page_prices_and_no_deeper(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """`bounds.DEPTH` is the last chain `ancestry` resolves; one level past it raises.

    The response's bound is arithmetic over the depth and the per-level cap, so a deeper chain
    is not a bigger page — it is a page whose size was never computed. Read at the boundary
    from both sides, because a bound that refused at `DEPTH` would silently cost the deepest
    page the arithmetic paid for. The corpus's deepest nesting is three runs, so the shape is
    built rather than recorded: a ladder of runs, each spawned from a turn of the one above it.

    A rung is four levels now that a run hangs off the tool call that spawned it — the run, and
    the turn, api call and tool call above it — which is what sizes the ladder here.
    """
    assert bounds.DEPTH % 4 == 0, "the rung arithmetic below is written for a multiple of four"
    rung = 4
    ladder = [
        {
            "run_id": f"a{step}",
            "spawn_source": f"a{step - 1}" if step else MAIN_SOURCE,
            "spawn_turn_id": f"t{step}",
            "spawn_call_id": f"c{step}",
            "tool_use_id": f"u{step}",
        }
        # One rung short of the bound: the ladder ends where the deepest tool call the page
        # prices stands, and the run under that tool call is what falls past it.
        for step in range(bounds.DEPTH // rung - 1)
    ]
    corpus = nav_tree.Corpus(
        SPINE, held=NO_LEDGER, runs=ladder, described=Descriptions(), source=MAIN
    )
    # A short ladder resolves, which is what says a rung is worth four levels and not some
    # other number: one run is the session, a turn, a call, a tool call and the run itself.
    shallow = nav_tree.Corpus(SPINE, NO_LEDGER, ladder[:2], Descriptions(), MAIN)
    assert len(nav_tree.ancestry(shallow, [Ref(Kind.RUN, "a0", "a0")])) == 1 + rung
    assert len(nav_tree.ancestry(shallow, [Ref(Kind.RUN, "a1", "a1")])) == 1 + 2 * rung
    # Exactly `DEPTH` is served — a tool call of the deepest thread, seeded by its own page the
    # way `ancestry` takes one, standing where the run it spawned would hang...
    deepest = ladder[-1]["run_id"]
    tool = [
        Ref(Kind.TURN, deepest, "t"),
        Ref(Kind.CALL, deepest, "c"),
        Ref(Kind.TOOL, deepest, "u"),
    ]
    assert len(nav_tree.ancestry(corpus, tool)) == bounds.DEPTH
    # ...and one rung past the ladder, which is the run that tool call spawned, is refused.
    past = [*ladder, {**ladder[-1], "run_id": "past", "spawn_source": deepest}]
    with pytest.raises(ValueError, match=str(bounds.DEPTH)):
        nav_tree.ancestry(
            nav_tree.Corpus(SPINE, NO_LEDGER, past, Descriptions(), MAIN),
            [Ref(Kind.RUN, "past", "past")],
        )


def test_a_size_above_its_ceiling_is_refused(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The three sizes a node URL carries only go down — the ceiling is the production default.

    A fragment takes the same sizes, because the knobs ride the mount that opens it: a size it
    would not serve a page under is one it must refuse rather than mint into the fragment's own
    links.
    """
    at = url(open_turn(store))
    page = client.get(at, params={"kin": 1}).text
    opening = mounts(page)[0]
    # The rest of a level takes them as well, stripped back to the one thing it cannot answer
    # without: the sizes are what this leaf turns, and a URL carrying two of one is not a case.
    spilling = spilled(page)[0].partition("?")[0]
    for knob, bound in (("kin", bounds.KIN), ("log", bounds.LOG), ("detail", bounds.DETAIL)):
        for asked, fixed in ((at, {}), (opening, {}), (spilling, {"thread": MAIN, "depth": 1})):
            for size, answer in ((bound.ceiling + 1, 400), (bound.ceiling, 200)):
                served = client.get(asked, params=fixed | {knob: size})
                assert served.status_code == answer, (asked, knob, size)
