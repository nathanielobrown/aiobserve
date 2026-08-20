"""The run node: one agent run's own thread, and where the store places it.

A run is the one node whose id is also a `source` — its turns, its calls and its compactions
are written to a transcript of its own. What makes its page more than a session page at
another thread is placement: a run hangs where its *spawning call* sits, and the corpus
records the two ways that resolves to nothing. `test_tree.py` owns the tree's ordering; these
leaves own what is true of a run whichever tree it appears in.
"""

import duckdb
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
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
from tests.view.conftest import SPAWN_OF, fields, inside, one, values


def test_a_run_page_is_that_runs_own_thread(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The children log holds the turns written to this run's transcript, and nothing else.

    The run id is substituted for the thread everywhere the session page reads `main`, so a
    page that leaked the session's turns or the session's spend would look identical but for
    these two numbers.
    """
    page = client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
    turns = [
        row[0]
        for row in store.execute(
            'SELECT id FROM live_turns WHERE session_id = ? AND source = ? ORDER BY "index"',
            [SPINE, SPINE_RUN],
        ).fetchall()
    ]
    assert turns, "this run recorded no turns of its own: it no longer proves the case"
    assert values(page, "data-child") == [f"turn:{turn_id}" for turn_id in turns]
    # And the pane is the run's own spend, not the session's.
    api_calls, cost = one(
        store,
        "SELECT count(*), round(coalesce(sum(cost_usd), 0), 4) FROM live_api_calls"
        " WHERE session_id = ? AND source = ?",
        [SPINE, SPINE_RUN],
    )
    pane = fields(page, "data-body", "run")
    assert pane["api_calls"] == str(api_calls)
    assert pane["cost_usd"] == f"${cost:.2f}"


def test_a_nested_run_breadcrumbs_through_every_run_above_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run two levels down names the whole chain of turns and runs that reached it.

    `SPINE_LEAF` was spawned from a turn of `SPINE_RUN`'s thread, which was itself spawned
    from a turn of `main` — so the trail alternates, and the turn in each step is the one on
    the *spawning call's own thread*. The expectation reads that join out of the store rather
    than pinning the ids, so a re-recorded fixture moves it.
    """
    trail = [f"session:{SPINE}"]
    for run_id in (SPINE_RUN, SPINE_LEAF):
        _, _, turn_id, _ = one(store, SPAWN_OF, [run_id])
        assert turn_id is not None, f"{run_id} no longer resolves a spawning turn"
        trail += [f"turn:{turn_id}", f"run:{run_id}"]
    page = client.get(f"/session/{SPINE}/run/{SPINE_LEAF}").text
    assert values(page, "data-crumb") == trail
    # Every step of the trail is a link a reader can follow back up.
    assert inside(page, "data-crumb", f"run:{SPINE_RUN}", "href") == [
        f"/session/{SPINE}/run/{SPINE_RUN}"
    ]


def test_a_run_whose_spawning_call_resolves_to_nothing_is_unattached(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run the store cannot place hangs off the unattached bucket, not off `main`.

    `BYREF_FORK` forked mid-conversation: the call that spawned it lives in another session's
    files, so the join finds no thread at all. The page is honest about it — nothing in the
    store says the run hangs off the session's main thread, so the trail does not claim it
    does.
    """
    _, source, turn_id, _ = one(store, SPAWN_OF, [BYREF_FORK])
    assert (source, turn_id) == (None, None), "this fork's spawning call now resolves"
    page = client.get(f"/session/{NO_PROJECT_SESSION}/run/{BYREF_FORK}").text
    assert values(page, "data-crumb") == [
        f"session:{NO_PROJECT_SESSION}",
        f"unattached:{NO_PROJECT_SESSION}",
        f"run:{BYREF_FORK}",
    ]


def test_a_forks_calls_under_no_turn_are_its_own_bucket(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run's api calls that answer no turn of its own thread get a node, not silence.

    `BYREF_FORK`'s first calls answer turns that live in the transcript it forked from, so
    they belong to the run's thread and to no turn in it. That is the unattributed bucket,
    and it hangs under the run rather than under the session.
    """
    calls = [
        row[0]
        for row in store.execute(
            "SELECT id FROM live_api_calls WHERE session_id = ? AND source = ?"
            ' AND turn_id IS NULL ORDER BY "index"',
            [NO_PROJECT_SESSION, BYREF_FORK],
        ).fetchall()
    ]
    assert len(calls) == 2, "this fork's unattributed calls moved: re-pick the fixture"
    bucket = client.get(f"/session/{NO_PROJECT_SESSION}/unattributed/{BYREF_FORK}")
    assert bucket.status_code == 200
    assert values(bucket.text, "data-body") == ["unattributed"]
    assert values(bucket.text, "data-child") == [f"call:{call_id}" for call_id in calls]
    # The bucket's own crumb sits under the run whose thread it stands for.
    assert values(bucket.text, "data-crumb")[-2:] == [
        f"run:{BYREF_FORK}",
        f"unattributed:{BYREF_FORK}",
    ]


def test_a_fork_is_never_its_own_child(client: TestClient) -> None:
    """A fork's transcript replays the call that spawned it, and the tree ignores that copy.

    `view_runs` excludes a spawning call recorded on the run's own thread (`tc.source <> a.id`).
    Drop the exclusion and the fork resolves to a turn of its own timeline: it becomes its own
    child, which is a tree with a cycle in it.
    """
    page = client.get(f"/session/{FORK_ORIGIN}/run/{FORK_RUN}").text
    # Its own page lists no child, and no row of the open tree repeats it.
    assert values(page, "data-child") == []
    assert values(page, "data-tree").count(f"run:{FORK_RUN}") == 1
    # Nor does the run it forked from claim it — the exclusion leaves the edge unresolved,
    # which is what puts both in the unattached bucket.
    assert values(page, "data-crumb")[-2:] == [
        f"unattached:{FORK_ORIGIN}",
        f"run:{FORK_RUN}",
    ]
    assert f"run:{FORK_ORIGIN_RUN}" not in values(page, "data-crumb")


def test_the_run_page_cites_the_two_queries_that_read_its_thread(client: TestClient) -> None:
    """The run's header and its thread are read at the run id rather than at `main`.

    That substitution is the whole difference between this page and the session page, so the
    citations are where it has to show. The rest of the footer is the frame every node page
    carries, which `test_app.py` pins on the session.
    """
    citations = fields(client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text, "id", "citation")
    assert citations["view_run_header"] == (
        f"-- queries/view_run_header.sql session_id={SPINE} run_id={SPINE_RUN}"
        " head_chars=100 detail_chars=4000"
    )
    assert citations["run_digest"] == (
        f"-- queries/run_digest.sql session_id={SPINE} log_chars={queries.LOG_CHARS}"
        f" source={SPINE_RUN}"
    )
