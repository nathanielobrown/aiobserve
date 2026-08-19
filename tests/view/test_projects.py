"""The landing page: one row per project the store holds sessions for.

The expectations are derived from the store the app is serving rather than listed here — the
fold is re-run through `sessions.project_predicate`, the same shape the query file and the
CLI's `--project` use, so a page that folded some other way reds even though both sides move
together. Two things the fixture corpus cannot show are planted and labelled: no recorded
session ran in a worktree, and every recorded timestamp recedes from the wall clock, so the
trailing windows would go quietly empty as the corpus ages.
"""

import datetime as dt
from collections import defaultdict

import duckdb
from fastapi.testclient import TestClient

from aiobserve.sessions import project_predicate
from aiobserve.view import bounds
from aiobserve.view.app import build_app
from aiobserve.view.format import ABSENT
from aiobserve.view.store import Page
from tests.conftest import MYCELIA, NO_PROJECT_SESSION, SPINE
from tests.view.conftest import Planter, fields, inside, one, suggestions, values

# Every session in the store beside the project it folds onto: the shortest stored directory
# it sits in, by the predicate the CLI filters with. Written here rather than imported from
# the query so the page is checked against a second statement of the rule.
FOLD = f"""
SELECT
    r.session_id,
    r.started_at,
    r.cost_usd,
    (SELECT min_by(a.project_dir, length(a.project_dir))
     FROM (SELECT DISTINCT project_dir FROM corpus_rollups WHERE project_dir IS NOT NULL) a
     WHERE {project_predicate("r.project_dir", "a.project_dir")}) AS root
FROM corpus_rollups r
"""

# What the two trailing windows are, read off the manifest: the page labels them from the
# same parameters, so a window renamed here is a window the page stopped counting.
RECENT_DAYS = "recent_days"
WINDOW_DAYS = "window_days"


def folded(store: duckdb.DuckDBPyConnection) -> dict[str | None, list[str]]:
    """The sessions of each project the store holds, keyed by the root they fold onto."""
    grouped: dict[str | None, list[str]] = defaultdict(list)
    for session_id, root in store.execute(f"SELECT session_id, root FROM ({FOLD})").fetchall():
        grouped[root].append(session_id)
    return grouped


def cited(page: str, name: str) -> dict[str, str]:
    """The bindings a page's citation carries for one query, keyed by parameter."""
    line = fields(page, "id", "citation")[name]
    return dict(binding.split("=", 1) for binding in line.split()[2:])


def window(
    store: duckdb.DuckDBPyConnection, root: str, as_of: str, days: str
) -> tuple[set[str], float]:
    """Which of a project's sessions fall inside a trailing window, and what they cost.

    Bound with the values the page cited, so the expectation is the window the reader sees
    rather than one this test computed from a clock of its own — and closed at both ends the
    way the runner's window is, so the cited line answers the same tomorrow.
    """
    rows = store.execute(
        f"SELECT session_id, cost_usd FROM ({FOLD}) WHERE root = ?"
        " AND started_at >= CAST(? AS DATE) - to_days(CAST(? AS INTEGER))"
        " AND started_at < CAST(? AS DATE) + INTERVAL 1 DAY",
        [root, as_of, days, as_of],
    ).fetchall()
    return {session_id for session_id, _ in rows}, sum(cost for _, cost in rows)


def test_the_landing_page_lists_projects_and_the_list_moved_to_sessions(client: TestClient) -> None:
    """`/` answers with projects and `/sessions` with sessions — neither serves the other."""
    landing = client.get("/")
    listing = client.get("/sessions")
    assert landing.status_code == listing.status_code == 200
    assert values(landing.text, "data-project") and not values(landing.text, "data-session-id")
    assert values(listing.text, "data-session-id") and not values(listing.text, "data-project")


