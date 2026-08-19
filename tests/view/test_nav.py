"""The session map: the nav fragment a session page loads beside its timeline.

The map is its own response — the page it lands on has no room for it (`view/bounds.py`) — so
every leaf here fetches `/fragment/nav/{session_id}` and reads the nodes it rendered.
Expectations come from the store the app is serving: which turns a session holds, which runs
hang off them, and what each of them cost.

Two attributes carry the tree. `data-nav` marks a node *and everything under it*, which is
what a nesting assertion reads; `data-node` marks the node's own row, so a turn's cost is not
read as its cost plus its runs'.
"""

import math
import re

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view import bounds
from aiobserve.view import format as fmt
from aiobserve.view.app import build_app
from aiobserve.view.format import ABSENT
from tests.conftest import (
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    FORK_RUN,
    MODEL_ONLY,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
    TEAMMATE,
    TEAMMATE_RUN,
)
from tests.view.conftest import MISSING, Planter, fields, inside, one, values

# The turn `SPINE` spawned its `claude` run in, third of the four its main thread holds — the
# one turn of the corpus whose node carries a run node that carries a run node of its own.
SPINE_CHIPPED_TURN = "818588ad-3849-48fe-a546-573163768e04"


def nav(client: TestClient, session_id: str, **params: int) -> str:
    """The map one session's page loads, at the window and the size a request asks for."""
    response = client.get(f"/fragment/nav/{session_id}", params=params)
    assert response.status_code == 200, response.text[:200]
    return response.text


def main_turns(store: duckdb.DuckDBPyConnection, session_id: str) -> list[tuple[str, int]]:
    """One session's main-thread turns and their indexes, in the order they ran."""
    return [
        (str(row[0]), int(row[1]))
        for row in store.execute(
            'SELECT id, "index" FROM live_turns'
            " WHERE session_id = ? AND source = 'main' ORDER BY \"index\"",
            [session_id],
        ).fetchall()
    ]


def spend(store: duckdb.DuckDBPyConnection, session_id: str) -> dict[str, float]:
    """What each of a session's main-thread turns spent, as the store adds it up."""
    return {
        str(row[0]): float(row[1])
        for row in store.execute(
            "SELECT turn_id, round(sum(cost_usd), 4) FROM live_api_calls"
            " WHERE session_id = ? AND source = 'main' AND turn_id IS NOT NULL GROUP BY turn_id",
            [session_id],
        ).fetchall()
    }


def runs_of(store: duckdb.DuckDBPyConnection, session_id: str) -> set[str]:
    return {
        str(row[0])
        for row in store.execute(
            "SELECT id FROM live_agent_runs WHERE session_id = ?", [session_id]
        ).fetchall()
    }


def between(fragment: str, outer: str, inner: str) -> str:
    """The markup between one node's own tag and the node nested under it."""
    start = fragment.index(f'data-nav="{outer}"')
    return fragment[start : fragment.index(f'data-nav="{inner}"', start)]


def test_the_map_holds_a_node_for_every_turn_of_the_session_and_the_runs_under_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The map is the whole session's main thread, not the page's window of it.

    A map of the turns already on screen would say nothing a reader cannot see, so the
    fragment reads its own unpaged query. The runs hang where the page hangs them: a run
    under the turn that spawned it, and a run spawned inside a run under that run.
    """
    fragment = nav(client, SPINE)
    turns = [turn_id for turn_id, _ in main_turns(store, SPINE)]
    # Every main-thread turn is a top-level node, in the order the thread ran them...
    assert [node for node in values(fragment, "data-nav") if node in turns] == turns
    # ...the run a turn spawned hangs under that turn, and the run *it* spawned under it...
    assert inside(fragment, "data-nav", SPINE_CHIPPED_TURN, "data-nav") == [
        SPINE_CHIPPED_TURN,
        SPINE_RUN,
        SPINE_LEAF,
    ]
    assert inside(fragment, "data-nav", SPINE_RUN, "data-nav") == [SPINE_RUN, SPINE_LEAF]
    # ...and nothing else is a node: the grain is turns and runs, never api calls.
    assert set(values(fragment, "data-nav")) == set(turns) | runs_of(store, SPINE)


def test_every_run_the_session_holds_is_in_the_map_exactly_once(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Across the corpus, every agent run is in its session's map once — no more, no less.

    Two rules make it true: a run hangs off the turn that spawned it, and a run no turn
    claims hangs in the tail group. The corpus decides the cases — one session's orphan has
    no spawning call at all, another's runs were spawned by calls under no turn — so a map
    that dropped either fails here rather than in a reader's head.
    """
    for session_id in [str(row[0]) for row in store.execute("SELECT id FROM sessions").fetchall()]:
        nodes = values(nav(client, session_id), "data-nav")
        assert len(nodes) == len(set(nodes)), session_id
        assert runs_of(store, session_id) <= set(nodes), session_id


