"""The two meters a NavTree row draws: what the node cost, and how full the window was.

Both are read back off the rendered row rather than computed beside it — a cost badge is a step
per decade of the session's spend, a context bar a pair of classes over the window the model
that answered works in. Each leaf plants or scales what the store holds and reads back the step
the row landed on, so what moves a meter is the data and not the arithmetic written twice.
"""

import re
from typing import NamedTuple

import duckdb
from fastapi.testclient import TestClient

from hyphae.extract.pricing import CONTEXT_WINDOWS, SYNTHETIC_MODEL
from hyphae.view.app import build_app
from hyphae.view.nodes import (
    BAR_STEPS,
    STEPS,
    Kind,
    Preset,
)
from tests.conftest import MAIN, SPINE, SPINE_LEAF, SPINE_RUN
from tests.view.conftest import (
    Planter,
    bar,
    fields,
    inside,
    one,
    step,
    values,
)
from tests.view.nav_trees import (
    THREAD,
    candidates,
    node_url,
    weighed,
)


def test_a_row_badges_its_cost_only_where_it_has_a_share_to_draw(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The badge is a class on the row, stepped by the row's share of what the session spent.

    Rows that cost nothing of their own — a tool call, a compaction — carry no badge rather
    than an empty one, because a wash drawn at zero reads as a measurement.
    """
    sessions = [str(row[0]) for row in store.execute("SELECT id FROM sessions").fetchall()]
    (whole,) = one(
        store,
        "SELECT sum(cost_usd) FROM live_api_calls WHERE session_id = ?",
        [SPINE],
    )
    # The session is the basis, so a session that spent anything wears the deepest badge.
    spine = client.get(f"/session/{SPINE}").text
    assert "s10" in inside(spine, "data-nav-tree", f"session:{SPINE}", "class")[0].split()
    # Swept over every session under every preset rather than over the deepest session alone.
    # The rows that take their own share — the buckets, which are not rows of the store — are
    # gathered by a different builder under each preset, and are not all on one session's page.
    for session_id in sessions:
        badges: dict[str, tuple[str | None, frozenset[str]]] = {}
        for preset in Preset:
            html = client.get(f"/session/{session_id}", params={"nav": preset}).text
            for key in values(html, "data-nav-tree"):
                classes = inside(html, "data-nav-tree", key, "class")[0].split()
                steps = frozenset(n for n in classes if n.startswith("s") and n[1:].isdigit())
                cost = fields(html, "data-nav-tree", key).get("cost_usd")
                # Every row either shows what it cost with a badge behind it, or shows neither.
                assert bool(steps) == (cost is not None), key
                # And a preset decides which rows are drawn, never what one of them spent or how
                # much of the session that was: a badge that moved between presets is a share
                # taken against something other than the session.
                assert badges.setdefault(key, (cost, steps)) == (cost, steps), (key, preset)
    assert whole, "the session this reads has a spend to take shares of"


def test_a_cost_badge_steps_by_decade_so_three_orders_of_magnitude_deepen_it(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """Which step a share is drawn at, read at the top of the scale, the bottom, and between.

    A recorded session's rows do not span the scale, so the ladder itself — ten steps over
    three decades of share — is a rule no fixture exercises: every badge could be drawn a step
    too deep and the corpus would agree. The shares are planted instead, on the calls of one
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
            classes = inside(page, "data-nav-tree", f"{Kind.CALL}:{call_id}", "class")[0].split()
            assert step in classes, (cost, classes)


def test_a_cost_badge_deepens_at_every_step_and_washes_nothing_but_the_cost(
    client: TestClient,
) -> None:
    """What a step is drawn as: a warm wash behind the dollar value, deeper the dearer the node.

    The ladder itself is unchanged — the same ten classes `nodes.meter` has always put on a row
    — so what this reads is only what a step paints. Off the served stylesheet, because that is
    the one place it is decided: the markup carries the class whatever the wash does, and
    nothing in this tier can see a painted box.
    """
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.DOTALL)
    washes = {
        int(step): int(part)
        for step, part in re.findall(
            r'li\.node\.s(\d+) \[data-field="cost_usd"\] \{[^}]*--cost-wash: (\d+)%', style
        )
    }
    # One rule spends them, so the warm token is named once and every step is a share of it.
    assert re.findall(r"color-mix\(in srgb, var\(--hot\) var\(--cost-wash[^)]*\)", style)
    # Every step the ladder can hand a row is drawn, and `s0` — a row that spent nothing at all
    # — is not: a wash at the bottom of the scale would read as a measurement of nothing.
    assert sorted(washes) == list(range(1, STEPS + 1)), sorted(washes)
    # Deeper at every step, and never twice the same depth: two steps drawn alike are one step.
    assert list(washes.values()) == sorted(set(washes.values())), washes
    # And the wash lands on the cost alone. The row's own edge is left unpainted, which is what
    # the step used to take — a badge behind a number says the same thing without the width.
    assert not re.findall(r"li\.node\.s\d+ > a[ ,{]", style)


