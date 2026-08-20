"""Getting to a failure: the mark on a tree row, the session's list, and the stepper.

A session can fail a tool call five spawns down a run tree, and neither the tree — which opens
one path — nor the walk gets a reader there without reading everything in front of it. These
leaves cover the three surfaces that do: the `error` mark a tree row carries, the session-wide
list at `/session/{session_id}/errors`, and the prev/next pair a pane offers when the node it
is reading is itself a failure.

The fixture corpus records two failed tool calls, one apiece in two different sessions, which
is enough for the mark and for the list but not for an order or a step. A session that failed
several is planted onto recorded rows: `is_error` is a flag the store already holds — both
recorded failures prove the shape — so flipping it on a real tool call is what a busier
session looks like, not an invented one.
"""

import duckdb
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view import bounds
from aiobserve.view.app import build_app
from tests.conftest import DENSE_TOOL, FORK_ORIGIN, FORK_ORIGIN_RUN, SPINE
from tests.view.conftest import MISSING, Planter, fields, inside, one, rows, values

# Every tool call of the one session whose threads both hold one, failed. The list, its order
# and the stepper are all claims about a session with several failures on more than one
# thread, and no recorded session has that: `FORK_ORIGIN` records one failure of seven calls.
ALL_FAILED = ("UPDATE tool_calls SET is_error = true WHERE session_id = ?", [FORK_ORIGIN])


def failed(store: duckdb.DuckDBPyConnection, session_id: str) -> list[tuple[str, str]]:
    """One session's failed tool calls in the order the list shows them, thread beside id.

    The expectation's own spelling of `view_session_errors`'s order: the clock, then the
    thread, its index and its id — the last two of which are unique, so the order is total
    and a page that cut the tail of it cut the same rows twice running.
    """
    return [
        (source, tool_id)
        for source, tool_id in store.execute(
            "SELECT source, id FROM live_tool_calls WHERE session_id = ? AND is_error"
            ' ORDER BY started_at, source, "index", id',
            [session_id],
        ).fetchall()
    ]


