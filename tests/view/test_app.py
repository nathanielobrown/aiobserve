"""The app around the node browser: the landing page, the session list, and what every
response owes a reader — escaped transcript text, a citation per query, and assets the
viewer ships itself.

Every expectation is derived from the store the app is serving rather than written down, so
a fixture added to the corpus does not silently stop being covered. The node pages
themselves live in `test_node.py`; the tree beside them in `test_tree.py`.
"""

import datetime as dt
import json
import re
from collections import defaultdict
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view import app as view_app
from aiobserve.view import bounds, nodes
from aiobserve.view import format as fmt
from aiobserve.view.app import CSP, TEMPLATES, build_app
from aiobserve.view.format import ABSENT
from aiobserve.view.labels import LABELS
from aiobserve.view.listing import (
    ARIA_SORT,
    DEFAULT_DIRECTION,
    DEFAULT_SORT,
    DIRECTIONS,
    FILTERS,
    LIST_KEYS,
    SORTS,
)
from tests.conftest import (
    ANCESTOR,
    BASH_TOOL,
    DENSE_CALL,
    DENSE_CALL_TURN,
    DENSE_TOOL,
    DENSE_TURN,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    HOME,
    MAIN,
    MYCELIA,
    NO_PROJECT_SESSION,
    SLASH_TURN,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
)
from tests.view.conftest import (
    MISSING,
    Planter,
    Statement,
    fields,
    inside,
    one,
    pages,
    suggestions,
    values,
)

# What every list citation says about the display cut, which the viewer composes around the
# query the same way it composes the paging: re-running the file alone answers whole values.
CUT = (
    f"head_chars={queries.LIST_CHARS} item_chars={queries.LIST_ITEM_CHARS}"
    f" head_items={queries.LIST_ITEMS}"
)


def sessions(store: duckdb.DuckDBPyConnection) -> list[str]:
    """Every session in the store in the list's default order: newest first, empties last.

    A session the store gave no start sorts to the bottom whichever way the list is ordered:
    "the store does not know" is not a date, and a row that carries none is not the newest
    thing that happened.
    """
    return [
        row[0]
        for row in store.execute(
            "SELECT session_id FROM session_rollups"
            " ORDER BY started_at DESC NULLS LAST, session_id DESC"
        ).fetchall()
    ]


def money(amount: float) -> str:
    """A cost as the pages print it."""
    return f"${amount:.2f}"


def counted(value: int) -> str:
    """A count as the pages print it: thousands separated."""
    return f"{value:,}"


