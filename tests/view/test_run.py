"""The run page: one agent run's own timeline, the trail above it, and the runs under it.

A run is a thread of its own — its turns, its calls and its compactions are written to its
own transcript under its agent id. What makes this page more than the session page at
another source is where it sits: the tree the fixtures record includes a fork, which is the
shape that breaks a naive parent join in both directions.
"""

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.model import MAIN_SOURCE
from tests.conftest import (
    BYREF_FORK,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    FORK_RUN,
    NO_PROJECT_SESSION,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
)
from tests.view.conftest import fields, inside, one, values


def test_a_run_page_is_that_runs_own_turns(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The timeline on a run page holds the turns written to that run's transcript."""
    page = client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
    turns = [
        row[0]
        for row in store.execute(
            'SELECT id FROM live_turns WHERE session_id = ? AND source = ? ORDER BY "index"',
            [SPINE, SPINE_RUN],
        ).fetchall()
    ]
    assert turns, "this run recorded no turns of its own: it no longer proves the case"
    assert values(page, "data-turn") == turns
    # And the header is the run's own spend, not the session's.
    api_calls, cost = one(
        store,
        "SELECT count(*), round(coalesce(sum(cost_usd), 0), 4) FROM live_api_calls"
        " WHERE session_id = ? AND source = ?",
        [SPINE, SPINE_RUN],
    )
    header = fields(page, "id", "run-header")
    assert header["api_calls"] == str(api_calls)
    assert header["cost_usd"] == f"${cost:.2f}"


@pytest.mark.parametrize(
    ("session_id", "run_id", "trail"),
    [
        # A run whose transcript names its parent breadcrumbs through the named run...
        (SPINE, SPINE_LEAF, [MAIN_SOURCE, SPINE_RUN]),
        # ...one that names none breadcrumbs through the thread its spawning call sat in...
        (SPINE, SPINE_RUN, [MAIN_SOURCE]),
        # ...and a fork, whose spawning call the chip join deliberately cannot reach, has
        # only the first rule left. Its trail stops at the run that spawned it: that run's
        # own spawning call is in files this store does not hold, and `main` there would be
        # a guess rather than a link.
        (FORK_ORIGIN, FORK_RUN, [FORK_ORIGIN_RUN]),
    ],
)
def test_a_run_breadcrumbs_by_whichever_parent_rule_applies(
    session_id: str, run_id: str, trail: list[str], client: TestClient
) -> None:
    """A run page says where the run sits, by the named parent or the spawning thread."""
    page = client.get(f"/session/{session_id}/run/{run_id}").text
    assert values(page, "data-breadcrumb") == trail


def test_a_run_that_reaches_no_thread_says_so(client: TestClient) -> None:
    """A run the store cannot place shows an empty trail rather than inventing one.

    `BYREF_FORK` names no parent and its spawning call lives in another session's files, so
    neither rule resolves. The page is honest about it: the session's main thread is not in
    the trail, because nothing in the store says the run hangs off it.
    """
    page = client.get(f"/session/{NO_PROJECT_SESSION}/run/{BYREF_FORK}").text
    assert values(page, "data-breadcrumb") == []
    assert fields(page, "id", "run-header")["placement"] == "under no thread in this store"


def test_a_forks_calls_under_no_turn_are_its_continuation(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run's api calls that answer no turn of its own get a row rather than vanishing.

    `BYREF_FORK` forked mid-conversation, so the turns its first calls answer live in the
    transcript it forked from. Its page shows them as the continuation of that thread.
    """
    (calls,) = one(
        store,
        "SELECT count(*) FROM live_api_calls WHERE session_id = ? AND source = ?"
        " AND turn_id IS NULL",
        [NO_PROJECT_SESSION, BYREF_FORK],
    )
    assert calls == 2, "this fork's unattributed calls moved: re-pick the fixture"
    page = client.get(f"/session/{NO_PROJECT_SESSION}/run/{BYREF_FORK}").text
    assert fields(page, "data-turn", "(unattributed)")["api_calls"] == str(calls)
    # It is marked as a continuation, not as a turn nobody asked: the calls answer a prompt
    # this transcript does not hold.
    assert "(unattributed)" in values(page, "data-continuation")


def test_a_fork_is_its_parents_child_and_has_no_children_of_its_own(client: TestClient) -> None:
    """The fork hangs under the run that spawned it, exactly once, and lists nothing itself.

    Both halves matter. The chip join excludes the fork's own copy of its spawning call, so
    the parent's page can only reach it through `parent_agent_id`; drop the exclusion instead
    and the fork becomes its own child, which is what this asserts it is not.
    """
    parent = client.get(f"/session/{FORK_ORIGIN}/run/{FORK_ORIGIN_RUN}").text
    assert values(parent, "data-child").count(FORK_RUN) == 1
    child = client.get(f"/session/{FORK_ORIGIN}/run/{FORK_RUN}").text
    assert values(child, "data-child") == []


def test_the_run_page_cites_every_query_it_ran(client: TestClient) -> None:
    """The run page's footer holds one re-runnable line per query behind it."""
    page = client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
    # The run's own thread is read at the run id rather than at the main thread — that
    # substitution is the whole difference between this page and the session page, so the
    # citations are where it has to show. `view_runs` is the exception: the trail above the
    # run and the chips under it come from the session's whole set of links.
    assert fields(page, "id", "citation") == {
        "view_run_header": f"-- queries/view_run_header.sql session_id={SPINE} run_id={SPINE_RUN}",
        "run_digest": f"-- queries/run_digest.sql session_id={SPINE} source={SPINE_RUN}",
        "view_runs": f"-- queries/view_runs.sql session_id={SPINE}",
        "view_compactions": f"-- queries/view_compactions.sql session_id={SPINE}"
        f" source={SPINE_RUN}",
        "view_turn_records": f"-- queries/view_turn_records.sql session_id={SPINE}"
        f" source={SPINE_RUN}",
    }


def test_a_run_page_links_back_to_the_session_and_to_its_children(client: TestClient) -> None:
    """Every id the page names is a link a reader can follow."""
    page = client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
    assert f"/session/{SPINE}" in inside(page, "id", "run-header", "href")
    assert f"/session/{SPINE}/run/{SPINE_LEAF}" in inside(page, "data-child", SPINE_LEAF, "href")


def test_a_run_the_store_does_not_hold_is_a_404(client: TestClient) -> None:
    """An id that matches no run of this session gets a 404, not an empty page."""
    response = client.get(f"/session/{SPINE}/run/{FORK_RUN}")
    assert response.status_code == 404
    assert FORK_RUN not in response.text
