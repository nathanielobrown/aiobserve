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

import datetime as dt
import re
from collections import Counter
from collections.abc import Callable, Sequence
from html import unescape
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.model import MAIN_SOURCE
from aiobserve.view import bounds, tree
from aiobserve.view.app import build_app
from aiobserve.view.enrichment import Descriptions
from aiobserve.view.format import cut, money
from aiobserve.view.nodes import BODY_URL, KIN_URL, Kind, Preset, Ref, meter
from tests.conftest import MAIN, SPINE, SPINE_RUN
from tests.view.conftest import SPAWNS, Planter, fields, inside, kin, one, rows, values, wired


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
    """The api calls under one turn and the compactions among them, each run hoisted after the
    call that spawned it.

    `turn_id` None is the unattributed bucket's own level, which reads the same way: the calls
    that answer no turn, and the runs those calls spawned.
    """
    calls = store.execute(
        "SELECT c.id, c.started_at FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE c.session_id = ? AND c.source = ? AND t.id IS NOT DISTINCT FROM ?"
        ' ORDER BY c."index"',
        [session_id, source, turn_id],
    ).fetchall()
    hoisted: dict[str, list[str]] = {}
    for run_id, spawn_source, spawn_turn, spawn_call in spawned(store, session_id):
        if spawn_source == source and spawn_turn == turn_id:
            hoisted.setdefault(str(spawn_call), []).append(f"run:{run_id}")
    placed: list[str] = []
    for key in dropped_in(
        [(f"call:{call_id}", started) for call_id, started in calls],
        turn_marks(store, session_id, source, turn_id),
    ):
        placed.append(key)
        placed += hoisted.pop(key.removeprefix("call:"), [])
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


