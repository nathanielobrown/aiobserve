"""The two pages slice 1 serves: the session list, and one session's timeline.

Every expectation is derived from the store the app is serving rather than written down, so
a fixture added to the corpus does not silently stop being covered.
"""

import re
from pathlib import Path
from typing import NamedTuple

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view import bounds
from aiobserve.view.app import CSP, TEMPLATES, build_app
from aiobserve.view.listing import (
    DEFAULT_DIRECTION,
    DEFAULT_SORT,
    DIRECTIONS,
    FILTERS,
    SORTS,
)
from tests.conftest import (
    ANCESTOR,
    DENSE_CALL,
    DENSE_CALL_TURN,
    DENSE_TOOL,
    DENSE_TURN,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    FORK_RUN,
    MAIN,
    MYCELIA,
    RESUME,
    SPINE,
    SPINE_RUN,
)
from tests.view.conftest import MISSING, Planter, fields, inside, one, values

# What every list citation says about the display cut, which the viewer composes around the
# query the same way it composes the paging: re-running the file alone answers whole values.
CUT = (
    f"head_chars={queries.LIST_CHARS} item_chars={queries.LIST_ITEM_CHARS}"
    f" head_items={queries.LIST_ITEMS}"
)


def sessions(store: duckdb.DuckDBPyConnection) -> list[str]:
    """Every session in the store in the list's default order: newest first.

    A session with no start sorts to the top rather than the bottom, because the direction
    carries the NULLs with it — that is what makes a sort and its reverse exact opposites.
    """
    return [
        row[0]
        for row in store.execute(
            "SELECT session_id FROM session_rollups"
            " ORDER BY started_at DESC NULLS FIRST, session_id DESC"
        ).fetchall()
    ]


def money(amount: float) -> str:
    """A cost as the pages print it."""
    return f"${amount:.2f}"