def test_a_tree_row_for_a_tool_call_that_failed_says_so(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The tree marks the tool calls that came back an error, and marks nothing else."""
    # If a session recorded a failed tool call, on a thread of its own...
    source, tool_id = failed(store, FORK_ORIGIN)[0]
    # ...then the tree beside that call carries the mark on its row...
    page = client.get(f"/session/{FORK_ORIGIN}/tool/{source}/{tool_id}").text
    assert fields(page, "data-tree", f"tool:{tool_id}")["is_error"] == "error"
    # ...and on no other row of the session, whatever kind of node it stands for.
    marked = {key for _, key in rows(page) if "is_error" in fields(page, "data-tree", key)}
    assert marked == {f"tool:{tool_id}"}


def test_the_errors_page_lists_every_failure_of_the_session_in_the_order_they_happened(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """The list spans the whole session: what a subagent failed at is what the session failed at.

    Planted over `FORK_ORIGIN`, whose seven tool calls are split across two run threads — the
    shape the list exists for, and the one the tree cannot show in a single open path.
    """
    path = plant(ALL_FAILED)
    with TestClient(build_app(path)) as planted:
        response = planted.get(f"/session/{FORK_ORIGIN}/errors")
    assert response.status_code == 200
    page = response.text
    # Every failure the session holds, in the clock order the query states...
    with duckdb.connect(str(path), read_only=True) as connection:
        order = failed(connection, FORK_ORIGIN)
    assert values(page, "data-error") == [f"tool:{tool_id}" for _, tool_id in order]
    # ...more than one thread among them, which is what makes the list session-wide...
    assert len({source for source, _ in order}) > 1
    # ...each row leading to the tool call's own page, on the thread it ran on...
    assert [inside(page, "data-error", f"tool:{tool_id}", "href")[0] for _, tool_id in order] == [
        f"/session/{FORK_ORIGIN}/tool/{source}/{tool_id}" for source, tool_id in order
    ]
    # ...and each row saying what the call was and when it ran, so two calls of one tool are
    # told apart without opening either.
    row = fields(page, "data-error", f"tool:{order[0][1]}")
    assert row["label"] and row["started_at"]


def test_a_session_with_no_failure_to_jump_to_has_no_errors_page(client: TestClient) -> None:
    """A session that never failed a call and one the store never held are different misses."""
    # A session whose tool calls all succeeded has nothing at this URL...
    succeeded = client.get(f"/session/{SPINE}/errors")
    assert succeeded.status_code == 404
    # ...and neither does a session the store never held, said apart from it: one is a store
    # that does not hold the session, the other a session that holds no failure.
    unheld = client.get(f"/session/{MISSING}/errors")
    assert unheld.status_code == 404
    assert (
        fields(succeeded.text, "id", "error")["message"]
        != fields(unheld.text, "id", "error")["message"]
    )


def test_the_stepper_steps_between_failures_and_only_where_the_pane_stands_on_one(
    plant: Planter, client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A pane reading a failed tool call offers the failure before it and the one after.

    The step is between failures rather than between nodes, so it crosses threads the way the
    list does — and it costs a query, which is why a pane reading anything else does not
    offer one.
    """
    path = plant(ALL_FAILED)
    with duckdb.connect(str(path), read_only=True) as connection:
        order = failed(connection, FORK_ORIGIN)
    with TestClient(build_app(path)) as planted:
        served = [planted.get(f"/session/{FORK_ORIGIN}/tool/{s}/{t}").text for s, t in order]
    for place, page in enumerate(served):
        # Every failure offers the way to the whole list...
        offered = set(values(page, "data-step"))
        # ...and a step in each direction there is a failure in: the first has nothing before
        # it, the last nothing after.
        expected = {"all"}
        if place:
            expected.add("previous")
            assert inside(page, "data-step", "previous", "data-node") == [
                f"tool:{order[place - 1][1]}"
            ]
        if place + 1 < len(order):
            expected.add("next")
            assert inside(page, "data-step", "next", "data-node") == [f"tool:{order[place + 1][1]}"]
        assert offered == expected, place
    # A step lands on the neighbour's own page, thread and all — the list is not one thread's.
    second = inside(served[0], "data-step", "next", "href")[0]
    assert second.startswith(f"/session/{FORK_ORIGIN}/tool/{order[1][0]}/{order[1][1]}")
    # And a pane reading a tool call that succeeded offers only the way to the list, because
    # there is no step between failures to take from a node that is not one.
    succeeded = client.get(f"/session/{FORK_ORIGIN}/tool/{FORK_ORIGIN_RUN}/{DENSE_TOOL}").text
    assert values(succeeded, "data-step") == ["all"]


def test_every_node_page_of_a_failing_session_offers_the_way_to_its_failures(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The count beside the link is the whole session's, whatever node the pane is reading."""
    (failures,) = one(
        store,
        "SELECT count(*) FROM live_tool_calls WHERE session_id = ? AND is_error",
        [FORK_ORIGIN],
    )
    source, tool_id = failed(store, FORK_ORIGIN)[0]
    # Wherever the reader is standing in a session that failed a call — its own node, a run
    # of it, the failure itself — the link says how many the session failed...
    for url in (
        f"/session/{FORK_ORIGIN}",
        f"/session/{FORK_ORIGIN}/run/{FORK_ORIGIN_RUN}",
        f"/session/{FORK_ORIGIN}/tool/{source}/{tool_id}",
    ):
        page = client.get(url).text
        assert fields(page, "data-step", "all")["tool_errors"] == str(failures), url
        assert inside(page, "data-step", "all", "href") == [f"/session/{FORK_ORIGIN}/errors"], url
    # ...and a session that failed none offers nothing at all, rather than a link to a 404.
    assert values(client.get(f"/session/{SPINE}").text, "data-step") == []


def test_the_errors_page_and_the_stepper_cite_the_one_query_behind_them(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Both surfaces cite the same line, and a page that reads neither cites nothing.

    The stepper is a conditional read — only a pane standing on a failure runs it — so the
    citation is what says a page paid for it, and its absence is what says a page did not.
    """
    line = (
        f"-- queries/view_session_errors.sql session_id={FORK_ORIGIN}"
        f" nav_chars={queries.NAV_CHARS} errors={bounds.ERRORS.default}"
    )
    # The list itself is that one query and nothing else...
    assert fields(client.get(f"/session/{FORK_ORIGIN}/errors").text, "id", "citation") == {
        "view_session_errors": line
    }
    # ...a node page standing on a failure cites it beside the reads every node page makes...
    source, tool_id = failed(store, FORK_ORIGIN)[0]
    standing = client.get(f"/session/{FORK_ORIGIN}/tool/{source}/{tool_id}").text
    assert fields(standing, "id", "citation")["view_session_errors"] == line
    # ...and a node page of the same session standing on a call that succeeded does not, which
    # is the whole reason the read is conditional.
    beside = client.get(f"/session/{FORK_ORIGIN}/tool/{FORK_ORIGIN_RUN}/{DENSE_TOOL}").text
    assert "view_session_errors" not in fields(beside, "id", "citation")
