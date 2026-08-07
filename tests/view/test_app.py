"""The two pages slice 1 serves: the session list, and one session's timeline.

Every expectation is derived from the store the app is serving rather than written down, so
a fixture added to the corpus does not silently stop being covered.
"""

import re
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view.app import (
    CSP,
    DEFAULT_DIRECTION,
    DEFAULT_SORT,
    DIRECTIONS,
    MAX_PAGE_SESSIONS,
    PAGE_SESSIONS,
    SORTS,
    TEMPLATES,
    build_app,
)
from tests.conftest import (
    ANCESTOR,
    DENSE_CALL,
    DENSE_TOOL,
    DENSE_TURN,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    FORK_RUN,
    MAIN,
    RESUME,
    SPINE,
)
from tests.view.conftest import Planter, fields, inside, one, values

# A session that does not exist, in the shape a session id has.
MISSING = "00000000-0000-0000-0000-000000000000"


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
    returned = {row[0] for row in store.execute(f"DESCRIBE ({listing})").fetchall()}
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
        "-- queries/view_sessions.sql sort=cost_usd direction=asc limit=5 offset=0"
    )
    # A bare request cites the defaults, so a copied line reproduces what was seen.
    default = fields(client.get("/").text, "id", "citation")
    assert default["view_sessions"] == (
        f"-- queries/view_sessions.sql sort={DEFAULT_SORT} direction={DEFAULT_DIRECTION}"
        f" limit={PAGE_SESSIONS} offset=0"
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
    assert str(MAX_PAGE_SESSIONS) in response.text


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


def test_every_session_page_accounts_for_all_of_its_runs(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Across the corpus, every agent run appears on its session's page exactly once."""
    for session_id in sessions(store):
        page = client.get(f"/session/{session_id}").text
        shown = values(page, "data-chip") + values(page, "data-unattached")
        runs = [
            row[0]
            for row in store.execute(
                "SELECT id FROM live_agent_runs WHERE session_id = ?", [session_id]
            ).fetchall()
        ]
        assert sorted(shown) == sorted(runs), session_id


def test_the_unattached_section_is_the_chip_joins_complement(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run is listed as unattached exactly when the chip join fails to place it."""
    listing = queries.load("view_runs").strip().rstrip(";")
    for session_id in sessions(store):
        unplaced = {
            row[0]
            for row in store.execute(
                f"SELECT run_id FROM ({listing}) WHERE spawn_turn_id IS NULL",
                {"session_id": session_id},
            ).fetchall()
        }
        page = client.get(f"/session/{session_id}").text
        assert set(values(page, "data-unattached")) == unplaced, session_id


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
    )
    with TestClient(build_app(path)) as client:
        served = (
            client.get("/").text,
            client.get(f"/session/{SPINE}").text,
            client.get(f"/fragment/turn/{ANCESTOR}/{MAIN}/{DENSE_TURN}").text,
            client.get(f"/fragment/text/{ANCESTOR}/{MAIN}/{DENSE_TURN_CALL}").text,
            client.get(f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}").text,
        )
        for page in served:
            # The sentinel survives to the page as text — angle brackets escaped, the one form
            # Jinja, markdown-it and markupsafe all agree on...
            assert "&lt;script&gt;alert(" in page
            # ...and never as markup the browser would run.
            assert "<script>alert" not in page


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