def test_the_list_holds_every_session_with_its_own_numbers(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The list is one row per session in the store, carrying that session's rollup."""
    page = client.get("/").text
    # Every session gets a row, and the default order is newest first...
    assert values(page, "data-session-id") == sessions(store)
    # ...whose cells are that session's rollup, not a number computed anywhere else.
    row = fields(page, "data-session-id", SPINE)
    turns, tool_calls, agent_runs, compactions, cost = one(
        store,
        "SELECT turns, tool_calls, agent_runs, compactions, cost_usd"
        " FROM session_rollups WHERE session_id = ?",
        [SPINE],
    )
    assert row["turns"] == str(turns)
    assert row["tool_calls"] == str(tool_calls)
    assert row["agent_runs"] == str(agent_runs)
    assert row["compactions"] == str(compactions)
    assert row["cost_usd"] == money(cost)


def test_a_list_row_links_to_the_session_it_names(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The link on a row opens that session's page — the list's whole purpose."""
    page = client.get("/").text
    session_id = sessions(store)[0]
    assert f"/session/{session_id}" in inside(page, "data-session-id", session_id, "href")
    assert client.get(f"/session/{session_id}").status_code == 200


def test_every_sort_key_names_a_column_the_query_returns(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """No sort key can reach past the library query into SQL of its own."""
    listing = queries.load("view_sessions").strip().rstrip(";")
    # At the query's own defaults, read off the manifest rather than listed: what a sort key
    # names is a column, and no binding the file takes changes which columns it returns.
    defaults = {
        name: spec.default for name, spec in queries.QUERIES["view_sessions"].params.items()
    }
    returned = {row[0] for row in store.execute(f"DESCRIBE ({listing})", defaults).fetchall()}
    assert set(SORTS) <= returned


@pytest.mark.parametrize("sort", sorted(SORTS))
def test_a_sort_and_its_reverse_are_exact_opposites(sort: str, client: TestClient) -> None:
    """Every sort key totally orders the list, so flipping the direction reverses it."""
    order = {
        direction: values(
            client.get("/", params={"sort": sort, "direction": direction}).text, "data-session-id"
        )
        for direction in DIRECTIONS
    }
    assert len(order["asc"]) > 1
    assert order["asc"] == list(reversed(order["desc"]))


@pytest.mark.parametrize(
    "parameters",
    [
        # A key that is not in the closed dict, however plausible...
        {"sort": "session_id"},
        # ...a direction that is not one of the two...
        {"direction": "sideways"},
        # ...and the shape of an attempt to reach the SQL through either.
        {"sort": "cost_usd; DROP TABLE sessions"},
        {"direction": "asc, 1"},
    ],
)
def test_an_unknown_sort_or_direction_is_refused(
    parameters: dict[str, str], client: TestClient
) -> None:
    """Sort and direction come from closed dictionaries; anything else is a 400."""
    response = client.get("/", params=parameters)
    assert response.status_code == 400
    # The refusal says what is allowed, and does not echo what was asked for.
    for value in parameters.values():
        assert value not in response.text
    assert DEFAULT_SORT in response.text


def test_the_list_footer_cites_its_query_and_what_was_composed_around_it(
    client: TestClient,
) -> None:
    """The page carries the query behind it, at the sort and the page this request ran."""
    page = client.get("/", params={"sort": "cost_usd", "direction": "asc", "size": 5}).text
    cited = fields(page, "id", "citation")
    assert cited["view_sessions"] == (
        f"-- queries/view_sessions.sql sort=cost_usd direction=asc limit=5 offset=0 {CUT}"
    )
    # A bare request cites the defaults, so a copied line reproduces what was seen.
    default = fields(client.get("/").text, "id", "citation")
    assert default["view_sessions"] == (
        f"-- queries/view_sessions.sql sort={DEFAULT_SORT} direction={DEFAULT_DIRECTION}"
        f" limit={bounds.SESSIONS.default} offset=0 {CUT}"
    )


def test_the_list_is_served_a_page_at_a_time(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The pages of the list tile the store in order: no row twice, none missing."""
    size = 5
    seen: list[str] = []
    # A size the fixture corpus needs four pages of, followed to the end...
    for page in range(1, 10):
        html = client.get("/", params={"size": size, "page": page}).text
        rows = values(html, "data-session-id")
        assert len(rows) <= size
        seen += rows
        if "next" not in values(html, "data-page"):
            break
    else:
        pytest.fail("the pager never ran out of pages")
    # ...holds every session once, in the order one long list would have had.
    assert seen == sessions(store)
    # Past the end is an empty page rather than an error: a stale link is not a fault...
    beyond = client.get("/", params={"page": 99}).text
    assert values(beyond, "data-session-id") == []
    # ...and it says so, rather than counting a range that ends before it starts.
    assert fields(beyond, "data-pager", "top")["range"] == "No sessions"


@pytest.mark.parametrize("parameters", [{"page": 0}, {"page": -1}, {"size": 0}, {"size": 100_000}])
def test_a_page_outside_the_bounds_is_refused(
    parameters: dict[str, int], client: TestClient
) -> None:
    """The page size is bounded on both ends: a page cannot be asked to hold the store."""
    response = client.get("/", params=parameters)
    assert response.status_code == 400
    assert str(bounds.SESSIONS.ceiling) in response.text


# One value per filter, read off the fixture corpus rather than invented, chosen so each
# narrows the 16-session list without emptying it. The leaf below keeps the set honest when
# a filter is added; the values themselves are checked by the narrowing leaf, which fails
# loudly if a fixture change makes one of them match everything or nothing.
SAMPLES: dict[str, str] = {
    # 13 of the 16 fixture sessions ran in the mycelia checkout...
    "project": MYCELIA,
    # ...the corpus starts on 2026-06-30 and ends on 2026-08-06, so a bound inside that
    # window cuts rows off each end...
    "since": "2026-07-01",
    "until": "2026-08-01",
    # ...two sessions ran the grill-me skill...
    "skill": "grill-me",
    # ...and two recorded a failing tool call.
    "errors": "1",
}


def test_every_filter_the_list_offers_has_a_sample_to_check_it_with() -> None:
    """Each filter the list offers is exercised below, so a new one cannot land untested."""
    assert set(SAMPLES) == set(FILTERS)


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_a_filter_narrows_the_list_without_emptying_it(key: str, client: TestClient) -> None:
    """Every filter cuts the list to some of the sessions it held, never to all or none."""
    whole = values(client.get("/").text, "data-session-id")
    narrowed = values(client.get("/", params={key: SAMPLES[key]}).text, "data-session-id")
    # A filter that matched everything would pass a subset check while filtering nothing,
    # and one that matched nothing would pass it vacuously. This is a proper, non-empty cut.
    assert set(narrowed) < set(whole)
    assert narrowed
    # The rows kept their order rather than being re-sorted by the filtering.
    assert narrowed == [row for row in whole if row in set(narrowed)]


def test_a_filter_keeps_exactly_the_sessions_the_store_says_it_should(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A skill filter shows the sessions that ran that skill — the whole set, and no other."""
    ran_it = {
        row[0]
        for row in store.execute(
            "SELECT DISTINCT session_id FROM live_api_calls WHERE attribution_skill = ?",
            [SAMPLES["skill"]],
        ).fetchall()
    }
    shown = values(client.get("/", params={"skill": SAMPLES["skill"]}).text, "data-session-id")
    assert set(shown) == ran_it
    # Every row shown says the skill it was filtered by, so the page shows its own evidence.
    for session_id in shown:
        page = client.get("/", params={"skill": SAMPLES["skill"]}).text
        assert SAMPLES["skill"] in fields(page, "data-session-id", session_id)["skills"]


def test_a_filter_value_reaches_duckdb_only_as_a_binding(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A filter value that is SQL rather than a name matches nothing and runs nothing."""
    before = one(store, "SELECT count(*) FROM sessions")[0]
    response = client.get("/", params={"skill": "'; DROP TABLE sessions; --"})
    # A value that reached SQL as text would either error or execute; bound, it is a skill
    # name no session ran...
    assert response.status_code == 200
    assert values(response.text, "data-session-id") == []
    # ...and the table it named is still there, with every row it had.
    assert one(store, "SELECT count(*) FROM sessions")[0] == before


@pytest.mark.parametrize(
    ("parameters", "says"),
    [
        # A key the list does not offer, however plausible, is told the keys it does...
        ({"filter": "grill-me"}, "skill"),
        ({"Skill": "grill-me"}, "skill"),
        # ...and a known key whose value is not the type its predicate binds is told which.
        ({"since": "last tuesday"}, "since takes date values"),
        ({"errors": "many"}, "errors takes integer values"),
    ],
)
def test_an_unknown_filter_key_or_unparseable_value_is_refused(
    parameters: dict[str, str], says: str, client: TestClient
) -> None:
    """The list reads a closed set of query keys, each at one type; anything else is a 400."""
    response = client.get("/", params=parameters)
    assert response.status_code == 400
    # The refusal says what would have worked, and never echoes what was asked for — a page
    # that reflected the value back would be the one place unescaped request text could land.
    assert says in response.text
    for value in parameters.values():
        assert value not in response.text


def test_a_filter_rides_the_links_and_the_citation(client: TestClient) -> None:
    """A filter survives re-sorting and paging, and the footer says the list was filtered."""
    page = client.get("/", params={"skill": SAMPLES["skill"], "sort": "cost_usd", "size": 1}).text
    # Every heading link and every pager link carries the filter, so changing the order or
    # turning the page does not quietly widen the list back to the corpus...
    links = re.findall(r'href="(/\?[^"]*)"', page)
    assert links
    for link in links:
        assert "skill=grill-me" in link
    # ...and the citation carries it too, after the paging, so the line reproduces the rows.
    assert fields(page, "id", "citation")["view_sessions"] == (
        "-- queries/view_sessions.sql sort=cost_usd direction=desc limit=1 offset=0"
        f" {CUT} skill=grill-me"
    )


def test_the_session_header_holds_what_the_store_says_about_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The session page's header is that session's own rollup and identity."""
    page = client.get(f"/session/{SPINE}").text
    header = fields(page, "id", "session-header")
    title, turns, agent_runs, cost = one(
        store,
        "SELECT s.title, r.turns, r.agent_runs, r.cost_usd FROM sessions s"
        " JOIN session_rollups r ON r.session_id = s.id WHERE s.id = ?",
        [SPINE],
    )
    assert header["title"] == title
    assert header["turns"] == str(turns)
    assert header["agent_runs"] == str(agent_runs)
    assert header["cost_usd"] == money(cost)


def test_the_session_page_cites_every_query_it_ran(client: TestClient) -> None:
    """The session page's footer holds one re-runnable line per query behind it."""
    # If the page is asked for at a cursor and a size of its own, so the paging in the
    # citation is this request's rather than the query file's default...
    page = client.get(f"/session/{SPINE}", params={"after": 0, "turns": 3}).text
    # ...then every query the page ran is cited, keyed by the session, and the ones that read
    # a single thread say which — the main thread, on a session page. Nothing else is listed:
    # `view_enrichment` never ran, because this store holds no enrichment tables to read.
    assert fields(page, "id", "citation") == {
        "view_session_header": f"-- queries/view_session_header.sql session_id={SPINE}",
        # The context panel's rows, cited whether or not the panel drew: the query ran, and a
        # citation says what produced the page rather than what reached it.
        "view_context_timeline": (
            f"-- queries/view_context_timeline.sql session_id={SPINE} source={MAIN}"
            f" max_points={queries.CONTEXT_POINTS}"
        ),
        "session_digest": f"-- queries/session_digest.sql session_id={SPINE} after=0 limit=3",
        "view_runs": f"-- queries/view_runs.sql session_id={SPINE}",
        "view_compactions": f"-- queries/view_compactions.sql session_id={SPINE} source={MAIN}",
        "view_turn_records": f"-- queries/view_turn_records.sql session_id={SPINE} source={MAIN}",
    }


def test_the_timeline_is_the_sessions_turns_in_order(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Every turn of the main thread appears once, in the order it ran."""
    page = client.get(f"/session/{SPINE}").text
    turns = [
        row[0]
        for row in store.execute(
            "SELECT id FROM live_turns WHERE session_id = ? AND source = 'main' ORDER BY \"index\"",
            [SPINE],
        ).fetchall()
    ]
    assert values(page, "data-turn") == turns
    # A thread that fits one page mints no pager: it is the page it was before paging landed.
    assert values(page, "data-more-turns") == []


def test_calls_under_no_turn_get_their_own_row(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A resume's spend sits under no turn, so the page shows it rather than losing it."""
    # `RESUME` answers turns that live in the session it resumed: it has no turns of its
    # own, and every one of its api calls is unattributed...
    page = client.get(f"/session/{RESUME}").text
    (cost,) = one(store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [RESUME])
    # ...so the unattributed row carries the whole session's cost...
    unattributed = fields(page, "data-turn", "(unattributed)")
    assert unattributed["cost_usd"] == money(cost)
    # ...and the header agrees with it, which is the disagreement this row exists to stop.
    assert fields(page, "id", "session-header")["cost_usd"] == money(cost)


def test_a_turns_chips_are_the_runs_that_turn_spawned(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run chips onto the turn whose tool call spawned it."""
    page = client.get(f"/session/{SPINE}").text
    spawned = store.execute(
        "SELECT c.turn_id, a.id FROM live_agent_runs a"
        " JOIN live_tool_calls tc ON tc.session_id = a.session_id AND tc.id = a.tool_use_id"
        "  AND tc.source <> a.id"
        " JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "  AND c.id = tc.api_call_id"
        " WHERE a.session_id = ? AND c.source = 'main'",
        [SPINE],
    ).fetchall()
    assert spawned, "the chip join returns nothing: this session no longer proves the case"
    for turn_id, run_id in spawned:
        assert run_id in inside(page, "data-turn", turn_id, "data-chip")


def test_a_fork_does_not_chip_onto_a_turn_of_its_own_timeline(client: TestClient) -> None:
    """A fork carries an un-replayed copy of its spawning call; it is not its own child."""
    page = client.get(f"/session/{FORK_ORIGIN}").text
    # The copy sits in the fork's own transcript, so the join that ignores it leaves the run
    # unattached rather than hanging it off a turn the fork itself recorded.
    assert FORK_RUN in values(page, "data-unattached")
    assert FORK_RUN not in values(page, "data-chip")


@pytest.mark.parametrize("turns", [bounds.TURNS.default, 1])
def test_every_session_page_accounts_for_all_of_its_runs(
    client: TestClient, store: duckdb.DuckDBPyConnection, turns: int
) -> None:
    """Across the corpus, every agent run is reachable from its session's pages, once a page.

    At one turn a page as well as at the default, because placement is checked over the
    session's whole thread rather than over the page: a run whose spawning turn is on another
    page has to stay placed rather than raise, and no page may show one twice.
    """
    for session_id in sessions(store):
        pages = walk(client, session_id, turns=turns, chips=bounds.CHIP_BUDGET // (turns + 1))
        shown = [values(page, "data-chip") + values(page, "data-unattached") for page in pages]
        runs = {
            row[0]
            for row in store.execute(
                "SELECT id FROM live_agent_runs WHERE session_id = ?", [session_id]
            ).fetchall()
        }
        # The unattached list rides every page, so a run can be reached from more than one —
        # but never twice from the same one.
        assert {run for page in shown for run in page} == runs, session_id
        for page in shown:
            assert len(page) == len(set(page)), session_id


class Chipped(NamedTuple):
    """A recorded run that chips onto a turn, and everything a plant needs to move it."""

    run_id: str
    # The api call the run was spawned from, and the turn that call answers.
    call_id: str
    turn_id: str
    # Where that turn sits in its thread, which is the cursor a page of one turn opens at.
    turn_index: int


def chipped(store: duckdb.DuckDBPyConnection) -> Chipped:
    """The first run of `SPINE` the chip join hangs on a turn of the main thread.

    The join `view_runs` makes, in the expectation's own SQL: a run is a chip when its
    `tool_use_id` names a tool call outside its own transcript, whose api call sits under a
    turn. Read from the store rather than pinned, so a re-recorded fixture moves it.
    """
    run_id, call_id, turn_id, turn_index = one(
        store,
        'SELECT a.id, c.id, t.id, t."index" FROM live_agent_runs a'
        " JOIN live_tool_calls tc ON tc.session_id = a.session_id AND tc.id = a.tool_use_id"
        "  AND tc.source <> a.id"
        " JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "  AND c.id = tc.api_call_id"
        " JOIN live_turns t ON t.session_id = a.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE a.session_id = ? AND c.source = ? ORDER BY a.id LIMIT 1",
        [SPINE, MAIN],
    )
    return Chipped(run_id, call_id, turn_id, turn_index)


@pytest.mark.parametrize("turns", [bounds.TURNS.default, 1])
def test_a_run_the_page_cannot_place_stops_the_page(
    plant: Planter, store: duckdb.DuckDBPyConnection, turns: int
) -> None:
    """A run that lands on no turn and in no list crashes the page instead of vanishing from it.

    The complement of the leaf above, and the reason its guarantee is worth anything: the page
    counts every run in its header, so one the layout cannot place would be a number with no
    row behind it. The shape is planted and invented — no recorded session has a spawning call
    naming a turn its own thread does not hold — and it is checked at one turn a page as well,
    where placement is still computed over the whole thread and not over the page.
    """
    # The run whose spawning call sits under a turn of the main thread...
    run = chipped(store)
    # ...answers a turn no thread of the session holds, so the chip join has nothing to hang it
    # on and the unattached list does not want it either: its spawning turn is not missing, it
    # is unknown.
    path = plant(
        (
            "UPDATE api_calls SET turn_id = 'planted-turn-nothing-holds'"
            " WHERE session_id = ? AND source = ? AND id = ?",
            [SPINE, MAIN, run.call_id],
        ),
    )
    with (
        TestClient(build_app(path)) as planted,
        pytest.raises(ValueError, match="hang off no turn and no run") as raised,
    ):
        planted.get(f"/session/{SPINE}", params={"turns": turns, "chips": 1})
    # The unplaceable run, and the run it spawned — which the page could only have reached
    # through it, so an unmoored run takes its subtree with it.
    under = {
        row[0]
        for row in store.execute(
            "SELECT id FROM live_agent_runs WHERE session_id = ? AND parent_agent_id = ?",
            [SPINE, run.run_id],
        ).fetchall()
    }
    assert re.findall(r"'([^']+)'", str(raised.value)) == sorted({run.run_id} | under)


def test_a_turns_chips_are_capped_by_the_nodes_of_its_forest(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A turn renders as many runs as the page's chip size allows, counts the rest, links to them.

    The cap counts every node of the forest and not its top level: a run under a run is a row
    on the page and costs what any other row costs.
    """
    # The recorded pair where one run spawned another, and the main turn that spawned the
    # first of them — three top-level chips can carry fifty nodes, so this is the shape a
    # top-level cap bounds nothing about...
    listing = queries.load("view_runs").strip().rstrip(";")
    ((child, parent, turn_id),) = store.execute(
        f"SELECT child.run_id, parent.run_id, parent.spawn_turn_id FROM ({listing}) child"
        f" JOIN ({listing}) parent ON child.spawn_source = parent.run_id"
        f" WHERE parent.spawn_source = '{MAIN}' AND parent.spawn_turn_id IS NOT NULL",
        {"session_id": SPINE, "chip_chars": queries.CHIP_CHARS},
    ).fetchall()
    # ...renders the top of the forest and nothing under it when the page has room for one...
    narrow = client.get(f"/session/{SPINE}?chips=1").text
    assert inside(narrow, "data-turn", turn_id, "data-chip") == [parent]
    # ...says how many it cut...
    assert fields(narrow, "data-turn", turn_id)["cut"] == "1"
    # ...and the link it mints opens a page holding that turn with the whole forest.
    (wider,) = inside(narrow, "data-turn", turn_id, "data-more-chips")
    assert inside(client.get(wider).text, "data-turn", turn_id, "data-chip") == [parent, child]


def test_the_unattached_list_is_capped_and_counts_what_the_session_holds(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The runs under no turn are capped like a turn's chips, under a heading that counts them all.

    The list rides every page, so it is one of the sizes that multiply. Cutting it silently
    would put the header's run count and the runs a reader can reach back out of agreement,
    which is the disagreement this section exists to end.
    """
    # The fixture session listing the most runs under no turn...
    listed = {
        session_id: values(client.get(f"/session/{session_id}").text, "data-unattached")
        for session_id in sessions(store)
    }
    session_id = max(listed, key=lambda name: len(listed[name]))
    assert len(listed[session_id]) > 1, "no fixture session has a list long enough to cut"
    whole = client.get(f"/session/{session_id}").text
    nodes = [
        run
        for attribute in ("data-unattached", "data-chip")
        for run in inside(whole, "id", "unattached", attribute)
    ]
    # ...renders one of them when the page has room for one chip...
    narrow = client.get(f"/session/{session_id}?chips=1").text
    assert values(narrow, "data-unattached") == listed[session_id][:1]
    # ...counts every run the list holds in its heading, cap or no cap...
    assert fields(narrow, "id", "unattached")["runs"] == str(len(nodes))
    # ...and the link it mints opens a page holding the whole list.
    (wider,) = inside(narrow, "id", "unattached", "data-more-chips")
    assert values(client.get(wider).text, "data-unattached") == listed[session_id]


def test_the_unattached_section_is_the_chip_joins_complement(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run is listed as unattached exactly when the chip join fails to place it.

    Placing it takes a turn *or* a run of this session: a run spawned by a call that names
    the run but no turn hangs under that run, so it is placed and not listed here.
    """
    listing = queries.load("view_runs").strip().rstrip(";")
    for session_id in sessions(store):
        unplaced = {
            row[0]
            for row in store.execute(
                f"SELECT run_id FROM ({listing}) WHERE spawn_turn_id IS NULL"
                f" AND (spawn_source IS NULL OR spawn_source NOT IN"
                f" (SELECT run_id FROM ({listing})))",
                {"session_id": session_id, "chip_chars": queries.CHIP_CHARS},
            ).fetchall()
        }
        page = client.get(f"/session/{session_id}").text
        assert set(values(page, "data-unattached")) == unplaced, session_id


def test_a_run_spawned_by_a_call_in_no_turn_is_shown_once(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A run whose spawning call sits in no turn hangs under the run that spawned it, once.

    The shape is real — a call the transcript never tied to a turn is exactly what the store
    records as a NULL `turn_id` — but no recorded session carries an instance of it under a
    *run*, so the test plants one by taking the turn away from the call that spawned a nested
    run. Without the planting, the run would render twice: under its parent and in the
    unattached list, which is the list for runs nothing places at all.
    """
    # The call one run made to spawn another, in the session whose runs nest...
    parent_run, spawned, call_id = one(
        store,
        "SELECT tc.source, a.id, c.id FROM live_agent_runs a"
        " JOIN live_tool_calls tc ON tc.session_id = a.session_id AND tc.id = a.tool_use_id"
        "  AND tc.source <> a.id"
        " JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "  AND c.id = tc.api_call_id"
        " WHERE a.session_id = ? AND tc.source <> ? LIMIT 1",
        [SPINE, MAIN],
    )
    # ...loses the turn it was made in.
    path = plant(
        (
            "UPDATE api_calls SET turn_id = NULL WHERE session_id = ? AND source = ? AND id = ?",
            [SPINE, parent_run, call_id],
        )
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{SPINE}").text
    # The run it spawned still hangs under it, and appears nowhere else on the page.
    assert spawned in inside(page, "data-chip", parent_run, "data-chip")
    assert (values(page, "data-chip") + values(page, "data-unattached")).count(spawned) == 1


def test_a_compaction_appears_in_the_timeline_where_it_happened(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A main-thread compaction is a marker between the turns it fell between."""
    session_id, compaction_id = one(
        store,
        "SELECT session_id, id FROM live_compactions WHERE source = 'main'"
        " ORDER BY session_id, timestamp LIMIT 1",
    )
    page = client.get(f"/session/{session_id}").text
    assert compaction_id in values(page, "data-compaction")


def test_a_compaction_rides_the_page_of_the_turn_it_precedes(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """Paging a timeline moves no compaction and loses none: each lands on exactly one page.

    A mark hangs off the turn it precedes, because what that turn could still see depends on
    it — so a mark between two turns rides the page holding the later one, and a mark after
    every turn rides the last page.
    """
    # Two turns of the session long enough to page, in index order and ascending in time...
    first, second, started = one(
        store,
        'SELECT a."index", b."index", b.started_at FROM live_turns a JOIN live_turns b'
        ' ON b.session_id = a.session_id AND b.source = a.source AND b."index" = a."index" + 1'
        " WHERE a.session_id = ? AND a.source = ? AND a.started_at < b.started_at"
        ' ORDER BY a."index" LIMIT 1',
        [SPINE, MAIN],
    )
    assert first == 0, "the pair is no longer SPINE's first two turns, so an earlier turn may claim"
    turn_id, *_ = one(
        store,
        'SELECT id FROM live_turns WHERE session_id = ? AND source = ? AND "index" = ?',
        [SPINE, MAIN, second],
    )
    # ...take two recorded compactions off threads that are not this one, and move them here:
    # one landing a hair before the second turn, one an hour after every turn.
    (at, over, between), (from_at, from_over, trailing) = store.execute(
        "SELECT session_id, source, id FROM live_compactions"
        " WHERE NOT (session_id = ? AND source = ?) ORDER BY session_id, source, id LIMIT 2",
        [SPINE, MAIN],
    ).fetchall()
    # A compaction id is unique within its thread and not across the store, so the row a plant
    # moves is named by all three of its key columns.
    move = (
        "UPDATE compactions SET session_id = ?, source = ?, timestamp = "
        " (SELECT {when} FROM live_turns WHERE session_id = ? AND source = ?{at}) {shift}"
        " WHERE session_id = ? AND source = ? AND id = ?"
    )
    path = plant(
        (
            move.format(when="started_at", at=' AND "index" = ?', shift="- INTERVAL 1 MICROSECOND"),
            [SPINE, MAIN, SPINE, MAIN, second, at, over, between],
        ),
        (
            move.format(when="max(started_at)", at="", shift="+ INTERVAL 1 HOUR"),
            [SPINE, MAIN, SPINE, MAIN, from_at, from_over, trailing],
        ),
    )
    assert started is not None
    with TestClient(build_app(path)) as planted:
        pages = walk(planted, SPINE, turns=1, chips=1)
        unpaged = values(planted.get(f"/session/{SPINE}").text, "data-compaction")
    marks = [values(page, "data-compaction") for page in pages]
    # The mark between two turns opens the page of the turn it precedes...
    assert marks[[values(page, "data-turn")[0] for page in pages].index(turn_id)] == [between]
    # ...the mark after every turn closes the last page...
    assert marks[-1][-1] == trailing
    # ...and between them the pages hold what the unpaged timeline holds, no mark twice.
    assert [mark for page in marks for mark in page] == unpaged


def test_a_thread_with_more_compactions_than_a_page_holds_says_how_many_it_cut(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A timeline renders `bounds.MARKS` compactions and counts the rest rather than dropping them.

    Compactions are not a size a URL carries, so this cap is the payload arithmetic's backstop:
    without it a thread's markers are however many the session ran, and the ceiling budgets a
    fixed number of them. The overflow is planted — the densest recorded thread holds 18, which
    is why the cap sits where it does — and each planted mark precedes every turn, so they all
    land on the one page this reads.
    """
    (recorded,) = one(
        store,
        "SELECT count(*) FROM live_compactions WHERE session_id = ? AND source = ?",
        [SPINE, MAIN],
    )
    over = bounds.MARKS + 3
    path = plant(
        (
            "INSERT INTO compactions (SELECT 'planted-' || i, ?, ?,"
            " '1970-01-01T00:00:00Z', 'planted', 1, 1, 1 FROM range(1, ?) t(i))",
            [SPINE, MAIN, over + 1],
        ),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{SPINE}").text
    # The page shows the cap's worth of markers...
    assert len(values(page, "data-compaction")) == bounds.MARKS
    # ...and says how many of the thread's own it left, so a cut list is never a silent one.
    assert values(page, "data-more-marks") == [str(recorded + over - bounds.MARKS)]


# The most pages a walk of one fixture session may take. The longest fixture thread holds a
# handful of turns, so a walk that runs past this is a pager that never ends rather than a
# session that is large.
WALK_CEILING = 20


def digest(store: duckdb.DuckDBPyConnection, session_id: str) -> list[str]:
    """The turn ids of a session's whole digest, in the order the unpaged query gives them.

    What the pages of the timeline have to add up to — read off the library query itself
    rather than written down, so the expectation moves with the digest.
    """
    listing = queries.load("session_digest").strip().rstrip(";")
    return [
        row[0]
        for row in store.execute(
            f"SELECT turn_id FROM ({listing}) ORDER BY turn_index NULLS LAST",
            {"session_id": session_id},
        ).fetchall()
    ]


def walk(client: TestClient, session_id: str, turns: int, chips: int) -> list[str]:
    """Every page of one session's timeline, followed the way a reader follows it.

    The cursor comes off the page's own pager, so nothing here assembles a URL the viewer
    does not mint.
    """
    after, served = queries.FIRST_PAGE, []
    while len(served) < WALK_CEILING:
        served.append(
            client.get(f"/session/{session_id}?after={after}&turns={turns}&chips={chips}").text
        )
        cursor = values(served[-1], "data-more-turns")
        if not cursor:
            return served
        after = int(cursor[0])
    raise AssertionError(f"{session_id} is still paging after {WALK_CEILING} pages")


def test_walking_the_timeline_covers_the_whole_digest_once(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The pages of a session's timeline hold every row of its digest, once, in digest order."""
    # Bound to one turn a page against the longest main thread the fixtures record, so the
    # page boundary is a real overflow of recorded turns rather than a staged one...
    rows = digest(store, SPINE)
    assert len(rows) > 2, "SPINE no longer holds enough turns for the boundary to bite"
    pages = walk(client, SPINE, turns=1, chips=1)
    # ...and what the pages carry between them is the digest, in the digest's own order.
    assert [turn for page in pages for turn in values(page, "data-turn")] == rows


def test_the_unattributed_row_lands_on_the_last_page_and_no_other(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """The row for calls under no turn sits after the last turn, so it rides the last page.

    The shape is real — a resume's calls answer turns it does not hold — but no recorded
    fixture has both turns of its own and calls under none, so the test takes the turn away
    from one main call of the session whose thread is long enough to page.
    """
    (call_id,) = one(
        store,
        "SELECT id FROM live_api_calls WHERE session_id = ? AND source = ?"
        " AND turn_id IS NOT NULL ORDER BY id LIMIT 1",
        [SPINE, MAIN],
    )
    path = plant(
        (
            "UPDATE api_calls SET turn_id = NULL WHERE session_id = ? AND source = ? AND id = ?",
            [SPINE, MAIN, call_id],
        )
    )
    with TestClient(build_app(path)) as planted:
        served = walk(planted, SPINE, turns=1, chips=1)
    pages = [values(page, "data-turn") for page in served]
    # It has no turn index, so it cannot ride the window — it is fetched on the last page...
    assert pages[-1][-1] == queries.UNATTRIBUTED
    # ...and on no other, which is what a second fetch of it would quietly break.
    assert [row for page in pages for row in page].count(queries.UNATTRIBUTED) == 1
    # It costs a turn row and no run rows, which is what the ceiling budgets for it: the
    # digest gives it a sentinel turn id, and no run's spawning call names that turn.
    assert inside(served[-1], "data-turn", queries.UNATTRIBUTED, "data-chip") == []


def test_a_cursor_past_the_last_turn_is_a_404(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A cursor beyond a session's turns answers nothing; page one of a short session answers.

    A thread that was never there and a cursor past the end of one that is are the same
    answer — the records browser's rule. Page one is not: 102 of the canonical store's 575
    sessions hold no main turn at all, and their spend is still on the page.
    """
    assert client.get(f"/session/{SPINE}?after={len(digest(store, SPINE)) + 99}").status_code == 404
    # RESUME answers turns that live in the session it resumed, so it has none of its own.
    served = client.get(f"/session/{RESUME}")
    assert served.status_code == 200
    assert fields(served.text, "id", "session-header")["session_id"] == RESUME


def test_a_turns_permalink_opens_the_page_that_turn_starts(client: TestClient) -> None:
    """The link a turn row mints opens a page whose first row is that turn."""
    page = client.get(f"/session/{SPINE}").text
    turns, links = values(page, "data-turn"), values(page, "data-permalink")
    # Every row but the continuation one mints a link: that row has no index to cursor from.
    assert len(links) == len([turn for turn in turns if turn != queries.UNATTRIBUTED])
    for turn_id, link in zip(turns, links, strict=False):
        opened = client.get(link).text
        assert values(opened, "data-turn")[0] == turn_id
        # ...and the anchor the fragment names is on the page it opens.
        assert f'id="turn-{turn_id}"' in opened


@pytest.mark.parametrize(
    "sizes",
    [
        "turns=0",
        f"turns={bounds.TURNS.ceiling + 1}",
        "chips=0",
        f"chips={bounds.CHIPS.ceiling + 1}",
        # Each size is inside its own ceiling; what they multiply into is not. The unattached
        # list rides every page at the same size, so the budget buys `turns + 1` of them.
        f"turns={bounds.TURNS.default}"
        f"&chips={bounds.CHIP_BUDGET // (bounds.TURNS.default + 1) + 1}",
        f"turns=2&chips={bounds.CHIPS.ceiling}",
    ],
)
def test_a_timeline_size_outside_its_bounds_is_refused(sizes: str, client: TestClient) -> None:
    """Both sizes are checked against their own ceiling and against the budget they multiply."""
    assert client.get(f"/session/{SPINE}?{sizes}").status_code == 400


def test_the_whole_chip_budget_is_reachable_on_one_turn(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A reader who wants a turn's whole run forest can have it, one turn at a time.

    The chip ceiling is sized for the widest forest the corpus records — 94 runs under one
    turn — so that no run sits behind a "+N more" nobody can open. No fixture session has a turn
    that wide, so the leaf plants one past the cap: the rows are what the ceiling bought, and a
    page that refused them, or rendered fewer than it was asked for, would leave the budget
    spent on nothing.
    """
    run = chipped(store)
    path = plant(
        (
            "INSERT INTO agent_runs (SELECT a.* REPLACE (a.id || '-planted-' || i AS id)"
            " FROM agent_runs a, range(1, ?) t(i) WHERE a.session_id = ? AND a.id = ?)",
            [bounds.CHIPS.ceiling + 1, SPINE, run.run_id],
        ),
    )
    # A clone of a chipped run is a chip on the same turn: what the join reads is the tool call
    # it names, and the clones name the one the recorded run does.
    (forest,) = one(
        store,
        "SELECT count(*) FROM live_agent_runs WHERE session_id = ? AND parent_agent_id = ?",
        [SPINE, run.run_id],
    )
    planted_rows = 1 + bounds.CHIPS.ceiling + forest
    with TestClient(build_app(path)) as served:
        # The page of that one turn, opened at the cursor its permalink carries.
        page = served.get(
            f"/session/{SPINE}",
            params={"after": run.turn_index - 1, "turns": 1, "chips": bounds.CHIPS.ceiling},
        )
    assert page.status_code == 200
    # The turn renders the whole budget the URL asked for...
    assert values(page.text, "data-turn")[0] == run.turn_id
    assert len(values(page.text, "data-chip")) == bounds.CHIPS.ceiling
    # ...and counts what is still behind it, so the widest list is a page and not a ceiling.
    assert fields(page.text, "data-turn", run.turn_id)["cut"] == str(
        planted_rows - bounds.CHIPS.ceiling
    )


def test_a_session_the_store_does_not_hold_is_a_404(client: TestClient) -> None:
    """An id that matches nothing gets a 404, not an empty page pretending to be one."""
    response = client.get(f"/session/{MISSING}")
    assert response.status_code == 404
    assert MISSING not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/?sort=bogus",
        f"/session/{SPINE}",
        f"/session/{MISSING}",
        f"/fragment/turn/{ANCESTOR}/{MAIN}/{DENSE_TURN}",
        f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}",
        f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{MISSING}",
        "/static/style.css",
    ],
)
def test_every_response_carries_the_content_security_policy(path: str, client: TestClient) -> None:
    """The policy rides every response, error pages and static files included."""
    assert client.get(path).headers["content-security-policy"] == CSP


def test_planted_markup_arrives_inert(plant: Planter) -> None:
    """Text from a transcript is escaped everywhere it lands on a page or a fragment.

    The sentinels are invented — no redacted fixture carries markup — and each lands on a
    real row, so this checks the template chain rather than a hand-built page. `render.py`'s
    own leaves cannot stand in for this one: a template that piped a value through `|safe`
    would bypass them entirely, and only a rendered response shows it.
    """
    sentinel = "<script>alert('planted')</script>"
    path = plant(
        ("UPDATE sessions SET title = ? WHERE id = ?", [sentinel, SPINE]),
        ('UPDATE turns SET prompt = ? WHERE session_id = ? AND "index" = 0', [sentinel, SPINE]),
        ("UPDATE agent_runs SET description = ? WHERE session_id = ?", [sentinel, SPINE]),
        # The markdown path: what a model wrote, which is the one value the viewer renders
        # rather than escapes, and the tool arguments beside it.
        (
            "UPDATE api_calls SET text = ?, thinking = ? WHERE session_id = ?",
            [sentinel] * 2 + [ANCESTOR],
        ),
        (
            "UPDATE tool_calls SET input = ?, result = ? WHERE session_id = ?",
            [sentinel] * 2 + [FORK_ORIGIN],
        ),
        # The transcript itself, which the records browser previews and serves whole. Raw
        # records are the least filtered thing the viewer shows: what Claude Code wrote.
        ("UPDATE raw_records SET raw = ? WHERE session_id = ?", [sentinel, ANCESTOR]),
    )
    with TestClient(build_app(path)) as client:
        served = (
            client.get("/").text,
            client.get(f"/session/{SPINE}").text,
            client.get(f"/fragment/turn/{ANCESTOR}/{MAIN}/{DENSE_TURN}").text,
            client.get(f"/fragment/text/{ANCESTOR}/{MAIN}/{DENSE_TURN_CALL}").text,
            client.get(f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}").text,
            client.get(f"/session/{ANCESTOR}/records/{MAIN}").text,
            client.get(f"/fragment/record/{ANCESTOR}/{MAIN}/1").text,
        )
        for page in served:
            # The sentinel survives to the page as text — angle brackets escaped, the one form
            # Jinja, markdown-it and markupsafe all agree on...
            assert "&lt;script&gt;alert(" in page
            # ...and never as markup the browser would run.
            assert "<script>alert" not in page


def test_a_pr_link_is_a_link_only_when_a_browser_should_follow_it(plant: Planter) -> None:
    """A session's PR links are followable URLs; anything else on that list renders as text.

    A `pr_url` is the one transcript value that reaches an attribute the browser acts on, so
    escaping alone does not settle it — an escaped `javascript:` URL is still a `javascript:`
    URL in an `href`. Both values are planted and invented: the recorded sessions carry PR
    links redaction flattened to a placeholder.
    """
    followable = "https://example.test/org/repo/pull/1"
    unfollowable = "javascript:alert('planted')"
    path = plant(
        (
            "INSERT INTO pr_links VALUES"
            " (?, 900001, 1, ?, 'planted/repo', '2026-01-01T00:00:00Z'),"
            " (?, 900002, 2, ?, 'planted/repo', '2026-01-01T00:00:00Z')",
            [SPINE, followable, SPINE, unfollowable],
        ),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{SPINE}").text
    # The http URL is a link the reader can click...
    assert inside(page, "data-pr", followable, "href") == [followable]
    # ...and the other reaches no href at all, while still being shown for what it is.
    assert inside(page, "data-pr", unfollowable, "href") == []
    assert "javascript:alert(&#39;planted&#39;)" in page


def test_a_per_value_fragment_returns_the_one_value_it_names(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Opening one tool call fetches that call and nothing else from the same api call.

    The per-value routes are the exception to the payload bound — they ship a fat column
    whole — so what keeps the bound is that the unit really is one value. A fragment that
    quietly carried its siblings would be a page of them under another name.
    """
    siblings = [
        row[0]
        for row in store.execute(
            "SELECT id FROM live_tool_calls"
            " WHERE session_id = ? AND source = ? AND api_call_id = ?",
            [FORK_ORIGIN, FORK_ORIGIN_RUN, DENSE_CALL],
        ).fetchall()
    ]
    assert DENSE_TOOL in siblings and len(siblings) > 1
    served = client.get(f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}").text
    # The value it was asked for is there, with what the tool returned...
    assert values(served, "data-tool-value") == [DENSE_TOOL]
    assert fields(served, "data-tool-value", DENSE_TOOL)["result"]
    # ...and no sibling of the same call rode along with it.
    for other in siblings:
        assert other == DENSE_TOOL or other not in served


def test_a_fragment_cites_the_query_that_fetched_it(client: TestClient) -> None:
    """A fragment carries its own query and bindings, whole pages and nested lists alike.

    A fragment arrives on a page that has already been served, so it cannot ride the footer
    the pages share: each one carries the line itself.
    """
    # If a page of one turn's api calls is fetched at sizes of its own...
    turn = client.get(
        f"/fragment/turn/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL_TURN}",
        params={"calls": 2, "tools": 3},
    ).text
    keyed = f"session_id={FORK_ORIGIN} source={FORK_ORIGIN_RUN}"
    # ...then that call carries two lines: the query that fetched the call, at this request's
    # page, and — for the tool list nested under it — the second query those rows came from,
    # bound to the one call they hang off rather than to the turn above them.
    assert inside(turn, "data-api-call", DENSE_CALL, "data-query") == [
        f"-- queries/view_turn_calls.sql {keyed} turn_id={DENSE_CALL_TURN}"
        f" after={queries.FIRST_PAGE} page_calls=2",
        f"-- queries/view_call_tools.sql {keyed} api_call_id={DENSE_CALL}"
        f" after={queries.FIRST_PAGE} page_tools=3",
    ]
    # The same list fetched on its own — what the "+N more" asks for — cites the same query
    # at the cursor and size it was asked at, so a reader re-runs the page they are looking at.
    tools = client.get(
        f"/fragment/tools/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}",
        params={"after": 1, "tools": 2},
    ).text
    assert values(tools, "data-query") == [
        f"-- queries/view_call_tools.sql {keyed} api_call_id={DENSE_CALL} after=1 page_tools=2"
    ]
    # And a whole-value fragment, which takes no paging at all, cites the keys it was fetched
    # by. All four routes hand one shared seam their own keys, so each is here: a seam pinned
    # through `tool` alone would still let another route cite a key it was not fetched by.
    for url, expected in (
        (
            f"/fragment/text/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}",
            f"-- queries/view_call_text.sql {keyed} api_call_id={DENSE_CALL}",
        ),
        (
            f"/fragment/thinking/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}",
            f"-- queries/view_call_thinking.sql {keyed} api_call_id={DENSE_CALL}",
        ),
        (
            f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}",
            f"-- queries/view_tool_value.sql {keyed} tool_call_id={DENSE_TOOL}",
        ),
        # The record route keys on a line number rather than an id. Fetched off a subagent
        # thread at a line past the first, so neither key can be a constant the fixture hides.
        (
            f"/fragment/record/{SPINE}/{SPINE_RUN}/2",
            f"-- queries/view_record.sql session_id={SPINE} source={SPINE_RUN} line_no=2",
        ),
    ):
        assert values(client.get(url).text, "data-query") == [expected], url


def test_a_fragment_naming_nothing_is_a_404(client: TestClient) -> None:
    """A per-value fragment for an id the store lacks is a 404, not an empty box."""
    response = client.get(f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{MISSING}")
    assert response.status_code == 404
    assert MISSING not in response.text


def test_every_asset_a_page_asks_for_is_one_the_viewer_ships(client: TestClient) -> None:
    """No page reaches off the machine for an asset — the viewer works with the wifi off.

    A CDN reference is also the one thing that would make the CSP fail loudly in a browser
    and silently in this tier, so the check is on the templates rather than on a response.
    """
    # Every `src` and `href` a template writes is a path on this server...
    for template in sorted(TEMPLATES.glob("*.html")):
        remote = re.findall(r'(?:src|href)="(\w+:)?//[^"]*"', template.read_text())
        assert remote == [], template.name
    # ...and each asset the base page asks for is served, htmx included.
    page = client.get("/").text
    assets = re.findall(r'(?:src|href)="(/static/[^"]*)"', page)
    assert any("htmx" in asset for asset in assets), page
    for asset in assets:
        assert client.get(asset).status_code == 200, asset


def test_serving_the_store_leaves_it_read_only(corpus_db: Path, client: TestClient) -> None:
    """Nothing the viewer serves writes to the store it is pointed at."""
    before = corpus_db.stat().st_mtime_ns
    client.get("/")
    client.get(f"/session/{SPINE}")
    client.get(f"/session/{MISSING}")
    assert corpus_db.stat().st_mtime_ns == before