class Call(NamedTuple):
    """One recorded api call, as the context oracle below reads it out of the store."""

    api_call_id: str
    source: str
    turn_id: str | None
    model: str
    synthetic: bool
    # Where the call left the model's window, and how much of that the call itself put there:
    # everything it was billed for, and that less the cache it read. Restated here in the
    # test's own SQL rather than read off `analyze/macros.py`, so the two can disagree.
    fill: int
    added: int


def calls(store: duckdb.DuckDBPyConnection, session_id: str) -> list[Call]:
    """Every api call one session recorded, on every thread, in the order each thread made them."""
    return [
        Call(*row)
        for row in store.execute(
            "SELECT id, source, turn_id, model, synthetic,"
            " cache_read_tokens + cache_creation_tokens + input_tokens + output_tokens,"
            " cache_creation_tokens + input_tokens + output_tokens"
            ' FROM live_api_calls WHERE session_id = ? ORDER BY source, "index"',
            [session_id],
        ).fetchall()
    ]


def test_a_row_bars_the_context_it_left_against_the_window_its_model_answers_in(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Where each kind of node left the model's context window, read against the store's tokens.

    One session, read whole rather than swept: the spine records the four kinds that end on a
    window — a session, its turns, its runs, and the calls themselves — and the two that do not.
    What each row draws is two steps, the fill and the tip: where the window stood when the node
    ended, and how much of that the node put there.

    The expectation is built from `live_api_calls` here rather than from the columns the page
    reads, so a derivation that drifted in the NavTree's SQL has nothing to agree with. Built from
    the store rather than written down, so re-recording the fixture moves the oracle.
    """
    recorded = calls(store, SPINE)
    answered = [call for call in recorded if not call.synthetic]
    main = [call for call in answered if call.source == MAIN]
    page = client.get(f"/session/{SPINE}").text
    # A session reads the window its main thread was left in, and draws no tip: nothing came
    # before a session for it to have added anything to.
    assert bar(page, f"{Kind.SESSION}:{SPINE}") == (step(main[-1].fill, main[-1].model), None)
    # And the call it reads is not the last one the thread made. The spine ends on an interrupt
    # Claude Code wrote itself, which reports no tokens at all (`docs/schema.md`) — so a
    # derivation that took the thread's last call would open the session on an empty window.
    ended = [call for call in recorded if call.source == MAIN][-1]
    assert ended.synthetic and ended.fill == 0
    # Each turn draws where it left the window and what it put there: its fill, less the fill
    # the turn before it left behind. The first turn built the whole of what it holds.
    stood = 0
    for turn_id in dict.fromkeys(call.turn_id for call in main):
        last = [call for call in main if call.turn_id == turn_id][-1]
        assert bar(page, f"{Kind.TURN}:{turn_id}") == (
            step(last.fill, last.model),
            step(last.fill - stood, last.model),
        ), turn_id
        stood = last.fill
    # The turn the interrupt answered has no bar at all: no call under it says where the window
    # stood, and a bar drawn at nothing would say the window emptied.
    silent = {call.turn_id for call in recorded if call.synthetic} - {c.turn_id for c in main}
    assert silent
    for turn_id in silent:
        assert bar(page, f"{Kind.TURN}:{turn_id}") == (None, None), turn_id
    # A run reads the window of its own thread, and its tip is the whole of its fill: a run
    # starts on an empty window and builds all of what it holds while it runs.
    for run_id in (SPINE_RUN, SPINE_LEAF):
        ran = [call for call in answered if call.source == run_id]
        drawn = step(ran[-1].fill, ran[-1].model)
        assert bar(client.get(f"/session/{SPINE}/run/{run_id}").text, f"{Kind.RUN}:{run_id}") == (
            drawn,
            drawn,
        ), run_id
    # A call draws its own fill and the part of it the call itself sent — and the interrupt,
    # which went to no model, draws nothing. Read on each call's own page, where the level of
    # calls is the one open.
    for call in (found for found in recorded if found.source == MAIN):
        row = client.get(node_url(Kind.CALL, SPINE, MAIN, call.api_call_id)).text
        drawn = (
            (None, None)
            if call.synthetic
            else (step(call.fill, call.model), step(call.added, call.model))
        )
        assert bar(row, f"{Kind.CALL}:{call.api_call_id}") == drawn, call.api_call_id
        # And nothing under a call is barred: a tool call's tokens are its api call's, and a
        # compaction is not a call at all.
        for key in values(row, "data-nav-tree"):
            if key.startswith((f"{Kind.TOOL}:", f"{Kind.COMPACTION}:")):
                assert bar(row, key) == (None, None), key


def test_an_interrupt_and_another_threads_calls_move_no_bar_a_row_draws(
    client: TestClient, store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """A row's window is read off its own thread's answers, and off nothing else.

    Three rules meet here, one per level: a turn and a run read the last call under them that
    went to a model, and a session reads the last call of its *main* thread. No recorded
    session can tell any of the three from the rule that dropped its filter — no turn in the
    corpus mixes a model's answers with an interrupt, no run thread ends on one, and the main
    thread holds the highest call index in every session recorded. So the three shapes are
    planted and the page is read against itself: every bar it draws over them is the bar it
    drew without them.

    INVENTED arrangement of recorded rows: the interrupt Claude Code wrote into the spine is
    moved into the turn a model had already answered, a second one is cloned onto a run's own
    thread, and the other run's calls are renumbered past the main thread's last. Every token
    count, model and cost under them is the transcript's.
    """
    answered = one(
        store,
        "SELECT turn_id FROM live_api_calls WHERE session_id = ? AND source = ?"
        ' AND NOT synthetic ORDER BY "index" DESC LIMIT 1',
        [SPINE, MAIN],
    )[0]
    planted = plant(
        # The reader interrupted a turn a model had already answered twice, so the turn holds
        # both — and the interrupt is the last call in it.
        (
            "UPDATE api_calls SET turn_id = ? WHERE session_id = ? AND source = ? AND synthetic",
            [answered, SPINE, MAIN],
        ),
        # A run's thread ends the same way. Cloned from its own last call rather than invented,
        # so every column but the ones an interrupt reports differently is the store's shape:
        # a placeholder went to no model, so it names none and reports no tokens at all.
        (
            "INSERT INTO api_calls (SELECT c.* REPLACE (c.id || '-interrupt' AS id,"
            ' ? AS model, true AS synthetic, 1000000 AS "index", 0 AS input_tokens,'
            " 0 AS output_tokens, 0 AS cache_read_tokens, 0 AS cache_creation_tokens,"
            " 0 AS cache_5m_tokens, 0 AS cache_1h_tokens, 0.0 AS cost_usd)"
            " FROM (SELECT * FROM api_calls WHERE session_id = ? AND source = ?"
            ' ORDER BY "index" DESC LIMIT 1) c)',
            [SYNTHETIC_MODEL, SPINE, SPINE_RUN],
        ),
        # And the other run outlasts the main thread: an index counts a thread's own calls, so
        # a run that answered longer than the session's own thread carries the higher ones.
        (
            'UPDATE api_calls SET "index" = 1000000 + "index" WHERE session_id = ? AND source = ?',
            [SPINE, SPINE_LEAF],
        ),
    )
    paths = [
        f"/session/{SPINE}",
        f"/session/{SPINE}/run/{SPINE_RUN}",
        f"/session/{SPINE}/run/{SPINE_LEAF}",
    ]
    drawn: dict[str, dict[str, tuple[int | None, int | None]]] = {}
    with TestClient(build_app(planted)) as interrupted:
        for path in paths:
            page = client.get(path).text
            drawn[path] = {key: bar(page, key) for key in values(page, "data-nav-tree")}
            after = interrupted.get(path).text
            assert {key: bar(after, key) for key in drawn[path]} == drawn[path], path
    # And the rows the plant reached draw a bar at all: a sweep over rows that draw nothing
    # would agree with itself whatever a filter did.
    session = drawn[paths[0]]
    assert session[f"{Kind.SESSION}:{SPINE}"][0] is not None
    assert session[f"{Kind.TURN}:{answered}"] > (0, 0)
    for path, run_id in zip(paths[1:], (SPINE_RUN, SPINE_LEAF), strict=True):
        assert drawn[path][f"{Kind.RUN}:{run_id}"] > (0, 0), run_id


def test_a_model_we_hold_no_window_for_is_a_bar_the_nav_tree_does_not_draw(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """A window our table cannot name draws no bar, the way a price it lacks shows no cost.

    Every model the corpus records is in the table, so the gap is planted: the models Claude
    Code sends can gain a suffix — a larger window is asked for by an alias the reply does not
    echo — and a name we have not seen is a scale we would have to invent to draw.
    """
    path = plant(("UPDATE api_calls SET model = 'claude-mythos-9' WHERE session_id = ?", [SPINE]))
    with TestClient(build_app(path)) as unknown:
        page = unknown.get(f"/session/{SPINE}").text
        for key in values(page, "data-nav-tree"):
            assert bar(page, key) == (None, None), key
        # The row still says what it cost: the price is what the store recorded at extraction,
        # and only the bar is the table's to answer for.
        assert fields(page, "data-nav-tree", f"{Kind.SESSION}:{SPINE}")["cost_usd"]


def test_a_context_bar_fills_linearly_and_stops_at_a_full_window(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """Which step a fill is drawn at, at the bottom of the window, at the top, and past it.

    A recorded call fills half a window at most, so the top of the scale and the clamp above it
    are rules no fixture exercises — every bar could be drawn at twice its share and the corpus
    would agree. The tokens are planted instead, on the spine's own calls: each call is left
    with nothing but the input it sent, which the window counts twice over — a call's whole
    fill, and the whole of what it added — so one number is read back as both steps.
    """
    window = CONTEXT_WINDOWS[
        one(store, "SELECT model FROM live_api_calls WHERE session_id = ? LIMIT 1", [SPINE])[0]
    ]
    # A twentieth of the window, half of it, and three times it — the last of which is a call
    # the store can hold and the bar cannot draw past its own end.
    ladder = {window // BAR_STEPS: 1, window // 2: BAR_STEPS // 2, window * 3: BAR_STEPS}
    at = [
        row[0]
        for row in store.execute(
            "SELECT id FROM live_api_calls WHERE session_id = ? AND source = ? AND NOT synthetic"
            ' ORDER BY "index" LIMIT 3',
            [SPINE, MAIN],
        ).fetchall()
    ]
    assert len(at) == len(ladder), "three calls to hang the scale on"
    path = plant(
        *(
            (
                "UPDATE api_calls SET input_tokens = ?, cache_read_tokens = 0,"
                " cache_creation_tokens = 0, output_tokens = 0 WHERE session_id = ? AND id = ?",
                [fill, SPINE, call_id],
            )
            for fill, call_id in zip(ladder, at, strict=True)
        )
    )
    with TestClient(build_app(path)) as scaled:
        for (fill, drawn), call_id in zip(ladder.items(), at, strict=True):
            page = scaled.get(node_url(Kind.CALL, SPINE, MAIN, str(call_id))).text
            assert bar(page, f"{Kind.CALL}:{call_id}") == (drawn, drawn), (fill, call_id)


def test_a_context_bar_is_drawn_by_two_families_of_class_one_rule_spends(
    client: TestClient,
) -> None:
    """What a pair of steps is drawn as: a track, the fill, and the tip left bright on top.

    The policy forbids the inline width that would carry a percentage, so the two numbers ride
    in as classes and the stylesheet turns them back into widths. That makes the ladder a thing
    this tier can read: every step the markup can carry has to be a width here, or a row lands
    on a class that draws nothing and the bar quietly reads as empty.
    """
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.DOTALL)
    steps = list(range(BAR_STEPS + 1))
    for family, prop in (("f", "--ctx-fill"), ("t", "--ctx-added")):
        widths = {
            int(step): int(width)
            for step, width in re.findall(rf"li\.node\.{family}(\d+) \{{ {prop}: (\d+)%", style)
        }
        # Both families run the whole ladder, bottom to top: a fill of nothing is a drawn track
        # with nothing in it, and a full window is the bar's own end.
        assert sorted(widths) == steps, (family, sorted(widths))
        # And they are linear, evenly spaced from empty to full — the bar's whole claim is that
        # half of it is half a window.
        assert [widths[n] for n in steps] == [n * 100 // BAR_STEPS for n in steps], widths
    # One rule spends both, layering the track under the fill under the tip. Three layers and
    # not two, because the bright part is what is left of the fill once the part that was
    # already there is drawn over it.
    ((selector, body),) = re.findall(r"(li\.node:is\([^)]*\)) > a \{([^}]*)\}", style)
    assert "calc(var(--ctx-fill) - var(--ctx-added, 0%)) 3px" in body, body
    assert re.search(r"var\(--ctx-fill\) 3px,\s*100% 3px", body), body
    assert re.findall(r"var\(--(dim|mark|line)\)", body) == [
        "dim",
        "dim",
        "mark",
        "mark",
        "line",
        "line",
    ], body
    # Every fill class the markup can carry is named by that rule. A step outside it would set
    # a width nothing reads and draw no track at all.
    assert sorted(int(step) for step in re.findall(r"\.f(\d+)", selector)) == steps, selector
    # The tip alone draws nothing: a row that names what it added without naming where it left
    # the window has no bar to put the tip in, and the fill families are what carry the track.
    assert not re.findall(r"li\.node:is\([^)]*\.t\d+", style), style


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
    where its own row is on the open path whichever preset the reader picked.
    """
    # Keyed by session as well as by row, for the reason the titles are.
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
            for key in values(page, "data-nav-tree"):
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
