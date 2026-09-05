"""The run node: one agent run's own thread, and where the store places it.

A run is the one node whose id is also a `source` — its turns, its calls and its compactions
are written to a transcript of its own. What makes its page more than a session page at
another thread is placement: a run hangs where its *spawning call* sits, and the corpus
records the two ways that resolves to nothing. `test_nav_tree.py` owns the NavTree's ordering; these
leaves own what is true of a run whichever tree it appears in.
"""

import duckdb
from fastapi.testclient import TestClient

from hyphae.view import bounds
from hyphae.view.app import build_app
from tests.conftest import (
    BYREF_FORK,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    FORK_RUN,
    NO_PROJECT_SESSION,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
    TEAMMATE,
    TEAMMATE_RUN,
)
from tests.view.conftest import SPAWN_OF, Planter, fields, inside, one, values


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
    """A run two levels down names the whole chain of rows that reached it.

    `SPINE_LEAF` was spawned from a turn of `SPINE_RUN`'s thread, which was itself spawned
    from a turn of `main` — so the trail repeats, and each step is the rows a run hangs under:
    the turn on the *spawning call's own thread*, that api call, and the tool call that asked
    for the run. The expectation reads that join out of the store rather than pinning the
    ids, so a re-recorded fixture moves it.
    """
    trail = [f"session:{SPINE}"]
    for run_id in (SPINE_RUN, SPINE_LEAF):
        _, _, turn_id, call_id = one(store, SPAWN_OF, [run_id])
        assert turn_id is not None, f"{run_id} no longer resolves a spawning turn"
        (tool_id,) = one(store, "SELECT tool_use_id FROM live_agent_runs WHERE id = ?", [run_id])
        trail += [f"turn:{turn_id}", f"call:{call_id}", f"tool:{tool_id}", f"run:{run_id}"]
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


def test_an_agent_type_leads_a_runs_title_except_where_a_column_already_heads_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Which agent ran is the word a reader picks a run out of a list by, so it leads the
    title in brackets — everywhere the surface has no column to align it in.

    The tree, the crumbs, the pane's heading and the tab have no such column: the type is
    there only if the title carries it, and a tree of six runs named by their briefs alone
    says nothing about which agent did what. The unattached bucket's children log *does* have
    one, headed `◎ Agent`, and it reads the way the tools log reads — the name in its own
    narrow column, what it was asked in the wide one beside it. A row that printed the type in
    both would be saying one word twice under two headings, the second of them "Description".
    """
    (agent_type, brief) = one(
        store, "SELECT agent_type, brief FROM live_agent_runs WHERE id = ?", [BYREF_FORK]
    )
    assert agent_type and brief, "this fork lost the two halves this leaf reads"
    # The log names the agent once, in the column headed for it...
    log = client.get(f"/session/{NO_PROJECT_SESSION}/unattached").text
    row = fields(log, "data-child", f"run:{BYREF_FORK}")
    assert row["agent_type"] == agent_type
    # ...and the wide column beside it holds what the run was asked, not that word again.
    assert row["title"] == brief
    # The run's own page has no column for it, so every place that names the node leads with
    # the type and then says what it did.
    page = client.get(f"/session/{NO_PROJECT_SESSION}/run/{BYREF_FORK}").text
    # The brackets are what close the lead: a bracketed type says where it ends, so the dash
    # a composed title otherwise carries would be a second mark saying the same thing.
    led = f"[{agent_type}] {brief}"
    assert fields(page, "data-body", "run")["title"] == led
    assert fields(page, "data-crumb", f"run:{BYREF_FORK}")["run"] == led
    assert fields(page, "data-nav-tree", f"run:{BYREF_FORK}")["title"] == led
    assert f"<title>◎ {led} ·" in page
    # And the same shape on a run whose type a reader would recognise, read off the row rather
    # than off a field: what a NavTree row prints is `[architect]` and then what it did.
    (architect,) = one(store, "SELECT agent_type FROM live_agent_runs WHERE id = ?", [TEAMMATE_RUN])
    tree = client.get(f"/session/{TEAMMATE}/run/{TEAMMATE_RUN}").text
    assert fields(tree, "data-nav-tree", f"run:{TEAMMATE_RUN}")["title"].startswith(
        f"[{architect}] "
    )


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
    bucket = client.get(f"/session/{NO_PROJECT_SESSION}/thread/{BYREF_FORK}/unattributed")
    assert bucket.status_code == 200
    assert values(bucket.text, "data-body") == ["unattributed"]
    assert values(bucket.text, "data-child") == [f"call:{call_id}" for call_id in calls]
    # The bucket's own crumb sits under the run whose thread it stands for.
    assert values(bucket.text, "data-crumb")[-2:] == [
        f"run:{BYREF_FORK}",
        f"unattributed:{BYREF_FORK}",
    ]


def test_a_fork_is_never_its_own_child(client: TestClient) -> None:
    """A fork's transcript replays the call that spawned it, and the NavTree ignores that copy.

    `view_runs` excludes a spawning call recorded on the run's own thread (`tc.source <> a.id`).
    Drop the exclusion and the fork resolves to a turn of its own timeline: it becomes its own
    child, which is a tree with a cycle in it.
    """
    page = client.get(f"/session/{FORK_ORIGIN}/run/{FORK_RUN}").text
    # Its own page lists no child, and no row of the open tree repeats it.
    assert values(page, "data-child") == []
    assert values(page, "data-nav-tree").count(f"run:{FORK_RUN}") == 1
    # Nor does the run it forked from claim it — the exclusion leaves the edge unresolved,
    # which is what puts both in the unattached bucket.
    assert values(page, "data-crumb")[-2:] == [
        f"unattached:{FORK_ORIGIN}",
        f"run:{FORK_RUN}",
    ]
    assert f"run:{FORK_ORIGIN_RUN}" not in values(page, "data-crumb")


def test_a_run_that_answered_nothing_spends_zero_rather_than_nothing(plant: Planter) -> None:
    """A run whose thread holds no api call reads as $0.00 spent, not as a blank.

    `view_runs.sql` gathers a run's numbers per thread, so a run no group covers takes the
    zero the join could not find. The fixture corpus records no such run — the real store
    holds one in 3,005 — so this plants it by taking a recorded run's calls away.
    """
    # If a recorded run's own api calls are all gone, leaving the run row and its turns...
    path = plant(("DELETE FROM api_calls WHERE session_id = ? AND source = ?", [SPINE, SPINE_RUN]))
    with TestClient(build_app(path)) as emptied:
        page = emptied.get(f"/session/{SPINE}/run/{SPINE_RUN}").text

    # ...then the run's own page still prices its thread, at nothing.
    pane = fields(page, "data-body", "run")
    assert pane["api_calls"] == "0"
    assert pane["cost_usd"] == "$0.00"
    # ...and the row the NavTree draws for it agrees, so no ancestor sums a null.
    assert fields(page, "data-nav-tree", f"run:{SPINE_RUN}")["cost_usd"] == "$0.00"


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
    assert citations["run_timeline"] == (
        f"-- queries/run_timeline.sql session_id={SPINE} source={SPINE_RUN}"
        f" log_chars={bounds.NAV_TREE_WIDTHS.log_chars}"
    )
