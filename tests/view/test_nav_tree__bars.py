"""The context bar a NavTree row draws: how full the model's window was when the node ended.

Read back off the rendered row rather than computed beside it — a bar is a step of the window
the model that answered works in, so what moves one is the data and not arithmetic written
twice. Each leaf plants or scales what the store holds and reads back the step the row landed
on. The other meter a row draws is the cost badge (`test_nav_tree__badges.py`).
"""

import re
from typing import NamedTuple

import duckdb
from fastapi.testclient import TestClient

from hyphae.extract.pricing import CONTEXT_WINDOWS, SYNTHETIC_MODEL
from hyphae.view.app import build_app
from hyphae.view.nodes import BAR_STEPS, Kind
from tests.conftest import MAIN, SPINE, SPINE_LEAF, SPINE_RUN
from tests.view.conftest import (
    Planter,
    bar,
    fields,
    one,
    step,
    values,
)
from tests.view.nav_trees import node_url


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