def test_the_list_holds_every_session_with_its_own_numbers(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The list is one row per session, its counts stacked two to a cell over that session's
    rollup — the primary someone scans for, and the texture under it."""
    page = client.get("/sessions").text
    # Every session gets a row, and the default order is newest first...
    assert values(page, "data-session-id") == sessions(store)
    # ...whose cells are that session's rollup, not a number computed anywhere else.
    row = fields(page, "data-session-id", SPINE)
    turns, api_calls, tool_calls, compactions, cost, tokens, wall, active, started = one(
        store,
        "SELECT turns, api_calls, tool_calls, compactions, cost_usd, output_tokens,"
        " wall_ms, active_ms, started_at FROM session_rollups WHERE session_id = ?",
        [SPINE],
    )
    (errors,) = one(
        store,
        "SELECT count(*) FROM live_tool_calls WHERE session_id = ? AND is_error",
        [SPINE],
    )
    # The four plain counts, each through the same formatter every count on the page uses...
    assert row["turns"] == counted(turns)
    assert row["api_calls"] == counted(api_calls)
    assert row["tool_calls"] == counted(tool_calls)
    assert row["compactions"] == counted(compactions)
    # ...the stacked cells, whose secondary is the texture the recompose demoted rather than
    # dropped: what the errors were a rate of, what the spend bought, how long of the wall
    # clock was work. `tests/view/test_format.py` owns what each of these strings looks like;
    # this leaf owns which of the session's values reaches which cell.
    assert (row["error_rate"], row["tool_errors"]) == (fmt.share(errors, tool_calls), str(errors))
    assert (row["cost_usd"], row["output_tokens"]) == (money(cost), counted(tokens))
    assert (row["wall_ms"], row["active_ms"]) == (fmt.duration(wall), fmt.duration(active))
    assert row["started_at"] == fmt.when(started)


def test_a_column_the_store_left_null_reads_as_one_dash(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A cell over a column the store holds nothing in prints a dash, not "None" or a blank.

    `fork_byref`'s fork is the recorded case on the list: it carries neither a project
    directory nor a start, so its row is the one place the list has to say "the store does not
    know" out loud. A run is the recorded case on a node page — most spawning calls name no
    model — so a run's own pane is checked here too, against the same convention rather than
    against each template's own idea of a gap.
    """
    row = fields(client.get("/sessions").text, "data-session-id", NO_PROJECT_SESSION)
    assert row["project_dir"] == ABSENT
    assert row["started_at"] == ABSENT
    pane = fields(client.get(f"/session/{SPINE}/run/{SPINE_LEAF}").text, "data-body", "run")
    assert pane["model"] == ABSENT
    # And no page the store can serve prints a Python value anywhere: the three cells above are
    # the columns this corpus records a gap in, and a template that renders a NULL straight is
    # one recording away from showing `None` to a reader.
    for url in pages(store):
        assert ">None<" not in client.get(url).text, url


def test_a_project_directory_folds_the_readers_home_and_still_links_whole(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project cell prints `~` for the home of whoever is reading, and nothing else does.

    Every row of one person's corpus repeats the same home directory, which is column width
    spent on a constant — and the project column is the one that squeezes the lists beside it.
    The fold is display alone: the row's own attribute, the link the landing page mints and
    the box that suggests a filter all carry the path the store holds, because a filter
    matches that path and not a reader's shorthand for it.
    """
    monkeypatch.setattr(fmt, "home", lambda: HOME)
    listed, landing = client.get("/sessions").text, client.get("/").text
    folded = "~/repos/mycelia"
    assert fields(listed, "data-session-id", SPINE)["project_dir"] == folded
    assert fields(landing, "data-project", MYCELIA)["project_dir"] == folded
    # The session's own page says where it ran in the same words its row does.
    session = fields(client.get(f"/session/{SPINE}").text, "data-body", "session")
    assert session["project_dir"] == folded
    # What a reader clicks or types is untouched: the row is keyed by the stored path, the
    # link filters on it, and the box offers it.
    (link,) = set(inside(landing, "data-project", MYCELIA, "href"))
    assert parse_qs(urlsplit(link).query)["project"] == [MYCELIA]
    assert MYCELIA in suggestions(listed)
    # Read from anywhere else, the same cell prints the path whole. The fold is this reader's
    # own home and not a rule about any directory two levels under `/Users`.
    monkeypatch.setattr(fmt, "home", lambda: f"{HOME}ody")
    assert fields(client.get("/sessions").text, "data-session-id", SPINE)["project_dir"] == MYCELIA


def test_the_list_reads_the_clock_at_render_rather_than_at_startup(
    client: TestClient, store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """How long ago a session ran is measured against the clock this request read.

    A viewer left open is a long-lived process, so a clock captured when the app was built
    would freeze every row's freshness at whenever the server started. Two requests against
    two clocks, one app: the answers have to move.
    """
    (started,) = one(store, "SELECT started_at FROM session_rollups WHERE session_id = ?", [SPINE])

    def elapsed(later: dt.timedelta) -> str:
        monkeypatch.setattr(fmt, "utcnow", lambda: started + later)
        return fields(client.get("/sessions").text, "data-session-id", SPINE)["ago"]

    assert elapsed(dt.timedelta(hours=2)) == "2h ago"
    assert elapsed(dt.timedelta(days=3)) == "3d ago"


def test_the_errors_cell_shows_a_rate_over_the_count_it_sorts_by(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A row's errors read as a share of the tools it ran, over the count itself.

    Both recorded failing-tool sessions, because the pair is what makes the rate worth
    showing: one error in five calls and one in seven are the same count and different rates.
    """
    page = client.get("/sessions").text
    failing = store.execute(
        "SELECT * FROM (SELECT r.session_id, r.tool_calls,"
        " (SELECT count(*) FROM live_tool_calls t"
        "  WHERE t.session_id = r.session_id AND t.is_error) AS errors"
        " FROM session_rollups r) WHERE errors > 0"
    ).fetchall()
    assert len(failing) > 1, "the fixture corpus no longer records two failing sessions"
    for session_id, tool_calls, errors in failing:
        row = fields(page, "data-session-id", session_id)
        assert row["error_rate"] == fmt.share(errors, tool_calls)
        assert row["tool_errors"] == counted(errors)
    # The rates differ, so a cell showing the count where the rate belongs would fail above.
    assert len({fields(page, "data-session-id", row[0])["error_rate"] for row in failing}) > 1


def test_every_number_a_list_row_prints_carries_its_separators(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """Every integer a row prints goes through the count formatter — no bare `{{ row.int }}`.

    Planted, because no fixture session is large enough to tell a formatted count from an
    unformatted one: the corpus's busiest session ran 78 turns. One session's turns and api
    calls are cloned past a thousand, which is where the two spellings diverge.
    """
    over = 1_000
    path = plant(
        # Cloning recorded rows rather than inventing them: what a row counts is the
        # `live_*` population, and a clone of a real row is a member of it.
        (
            "INSERT INTO turns (SELECT t.* REPLACE (t.id || '-planted-' || i AS id)"
            " FROM turns t, range(1, ?) r(i) WHERE t.session_id = ?"
            " AND t.id = (SELECT min(id) FROM turns WHERE session_id = ?))",
            [over + 1, SPINE, SPINE],
        ),
        (
            "INSERT INTO api_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id)"
            " FROM api_calls c, range(1, ?) r(i) WHERE c.session_id = ?"
            " AND c.id = (SELECT min(id) FROM api_calls WHERE session_id = ?))",
            [over + 1, SPINE, SPINE],
        ),
    )
    with TestClient(build_app(path)) as planted:
        row = fields(planted.get("/sessions").text, "data-session-id", SPINE)
    # Every number the row prints is either grouped in threes or the dash a NULL prints...
    counts = ("turns", "api_calls", "tool_calls", "compactions", "tool_errors", "output_tokens")
    for field in counts:
        assert re.fullmatch(r"\d{1,3}(,\d{3})*|—", row[field]), f"{field} prints {row[field]!r}"
    # ...and the plant really did push two of them past the point where that is a claim.
    assert "," in row["turns"] and "," in row["api_calls"]


def test_the_subagents_cell_counts_the_runs_of_each_agent_type(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A row says which agent types the session spawned and how many runs of each.

    The count is what the recompose bought: `agent_runs` alone said a session spawned six
    subagents and not what any of them were.
    """
    row = fields(client.get("/sessions").text, "data-session-id", SPINE)
    kinds = store.execute(
        "SELECT agent_type, count(*) FROM live_agent_runs WHERE session_id = ?"
        " GROUP BY 1 ORDER BY 2 DESC, 1",
        [SPINE],
    ).fetchall()
    assert kinds, "the fixture session no longer spawns any agent runs"
    assert row["agent_types"] == ", ".join(f"{name} ×{runs}" for name, runs in kinds)


def test_the_subagents_cell_ranks_by_count_and_says_what_it_cut(plant: Planter) -> None:
    """The list is ordered by runs descending and cut like the skills beside it.

    Planted twice over: no fixture session runs one agent type twice, and none spawns more
    types than the cell shows. Both are properties of a redacted corpus rather than of the
    store the viewer serves, so the row is built to have them.
    """
    over = queries.LIST_ITEMS + 2
    path = plant(
        # One recorded run cloned into `over` types of its own, the kth spawned k times: more
        # types than the cell shows, no two of them tied, so the order it shows them in is a
        # claim rather than an accident.
        (
            "INSERT INTO agent_runs (SELECT a.* REPLACE ("
            " a.id || '-planted-' || i || '-' || j AS id, 'planted-' || i AS agent_type)"
            " FROM agent_runs a, range(1, ?) r(i), range(1, ?) s(j)"
            " WHERE j <= i AND a.session_id = ?"
            " AND a.id = (SELECT min(id) FROM agent_runs WHERE session_id = ?))",
            [over + 1, over + 1, SPINE, SPINE],
        ),
    )
    with TestClient(build_app(path)) as planted:
        row = fields(planted.get("/sessions").text, "data-session-id", SPINE)
    listed = row["agent_types"].split(" and ")[0].split(", ")
    counts = [int(entry.rsplit(" ×", 1)[1]) for entry in listed]
    # As many types as the cell shows, no more, ranked by the runs each stood for...
    assert len(listed) == queries.LIST_ITEMS
    assert counts == sorted(counts, reverse=True) and len(set(counts)) == len(counts)
    # ...and a tail counting the types it left out rather than dropping them silently. Two
    # recorded types sit under the planted ones, which is what the cut has to reach past.
    assert row["agent_types"].endswith(f"and {over + 2 - queries.LIST_ITEMS} more")


def test_a_list_row_links_to_the_session_it_names(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The link on a row opens that session's page — the list's whole purpose."""
    page = client.get("/sessions").text
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
def test_a_sort_and_its_reverse_are_exact_opposites(
    sort: str, client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Every sort key totally orders the rows that carry a value, so flipping the direction
    reverses them — and the rows carrying none sit at the end of both.

    Two claims rather than one, because the empty rows are pinned. A session the store knows
    nothing about is not the newest, the cheapest or the busiest, so it sorts last whichever
    way the reader asked; the rows that do carry a value are the ones reversal is about.
    """
    # Which sessions carry no value in this column, asked of the query the list ranks rather
    # than of a table beside it: two of the eleven keys are the query's own arithmetic.
    listing = queries.load("view_sessions").strip().rstrip(";")
    defaults = {
        name: spec.default for name, spec in queries.QUERIES["view_sessions"].params.items()
    }
    empty = {
        row[0]
        for row in store.execute(
            f"SELECT session_id FROM ({listing}) WHERE {sort} IS NULL", defaults
        ).fetchall()
    }
    order = {
        direction: values(
            client.get("/sessions", params={"sort": sort, "direction": direction}).text,
            "data-session-id",
        )
        for direction in DIRECTIONS
    }
    assert len(order["asc"]) > 1
    valued = {
        direction: [row for row in rows if row not in empty] for direction, rows in order.items()
    }
    assert valued["asc"] == list(reversed(valued["desc"]))
    # And the empties trail both lists, rather than riding to the top of one of them.
    for direction, rows in order.items():
        assert set(rows[len(valued[direction]) :]) == empty & set(rows), direction


@pytest.mark.parametrize("direction", sorted(DIRECTIONS))
def test_the_sorted_heading_says_which_way_in_arias_own_words(
    direction: str, client: TestClient
) -> None:
    """The column in force is the only one marked `aria-sort`, in the words ARIA defines.

    The query string's `asc` and `desc` are ours; `ascending` and `descending` are the tokens
    a screen reader reads. An invalid token is not read as "unsorted" — it is read as nothing
    at all, which is the one thing the mark exists to prevent.
    """
    page = client.get("/sessions", params={"sort": "cost_usd", "direction": direction}).text
    marked = re.findall(r'<th[^>]*\bdata-column="([^"]*)"[^>]*\baria-sort="([^"]*)"', page)
    assert marked == [("cost_usd", ARIA_SORT[direction])]
    # And the vocabulary is ARIA's, not a rewording of ours that happens to be longer.
    assert ARIA_SORT[direction] in {"ascending", "descending"}


@pytest.mark.parametrize(
    "parameters",
    [
        # A key that is not in the closed dict, however plausible...
        {"sort": "session_id"},
        # ...the two the recompose demoted to secondary lines, which the query still returns
        # and the list no longer offers — a stale bookmark, not an injection...
        {"sort": "output_tokens"},
        {"sort": "active_ms"},
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
    response = client.get("/sessions", params=parameters)
    assert response.status_code == 400
    # The refusal says what is allowed, and does not echo what was asked for.
    for value in parameters.values():
        assert value not in response.text
    assert DEFAULT_SORT in response.text


def test_the_list_footer_cites_its_query_and_what_was_composed_around_it(
    client: TestClient,
) -> None:
    """The page carries the query behind it, at the sort and the page this request ran."""
    page = client.get("/sessions", params={"sort": "cost_usd", "direction": "asc", "size": 5}).text
    cited = fields(page, "id", "citation")
    assert cited["view_sessions"] == (
        f"-- queries/view_sessions.sql sort=cost_usd direction=asc limit=5 offset=0 {CUT}"
    )
    # A bare request cites the defaults, so a copied line reproduces what was seen.
    default = fields(client.get("/sessions").text, "id", "citation")
    assert default["view_sessions"] == (
        f"-- queries/view_sessions.sql sort={DEFAULT_SORT} direction={DEFAULT_DIRECTION}"
        f" limit={bounds.SESSIONS.default} offset=0 {CUT}"
    )


def test_the_list_is_served_a_page_at_a_time(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The pages of the list tile the store in order: no row twice, none missing.

    Turned the way a reader turns them: the "older page" href the list itself minted is the
    string fetched, unescaped as a browser would unescape it. A test that built its own
    `?page=` would tile the store just as well against a pager pointing at the wrong page,
    so following the link is what puts the link under test.
    """
    size = 5
    seen: list[str] = []
    url = f"/sessions?size={size}"
    # A size the fixture corpus needs four pages of, followed to the end...
    for _ in range(9):
        html = client.get(url).text
        rows = values(html, "data-session-id")
        assert len(rows) <= size
        seen += rows
        onward = {unescape(href) for href in inside(html, "data-page", "next", "href")}
        if not onward:
            break
        # The pager above the table and the one below it offer the same next page.
        assert len(onward) == 1
        url = onward.pop()
    else:
        pytest.fail("the pager never ran out of pages")
    # ...holds every session once, in the order one long list would have had.
    assert seen == sessions(store)
    # Past the end is an empty page rather than an error: a stale link is not a fault...
    beyond = client.get("/sessions", params={"page": 99}).text
    assert values(beyond, "data-session-id") == []
    # ...and it says so, rather than counting a range that ends before it starts.
    assert fields(beyond, "data-pager", "top")["range"] == "No sessions"


def test_the_pager_counts_a_store_deeper_than_a_page_with_separators(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """The range the pager prints goes through the formatter every count on a page does.

    Planted, because the fixture corpus holds sixteen sessions and the store this list is read
    against holds thousands: under a thousand a formatted range and a bare one are the same
    string. The clones are of a recorded session, so each one is a row the list really builds.
    """
    over = 1_200
    path = plant(
        (
            "INSERT INTO sessions (SELECT s.* REPLACE (s.id || '-planted-' || i AS id)"
            " FROM sessions s, range(1, ?) t(i) WHERE s.id = ?)",
            [over + 1, SPINE],
        ),
    )
    size, page_number = bounds.SESSIONS.default, 11
    with TestClient(build_app(path)) as planted:
        page = planted.get("/sessions", params={"size": size, "page": page_number}).text
    first = (page_number - 1) * size + 1
    last = first + len(values(page, "data-session-id")) - 1
    # A page deep into the list says which rows of it these are, both ends grouped in threes.
    assert first > 1_000, "the plant no longer reaches past a thousand rows"
    assert fields(page, "data-pager", "top")["range"] == f"Sessions {first:,}–{last:,}"


@pytest.mark.parametrize("parameters", [{"page": 0}, {"page": -1}, {"size": 0}, {"size": 100_000}])
def test_a_page_outside_the_bounds_is_refused(
    parameters: dict[str, int], client: TestClient
) -> None:
    """The page size is bounded on both ends: a page cannot be asked to hold the store."""
    response = client.get("/sessions", params=parameters)
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
    whole = values(client.get("/sessions").text, "data-session-id")
    narrowed = values(client.get("/sessions", params={key: SAMPLES[key]}).text, "data-session-id")
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
    shown = values(
        client.get("/sessions", params={"skill": SAMPLES["skill"]}).text, "data-session-id"
    )
    assert set(shown) == ran_it
    # Every row shown says the skill it was filtered by, so the page shows its own evidence.
    for session_id in shown:
        page = client.get("/sessions", params={"skill": SAMPLES["skill"]}).text
        assert SAMPLES["skill"] in fields(page, "data-session-id", session_id)["skills"]


# The filters whose predicates a value could break out of, one per shape: `skill` binds its
# parameter once, `project` binds the same one twice and concatenates it, which is the place
# a value spliced as text would have two chances to become SQL.
@pytest.mark.parametrize("key", ["skill", "project"])
def test_a_filter_value_reaches_duckdb_only_as_a_binding(
    key: str, client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A filter value that is SQL rather than a name matches nothing and runs nothing."""
    before = one(store, "SELECT count(*) FROM sessions")[0]
    response = client.get("/sessions", params={key: "'; DROP TABLE sessions; --"})
    # A value that reached SQL as text would either error or execute; bound, it is a name
    # no session carries...
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
    response = client.get("/sessions", params=parameters)
    assert response.status_code == 400
    # The refusal says what would have worked, and never echoes what was asked for — a page
    # that reflected the value back would be the one place unescaped request text could land.
    assert says in response.text
    for value in parameters.values():
        assert value not in response.text


def test_a_form_submitted_with_every_key_filled_in_is_still_a_narrowing(
    client: TestClient,
) -> None:
    """Every key the list reads, sent at once, is a legal request rather than a 400.

    The filter form posts all five filters and rides the sort, the page and the size, so a
    reader who types into every box sends the whole of `LIST_KEYS` — the boundary the
    membership test sits on. The samples are the same recorded values the filter leaves use,
    so the request that comes back is a real cut of the corpus and not an empty page.
    """
    filled = dict(SAMPLES) | {"sort": "cost_usd", "direction": "asc", "page": "1", "size": "5"}
    assert filled.keys() == LIST_KEYS, "the list reads a key this leaf does not fill in"
    response = client.get("/sessions", params=filled)
    assert response.status_code == 200
    # It narrowed rather than merely surviving: the corpus is wider than what came back.
    shown = values(response.text, "data-session-id")
    assert shown
    assert set(shown) < set(values(client.get("/sessions").text, "data-session-id"))


def test_a_filter_rides_the_links_and_the_citation(client: TestClient) -> None:
    """A filter survives re-sorting and paging, and the footer says the list was filtered."""
    page = client.get(
        "/sessions", params={"skill": SAMPLES["skill"], "sort": "cost_usd", "size": 1}
    ).text
    # Every heading link and every pager link carries the filter, so changing the order or
    # turning the page does not quietly widen the list back to the corpus...
    links = re.findall(r'href="(/sessions\?[^"]*)"', page)
    assert links
    for link in links:
        assert "skill=grill-me" in link
    # The list lives at `/sessions` whole — its form, its clear link and every link it mints
    # go there. A `/?sort=` survivor would land on the projects page, which answers a
    # different question and would drop the filter on the way.
    assert re.findall(r'href="(/\?[^"]*)"', page) == []
    assert '<form id="filters" method="get" action="/sessions">' in page
    assert '<a href="/sessions">clear</a>' in page
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
    pane = fields(page, "data-body", "session")
    title, turns, agent_runs, cost = one(
        store,
        "SELECT s.title, r.turns, r.agent_runs, r.cost_usd FROM sessions s"
        " JOIN session_rollups r ON r.session_id = s.id WHERE s.id = ?",
        [SPINE],
    )
    # The title is what the node is called rather than a fact under it, so it heads the pane.
    assert fields(page, "data-body", "session")["title"] == title
    assert pane["turns"] == str(turns)
    assert pane["agent_runs"] == str(agent_runs)
    assert pane["cost_usd"] == money(cost)


def test_a_header_labels_its_facts_in_words(client: TestClient) -> None:
    """A header names each fact the way a reader says it, with the store's column beside it.

    Both halves, because they answer to different readers: the `<dt>` is what a person reads
    and the `data-field` is what the rest of this suite reads a header by, so neither can drift
    into the other. `wall_ms` is the case that forces the split — the value under it already
    prints as `24h 25m`, and a label ending in `_ms` contradicts the cell it stands over.
    """
    labelled = dict(
        re.findall(
            r"<dt>([^<]*)</dt><dd data-field=\"([^\"]+)\"", client.get(f"/session/{SPINE}").text
        )
    )
    assert labelled["Wall time"] == "wall_ms"
    assert labelled["Session"] == "session_id"
    assert labelled["Cost"] == "cost_usd"


def test_every_fact_a_header_asks_for_has_a_label() -> None:
    """The label registry is closed over the templates: no extra entries, and no missing ones.

    A header field with no label would reach a reader as a column name, which is the thing
    `LABELS` exists to stop, and an entry nothing asks for is a word nobody sees. Read off the
    templates, the panes and the log's column table rather than listed here, so a fact added
    to any of them lands in this check. The panes are a source because a previewed value is
    labelled by the name the route passed it under, which no template holds; the column table
    is one because a children log heads itself from a variable, which no regex over a template
    can see.
    """
    asked = {
        name
        for path in TEMPLATES.rglob("*.html")
        for name in re.findall(r"(?:parts\.fact|label)\('([a-z_]+)'", path.read_text())
    }
    previewed = set(re.findall(r'detail_of\(\s*"([a-z_]+)"', Path(view_app.__file__).read_text()))
    headed = {column.field for columns in nodes.COLUMNS.values() for column in columns}
    assert asked | previewed | headed == set(LABELS)


def test_a_column_that_prints_a_length_says_so_in_its_heading() -> None:
    """A column of bare numbers has to name its unit, or the number is unreadable.

    A children log prints lengths where the page under it prints the values — `text_chars` is
    how much the model said, `result_chars` how much a tool answered. Heading either with the
    word the pane gives the value itself leaves a reader deciding whether the column counts
    characters, calls or answers. Read off the column table, so a length column added to any
    shape lands in this check.
    """
    lengths = {
        column.field
        for columns in nodes.COLUMNS.values()
        for column in columns
        if column.field.endswith("_chars")
    }
    assert lengths, "the log heads no length column, so this contract has no subject"
    for field in lengths:
        assert "chars" in LABELS[field].lower(), field


def test_every_filter_the_app_registers_is_one_a_template_names() -> None:
    """A filter is registered so a template can name it, so every registration has a caller.

    The formatters themselves are Python one page or another calls directly; what this closes
    is the Jinja registry, where a filter nothing names is a name in the environment of every
    render for no reader. Read off the app's own registration block and the templates rather
    than listed here, so a filter added to either lands in this check.
    """
    source = Path(view_app.__file__).read_text()
    block = source.partition("templates.env.filters |= {")[2].partition("}")[0]
    # Both halves read a Python identifier, not a word: a filter named `to_json` or `md2` has
    # to reach both sides of this comparison or the leaf passes by never seeing it.
    registered = set(re.findall(r'"(\w+)":', block))
    assert len(registered) > 5, "the registration block is not where this expects it"
    named = {
        name
        for path in TEMPLATES.rglob("*.html")
        for name in re.findall(r"\|\s*(\w+)", path.read_text())
    }
    assert not registered - named


def test_every_number_a_header_prints_carries_its_separators(plant: Planter) -> None:
    """A header's counts go through the same formatter every count on a page does.

    Both panes, because they show the same rollup of two different threads: a session's, and
    one run's. Planted, because the busiest thread the corpus records made a handful of
    calls — under a thousand a formatted count and a bare one are the same string. The clones
    are of recorded rows, so what a header counts stays the `live_*` population it counts today.
    """
    over = 1_000

    def cloned(table: str, source: str) -> Statement:
        # One recorded row of that thread, cloned past the point the two spellings diverge.
        return (
            f"INSERT INTO {table} (SELECT t.* REPLACE (t.id || '-planted-' || i AS id)"
            f" FROM {table} t, range(1, ?) r(i) WHERE t.session_id = ? AND t.id ="
            f" (SELECT min(id) FROM {table} WHERE session_id = ? AND source = ?))",
            [over + 1, SPINE, SPINE, source],
        )

    path = plant(
        *(
            cloned(table, source)
            for table in ("turns", "api_calls", "tool_calls")
            for source in (MAIN, SPINE_RUN)
        )
    )
    with TestClient(build_app(path)) as planted:
        session = fields(planted.get(f"/session/{SPINE}").text, "data-body", "session")
        run = fields(planted.get(f"/session/{SPINE}/run/{SPINE_RUN}").text, "data-body", "run")
    # Every number either header prints is grouped in threes or the dash a NULL prints...
    counted = ("turns", "api_calls", "tool_calls", "tool_errors", "compactions", "output_tokens")
    for header, name in ((session, "session"), (run, "run")):
        for field in (*counted, "unpriced_api_calls"):
            assert re.fullmatch(r"\d{1,3}(,\d{3})*|—", header[field]), (name, field, header[field])
        # ...and the plant pushed three of them past the point where that is a claim.
        assert all("," in header[field] for field in counted[:3]), name


def test_a_node_page_cites_every_query_it_ran(client: TestClient) -> None:
    """A node page's footer holds one re-runnable line per query behind it.

    The session node is the case with the most reads behind one page: its own header, the
    level of the tree under it, and the runs and compactions every level needs to place. Each
    line carries the bindings this request made rather than the query file's defaults, which
    is what makes it a citation and not a filename.
    """
    page = client.get(f"/session/{SPINE}", params={"log": 3}).text
    assert fields(page, "id", "citation") == {
        "view_session_header": (
            f"-- queries/view_session_header.sql session_id={SPINE}"
            " head_chars=100 item_chars=60 head_items=5"
        ),
        "view_tree_turns": (
            f"-- queries/view_tree_turns.sql session_id={SPINE} source={MAIN}"
            f" nav_chars={queries.NAV_CHARS}"
        ),
        # A run is printed twice on this page — as a tree row and as a children log row — so
        # the citation says which of the two widths this request read them at: the wider.
        "view_runs": f"-- queries/view_runs.sql session_id={SPINE} chip_chars={queries.LOG_CHARS}",
        "view_compactions": (
            f"-- queries/view_compactions.sql session_id={SPINE} source={MAIN}"
            f" chip_chars={queries.NAV_CHARS}"
        ),
        # The whole thread in outline, which is what places the runs: no window, so no paging.
        "session_digest": (
            f"-- queries/session_digest.sql session_id={SPINE} log_chars={queries.LOG_CHARS}"
        ),
    }


def test_every_id_a_url_carries_is_named_by_the_word_in_front_of_it(client: TestClient) -> None:
    """Every id in a path has a word in front of it saying what kind of id it is.

    The one rule the URL scheme is built on (`docs/viewer.md`), and it has two halves. No two
    ids sit side by side: read a path that breaks that and the eye pairs the segments the wrong
    way — a turn and something under it, where the second id is really the thread the turn is
    on. And the word in front *names* the id, which is what the first half alone does not say:
    `/session/{session_id}/unattributed/{source}` puts no two ids together and still calls a
    thread by the name of the bucket hanging off it.

    Naming is checked across the table rather than against a list of words, which would be the
    rule written twice: an id kind that follows two different words is one of the two lying.
    That catches a word changed at one route and misses a parameter used at exactly one — for
    those, the closed registry in `test_bounds.py` is what holds the shape.

    `{kind}` is the one parameter that counts as a word rather than an id: it carries a member
    of `nodes.Kind`, and every one of those is a bare literal segment.
    """
    assert all(str(kind).isalpha() for kind in nodes.Kind)
    routes = [route for route in client.app.routes if isinstance(route, APIRoute)]  # pyrefly: ignore
    assert routes, "the app exposes no routes"
    naming: dict[str, set[str]] = defaultdict(set)
    for path in sorted(route.path for route in routes):
        segments = ["kind" if part == "{kind}" else part for part in path.split("/") if part]
        for at, part in enumerate(segments):
            if not part.startswith("{"):
                continue
            assert at, f"{path} opens on an id nothing names"
            assert not segments[at - 1].startswith("{"), f"{path} puts two ids side by side"
            # The parameter's own name, past the converter an offloaded file path carries.
            naming[part.strip("{}").partition(":")[0]].add(segments[at - 1])
    assert naming, "no route carries an id"
    for parameter, words in sorted(naming.items()):
        assert len(words) == 1, f"{parameter} is called {sorted(words)} at different routes"


# The ratio WCAG 2.2 asks of body text against what it is printed on. Both schemes are held
# to it: a dark page is a page someone reads, not a courtesy.
READABLE = 4.5
# How much of the accent the one wash a page composes carries — `:target` on a record, and a
# hovered node — over whatever surface it lands on.
WASH = 0.12


def _channel(value: int) -> float:
    """One sRGB channel, linearised — the relative-luminance formula's own step."""
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def _contrast(ink: str, surface: str) -> float:
    lit, dark = sorted((_luminance(ink), _luminance(surface)), reverse=True)
    return (lit + 0.05) / (dark + 0.05)


def _over(ink: str, surface: str, part: float) -> str:
    """`color-mix(in srgb, ink part%, transparent)` painted over an opaque surface."""
    mixed = (
        round(
            int(ink[index : index + 2], 16) * part
            + int(surface[index : index + 2], 16) * (1 - part)
        )
        for index in (1, 3, 5)
    )
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def test_both_schemes_print_every_color_of_text_readably(client: TestClient) -> None:
    """Every color the stylesheet sets text in clears 4.5:1 over every surface it lands on.

    Read off the served stylesheet rather than written down, so a token retuned for one scheme
    cannot quietly darken the other. The surfaces are the page itself and the one wash the
    sheet composes rather than names — 12% of the accent, which a targeted record and a
    hovered node are both painted with, and which is where `--dim` comes closest to failing.
    A chip's outline is its own text color (`currentColor`), so it clears whatever this does.
    """
    sheet = client.get("/static/style.css").text
    # Tokens are declared in exactly two places, and dark restates only what it changes.
    head, _, tail = sheet.partition("prefers-color-scheme: dark")

    def read(block: str) -> dict[str, str]:
        return dict(re.findall(r"--([a-z]+):\s*(#[0-9a-f]{6})", block))

    light = read(head)
    schemes = {"light": light, "dark": light | read(tail)}
    assert set(light) == {"ink", "dim", "line", "paper", "mark", "bad"}
    for scheme, tokens in schemes.items():
        surfaces = {
            "the page": tokens["paper"],
            "the wash": _over(tokens["mark"], tokens["paper"], WASH),
        }
        for role in ("ink", "dim", "mark", "bad"):
            for where, surface in surfaces.items():
                ratio = _contrast(tokens[role], surface)
                assert ratio >= READABLE, f"{scheme} --{role} on {where}: {ratio:.2f}:1"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/sessions",
        "/sessions?sort=bogus",
        f"/session/{SPINE}",
        f"/session/{MISSING}",
        f"/session/{ANCESTOR}/thread/{MAIN}/turn/{DENSE_TURN}",
        f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{MISSING}",
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
        # Both columns a turn's heading can read: a plain turn shows the prompt, a slash turn
        # shows what followed the command instead, and neither may reach the page as markup.
        (
            "UPDATE turns SET prompt = ?, command_args = ? WHERE session_id = ?",
            [sentinel, sentinel, SPINE],
        ),
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
            client.get("/sessions").text,
            # The session pane, whose tree rows are named by the turn prompts and the run
            # descriptions the plant rewrote.
            client.get(f"/session/{SPINE}").text,
            # A turn pane, whose children log previews the calls' text.
            client.get(f"/session/{ANCESTOR}/thread/{MAIN}/turn/{DENSE_TURN}").text,
            client.get(
                f"/fragment/text/session/{ANCESTOR}/thread/{MAIN}/call/{DENSE_TURN_CALL}"
            ).text,
            client.get(
                f"/fragment/input/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
            ).text,
            client.get(
                f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
            ).text,
            # What followed a slash command, which is rendered rather than escaped, like the
            # prompt a plain turn shows in its place.
            client.get(f"/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{SLASH_TURN}").text,
            client.get(f"/session/{ANCESTOR}/thread/{MAIN}/records").text,
            client.get(f"/fragment/record/session/{ANCESTOR}/thread/{MAIN}/line/1").text,
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
    """Opening one tool call's result fetches that call's and nothing else from the same call.

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
    served = client.get(
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
    ).text
    # The value it was asked for arrives, and it is not empty...
    whole = one(
        store,
        "SELECT length(result) FROM live_tool_calls WHERE id = ? AND session_id = ?",
        [DENSE_TOOL, FORK_ORIGIN],
    )[0]
    assert [int(size) for size in values(served, "data-value")] == [whole]
    # ...and no sibling of the same call rode along with it.
    for other in siblings:
        assert other == DENSE_TOOL or other not in served


def test_a_fragment_cites_the_query_that_fetched_it(client: TestClient) -> None:
    """Every whole-value fragment carries the query and the keys it was fetched by.

    A fragment arrives on a page that has already been served, so it cannot ride the footer
    the pages share: each one carries the line itself. All nine routes hand one shared seam
    their own keys, so each is here — a seam pinned through one route alone would still let
    another cite a key it was not fetched by.
    """
    keyed = f"session_id={FORK_ORIGIN} source={FORK_ORIGIN_RUN}"
    for url, expected in (
        (
            f"/fragment/text/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}",
            f"-- queries/view_call_text.sql {keyed} api_call_id={DENSE_CALL}",
        ),
        (
            f"/fragment/thinking/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}",
            f"-- queries/view_call_thinking.sql {keyed} api_call_id={DENSE_CALL}",
        ),
        (
            f"/fragment/input/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
            f"-- queries/view_tool_input.sql {keyed} tool_call_id={DENSE_TOOL}",
        ),
        (
            f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
            f"-- queries/view_tool_result.sql {keyed} tool_call_id={DENSE_TOOL}"
            f" head_chars={queries.HEADER_CHARS}",
        ),
        # The command a `Bash` call ran, which only a `Bash` call has — so this one is keyed
        # off the thread that holds one rather than off the dense call above.
        (
            f"/fragment/command/session/{SPINE}/thread/{MAIN}/tool/{BASH_TOOL}",
            f"-- queries/view_tool_command.sql session_id={SPINE} source={MAIN}"
            f" tool_call_id={BASH_TOOL}",
        ),
        (
            f"/fragment/prompt/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/turn/{DENSE_CALL_TURN}",
            f"-- queries/view_turn_prompt.sql {keyed} turn_id={DENSE_CALL_TURN}",
        ),
        # The arguments of a slash turn, which only the one recorded slash turn has.
        (
            f"/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{SLASH_TURN}",
            f"-- queries/view_turn_command_args.sql session_id={SPINE} source={MAIN}"
            f" turn_id={SLASH_TURN}",
        ),
        # A run is keyed by the session and its own id: a run has one home, so no thread
        # names it.
        (
            f"/fragment/brief/session/{FORK_ORIGIN}/run/{FORK_ORIGIN_RUN}",
            f"-- queries/view_run_brief.sql session_id={FORK_ORIGIN} run_id={FORK_ORIGIN_RUN}",
        ),
        # The record route keys on a line number rather than an id. Fetched off a subagent
        # thread at a line past the first, so neither key can be a constant the fixture hides.
        (
            f"/fragment/record/session/{SPINE}/thread/{SPINE_RUN}/line/2",
            f"-- queries/view_record.sql session_id={SPINE} source={SPINE_RUN} line_no=2",
        ),
    ):
        assert values(client.get(url).text, "data-query") == [expected], url


def test_a_fragment_naming_nothing_is_a_404(client: TestClient) -> None:
    """A per-value fragment for an id the store lacks is a 404, not an empty box."""
    response = client.get(
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{MISSING}"
    )
    assert response.status_code == 404
    assert MISSING not in response.text


def test_every_asset_a_page_asks_for_is_one_the_viewer_ships(client: TestClient) -> None:
    """No page reaches off the machine for an asset, and none writes an inline style.

    Both are things the policy in `app.CSP` forbids, and both fail the same way: loudly in a
    browser and silently in this tier, because a blocked asset and a dropped attribute leave
    a 200 behind. So the check is on the templates rather than on a response — the fragments
    included, which are the templates no page-level sweep renders.
    """
    for template in sorted(TEMPLATES.rglob("*.html")):
        markup = template.read_text()
        # Every `src` and `href` a template writes is a path on this server...
        assert re.findall(r'(?:src|href)="(\w+:)?//[^"]*"', markup) == [], template.name
        # ...and nothing carries a style attribute. This is the trap the spend meter's decile
        # classes exist to dodge: a width written inline is a meter no reader ever sees.
        assert ' style="' not in markup, template.name
        # ...and nothing wears the class htmx paints, which the config below stops it painting.
        assert "htmx-indicator" not in markup, template.name
    # ...and each asset the base page asks for is served, htmx included.
    page = client.get("/").text
    assets = re.findall(r'(?:src|href)="(/static/[^"]*)"', page)
    assert any("htmx" in asset for asset in assets), page
    for asset in assets:
        assert client.get(asset).status_code == 200, asset
    # Clean templates are not enough: htmx writes a `<style>` block of its own for the
    # indicator class as it loads, which the policy blocks and the browser reports on every
    # page. This meta is what stops it writing one — htmx merges the config before it paints.
    (config,) = re.findall(r"<meta name=\"htmx-config\" content='([^']*)'>", page)
    assert json.loads(config)["includeIndicatorStyles"] is False


def test_serving_the_store_leaves_it_read_only(corpus_db: Path, client: TestClient) -> None:
    """Nothing the viewer serves writes to the store it is pointed at."""
    before = corpus_db.stat().st_mtime_ns
    client.get("/")
    client.get(f"/session/{SPINE}")
    client.get(f"/session/{MISSING}")
    assert corpus_db.stat().st_mtime_ns == before