def test_a_row_per_project_and_one_for_the_sessions_that_named_none(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Every project the store holds gets a row, and what it cannot attribute gets one too."""
    page = client.get("/").text
    expected = folded(store)
    # One row per root the fold produced, the sessions with no project directory among them
    # under the empty key: a row, because the store holds their spend like any other.
    assert set(values(page, "data-project")) == {root or "" for root in expected}
    assert None in expected and NO_PROJECT_SESSION in expected[None]
    # The row for those sessions says so in words and links nowhere: there is no project page
    # to open, and a link to `?project=` would filter by a value no session carries.
    unattributed = fields(page, "data-project", "")
    assert unattributed["project_dir"] == "(no project)"
    assert inside(page, "data-project", "", "href") == []
    # Every other row counts exactly the sessions that folded onto it.
    for root, sessions in expected.items():
        if root is not None:
            assert fields(page, "data-project", root)["sessions"] == f"{len(sessions):,}"


def test_a_worktree_folds_into_its_checkout_and_a_prefix_sibling_does_not(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A checkout's worktrees count as the checkout; a directory beside it stays its own row.

    Planted and labelled: no recorded session ran in a worktree, so the masquerading
    directories the design fold exists for cannot be reproduced from the fixtures. The
    sibling is the case that separates the predicate from `starts_with(dir, ancestor)` — one
    character short of it and `mycelia-other` disappears into `mycelia`.
    """
    worktree = f"{MYCELIA}/.claude/worktrees/wt-1"
    sibling = f"{MYCELIA}-other"
    path = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [worktree, SPINE]),
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [sibling, NO_PROJECT_SESSION]),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get("/").text
    # The worktree is not a project of its own...
    assert worktree not in values(page, "data-project")
    # ...its session counts under the checkout it was cut from...
    with duckdb.connect(str(path), read_only=True) as connection:
        connection.execute("SET TimeZone='UTC'")
        expected = folded(connection)
    assert SPINE in expected[MYCELIA]
    assert fields(page, "data-project", MYCELIA)["sessions"] == f"{len(expected[MYCELIA]):,}"
    # ...and the directory that merely shares its name is a row of its own.
    assert fields(page, "data-project", sibling)["sessions"] == "1"
    # The list the row opens folds the same way, because the row's count and the list it
    # links to would otherwise disagree by exactly the sessions a worktree recorded.
    with TestClient(build_app(path)) as planted:
        (link,) = set(inside(page, "data-project", MYCELIA, "href"))
        listed = values(planted.get(link).text, "data-session-id")
    assert set(listed) == set(expected[MYCELIA])
    assert SPINE in listed and NO_PROJECT_SESSION not in listed