def test_a_runs_own_children_arrive_closed_and_a_turns_runs_do_not(client: TestClient) -> None:
    """The expansion rule: turns and their direct runs are visible, deeper runs are folded.

    `SPINE`'s recorded two-level forest is the case — a `claude` run under a turn, and an
    `Explore` under that — so the fold is over a real nesting rather than a staged one.
    """
    fragment = nav(client, SPINE)
    # The run under a turn is on the page as it stands: nothing to open between the two...
    assert "<details" not in between(fragment, SPINE_CHIPPED_TURN, SPINE_RUN)
    # ...and the run under *that* run sits behind a `details` a reader has to open, which
    # arrives closed because nothing marks it open.
    folds = re.findall(r"<details[^>]*>", between(fragment, SPINE_RUN, SPINE_LEAF))
    assert len(folds) == 1 and " open" not in folds[0]


def test_a_session_that_spawned_nothing_is_a_plain_list_of_turns(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A session with no runs has nothing to fold, so its map is a list and says so."""
    assert not runs_of(store, MODEL_ONLY)
    fragment = nav(client, MODEL_ONLY)
    assert values(fragment, "data-nav") == [turn for turn, _ in main_turns(store, MODEL_ONLY)]
    assert "<details" not in fragment


def test_an_in_window_turn_anchors_into_the_page_and_the_rest_link_to_their_own(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A node goes to its turn: down the page when the turn is on it, to the page that starts
    with that turn when it is not.

    At one turn a page, three of `SPINE`'s four are off the window — so the second kind of
    href is most of what the map is made of, and it is followed here rather than matched as
    a string.
    """
    turns = main_turns(store, SPINE)
    fragment = nav(client, SPINE, turns=1)
    first, _ = turns[0]
    assert inside(fragment, "data-node", first, "href") == [f"#turn-{first}"]
    for turn_id, index in turns[1:]:
        href = inside(fragment, "data-node", turn_id, "href")[0]
        assert href == f"/session/{SPINE}?after={index - 1}#turn-{turn_id}"
        # Following it lands on a page holding that turn, which is what makes the href a
        # permalink rather than a plausible string. The size rides the URL rather than a
        # `params` argument, which would replace the cursor the href carries.
        landed = client.get(f"{href.split('#')[0]}&turns=1")
        assert landed.status_code == 200
        assert turn_id in values(landed.text, "data-turn")


def test_a_run_node_goes_to_the_runs_own_page(client: TestClient) -> None:
    """A run has a page of its own, so its node links there rather than into the timeline.

    All three places a run node can sit, because each is built by its own call: under a turn,
    under another run, and in the tail of runs no turn claims.
    """
    fragment = nav(client, SPINE)
    for run_id in (SPINE_RUN, SPINE_LEAF):
        assert inside(fragment, "data-node", run_id, "href") == [f"/session/{SPINE}/run/{run_id}"]
    tail = nav(client, TEAMMATE)
    assert inside(tail, "data-node", TEAMMATE_RUN, "href") == [
        f"/session/{TEAMMATE}/run/{TEAMMATE_RUN}"
    ]


def test_the_session_page_asks_for_the_map_with_the_window_it_rendered(
    client: TestClient,
) -> None:
    """The sidebar is a hole the page asks htmx to fill, and it asks with its own window.

    This is the whole of the wiring: no first-party script, one `hx-get`, and the window
    riding that URL. A page that asked at the default window would mark the wrong turns on
    every page but the first, and the two leaves either side of this one would still pass —
    they fetch the fragment themselves.
    """
    page = client.get(f"/session/{SPINE}", params={"after": 1, "turns": 1}).text
    asked = re.search(r'id="map"[^>]*hx-get="([^"]*)"', page)
    assert asked is not None, "the session page loads no map"
    assert asked.group(1) == f"/fragment/nav/{SPINE}?after=1&turns=1"
    # And what it asks for answers, marking the one turn the page it lands beside rendered.
    answered = client.get(asked.group(1))
    assert answered.status_code == 200
    rendered = set(values(page, "data-turn")) - {queries.UNATTRIBUTED}
    assert set(values(answered.text, "data-here")) == rendered


@pytest.mark.parametrize("window", [{}, {"turns": 1}])
def test_the_map_marks_exactly_the_turns_the_page_rendered(
    client: TestClient, window: dict[str, int]
) -> None:
    """`data-here` is the emphasis a server render buys: which nodes are on the page the
    reader is looking at.

    Checked against the page itself at two windows, because the fragment derives the window
    from `after` and `turns` while the page derives it in SQL — one set, two derivations.
    """
    page = client.get(f"/session/{SPINE}", params=window).text
    fragment = nav(client, SPINE, **window)
    # The unattributed row is not a turn and has no node: it is the calls that answer none.
    rendered = set(values(page, "data-turn")) - {queries.UNATTRIBUTED}
    assert set(values(fragment, "data-here")) == rendered
    assert len(rendered) == (1 if window else 4)
    # A run is emphasised with the turn that spawned it rather than on its own account: the
    # reader is looking at that turn, so its runs are part of what is on the page. At one turn
    # a page `SPINE`'s chipped turn is off the window, which is what makes this two cases.
    dimmed = SPINE_CHIPPED_TURN not in rendered
    for run_id in (SPINE_RUN, SPINE_LEAF):
        classes = inside(fragment, "data-nav", run_id, "class")[0].split()
        assert ("away" in classes) == dimmed, (run_id, classes)
    # A run no turn claims has no turn to take an answer from, so it is never emphasised.
    assert "away" in inside(nav(client, TEAMMATE), "data-nav", TEAMMATE_RUN, "class")[0]


def test_every_node_states_what_it_cost_and_its_share_of_the_session(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Where the money went is the question the map exists to answer, so every node carries
    its own spend and what fraction of the session's that is.

    A turn's cost is its own thread's api calls and a run's is its own — they never overlap,
    so the shares of one map add to no more than the session.
    """
    (whole,) = one(store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [SPINE])
    fragment = nav(client, SPINE)
    spent = spend(store, SPINE)
    for turn_id, _ in main_turns(store, SPINE):
        cost = spent.get(turn_id, 0.0)
        node = fields(fragment, "data-node", turn_id)
        assert node["cost_usd"] == fmt.money(cost), turn_id
        assert node["share"] == fmt.share(cost, whole), turn_id
    # A run's own spend, the same number its row on the session page ranks it by.
    (run_cost,) = one(
        store,
        "SELECT round(sum(cost_usd), 4) FROM live_api_calls WHERE session_id = ? AND source = ?",
        [SPINE, SPINE_RUN],
    )
    for source in (SPINE_RUN, SPINE_LEAF):
        (run_cost,) = one(
            store,
            "SELECT round(coalesce(sum(cost_usd), 0), 4) FROM live_api_calls"
            " WHERE session_id = ? AND source = ?",
            [SPINE, source],
        )
        run = fields(fragment, "data-node", source)
        assert (run["cost_usd"], run["share"]) == (fmt.money(run_cost), fmt.share(run_cost, whole))
    # And a run in the tail is a node like any other: the map's shares cover every run it holds.
    (loose_whole,) = one(
        store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [TEAMMATE]
    )
    (loose_cost,) = one(
        store,
        "SELECT round(coalesce(sum(cost_usd), 0), 4) FROM live_api_calls"
        " WHERE session_id = ? AND source = ?",
        [TEAMMATE, TEAMMATE_RUN],
    )
    loose = fields(nav(client, TEAMMATE), "data-node", TEAMMATE_RUN)
    assert loose["cost_usd"] == fmt.money(loose_cost)
    assert loose["share"] == fmt.share(loose_cost, loose_whole)


def test_a_node_whose_cost_our_price_table_could_not_finish_says_so(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A total missing calls is not what a node cost, so the node hangs off it the same mark
    the session list does, counting what the price table left out.

    Every call the corpus recorded is priced, so the mark has to be planted: one turn's calls
    and one run's lose their cost, and every other node of the session keeps it.
    """
    turn_id, calls = one(
        store,
        "SELECT turn_id, count(*) FROM live_api_calls WHERE session_id = ? AND source = 'main'"
        " AND turn_id IS NOT NULL GROUP BY turn_id ORDER BY count(*) DESC, turn_id LIMIT 1",
        [SPINE],
    )
    (run_calls,) = one(
        store,
        "SELECT count(*) FROM live_api_calls WHERE session_id = ? AND source = ?",
        [SPINE, SPINE_RUN],
    )
    path = plant(
        (
            "UPDATE api_calls SET cost_usd = NULL WHERE session_id = ?"
            " AND ((turn_id = ? AND source = 'main') OR source = ?)",
            [SPINE, turn_id, SPINE_RUN],
        )
    )
    with TestClient(build_app(path)) as planted:
        fragment = nav(planted, SPINE)
    mark = "{} call(s) at a model our price table lacks"
    # The turn the plant unpriced, and the run — both count their own calls and nothing else.
    assert inside(fragment, "data-node", turn_id, "title") == [mark.format(calls)]
    assert inside(fragment, "data-node", SPINE_RUN, "title") == [mark.format(run_calls)]
    # Every node the table still priced whole carries no mark at all.
    for other, _ in main_turns(store, SPINE):
        if other != turn_id:
            assert inside(fragment, "data-node", other, "title") == [], other


def test_a_node_of_a_session_that_cost_nothing_shows_no_share(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A share of nothing is a gap rather than 0%: there was no spend to take a share of."""
    (whole,) = one(store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [MODEL_ONLY])
    assert whole == 0, "the zero-cost fixture moved: re-pick the session this measures"
    fragment = nav(client, MODEL_ONLY)
    for turn_id, _ in main_turns(store, MODEL_ONLY):
        assert fields(fragment, "data-node", turn_id)["share"] == ABSENT


def test_the_spend_meter_is_a_decile_of_what_the_node_took(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The meter is a class per decile, because the policy blocks the inline style a width
    would need.

    Any nonzero share rounds *up* into the first decile, or a session one turn dominates
    renders every other node with no bar at all. The corpus supplies both ends: `SPINE`'s
    third turn took 78% of it, and the run under that turn took 4.8%.
    """
    (whole,) = one(store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [SPINE])
    spent = spend(store, SPINE)
    fragment = nav(client, SPINE)
    classes = {
        turn_id: inside(fragment, "data-nav", turn_id, "class")[0].split()
        for turn_id, _ in main_turns(store, SPINE)
    }
    for turn_id, names in classes.items():
        decile = math.ceil(10 * spent.get(turn_id, 0.0) / whole)
        assert f"s{decile}" in names, turn_id
    # The two ends the docstring names, so this is a scale rather than one repeated class:
    # the turn that took most of the session sits near the top of it...
    assert "s8" in classes[SPINE_CHIPPED_TURN]
    # ...and a run that took a twentieth of it still shows a bar, rounded up into `s1`.
    assert "s1" in inside(fragment, "data-nav", SPINE_RUN, "class")[0].split()


def test_a_node_is_labelled_by_the_best_thing_the_store_says_about_it(
    client: TestClient,
    enriched_client: TestClient,
    store: duckdb.DuckDBPyConnection,
    enriched_store: duckdb.DuckDBPyConnection,
) -> None:
    """A label falls back prompt-ward: what a pass said the turn did, else the command it
    ran, else the prompt as typed.

    Three recorded sources, one rule. The prompt is last because a slash command's prompt is
    the `<command-…>` wrapper Claude Code put around it, which says nothing in 48 characters.
    """
    fragment = nav(client, SPINE)
    commanded = one(
        store,
        "SELECT id, command_name, command_args FROM live_turns"
        " WHERE session_id = ? AND source = 'main' AND command_name IS NOT NULL"
        ' ORDER BY "index"',
        [SPINE],
    )
    plain = one(
        store,
        "SELECT id, prompt FROM live_turns WHERE session_id = ? AND source = 'main'"
        ' AND command_name IS NULL ORDER BY "index"',
        [SPINE],
    )
    # A slash turn is labelled by the command and what followed it, never by the prompt...
    label = fields(fragment, "data-node", commanded[0])["label"]
    assert label == f"{commanded[1]} {commanded[2] or ''}".strip()[: queries.NAV_CHARS]
    assert "<command-" not in label and "command-name" not in label
    # ...a turn that ran no command by its prompt...
    assert fields(fragment, "data-node", plain[0])["label"] == plain[1][: queries.NAV_CHARS]
    # ...and either of them, over a store a pass has described, by what the pass wrote.
    described = {
        str(row[0]): str(row[1])
        for row in enriched_store.execute(
            "SELECT turn_id, description FROM turn_enrichments"
            " WHERE session_id = ? AND source = 'main'",
            [SPINE],
        ).fetchall()
    }
    assert described, "the enriched fixture describes none of this session's turns"
    enriched = nav(enriched_client, SPINE)
    for turn_id, description in described.items():
        assert fields(enriched, "data-node", turn_id)["label"] == description[: queries.NAV_CHARS]


def test_a_run_node_is_labelled_by_the_agent_and_what_it_was_asked_to_do(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run's label names the agent that ran and the task it was given, cut to one line.

    A run whose transcript recorded no description is labelled by the agent alone rather
    than by the word None — the corpus holds one, which is why the fallback is tested.
    """
    agent_type, description = one(
        store, "SELECT agent_type, description FROM live_agent_runs WHERE id = ?", [SPINE_RUN]
    )
    label = fields(nav(client, SPINE), "data-node", SPINE_RUN)["label"]
    assert label == f"{agent_type} {description}".strip()[: queries.NAV_CHARS]
    undescribed = store.execute(
        "SELECT session_id, id FROM live_agent_runs WHERE description IS NULL LIMIT 1"
    ).fetchone()
    assert undescribed is not None, "no recorded run lacks a description: the fallback is unproven"
    session_id, run_id = undescribed
    (agent_alone,) = one(store, "SELECT agent_type FROM live_agent_runs WHERE id = ?", [run_id])
    assert fields(nav(client, session_id), "data-node", run_id)["label"] == agent_alone


def test_the_runs_no_turn_claims_arrive_in_a_tail_group(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run the store places under no turn is listed after the turns rather than dropped.

    Both of the corpus's causes, recorded: one session's `architect` was started with no
    spawning tool call at all, and another's pair was spawned by calls the store holds no
    turn for. Without the tail they would be runs the map counts and never shows.
    """
    orphan = nav(client, TEAMMATE)
    assert inside(orphan, "id", "nav-unattached", "data-nav") == [TEAMMATE_RUN]
    assert fields(orphan, "id", "nav-unattached")["runs"] == "1"
    # A session whose every run is unattached and whose main thread holds no turn at all is
    # still a map of every run it holds.
    pair = nav(client, FORK_ORIGIN)
    assert not main_turns(store, FORK_ORIGIN)
    assert set(inside(pair, "id", "nav-unattached", "data-nav")) == {FORK_ORIGIN_RUN, FORK_RUN}
    # And a session that placed all of its runs grows no tail.
    assert 'id="nav-unattached"' not in nav(client, SPINE)


def test_the_node_cap_cuts_the_map_and_says_how_much_it_left(client: TestClient) -> None:
    """The map is bounded like every other response, and says what the bound cost.

    `SPINE`'s six nodes against a size the URL binds down: the ceiling is 200, which no
    recorded session reaches, and a cap nothing exercises is a cap nobody has weighed.
    """
    whole = values(nav(client, SPINE), "data-nav")
    assert 'id="nav-more"' not in nav(client, SPINE)
    fragment = nav(client, SPINE, nodes=2)
    # The cut is the walk's, so what it kept is the first two nodes of the whole map...
    assert values(fragment, "data-nav") == whole[:2]
    # ...and it counts what it left, which paging still reaches.
    assert fields(fragment, "id", "nav-more")["cut"] == str(len(whole) - 2)
    # The budget is over the whole map and not over its turns: `FORK_ORIGIN` holds no turn at
    # all, so a cap that bites there bites in the tail, and the count still has to add up.
    loose = values(nav(client, FORK_ORIGIN), "data-nav")
    assert len(loose) == 2
    cut = nav(client, FORK_ORIGIN, nodes=1)
    assert values(cut, "data-nav") == loose[:1]
    assert fields(cut, "id", "nav-more")["cut"] == "1"


def test_a_size_the_bound_refuses_is_a_400(client: TestClient) -> None:
    """A size past the ceiling is a request the viewer will not serve, like every other."""
    over = client.get(f"/fragment/nav/{SPINE}", params={"nodes": bounds.NAV.ceiling + 1})
    assert over.status_code == 400


def test_a_session_the_store_does_not_hold_has_no_map(client: TestClient) -> None:
    """A map of a session nothing recorded is a 404, not an empty sidebar."""
    response = client.get(f"/fragment/nav/{MISSING}")
    assert response.status_code == 404
    assert MISSING not in response.text


def test_the_map_cites_every_query_it_ran(client: TestClient) -> None:
    """A fragment arrives after the page it lands on, so it carries its own citations."""
    lines = values(nav(client, SPINE), "data-query")
    assert {line.split()[1] for line in lines} == {
        "queries/view_session_nav.sql",
        "queries/view_runs.sql",
        # What every share on the map is a share of, which is a query like the rest.
        "queries/view_session_header.sql",
    }
    for line in lines:
        assert f"session_id={SPINE}" in line
