"""The context bar a NavTree row draws: how full the model's window was when the node ended.

Read back off the rendered row rather than computed beside it — a bar is a set of nested edges
over the window the model that answered works in, so what moves one is the data and not
arithmetic written twice. Each leaf plants or scales what the store holds and reads back the
step the row landed on. The other meter a row draws is the cost badge
(`test_nav_tree__badges.py`).
"""

import re
from typing import NamedTuple

import duckdb
from fastapi.testclient import TestClient

from hyphae.extract.pricing import MODELS, SYNTHETIC_MODEL
from hyphae.view.app import build_app
from hyphae.view.nodes import (
    BAR_STEPS,
    Kind,
)
from tests.conftest import (
    ANCESTOR,
    COMPACTED,
    COMPACTED_RUN,
    MAIN,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
)
from tests.view.conftest import (
    Bar,
    Planter,
    bar,
    fields,
    marked,
    one,
    step,
    values,
    viewer_css,
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
    # What the call sent before it answered: the cache it read and the input it wrote. The
    # first main-thread call's is the context the session opened on, which is the ground every
    # turn's growth is drawn over.
    sent: int


def calls(store: duckdb.DuckDBPyConnection, session_id: str) -> list[Call]:
    """Every api call one session recorded, on every thread, in the order each thread made them."""
    return [
        Call(*row)
        for row in store.execute(
            "SELECT id, source, turn_id, model, synthetic,"
            " cache_read_tokens + cache_creation_tokens + input_tokens + output_tokens,"
            " cache_creation_tokens + input_tokens + output_tokens,"
            " cache_read_tokens + cache_creation_tokens + input_tokens"
            ' FROM live_api_calls WHERE session_id = ? ORDER BY source, "index"',
            [session_id],
        ).fetchall()
    ]


def bands(fill: int, prior: int, base: int | None, model: str) -> Bar:
    """The three edges a bar draws, from the tokens each band stands for.

    The oracle for every bar leaf below, and the one place the nesting rule is restated: a
    band is a prefix of the one that holds it, so an edge is held at the fill above it and at
    the base below it. Written here rather than imported, so an implementation that let a
    band run past its holder has nothing to agree with.
    """
    top = step(fill, model)
    assert top is not None, model
    grounded = min(step(base, model) or 0, top) if base is not None else None
    return Bar(top, max(min(step(prior, model) or 0, top), grounded or 0), grounded)


def test_a_row_bars_the_context_it_left_against_the_window_its_model_answers_in(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Where each kind of node left the model's context window, read against the store's tokens.

    One session, read whole rather than swept: the spine records the four kinds that end on a
    window — a session, its turns, its runs, and the calls themselves — and the two that do not.
    What each row draws is where the window stood when the node ended, and where inside that
    the node's own share begins; a turn draws a third edge, the context the session opened on.

    The expectation is built from `live_api_calls` here rather than from the columns the page
    reads, so a derivation that drifted in the NavTree's SQL has nothing to agree with. Built from
    the store rather than written down, so re-recording the fixture moves the oracle.
    """
    recorded = calls(store, SPINE)
    answered = [call for call in recorded if not call.synthetic]
    main = [call for call in answered if call.source == MAIN]
    page = client.get(f"/session/{SPINE}").text
    # A session reads the window its main thread was left in, and draws that alone: nothing came
    # before a session for it to have added anything to, and no prompt it stands its growth over.
    assert bar(page, f"{Kind.SESSION}:{SPINE}") == Bar(
        step(main[-1].fill, main[-1].model), None, None
    )
    # And the call it reads is not the last one the thread made. The spine ends on an interrupt
    # Claude Code wrote itself, which reports no tokens at all (`docs/schema.md`) — so a
    # derivation that took the thread's last call would open the session on an empty window.
    ended = [call for call in recorded if call.source == MAIN][-1]
    assert ended.synthetic and ended.fill == 0
    # Each turn draws three edges: where it left the window, where the turn before it left one —
    # which is where its own growth begins — and the context the session opened on, which is
    # what the first main-thread call sent before anything had been said.
    stood = 0
    for turn_id in dict.fromkeys(call.turn_id for call in main):
        last = [call for call in main if call.turn_id == turn_id][-1]
        assert bar(page, f"{Kind.TURN}:{turn_id}") == bands(
            last.fill, stood, main[0].sent, last.model
        ), turn_id
        stood = last.fill
    # The turn the interrupt answered has no bar at all: no call under it says where the window
    # stood, and a bar drawn at nothing would say the window emptied.
    silent = {call.turn_id for call in recorded if call.synthetic} - {c.turn_id for c in main}
    assert silent
    for turn_id in silent:
        assert bar(page, f"{Kind.TURN}:{turn_id}") == Bar(None, None, None), turn_id
    # A run reads the window of its own thread, and all of it is the run's own: a run starts on
    # an empty window and builds what it holds while it runs. No base band — the prompt the
    # session opened on is the main thread's, and a run's growth is measured from nothing.
    for run_id in (SPINE_RUN, SPINE_LEAF):
        ran = [call for call in answered if call.source == run_id]
        drawn = bands(ran[-1].fill, 0, None, ran[-1].model)
        assert bar(client.get(f"/session/{SPINE}/run/{run_id}").text, f"{Kind.RUN}:{run_id}") == (
            drawn
        ), run_id
    # A call draws its own fill and the part of it that was already there — and the interrupt,
    # which went to no model, draws nothing. Read on each call's own page, where the level of
    # calls is the one open.
    for call in (found for found in recorded if found.source == MAIN):
        row = client.get(node_url(Kind.CALL, SPINE, MAIN, call.api_call_id)).text
        drawn = (
            Bar(None, None, None)
            if call.synthetic
            else bands(call.fill, call.fill - call.added, None, call.model)
        )
        assert bar(row, f"{Kind.CALL}:{call.api_call_id}") == drawn, call.api_call_id
        # And nothing under a call is barred: a tool call's tokens are its api call's.
        for key in values(row, "data-nav-tree"):
            if key.startswith(f"{Kind.TOOL}:"):
                assert bar(row, key) == Bar(None, None, None), key


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
    drawn: dict[str, dict[str, Bar]] = {}
    with TestClient(build_app(planted)) as interrupted:
        for path in paths:
            page = client.get(path).text
            drawn[path] = {key: bar(page, key) for key in values(page, "data-nav-tree")}
            after = interrupted.get(path).text
            assert {key: bar(after, key) for key in drawn[path]} == drawn[path], path
    # And the rows the plant reached draw a bar at all: a sweep over rows that draw nothing
    # would agree with itself whatever a filter did.
    session = drawn[paths[0]]
    assert session[f"{Kind.SESSION}:{SPINE}"].fill is not None
    assert session[f"{Kind.TURN}:{answered}"].fill
    for path, run_id in zip(paths[1:], (SPINE_RUN, SPINE_LEAF), strict=True):
        assert drawn[path][f"{Kind.RUN}:{run_id}"].fill, run_id


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
            assert bar(page, key) == Bar(None, None, None), key
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
    with nothing but the input it sent, which is the whole of what it added — so the fill is
    read back against a band of its own that begins at nothing.
    """
    model = one(store, "SELECT model FROM live_api_calls WHERE session_id = ? LIMIT 1", [SPINE])[0]
    window = MODELS[model].context_window
    assert window is not None, model
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
            assert bar(page, f"{Kind.CALL}:{call_id}") == Bar(drawn, 0, None), (fill, call_id)


def test_a_context_bar_is_drawn_by_three_families_of_class_one_rule_spends(
    client: TestClient,
) -> None:
    """What three edges are drawn as: the fill, and the two bands nested inside it.

    The policy forbids the inline width that would carry a percentage, so the numbers ride in
    as classes and the stylesheet turns them back into widths. That makes the ladder a thing
    this tier can read: every step the markup can carry has to be a width here, or a row lands
    on a class that draws nothing and the bar quietly reads as empty.
    """
    style = re.sub(r"/\*.*?\*/", "", viewer_css(client), flags=re.DOTALL)
    steps = list(range(BAR_STEPS + 1))
    for family, prop in (("f", "--edge-fill"), ("p", "--edge-prior"), ("b", "--edge-base")):
        widths = {
            int(step): int(width)
            for step, width in re.findall(rf"li\.node\.{family}(\d+) \{{ {prop}: (\d+)%", style)
        }
        # Every family runs the whole ladder, bottom to top: a band of nothing draws nothing at
        # all, and a full window is the bar's own end.
        assert sorted(widths) == steps, (family, sorted(widths))
        # And they are linear, evenly spaced from empty to full — the bar's whole claim is that
        # half of it is half a window.
        assert [widths[n] for n in steps] == [n * 100 // BAR_STEPS for n in steps], widths
    # One rule spends all three, layering the fill under the two bands that stand inside it.
    # Each band is a prefix drawn over the one below, so the ground a reader sees between two
    # edges is the band the second one opens. Nothing is drawn under the widest of them: a bar
    # is only what the row holds, and an empty track would read as a window that emptied.
    selector, body = one_of(
        [
            (found, rule)
            for found, rule in re.findall(r"(li\.node:is\([^)]*\)) > a \{([^}]*)\}", style)
            if "background-image" in rule
        ]
    )
    thick = r"var\(--ctx-height\)"
    assert re.search(
        rf"var\(--edge-base, 0%\) {thick},\s*var\(--edge-prior, 0%\) {thick},"
        rf"\s*var\(--edge-fill, 0%\) {thick};",
        body,
    ), body
    # And the number that name stands for. How thick a bar reads is eyeballed on the gallery the
    # way its colours are (`.claude/rules/viewer-ui.md`), so this pins the value rather than
    # justifying it: nothing else in either tier fails when the bar changes height.
    assert re.search(r"--ctx-height: 3px;", style), style
    # Widths are edges and colours are bands, so the two vocabularies never collide. Each band
    # is a role a kind may take over, named in the order the layers stack: the context the
    # session opened on, under what stood before the node, under the node's own share.
    assert re.findall(r"linear-gradient\(var\((--[\w-]+)", body) == [
        "--band-base",
        "--band-past",
        "--band-added",
    ], body
    # A role is a property with its palette token as its default rather than a colour written
    # into the layer, so a kind override is one line and an unclaimed band paints itself.
    for role in ("base", "past", "added"):
        assert body.count(f"var(--band-{role}, var(--ctx-{role}))") == 2, (role, body)
    # Every fill class the markup can carry is named by that rule, and so is the mark a run
    # whose thread compacted carries: a step outside it would set a width nothing reads.
    assert sorted(int(step) for step in re.findall(r"\.f(\d+)", selector)) == steps, selector
    assert ".maxed" in selector, selector
    # A band alone draws nothing: a row that names where its own share begins without naming
    # where it left the window has no bar to put the band in, so the fill is what mints one.
    assert not re.findall(r"li\.node:is\([^)]*\.[pb]\d+", style), style


def test_a_thread_takes_one_gray_a_compaction_the_green_and_a_maxed_run_the_whole_bar(
    client: TestClient,
) -> None:
    """What a kind repaints, and the one row that is drawn full whatever it holds.

    A kind is keyed on the class the row already carries — a second class saying `run` on a run
    would be eight bytes a row for what the markup says already. A thread is one band and not
    the ramp: nothing stood before a run and a session has nothing to have added to, so a
    session and a run take one gray over both the bands a turn would draw. What no kind can say
    is that a run's own thread compacted, and that is still the one mark the bar mints — but it
    mints a width now and not a colour, because the red pill on the same row says why.

    The colours themselves are eyeballed on the gallery (`.claude/rules/viewer-ui.md`); what
    this holds is which band each kind takes over, and that every colour the bar spends is
    defined in both schemes — a token a dark page leaves unset is a band that vanishes for half
    the readers.
    """
    style = re.sub(r"/\*.*?\*/", "", viewer_css(client), flags=re.DOTALL)
    # What an unclaimed band paints itself in: the ramp, read off the paint rule's own defaults
    # so the claims below are about a kind departing from it rather than about three names.
    ramp = set(re.findall(r"var\(--band-\w+, var\((--[\w-]+)\)\)", style))
    # A session and a run are repainted by one rule, in one token, over both bands. A thread
    # given two would read as a ramp it has no second number to justify.
    thread = one_of(re.findall(r"li\.node:is\(\.session, \.run\) > a \{([^}]*)\}", style))
    bands = dict(re.findall(r"--band-(past|added): var\((--[\w-]+)\)", thread))
    assert bands.keys() == {"past", "added"}, thread
    (gray,) = set(bands.values())
    assert gray not in ramp, (gray, ramp)
    # A compaction repaints its tip alone, in a token of its own: what it gave back is the one
    # measurement here that is good news, and what stood before it is still the ramp.
    freed = one_of(re.findall(r"li\.node\.compaction > a \{([^}]*)\}", style))
    (given_back,) = re.findall(r"--band-added: var\((--[\w-]+)\)", freed)
    assert "--band-past" not in freed, freed
    assert given_back not in ramp | {gray}, (given_back, ramp, gray)
    # A maxed row is the whole bar and nothing more: a run that filled its window says so at
    # full width, whatever the last call of its thread happened to leave behind, in the gray it
    # already wears. The fill is the only edge it has to force — only a run is ever maxed, and a
    # run's own share is its whole fill, so the ladder already put its two inner edges at zero.
    maxed = one_of(re.findall(r"li\.node\.maxed > a \{([^}]*)\}", style))
    assert re.findall(r"--[\w-]+:[^;]+", maxed) == ["--edge-fill: 100%"], maxed
    # And every colour the bar spends is defined for both schemes, light and dark alike.
    dark = one_of(re.findall(r"@media \(prefers-color-scheme: dark\) \{([^}]*)\}", style))
    for token in ramp | {gray, given_back}:
        assert re.search(rf"^\s*{token}:\s+#", style, re.MULTILINE), token
        assert f"{token}: #" in dark, (token, dark)


def one_of[T](found: list[T]) -> T:
    """The single match a stylesheet leaf reads, so a second one is a failure and not a pick."""
    (only,) = found
    return only


def test_every_band_a_row_draws_nests_inside_the_one_that_holds_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """No band runs past its holder, on any row of any session the corpus records.

    The bar's whole grammar in one sweep: three edges drawn as prefixes of one another, so a
    pair out of order is a band drawn backwards — the base prompt reaching past the
    conversation that holds it, or a node's own share starting after the window ended. A sweep
    rather than a spot check, because the arithmetic that orders them is three clamps and each
    one is a rule some row of some session is the only witness to.
    """
    sessions = [str(row[0]) for row in store.execute("SELECT id FROM sessions").fetchall()]
    banded = 0
    ramped = []
    for session_id in sessions:
        page = client.get(f"/session/{session_id}").text
        for key in values(page, "data-nav-tree"):
            drawn = bar(page, key)
            if drawn.fill is None:
                # A row with no fill draws no bar at all, so it names no band either.
                assert drawn == Bar(None, None, None), (session_id, key)
                continue
            edges = [edge for edge in drawn if edge is not None]
            assert edges == sorted(edges, reverse=True), (session_id, key, drawn)
            assert drawn.fill <= BAR_STEPS, (session_id, key, drawn)
            banded += len(edges)
            # A turn whose three edges are all apart, and whose innermost is off the left, draws
            # all three bands at once with ground under each: the opening context in navy, the
            # conversation over it in medium, its own growth bright at the tip.
            ramp = 0 < (drawn.base or 0) < (drawn.prior or 0) < drawn.fill
            if ramp and key.startswith(f"{Kind.TURN}:"):
                ramped.append((session_id, key, drawn))
        # And no row carries a width of its own: the classes are the only hook there is, and a
        # `style` attribute anywhere under a row is markup the policy would refuse to paint
        # (`tests/view/test_app__headers.py`).
        assert not re.findall(r'data-nav-tree="[^"]*"[^>]*style="', page), session_id
    assert banded, "no row in the corpus drew a band"
    # And the corpus can show the ramp the palette was chosen for. Nothing above forces it:
    # every clamp here is satisfied by a row drawing two bands or one, and the corpus drew no
    # three-band turn at all until `spine/` kept the call that ends the turn before its last
    # one, which is the conversation that turn stands on (`tests/fixtures/spine/README.md`).
    # Without one the gallery leads a reader to no page where the three blues can be read
    # against each other, and a colour nobody can look at is a colour nothing defends.
    assert ramped, "no turn in the corpus draws all three bands"


def test_a_turns_bar_stands_on_the_context_the_session_opened_on(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The base band: the prompt, the instructions and the tools a session begins with.

    What the first main-thread call sent before a word had been said — the ground every turn's
    growth is drawn over, so a reader sees a conversation filling the window rather than a
    window that was already two thirds full when it started. A session constant: every turn of
    the session draws the same edge, whatever else its own bar says.
    """
    opening = one(
        store,
        "SELECT cache_read_tokens + cache_creation_tokens + input_tokens, model"
        " FROM live_api_calls WHERE session_id = ? AND source = ? AND NOT synthetic"
        ' ORDER BY "index" LIMIT 1',
        [SPINE, MAIN],
    )
    page = client.get(f"/session/{SPINE}").text
    drawn = {
        key: bar(page, key).base
        for key in values(page, "data-nav-tree")
        if key.startswith(f"{Kind.TURN}:") and bar(page, key).fill is not None
    }
    assert len(drawn) > 1, "one turn cannot show a constant"
    # One value across the page, and it is the opening context stepped against the window.
    assert set(drawn.values()) == {step(opening[0], opening[1])}, drawn
    # The base is what the first call the recording holds sent, whatever was said before it.
    # `ANCESTOR` is the session `RESUME` resumed, and its recording opens partway into a
    # conversation — on more context than the window holds. Its turn is drawn base to tip: a
    # bar with no room of its own. The design accepts that reading rather than an ideal one,
    # because an inherited context is still context the turn is working inside.
    inherited = one(
        store,
        "SELECT cache_read_tokens + cache_creation_tokens + input_tokens, model"
        " FROM live_api_calls WHERE session_id = ? AND source = ? AND NOT synthetic"
        ' ORDER BY "index" LIMIT 1',
        [ANCESTOR, MAIN],
    )
    window = MODELS[inherited[1]].context_window
    assert window is not None and inherited[0] > window, inherited
    resumed = client.get(f"/session/{ANCESTOR}").text
    turns = [
        bar(resumed, key)
        for key in values(resumed, "data-nav-tree")
        if key.startswith(f"{Kind.TURN}:")
    ]
    assert turns, "the resumed session draws no turn"
    for band in turns:
        assert band.base == band.fill and band.fill, band


def test_a_turn_that_gave_the_window_back_draws_no_band_of_its_own(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """A turn that ends on less than the turn before it opens no band, rather than a wrapped one.

    A compaction inside a turn leaves the window below where the turn before it stood, and the
    delta a bar would draw is negative. No recorded session holds one — the corpus's five
    compactions all sit outside a turn — so the drop is planted, on the spine's own calls:
    the first turn is left holding a window nothing after it reaches.

    INVENTED token counts on recorded rows. What the calls said, cost and answered on is the
    transcript's; only the cache the first turn read is moved, to a number the turns after it
    cannot climb back to.
    """
    first, model = one(
        store,
        "SELECT t.id, max(c.model) FROM live_turns t JOIN live_api_calls c"
        " ON c.session_id = t.session_id AND c.source = t.source AND c.turn_id = t.id"
        ' WHERE t.session_id = ? AND t.source = ? GROUP BY t.id, t."index" ORDER BY t."index"'
        " LIMIT 1",
        [SPINE, MAIN],
    )
    window = MODELS[model].context_window
    assert window is not None, model
    path = plant(
        (
            "UPDATE api_calls SET cache_read_tokens = ? WHERE session_id = ? AND turn_id = ?",
            [window, SPINE, first],
        )
    )
    with TestClient(build_app(path)) as given:
        page = given.get(f"/session/{SPINE}").text
        turns = [key for key in values(page, "data-nav-tree") if key.startswith(f"{Kind.TURN}:")]
        # The turn that was raised is full, and every turn after it draws a bar whose own band
        # is empty: it ends where it began, because what it added was given back before it ran.
        assert bar(page, f"{Kind.TURN}:{first}").fill == BAR_STEPS
        after = [bar(page, key) for key in turns if key != f"{Kind.TURN}:{first}"]
        drawn = [drawn for drawn in after if drawn.fill is not None]
        assert drawn, "no turn after the plant draws a bar"
        for band in drawn:
            assert band.prior == band.fill, band


def compactions(
    store: duckdb.DuckDBPyConnection, session_id: str
) -> list[tuple[str, str, int, int, str]]:
    """Every compaction of one session, with the model whose window the bar is drawn against.

    The window comes off the thread and not the session: the nearest call of the same source at
    or before the boundary, else the first after it. Restated in the test's own SQL, so the
    query the page reads has something to disagree with.
    """
    return store.execute(
        "SELECT k.id, k.source, k.pre_tokens, k.post_tokens,"
        " (SELECT coalesce("
        "     max_by(c.model, c.started_at) FILTER (c.started_at <= k.timestamp),"
        "     min_by(c.model, c.started_at) FILTER (c.started_at > k.timestamp))"
        "  FROM live_api_calls c WHERE c.session_id = k.session_id AND c.source = k.source"
        "    AND NOT c.synthetic) AS model"
        " FROM live_compactions k WHERE k.session_id = ? ORDER BY k.timestamp",
        [session_id],
    ).fetchall()


def test_a_compaction_bars_what_it_freed_between_the_two_fills_it_records(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The ⊟ row's bar: dim up to where the thread was left, and green up to where it stood.

    The one bar drawn from a row's own columns rather than from the calls under it — a
    compaction records the fill either side of itself and no model, so the window it is drawn
    against is the thread's nearest answered call. Both of the session's main-thread boundaries
    and the one its run hit, each read on the page that opens its level.
    """
    recorded = compactions(store, COMPACTED)
    assert len(recorded) == 3, recorded
    page = client.get(f"/session/{COMPACTED}").text
    for compaction_id, source, pre, post, model in recorded:
        # A boundary inside a turn is that turn's child, so it is read on the turn's page; the
        # main thread of this session recorded no prompt at all, and its two sit at the level.
        turn_id = store.execute(
            "SELECT t.id FROM live_turns t, live_compactions k WHERE k.id = ?"
            " AND t.session_id = k.session_id AND t.source = k.source"
            " AND k.timestamp >= t.started_at AND k.timestamp < t.ended_at",
            [compaction_id],
        ).fetchone()
        opened = (
            page
            if turn_id is None
            else client.get(node_url(Kind.TURN, COMPACTED, source, turn_id[0])).text
        )
        # The fill it was compacted at, and the fill it was left on: what stands between them
        # is the context the boundary gave back, which is the band the row draws green.
        assert bar(opened, f"{Kind.COMPACTION}:{compaction_id}") == bands(pre, post, None, model), (
            compaction_id
        )
        assert fields(opened, "data-nav-tree", f"{Kind.COMPACTION}:{compaction_id}")["title"]
    # The run's boundary is the one drawn against a window its session's main thread does not
    # name: it answered on a model of its own (`tests/fixtures/compaction/README.md`).
    assert {row[4] for row in recorded} == {"claude-fable-5", "claude-opus-4-8"}


def test_a_compaction_whose_thread_names_no_window_draws_no_bar(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """A boundary on a thread that answered nothing is a row with its facts and no bar.

    `compactions` records two fills and no model, so the scale comes from the thread's calls —
    and a thread that made none, or made them on a model our table holds no window for, gives
    the bar no denominator. Drawn at nothing it would read as a window that emptied, so it is
    not drawn. Planted, because every recorded thread that compacted also answered: the
    session's calls are dropped and its boundaries kept.
    """
    path = plant(("DELETE FROM api_calls WHERE session_id = ?", [COMPACTED]))
    with TestClient(build_app(path)) as unpriced:
        page = unpriced.get(f"/session/{COMPACTED}").text
        keys = [
            key for key in values(page, "data-nav-tree") if key.startswith(f"{Kind.COMPACTION}")
        ]
        assert keys, "the boundaries still stand on the page"
        for key in keys:
            assert bar(page, key) == Bar(None, None, None), key
            # The row is still a row: what a compaction is, and what triggered it, are its own.
            assert fields(page, "data-nav-tree", key)["title"]


def test_a_run_whose_own_thread_compacted_is_drawn_full(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The one warning the NavTree draws: a subagent that ran its own window out.

    A run's thread compacting is a fact about the run and not about the call that spawned it,
    so the row says so at full width whatever its last call left behind — the reader looking
    for why a run's answer thinned out has one place to see it.
    """
    page = client.get(f"/session/{COMPACTED}/run/{COMPACTED_RUN}").text
    assert marked(page, f"{Kind.RUN}:{COMPACTED_RUN}", "maxed")
    # The store agrees: the mark is drawn off the run's own thread having a boundary on it.
    assert one(
        store,
        "SELECT count(*) FROM live_compactions WHERE session_id = ? AND source = ?",
        [COMPACTED, COMPACTED_RUN],
    ) == (1,)
    # And a run whose thread held out carries no mark. Read on its own page, the way the run
    # above is: the two rows are the same kind, drawn by the same builder.
    spine = client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
    assert not marked(spine, f"{Kind.RUN}:{SPINE_RUN}", "maxed")
    assert one(
        store,
        "SELECT count(*) FROM live_compactions WHERE session_id = ? AND source = ?",
        [SPINE, SPINE_RUN],
    ) == (0,)