def test_project_spend_is_counted_through_the_corpus_views(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A project's spend counts a resume's copied calls once, not once per session file.

    The fixture corpus holds a resume pair, so the two views disagree by $1.24 here: a
    regression to `session_rollups` would print the larger number.
    """
    corpus, live = one(
        store,
        "SELECT (SELECT sum(cost_usd) FROM corpus_rollups WHERE project_dir = ?),"
        " (SELECT sum(cost_usd) FROM session_rollups WHERE project_dir = ?)",
        [MYCELIA, MYCELIA],
    )
    assert corpus < live, "the resume pair the fixture corpus records no longer double-counts"
    assert fields(client.get("/").text, "data-project", MYCELIA)["cost_usd"] == f"${corpus:.2f}"


def test_the_windows_count_the_sessions_inside_the_window_the_page_cites(
    plant: Planter,
) -> None:
    """Each trailing window holds exactly the sessions the citation's `as_of` puts in it.

    The three timestamps are planted because every recorded one recedes: the fixture corpus
    ends in 2026-08 and its windows go empty as the wall clock moves, which would leave this
    leaf asserting zero against zero. One session inside both windows, one inside the longer
    only, and one outside both, so each boundary is exercised from the near side and the far.
    """
    now = dt.datetime.now(dt.UTC)
    # A copy of a recorded session per offset, so a planted row carries a real session's
    # numbers and the clock is the only invented part of it.
    path = plant(
        *(
            (
                "INSERT INTO sessions (SELECT s.* REPLACE (? AS id, ? AS project_dir,"
                " ? AS started_at) FROM sessions s WHERE s.id = ?)",
                [f"planted-{days}d", f"{MYCELIA}/planted", now - dt.timedelta(days=days), SPINE],
            )
            for days in (1, 10, 40)
        )
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get("/").text
    bindings = cited(page, Page.PROJECT_ROLLUPS.value)
    row = fields(page, "data-project", MYCELIA)
    with duckdb.connect(str(path), read_only=True) as connection:
        connection.execute("SET TimeZone='UTC'")
        # The planted sessions fold onto the checkout, so the counts below are the mycelia
        # row's — the same query the page ran, bound to the same `as_of` it cited.
        recent = window(connection, MYCELIA, bindings["as_of"], bindings[RECENT_DAYS])
        trailing = window(connection, MYCELIA, bindings["as_of"], bindings[WINDOW_DAYS])
    # The plants landed where they were aimed: the day-old session inside both windows, the
    # ten-day-old one inside the longer alone, and the forty-day-old one outside both. The
    # counts themselves come from the store, because the corpus holds recorded sessions
    # inside the longer window too.
    assert "planted-1d" in recent[0] and "planted-1d" in trailing[0]
    assert "planted-10d" in trailing[0] and "planted-10d" not in recent[0]
    assert "planted-40d" not in trailing[0]
    # And the page counts and prices exactly the sessions each window holds.
    assert row["recent_sessions"] == f"{len(recent[0]):,}"
    assert row["window_sessions"] == f"{len(trailing[0]):,}"
    assert row["recent_cost"] == f"${recent[1]:.2f}"
    assert row["window_cost"] == f"${trailing[1]:.2f}"


def test_a_window_holding_no_session_is_a_gap_rather_than_a_crash(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A project with nothing inside a window renders the dash, not a zero total or an error.

    The corpus's one-session projects are the case: their sessions were recorded in 2026-08
    and the short window has receded past them, so what the store holds for that window is
    nothing at all.
    """
    page = client.get("/").text
    quiet = [
        root
        for root in folded(store)
        if root is not None
        and one(
            store,
            f"SELECT count(*) FROM ({FOLD}) WHERE root = ?"
            " AND started_at >= current_date - to_days(?)",
            [root, int(cited(page, Page.PROJECT_ROLLUPS.value)[RECENT_DAYS])],
        )[0]
        == 0
    ]
    assert quiet, "every project has run recently: the empty-window case needs a plant now"
    for root in quiet:
        row = fields(page, "data-project", root)
        # No sessions is a count of zero — the store knows that — and no spend at all.
        assert row["recent_sessions"] == "0"
        assert row["recent_cost"] == ABSENT


def test_a_project_row_links_to_the_sessions_it_counts(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Following a row's link lands on the list holding exactly that project's sessions."""
    page = client.get("/").text
    (link,) = set(inside(page, "data-project", MYCELIA, "href"))
    listed = client.get(link)
    assert listed.status_code == 200
    assert set(values(listed.text, "data-session-id")) == set(folded(store)[MYCELIA])


def test_the_page_cites_the_query_and_the_window_it_ran(client: TestClient) -> None:
    """The footer carries the clock the windows were computed from, so they reproduce.

    A page whose windows came from SQL's own `now()` would cite a line that answers something
    different every time it is re-run, which is the reason the route binds the clock.
    """
    bindings = cited(client.get("/").text, Page.PROJECT_ROLLUPS.value)
    assert dt.date.fromisoformat(bindings["as_of"]) <= dt.datetime.now(dt.UTC).date()
    assert bindings["projects"] == str(bounds.PROJECTS.default)
    # And the two windows the columns are headed with, which are bindings like the rest.
    assert (int(bindings[RECENT_DAYS]), int(bindings[WINDOW_DAYS])) == (7, 30)


def test_the_page_is_ordered_by_what_ran_most_recently(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Projects arrive newest first, with the sessions that named no directory last.

    The store holds no timestamp for those, so they sort where every NULL the viewer prints
    sorts: at the end, rather than at the top of a page ranked by recency.
    """
    page = client.get("/").text
    last = dict(
        store.execute(f"SELECT root, max(started_at) FROM ({FOLD}) GROUP BY root").fetchall()
    )
    ordered = sorted(
        (root for root in last if last[root] is not None),
        key=lambda root: last[root],
        reverse=True,
    )
    assert values(page, "data-project") == [*ordered, ""]


def test_the_filter_box_suggests_the_projects_the_landing_page_lists(plant: Planter) -> None:
    """The box offers roots, so filling one in finds the sessions the row it came from counts.

    Planted for the same reason as the fold above: without a worktree in the store, a box
    that offered every recorded directory and one that offered only roots look identical.
    """
    path = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [f"{MYCELIA}/.claude/wt-1", SPINE]),
    )
    with TestClient(build_app(path)) as planted:
        offered = suggestions(planted.get("/sessions").text)
        listed = values(planted.get("/").text, "data-project")
        found = {
            option: values(
                planted.get("/sessions", params={"project": option}).text, "data-session-id"
            )
            for option in offered
        }
    # Every suggestion is a project the landing page counts...
    assert offered and set(offered) <= set(listed)
    # ...the worktree is not one of them, and the checkout it folds into is...
    assert f"{MYCELIA}/.claude/wt-1" not in offered
    assert MYCELIA in offered
    # ...and each one finds sessions rather than filling the box in with a dead value.
    assert all(found.values())