def test_a_compaction_hangs_off_the_turn_whose_span_covers_it(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """Where a compaction sits, read at each instant the placement rule turns on.

    A compaction that happened while a turn was running is a child of that turn; one that
    happened between two turns is a sibling of them, in time order. The corpus exercises both
    sides but neither edge: no recorded compaction has a turn of its own thread starting after
    it, and none lands on the instant a turn starts or the instant one ends, so the tree would
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
    for moment, under, above in (
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
            placed = rows(moved.get(f"/session/{session_id}/turn/{source}/{turn_id}").text)
        keys = [key for _, key in placed]
        at, turn_at = keys.index(f"compaction:{compaction_id}"), keys.index(f"turn:{turn_id}")
        # A child of the turn is one level deeper than it; a sibling shares its depth, and its
        # side of the turn says which way the time comparison went.
        assert (placed[at][0] == placed[turn_at][0] + 1) is under, (moment, placed)
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
            turn_id: served.get(f"/session/{SPINE}/turn/{MAIN}/{turn_id}").text
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
    )


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


def test_every_link_that_swaps_the_pane_lands_the_pane_in_the_pane(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The whole of what a click does, on both the mounts that mount a node link.

    A tree row, a children-log row and the two walk controls are how a reader moves without
    leaving the page, and all of them do the same thing: fetch the node's URL, take `#pane`
    out of the response, put it where the pane already is, and swap the rows out of band.
    Read as htmx composes it, inheritance and all, because that is what the browser acts on.

    `hx-target` is the half that has no default worth having: htmx aims at the clicked
    element, so a page missing it swaps the whole pane inside the `<a>` the reader clicked
    and leaves the pane itself showing the node they came from. `hx-swap` is `outerHTML`
    because `hx-select` hands back the `#pane` element itself, not its contents.
    """
    html = client.get(url(open_turn(store))).text
    swap = {
        "hx-target": "#pane",
        "hx-swap": "outerHTML",
        "hx-select": "#pane",
        "hx-select-oob": "#tree-rows",
        "hx-push-url": "true",
    }
    for mount in ("data-tree", "data-child", "data-walk"):
        # A row's other fetch is its body toggle, which opens in place and has nowhere to go:
        # the ones that move the reader are the ones fetching a node's own URL.
        moving = [(key, w) for key, w in wired(html, mount) if node_link(w["hx-get"])]
        assert len(moving) > 1, mount
        for key, wiring in moving:
            # A link fetches what it points at: one URL, however the reader gets there. A walk
            # control has no `href` to agree with — it is a button, because what it offers is
            # a move through the pane and not a place of its own to paste.
            assert wiring.get("href", wiring["hx-get"]) == wiring["hx-get"], (mount, key)
            assert {name: wiring.get(name) for name in swap} == swap, (mount, key)
    # The two ids the swap aims at, each written exactly once.
    assert html.count('id="pane"') == 1
    assert html.count('id="tree-rows"') == 1


def test_every_level_a_tree_opens_is_indented_one_step_further_than_the_one_above(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A row sits one step further in than its parent, however deep the session nests.

    A subagent's own turns render four levels down and its api calls deeper still, so a
    stylesheet with a rung for the first three levels laid them flush against the session and
    the hierarchy vanished exactly where a reader most needs it. CSS cannot read `data-depth`
    as a number portably and `app.CSP` forbids the inline style that would carry one, so every
    level a chain can open is written out — and this is what keeps that ladder as long as
    `bounds.DEPTH` says a chain can be.
    """
    # A turn of a subagent's own thread opens the session, the turn that spawned the run, the
    # run, the turn itself and its api calls — five levels, past the three the ladder had...
    turn_id, source = one(
        store,
        'SELECT id, source FROM live_turns WHERE session_id = ? AND source <> ? ORDER BY "index"'
        " LIMIT 1",
        [SPINE, MAIN],
    )
    page = client.get(f"/session/{SPINE}/turn/{source}/{turn_id}").text
    rendered = {depth for depth, _ in rows(page)}
    assert max(rendered) > 3, "the recorded subagent no longer nests past three levels"
    # ...and the stylesheet indents each of them by its own depth, in one step a level.
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.S)
    ladder = {
        int(depth): int(steps)
        for depth, steps in re.findall(
            r'li\.row\[data-depth="(\d+)"\][^{]*\{[^}]*calc\((\d+) \* var\(--tree-step\)\)', style
        )
    }
    # Every level a chain can open has a rung, and no rung stands for a level nothing reaches.
    assert ladder == {depth: depth for depth in range(1, bounds.DEPTH + 1)}
    assert rendered <= set(ladder) | {0}


def test_the_tree_keeps_its_place_because_the_scroller_is_not_what_swaps(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """What holds a reader's place in a long tree when a click replaces its rows.

    Nothing in the markup says "keep the scroll offset" — the tree keeps it because the
    element carrying the scrollbar is `#tree`, and the swap replaces `#tree-rows` inside it.
    An untouched scroller keeps its `scrollTop`, which is why the design could drop
    `hx-preserve`. Move `overflow` down onto the rows and every click sends the reader back to
    the top of the session, and no assertion on served HTML would notice.

    So the structure is what gets pinned: the rows the swap replaces are nested inside the
    element the stylesheet scrolls, and nothing scrolls below it.
    """
    page = client.get(url(open_turn(store))).text
    # The element the swap replaces sits inside the one the tree is scrolled by.
    assert "tree-rows" in inside(page, "id", "tree", "id")
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.S)
    scrolls = {
        selector.strip()
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style)
        if "overflow:" in body
    }
    # One of them scrolls, and it is the one the swap leaves alone. The two selectors that
    # could take the scrollbar off it are the rows themselves, under either name.
    assert "#tree" in scrolls
    assert not [rule for rule in scrolls if "#tree-rows" in rule or "#tree .rows" in rule]


def test_the_tree_is_widened_by_a_handle_and_the_width_outlives_the_page(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A handle beside the tree drags it wider, and the browser remembers how wide.

    Every other thing a reader sets rides the URL. A width cannot: it belongs to the screen
    they are reading on and not to the node they linked to, so a pasted link would carry
    someone else's column. What this pins is the chain that lets a script set it instead —
    a handle in the markup, a grid whose tree column is one custom property, and a script
    served from this app, because `app.CSP` forbids an inline one and a page load would
    forget a width that CSS alone had kept.
    """
    page = client.get(url(open_turn(store))).text
    # The handle sits between the two columns it divides, and says what it is to a reader who
    # cannot see it.
    assert [at for at in values(page, "id") if at in {"tree", "tree-grip", "pane"}] == [
        "tree",
        "tree-grip",
        "pane",
    ]
    grip = re.findall(r"<div id=\"tree-grip\"[^>]*>", page)
    assert len(grip) == 1 and 'role="separator"' in grip[0] and 'tabindex="0"' in grip[0]
    # The tree's column is one custom property, which is the whole of what the script writes:
    # a width the stylesheet fixed some other way is a handle that drags nothing.
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.S)
    (columns,) = re.findall(r"#browser\s*\{[^}]*grid-template-columns:([^;]*);", style)
    assert "var(--tree-width" in columns
    # And the script that writes it is a file this app serves, keeping the width where a page
    # load cannot reach it.
    (src,) = [asset for asset in values(page, "src") if "tree-width" in asset]
    served = client.get(src)
    assert served.status_code == 200
    assert "--tree-width" in served.text and "localStorage" in served.text


def test_a_run_hoists_after_the_call_that_spawned_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run renders inside the turn it belongs to, right after its spawning call.

    A run is a child of the turn and not of the api call — it has a thread of its own, and the
    call is only where it was asked for — so where it came from is said by the place it renders
    in rather than by a note on the row. Read here at the level, where the neighbour on either
    side is the whole point.
    """
    run_id, source, turn_id, call_id = one(store, SPAWNS + " LIMIT 1", [SPINE])
    assert turn_id is not None, "the recorded run this reads is placed under a turn"
    html = client.get(url(str(turn_id))).text
    level = turn_level(store, SPINE, str(source), str(turn_id))
    at = level.index(f"run:{run_id}")
    # The run sits immediately after the call that spawned it, and the level renders as read.
    assert level[at - 1] == f"call:{call_id}"
    assert [key for key in values(html, "data-tree") if key in level] == level
    # And the row carries the node's label and its cost, and nothing naming that call: the
    # place is the whole of what says where the run came from.
    assert set(fields(html, "data-tree", f"run:{run_id}")) == {"label", "cost_usd"}


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
    run_id, source, turn_id, call_id = one(store, SPAWNS + " LIMIT 1", [SPINE])
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

    Driven below the fixture corpus's fan-out rather than planted up to the production
    window (`bounds.KIN`), which no recorded session comes near: the knob exists for exactly
    this. The cap bites twice here — once on the level beside the selection, once on the calls
    under it — and the selection survives it either way. A cut that hid the open path would
    leave the pane describing a node the tree does not show.
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
    # ...with a tail saying how many rows are off the tree, and no way off the page at all:
    # what it offers is a fetch for the rest of its own level, which the leaf below follows.
    assert fields(html, "data-more", f"session:{SPINE}")["cut"] == str(len(level) - len(shown))
    assert inside(html, "data-more", f"session:{SPINE}", "href") == []
    # And the level under the selection takes the same cap, where no rescue is owed: one child
    # of the several the turn has, and a tail for the rest.
    assert [key for key in values(html, "data-tree") if key in under] == [under[0]]
    assert fields(html, "data-more", f"turn:{selection}")["cut"] == str(len(under) - 1)
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
    level, under = thread_level(store, SPINE, MAIN), turn_level(store, SPINE, MAIN, selection)
    tails = dict(wired(page, "data-more"))
    fetch, below = tails[f"session:{SPINE}"], tails[f"turn:{selection}"]
    # The whole of what the row does, inheritance and all...
    assert fetch == {
        "hx-get": (
            f"{KIN_URL}/session/{SPINE}/{SPINE}?kin=1&thread={MAIN}&depth=1&opened=turn:{selection}"
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
    assert rows(served.text) == [(1, key) for key in level if key != f"turn:{selection}"]
    # Each of them reads on under the sizes the reader typed, like any row the page drew, and
    # by one URL whether it is clicked or pasted.
    for key, wiring in wired(served.text, "data-tree"):
        assert wiring["href"] == wiring["hx-get"], key
        assert parse_qs(urlsplit(wiring["hx-get"]).query) == {"kin": ["1"]}, key
    # The level under the selection has no open path through it, so its tail row holds nothing
    # back and asks for everything past the window.
    assert (
        below["hx-get"] == f"{KIN_URL}/turn/{SPINE}/{MAIN}/{selection}?kin=1&thread={MAIN}&depth=2"
    )
    assert rows(client.get(below["hx-get"]).text) == [(2, key) for key in under[1:]]
    # The depth is the one thing a level cannot say for itself, and the tree's arithmetic
    # prices `DEPTH` of them: rows claiming to stand outside the tree a page draws are rows
    # no page ever asked for.
    for depth, answer in ((0, 400), (1, 200), (bounds.DEPTH, 200), (bounds.DEPTH + 1, 400)):
        asked = client.get(
            f"{KIN_URL}/turn/{SPINE}/{MAIN}/{selection}",
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
    drawn = {key: fields(whole, "data-tree", key)["label"] for at, key in rows(whole) if at == 1}
    # The same level under a window of one, and the rows its tail row stands for.
    tail = dict(wired(enriched_client.get(page, params={"kin": 1}).text, "data-more"))
    served = enriched_client.get(tail[f"session:{SPINE}"]["hx-get"]).text
    fetched = {key: fields(served, "data-tree", key)["label"] for _, key in rows(served)}
    assert fetched, "the window left nothing out: this page no longer proves the case"
    assert fetched == {key: drawn[key] for key in fetched}
    # And the claim has teeth: on its own page the main thread reads by its descriptions, so
    # a fragment that read the level's thread would have served those names instead.
    home = enriched_client.get(f"/session/{SPINE}").text
    described = {key: fields(home, "data-tree", key)["label"] for at, key in rows(home) if at == 1}
    assert any(described[key] != label for key, label in fetched.items())


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
    sessions = [str(row[0]) for row in store.execute("SELECT id FROM sessions").fetchall()]
    (whole,) = one(
        store,
        "SELECT sum(cost_usd) FROM live_api_calls WHERE session_id = ?",
        [SPINE],
    )
    # The session is the basis, so a session that spent anything is its own full bar.
    spine = client.get(f"/session/{SPINE}").text
    assert "s10" in inside(spine, "data-tree", f"session:{SPINE}", "class")[0].split()
    # Swept over every session under every fold rather than over the deepest session alone.
    # The rows that take their own share — the buckets, which are not rows of the store — are
    # gathered by a different builder under each fold, and are not all on one session's page.
    for session_id in sessions:
        bars: dict[str, tuple[str | None, frozenset[str]]] = {}
        for preset in Preset:
            html = client.get(f"/session/{session_id}", params={"nav": preset}).text
            for key in values(html, "data-tree"):
                classes = inside(html, "data-tree", key, "class")[0].split()
                steps = frozenset(n for n in classes if n.startswith("s") and n[1:].isdigit())
                cost = fields(html, "data-tree", key).get("cost_usd")
                # Every row either shows what it cost with a bar beside it, or shows neither.
                assert bool(steps) == (cost is not None), key
                # And a fold decides which rows are drawn, never what one of them spent or how
                # much of the session that was: a bar that moved between folds is a share taken
                # against something other than the session.
                assert bars.setdefault(key, (cost, steps)) == (cost, steps), (key, preset)
    assert whole, "the session this reads has a spend to take shares of"


def test_a_spend_bar_steps_by_decade_so_three_orders_of_magnitude_fill_it(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """Which step a share is drawn at, read at the top of the scale, the bottom, and between.

    A recorded session's rows do not span the scale, so the ladder itself — ten steps over
    three decades of share — is a rule no fixture exercises: every bar could be drawn a step
    too wide and the corpus would agree. The shares are planted instead, on the calls of one
    session, whose spend is the whole every share on its pages is taken against.
    """
    calls = store.execute(
        "SELECT source, id FROM live_api_calls WHERE session_id = ? ORDER BY id LIMIT 3",
        [SPINE],
    ).fetchall()
    assert len(calls) == 3, "three calls to hang the scale on"
    # A decade apart each time, against a whole of 1101: the dearest call takes almost all of
    # it, the next a tenth of that, and the last a thousandth — which is where the scale runs
    # out, and the step is held at its first rather than going below it.
    ladder = {1000: "s10", 100: "s7", 1: "s1"}
    path = plant(
        ("UPDATE api_calls SET cost_usd = 0 WHERE session_id = ?", [SPINE]),
        *(
            ("UPDATE api_calls SET cost_usd = ? WHERE session_id = ? AND id = ?", [cost, SPINE, at])
            for cost, (_, at) in zip(ladder, calls, strict=True)
        ),
    )
    with TestClient(build_app(path)) as scaled:
        for (cost, step), (source, call_id) in zip(ladder.items(), calls, strict=True):
            page = scaled.get(node_url(Kind.CALL, SPINE, str(source), str(call_id))).text
            classes = inside(page, "data-tree", f"{Kind.CALL}:{call_id}", "class")[0].split()
            assert step in classes, (cost, classes)


def labelled(store: duckdb.DuckDBPyConnection, session_id: str) -> dict[str, str]:
    """Every row of one session whose label the store composes, keyed the way a row is.

    Read off the columns the design names a node from, not off the page: a tool call named by
    its input alone, or a run named by the definition it ran where its own brief was recorded,
    is a row pointing at a node the reader did not ask for.
    """
    said: dict[str, str] = {}
    for tool_id, name, given in store.execute(
        "SELECT id, name, input FROM live_tool_calls WHERE session_id = ?", [session_id]
    ).fetchall():
        # The tool and the head of what it was asked, which is what tells two Bash calls apart.
        said[f"{Kind.TOOL}:{tool_id}"] = f"{name} {given or ''}".strip()
    for call_id, spoken, model in store.execute(
        "SELECT id, text, model FROM live_api_calls WHERE session_id = ?", [session_id]
    ).fetchall():
        # What the call said, and where it said nothing the model that was asked.
        said[f"{Kind.CALL}:{call_id}"] = spoken or model
    for compaction_id, trigger in store.execute(
        "SELECT id, trigger FROM live_compactions WHERE session_id = ?", [session_id]
    ).fetchall():
        said[f"{Kind.COMPACTION}:{compaction_id}"] = f"compaction · {trigger}"
    for turn_id, prompt, command_name, command_args in store.execute(
        "SELECT id, prompt, command_name, command_args FROM live_turns WHERE session_id = ?",
        [session_id],
    ).fetchall():
        # The command a turn ran and what followed it, else the prompt as the reader typed it.
        said[f"{Kind.TURN}:{turn_id}"] = (
            f"{command_name} {command_args or ''}".strip() if command_name is not None else prompt
        )
    for run_id, description, agent_type in store.execute(
        "SELECT id, description, agent_type FROM live_agent_runs WHERE session_id = ?",
        [session_id],
    ).fetchall():
        # The brief the run was given, and where none was recorded the definition it ran.
        said[f"{Kind.RUN}:{run_id}"] = description or agent_type
    return {key: cut(value, queries.NAV_CHARS).strip() for key, value in said.items()}


def test_every_row_is_named_from_the_column_its_kind_is_named_by(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A row's label is the whole of what it says, so it is read back against its own column.

    A tree row carries `TREE_ROW_BYTES` and no more, so the label is where a kind spends what
    it has to say — and every kind spends it differently. Every node the store names is read
    on its own page, where its row is on the open path whichever fold the reader picked: a
    column that only one recorded row exercises — a slash turn with no arguments after it —
    is one a sample would step over.
    """
    # Keyed by session as well as by row: two sessions of the corpus record an api call under
    # the same id, and a row carries the id alone.
    said = {
        (str(at), key): value
        for (at,) in store.execute("SELECT id FROM sessions").fetchall()
        for key, value in labelled(store, str(at)).items()
    }
    read: set[tuple[str, str]] = set()
    for kind in (Kind.TOOL, Kind.CALL, Kind.COMPACTION, Kind.RUN, Kind.TURN):
        for session_id, source, node_id in candidates(store, kind):
            # A page holds more than the node it opens, so the ones already read are skipped.
            if (session_id, f"{kind}:{node_id}") in read:
                continue
            page = client.get(node_url(kind, session_id, source, node_id)).text
            assert f"{kind}:{node_id}" in values(page, "data-tree"), node_id
            for key in values(page, "data-tree"):
                if (at := (session_id, key)) in said:
                    assert fields(page, "data-tree", key)["label"] == said[at], at
                    read.add(at)
    # Every row the store names a label for was reached. A sweep that missed one would pass on
    # a label built from any column at all.
    assert read == set(said)


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


def test_a_bucket_row_carries_the_totals_of_what_it_gathers(
    client: TestClient, store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """Neither bucket is a row of the store: its numbers are sums over what it holds.

    The rest of the tree hands a row the store's own numbers, so a bucket is the one place the
    viewer adds up. What it adds up is read back here — the spend, the bar that spend takes
    against the session, and the mark saying some of the calls under it went unpriced — for
    every bucket the corpus records, on the page the bucket hangs on.
    """
    # The session's threads whose calls answer no turn of them, and the runs nothing placed.
    standing = store.execute(
        "SELECT DISTINCT c.session_id, c.source FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE t.id IS NULL ORDER BY 1, 2"
    ).fetchall()
    gathered: tuple[str, list[str]] | None = None
    for session_id, source in standing:
        at = (
            f"/session/{session_id}"
            if source == MAIN_SOURCE
            else f"/session/{session_id}/run/{source}"
        )
        cost, unpriced = one(store, STANDING, [str(session_id), str(source)])
        weighed(client.get(at).text, f"unattributed:{source}", store, session_id, cost, unpriced)
    for (session_id,) in store.execute("SELECT id FROM sessions ORDER BY 1").fetchall():
        loose = [edge.run_id for edge in edges(store, str(session_id)) if edge.spawn_source is None]
        if not loose:
            continue
        # The bucket's own row is every loose run's thread at once, which is the sum the
        # session's page shows against a row that has no children of the store's to point at.
        totals = [one(store, THREAD, [str(session_id), run_id]) for run_id in loose]
        cost, unpriced = sum(row[0] for row in totals), sum(row[1] for row in totals)
        page = client.get(f"/session/{session_id}").text
        weighed(page, f"unattached:{session_id}", store, str(session_id), cost, unpriced)
        # Opening it hands its children the same basis: a run under the bucket draws its share
        # of the session, like every other run, and not a share of the bucket that gathered it.
        (whole,) = one(
            store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [str(session_id)]
        )
        opened = client.get(f"/session/{session_id}/unattached").text
        for run_id, (spent, _) in zip(loose, totals, strict=True):
            drawn = inside(opened, "data-tree", f"run:{run_id}", "class")[0].split()
            assert meter(spent / whole if whole else None) in drawn, run_id
        gathered = gathered or (str(session_id), loose)
    # Both buckets are read above rather than one of them: they are built by different code
    # over different rows, and only one of them can span threads.
    assert standing and gathered is not None
    # No recorded bucket holds a call our price table could not price, so the mark that says
    # one does is planted: a thread under each bucket loses its costs, and the bucket has to
    # both count what went unpriced and total what is left. The expectations read the planted
    # store through the same sums, so the plant moves the page and the oracle together.
    thread, source = str(standing[0][0]), str(standing[0][1])
    loose_at, loose_runs = gathered
    path = plant(
        (
            "UPDATE api_calls SET cost_usd = NULL WHERE session_id = ? AND source = ?",
            [thread, source],
        ),
        (
            "UPDATE api_calls SET cost_usd = NULL WHERE session_id = ? AND source = ?",
            [loose_at, loose_runs[0]],
        ),
    )
    planted = duckdb.connect(str(path), read_only=True)
    with TestClient(build_app(path)) as marked:
        cost, unpriced = one(planted, STANDING, [thread, source])
        assert unpriced, "the plant leaves the unattributed bucket calls to mark"
        at = f"/session/{thread}" if source == MAIN_SOURCE else f"/session/{thread}/run/{source}"
        weighed(marked.get(at).text, f"unattributed:{source}", planted, thread, cost, unpriced)
        totals = [one(planted, THREAD, [loose_at, run_id]) for run_id in loose_runs]
        cost, unpriced = sum(row[0] for row in totals), sum(row[1] for row in totals)
        assert unpriced, "and leaves the unattached bucket calls to mark"
        page = marked.get(f"/session/{loose_at}").text
        weighed(page, f"unattached:{loose_at}", planted, loose_at, cost, unpriced)
        # A child of the bucket says the same thing for itself: the run whose calls the plant
        # left unpriced carries its own count, and the runs beside it carry no mark at all.
        opened = marked.get(f"/session/{loose_at}/unattached").text
        for run_id, (_, missing) in zip(loose_runs, totals, strict=True):
            marks = inside(opened, "data-tree", f"run:{run_id}", "title")
            assert bool(marks) == bool(missing), run_id
            assert not missing or str(missing) in marks[0], run_id
    planted.close()


def weighed(
    page: str,
    key: str,
    store: duckdb.DuckDBPyConnection,
    session_id: str,
    cost: float,
    unpriced: int,
) -> None:
    """One row read against what the store holds under it: its spend, and what went unpriced."""
    (whole,) = one(store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [session_id])
    row = fields(page, "data-tree", key)
    assert row["cost_usd"] == money(cost), key
    # The bar is that spend against the session, not against the row's parent or its own
    # children — and a session with nothing to take a share of draws every row at nothing.
    share = cost / whole if whole else None
    assert meter(share) in inside(page, "data-tree", key, "class")[0].split(), key
    # A `title` inside the row is the mark on a total our price table could not complete —
    # there where some call under the row went unpriced, and nowhere else.
    marks = inside(page, "data-tree", key, "title")
    assert bool(marks) == bool(unpriced), key
    assert not unpriced or str(unpriced) in marks[0], key


def spend(store: duckdb.DuckDBPyConnection, session_id: str) -> dict[str, tuple[float, int]]:
    """What the store holds under each priced row of one session: its cost and its unpriced calls.

    A turn is worth the calls that answered it on its own thread; a call is worth itself, and a
    call our price table could not price is worth nothing rather than being free. The session
    is worth all of them, which is also the whole every share on its page is taken against.
    """
    said: dict[str, tuple[float, int]] = {}
    for cost, unpriced in store.execute(
        "SELECT round(cost_usd, 4), unpriced_api_calls FROM session_rollups WHERE session_id = ?",
        [session_id],
    ).fetchall():
        said[f"{Kind.SESSION}:{session_id}"] = (cost or 0, unpriced)
    for turn_id, cost, unpriced in store.execute(
        "SELECT t.id, coalesce(round(sum(c.cost_usd), 4), 0),"
        "  count(c.id) FILTER (c.cost_usd IS NULL)"
        " FROM live_turns t LEFT JOIN live_api_calls c"
        "  ON c.session_id = t.session_id AND c.source = t.source AND c.turn_id = t.id"
        " WHERE t.session_id = ? GROUP BY t.id",
        [session_id],
    ).fetchall():
        said[f"{Kind.TURN}:{turn_id}"] = (cost, unpriced)
    for call_id, cost in store.execute(
        "SELECT id, round(cost_usd, 4) FROM live_api_calls WHERE session_id = ?", [session_id]
    ).fetchall():
        said[f"{Kind.CALL}:{call_id}"] = (cost or 0, int(cost is None))
    for (run_id,) in store.execute(
        "SELECT id FROM live_agent_runs WHERE session_id = ?", [session_id]
    ).fetchall():
        # A run is worth its own thread — the same sum an unattached bucket gathers per run.
        said[f"{Kind.RUN}:{run_id}"] = one(store, THREAD, [session_id, str(run_id)])
    return said


def test_every_priced_row_carries_the_spend_the_store_holds_under_it(
    client: TestClient, store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """The cost on a row, the bar beside it and the mark above it, read against the store.

    The buckets add up for themselves; every other priced row is handed the store's own
    number, and this is where that number is read back. Read on the page of each priced node,
    where its own row is on the open path whichever fold the reader picked.
    """
    # Keyed by session as well as by row, for the reason the labels are.
    said = {
        (str(at), key): value
        for (at,) in store.execute("SELECT id FROM sessions").fetchall()
        for key, value in spend(store, str(at)).items()
    }
    read: set[tuple[str, str]] = set()
    for kind in (Kind.SESSION, Kind.TURN, Kind.CALL, Kind.RUN):
        for session_id, source, node_id in candidates(store, kind):
            # A page holds more than the node it opens, so the ones already read are skipped.
            if (session_id, f"{kind}:{node_id}") in read:
                continue
            page = client.get(node_url(kind, session_id, source, node_id)).text
            for key in values(page, "data-tree"):
                if (at := (session_id, key)) in said:
                    weighed(page, key, store, session_id, *said[at])
                    read.add(at)
    # Every priced row of the store was reached, so no kind is priced by a sample of itself.
    assert read == set(said)
    # Our price table prices every call the corpus recorded, so the mark that says otherwise is
    # planted on one call: it has to reach the call's own row, the turn above it, and the
    # session at the root, each of which counts what went unpriced for itself.
    session_id, source, call_id, turn_id = one(
        store,
        "SELECT c.session_id, c.source, c.id, t.id FROM live_api_calls c"
        " JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE c.cost_usd IS NOT NULL ORDER BY c.session_id, c.source, c.id LIMIT 1",
    )
    path = plant(("UPDATE api_calls SET cost_usd = NULL WHERE id = ?", [call_id]))
    planted = duckdb.connect(str(path), read_only=True)
    with TestClient(build_app(path)) as marked:
        said = spend(planted, session_id)
        assert said[f"{Kind.CALL}:{call_id}"] == (0, 1), "the plant left the call unpriced"
        page = marked.get(node_url(Kind.CALL, session_id, source, call_id)).text
        for key in (
            f"{Kind.CALL}:{call_id}",
            f"{Kind.TURN}:{turn_id}",
            f"{Kind.SESSION}:{session_id}",
        ):
            weighed(page, key, planted, session_id, *said[key])
    planted.close()


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
    """`noapi`'s level under a turn: its calls' tool calls and its compactions, with the runs
    hoisted among them.

    The api calls are hidden, so their tool calls rise to the turn in call-then-tool order and
    a run follows the tool call that spawned it rather than the api call that held it. A
    compaction hangs off the turn whichever fold the reader is in, so it drops in by time here
    too. `turn_id` None is the unattributed bucket's level, which reads the same way.
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
    hoisted: dict[str | None, list[str]] = {}
    for edge in edges(store, session_id):
        if edge.spawn_source == source and edge.spawn_turn_id == turn_id:
            hoisted.setdefault(edge.spawn_tool_id, []).append(f"run:{edge.run_id}")
    placed: list[str] = []
    for key in dropped_in(
        [(f"tool:{tool_id}", started) for tool_id, started in tools],
        turn_marks(store, session_id, source, turn_id),
    ):
        placed.append(key)
        placed += hoisted.pop(key.removeprefix("tool:"), [])
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


def mounts(html: str) -> list[str]:
    """Every expansion a page's log rows mount, unescaped — the markup carries `&` as `&amp;`."""
    return [unescape(href) for href in values(html, "hx-get") if href.startswith(BODY_URL)]


def spilled(html: str) -> list[str]:
    """Every level a page's tail rows fetch the rest of, unescaped."""
    return [unescape(href) for href in values(html, "hx-get") if href.startswith(KIN_URL)]


# An api call that made a `Task` call: the one call whose tool log mounts a body with a run
# link in it, read from the tool's side of the join `view_runs` makes.
SPAWNING_CALL = (
    "SELECT c.session_id, c.source, c.id FROM live_api_calls c"
    " JOIN live_tool_calls tc ON tc.session_id = c.session_id AND tc.source = c.source"
    "  AND tc.api_call_id = c.id"
    " JOIN live_agent_runs a ON a.session_id = tc.session_id AND a.tool_use_id = tc.id"
    "  AND tc.source <> a.id"
    " ORDER BY c.id LIMIT 1"
)


def mounting(store: duckdb.DuckDBPyConnection) -> list[str]:
    """One page per kind of body a children log can mount.

    A session's log mounts turns, a turn's mounts api calls, a call's mounts tool calls, and
    the unattached bucket's mounts runs — the only page whose rows reach `run_body`, the
    second of the two fragment routes. The call is one that spawned a run, because a tool body
    leads with the run it started and that link is the only node link a pane inside a fragment
    mints.
    """
    session_id, source, call_id = one(store, SPAWNING_CALL)
    loose, _, _ = candidates(store, Kind.UNATTACHED)[0]
    return [
        node_url(Kind.SESSION, str(session_id), MAIN, str(session_id)),
        url(open_turn(store)),
        node_url(Kind.CALL, str(session_id), str(source), str(call_id)),
        node_url(Kind.UNATTACHED, loose, MAIN, loose),
    ]


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
    # The fullest node cannot see a level that leaks sideways: children matched on the thread
    # alone would land under every node of the kind on it, and under the fullest one that is
    # the answer the cell wanted anyway. So the same cell is read again at a sibling on the
    # same thread that the corpus leaves empty, where a leak has nothing to hide behind.
    beside = [
        at
        for at in candidates(store, kind)
        if at[:2] == picked[:2] and at != picked and not cell(store, preset, kind, *at)
    ]
    if beside:
        empty = client.get(node_url(kind, *beside[0]), params={"nav": preset}).text
        assert kin(empty) == [], beside[0]


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
    Both the `href` a reader can paste and the `hx-get` a click follows, because the walk
    controls are buttons and mint only the second. The switcher above the tree is the one
    exception, and the only one: its whole job is to change the fold, so its three links are
    excluded here and checked on their own leaf. Read with `?kin=1` so the tail row is on the
    page to check too, and with `?log=1` so the children log runs past one page: the corpus's
    widest level is five children, so at the production page size no pager is ever minted.

    The body a log row expands is the same node under the same view, so the fold rides the
    mount as well — and rides out again on the links the fragment itself mints, which are the
    reader's way on from inside a parent's page. Every kind of body is opened, because the two
    fragment routes mint their suffix apart and only a tool's body mints a link of its own.
    """
    html = client.get(url(open_turn(store)), params={"nav": "agents", "kin": 1, "log": 1}).text
    switching = set(inside(html, "class", "switch", "href"))
    assert len(switching) == len(Preset), "the switcher's own links, which change the fold"
    # `values` reads the markup and `inside` reads it parsed, so an href with two knobs on it
    # arrives `&amp;`-escaped from one and bare from the other.
    links = [
        href
        for attribute in ("href", "hx-get")
        for href in values(html, attribute)
        if node_link(href) and unescape(href) not in switching
    ]
    assert len(links) > 5, "the page mints node links to check"
    assert values(html, "data-walk"), "the walk controls are among them"
    for href in links:
        assert parse_qs(href.partition("?")[2]).get("nav") == ["agents"], href
    opened: set[str] = set()
    led = 0
    for at in mounting(store):
        page = client.get(at, params={"nav": "agents", "kin": 1}).text
        found = mounts(page)
        assert found, f"the log rows on {at} mount an expansion"
        for mount in found:
            # The mount a log row opens its child's body through carries the fold...
            assert parse_qs(mount.partition("?")[2]).get("nav") == ["agents"], mount
            served = client.get(mount)
            assert served.status_code == 200, mount
            # ...and the body it serves links on under that fold rather than dropping it.
            onward = [href for href in values(served.text, "href") if node_link(href)]
            assert onward, f"the fragment offers the way to its own node: {mount}"
            for href in onward:
                assert parse_qs(unescape(href).partition("?")[2]).get("nav") == ["agents"], href
            opened.add(mount[len(BODY_URL) + 1 :].partition("/")[0])
            led += len(values(served.text, "data-spawned"))
    # And the rows a tail row fetches are minted by the fragment and not by the page, so the
    # fold rides the fetch out and comes back on every row it answers with.
    spilling = spilled(html)
    assert spilling, "the window left a tail row on the page"
    for fetch in spilling:
        assert parse_qs(fetch.partition("?")[2]).get("nav") == ["agents"], fetch
        served = client.get(fetch)
        assert served.status_code == 200, fetch
        onward = [unescape(href) for href in values(served.text, "href")]
        assert onward, f"the level it left out has rows in it: {fetch}"
        for href in onward:
            assert parse_qs(href.partition("?")[2]).get("nav") == ["agents"], href
    # The three kinds of body one route serves, and the run the other one does.
    assert opened == {Kind.TURN, Kind.CALL, Kind.TOOL, Kind.RUN}, opened
    # One of those tool bodies led with the run it started. That link is the only one the pane
    # macro mints inside a fragment, so without it the suffix the pane is handed goes unread.
    assert led, "a tool body that leads with its run"


def test_a_preset_the_viewer_does_not_have_is_refused(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """`?nav=` names one of the three views or the request is a 400, not a quiet full tree.

    Asked of a fragment as well as a page: the fold rides the mount an expansion opens, so a
    fold the viewer does not have has to be refused there too rather than written into every
    link the fragment serves.
    """
    at = url(open_turn(store))
    for asked in (at, mounts(client.get(at).text)[0]):
        assert client.get(asked, params={"nav": "everything"}).status_code == 400, asked
        for preset in Preset:
            assert client.get(asked, params={"nav": preset}).status_code == 200, preset
