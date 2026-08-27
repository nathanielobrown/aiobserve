"""The session list: one row per session, sorted, paged, and printed the way a reader reads.

Every expectation is derived from the store the app is serving rather than written down. The
order is re-derived in the test's own SQL, the numbers are re-counted through the corpus views,
and the page size comes from `view/bounds.py` — so a fixture added to the corpus joins the
expectation instead of falling out of it.
"""

import datetime as dt
import re
from html import unescape

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.analyze import manifest, queries
from aiobserve.view import bounds
from aiobserve.view import format as fmt
from aiobserve.view.app import build_app
from aiobserve.view.format import ABSENT
from aiobserve.view.listing import (
    ARIA_SORT,
    DEFAULT_DIRECTION,
    DEFAULT_SORT,
    DIRECTIONS,
    SORTS,
)
from tests.conftest import (
    NO_PROJECT_SESSION,
    SPINE,
    SPINE_LEAF,
)
from tests.view.conftest import (
    CUT,
    Planter,
    counted,
    fields,
    inside,
    money,
    one,
    pages,
    reads,
    values,
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
    # ...and the unit word stands off the number under it, which is the one thing on this row
    # no `data-field` carries: `0 errors`, never `0errors`. The space is written as an
    # expression because a literal one is the formatter's to drop (`_parts.html`).
    assert f"{errors} errors" in reads(page, "data-session-id", SPINE)


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
        name: spec.default for name, spec in manifest.QUERIES["view_sessions"].params.items()
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
        name: spec.default for name, spec in manifest.QUERIES["view_sessions"].params.items()
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
    # The first page whose numbers run past a thousand, derived rather than typed: the page
    # size moves with what a row costs (`tests/view/budgets.py`), and a page number that
    # did not move with it would stop reaching the boundary this test is about.
    size = bounds.SESSIONS.default
    page_number = 1_000 // size + 2
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
