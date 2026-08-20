"""Reading a session in order: the prev/next controls beside the pane.

The tree shows one open path, so a reader who wants the whole session reads it with these two
links instead. The order is depth-first over the whole session — into a node's children, then
on to its next sibling, then out — and both buckets are stops the walk descends into, because
the calls and runs they hold happened and nothing else reaches them.

These leaves follow the links themselves rather than calling `walk.py`: what a reader gets is
the chain of pages, and only fetching them proves the chain closes. Every page is kept as it
was served, so the order the tree drew each node's children is read back out of the same
response the walk stepped through.
"""

import duckdb
import pytest
from fastapi.testclient import TestClient

from tests.conftest import FORK_ORIGIN, SPINE
from tests.view.conftest import fields, inside, kin, values

# Every session the fixture corpus holds, walked whole. The corpus is small on purpose — its
# largest session is 24 nodes — so "the whole session" is a claim every session can carry.
SESSIONS = "SELECT id FROM sessions ORDER BY id"


def held(store: duckdb.DuckDBPyConnection, session_id: str) -> set[str]:
    """Every node key one session holds, in the test's own SQL: the walk's population.

    One row of the store is one node, plus the two buckets, which are not rows: a thread has
    an unattributed bucket where one of its calls answers no turn *of that thread* — a fork
    replays its parent's turn — and the session has an unattached bucket where a run's
    spawning call resolves to nothing at all.
    """
    keys = {f"session:{session_id}"}
    for kind, table in (
        ("turn", "live_turns"),
        ("call", "live_api_calls"),
        ("tool", "live_tool_calls"),
        ("run", "live_agent_runs"),
        ("compaction", "live_compactions"),
    ):
        keys |= {
            f"{kind}:{node_id}"
            for (node_id,) in store.execute(
                f"SELECT id FROM {table} WHERE session_id = ?", [session_id]
            ).fetchall()
        }
    keys |= {
        f"unattributed:{source}"
        for (source,) in store.execute(
            "SELECT DISTINCT c.source FROM live_api_calls c"
            " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
            "   AND t.id = c.turn_id"
            " WHERE c.session_id = ? AND t.id IS NULL",
            [session_id],
        ).fetchall()
    }
    loose = store.execute(
        "SELECT count(*) FROM live_agent_runs a"
        " LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id"
        "   AND tc.id = a.tool_use_id AND tc.source <> a.id"
        " LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "   AND c.id = tc.api_call_id"
        " WHERE a.session_id = ? AND c.id IS NULL",
        [session_id],
    ).fetchone()
    assert loose is not None
    if loose[0]:
        keys.add(f"unattached:{session_id}")
    return keys


class Page:
    """One page the walk stepped on: where it sits, and the tree it was served with."""

    def __init__(self, url: str, html: str) -> None:
        self.url = url
        # The crumbs are the open path, outermost first and ending at the selection, so the
        # last is this page's own node and the rest is where it hangs.
        self.chain = tuple(values(html, "data-crumb"))
        self.key = self.chain[-1]
        # This node's own children as its tree drew them: only the open path expands, so
        # nothing else on the page renders one level below the chain.
        self.children = kin(html)
        self.html = html


def follow(client: TestClient, start: str, control: str) -> list[Page]:
    """Every page one control reaches from `start`, the starting page first.

    Stops at the page that carries no such control, which is the end of the session in that
    direction. The cap is the corpus's own size with room to spare: a walk that did not close
    would loop here rather than hang.
    """
    walked: list[Page] = []
    url: str | None = start
    while url is not None:
        served = client.get(url)
        assert served.status_code == 200, f"{url}: {served.status_code}"
        walked.append(Page(url, served.text))
        step = inside(served.text, "data-walk", control, "href")
        url = step[0] if step else None
        assert len(walked) < 500, f"{start}: the walk did not end"
    return walked


def test_next_from_a_session_reads_every_node_it_holds_exactly_once(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The walk is the way to the whole session, so it reaches all of it and repeats none of it.

    Checked against every session in the corpus, and against the store's own rows rather than
    against the tree: what the walk has to cover is what the session recorded, including the
    calls and runs that attach to nothing and are only reachable through a bucket.
    """
    for (session_id,) in store.execute(SESSIONS).fetchall():
        walked = [page.key for page in follow(client, f"/session/{session_id}", "next")]
        assert len(walked) == len(set(walked)), f"{session_id}: a node was walked twice"
        assert set(walked) == held(store, session_id), session_id


def test_the_walk_descends_before_it_moves_on_and_keeps_the_trees_order(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Depth-first, in the order the tree drew each level.

    Two properties together say that: every step lands on a child of the node it came from or
    on a sibling of one of that node's ancestors — which is what depth-first means — and the
    children of any node arrive in the order that node's own page rendered them. The tree's
    order is what `tests/view/test_tree.py` pins against the store, so this leaf checks the
    reading order against what the reader sees rather than deriving it twice.
    """
    for (session_id,) in store.execute(SESSIONS).fetchall():
        walked = follow(client, f"/session/{session_id}", "next")
        for before, after in zip(walked, walked[1:], strict=False):
            # `after`'s parent is on `before`'s chain: either `before` itself, or something
            # `before` sits inside. Anything else is a jump across the tree.
            assert after.chain[:-1] == before.chain[: len(after.chain) - 1], (
                f"{session_id}: {after.key} does not follow {before.key} depth-first"
            )
        # Every node's children, gathered off the walk, in the order its own tree drew them.
        for page in walked:
            reached = [step.key for step in walked if step.chain[:-1] == page.chain]
            assert reached == page.children, f"{session_id}: {page.key}"


@pytest.mark.parametrize("session_id", [SPINE, FORK_ORIGIN])
def test_prev_walks_the_same_session_back(client: TestClient, session_id: str) -> None:
    """The two controls are one order read in two directions.

    `FORK_ORIGIN` is here for the nesting: its walk descends into a run, into the run that run
    spawned, and back out, so the mirror covers popping out of a chain and not only stepping
    along a level.
    """
    forward = follow(client, f"/session/{session_id}", "next")
    back = follow(client, forward[-1].url, "previous")
    assert [page.key for page in back] == [page.key for page in reversed(forward)]


def test_the_first_node_has_nothing_before_it_and_the_last_nothing_after(
    client: TestClient,
) -> None:
    """A session is the first thing read and its last leaf the last, so each control stops."""
    first = client.get(f"/session/{SPINE}").text
    assert inside(first, "data-walk", "previous", "href") == []
    walked = follow(client, f"/session/{SPINE}", "next")
    assert inside(walked[-1].html, "data-walk", "next", "href") == []


def test_a_control_says_what_the_neighbour_is_and_what_it_was(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A control names the neighbour's kind and its label — the same label its tree row carries.

    A reader deciding whether to step has the node's own words, not the word "next". The kind
    is printed rather than left in an attribute: the walk crosses levels — a turn's next is its
    first api call — and a reader who cannot see that has no warning before the step.
    """
    walked = follow(client, f"/session/{SPINE}", "next")
    step = walked[2]
    for control, neighbour in (("previous", walked[1]), ("next", walked[3])):
        (key,) = inside(step.html, "data-walk", control, "data-node")
        assert key == neighbour.key
        # Both halves are text on the page: what the neighbour is, and what it is called. The
        # label is the one the neighbour's own tree row carries — one node, one name, wherever
        # it is read.
        kind, _, _ = neighbour.key.partition(":")
        assert fields(step.html, "data-walk", control) == {
            "kind": kind,
            "label": fields(neighbour.html, "data-selected", neighbour.key)["label"],
        }


def test_the_walk_is_the_same_however_the_tree_is_capped(client: TestClient) -> None:
    """`?kin=` cuts the tree, never the reading order: the walk reads the store, not the rows.

    The cap is dropped to one child a level, which is the smallest the knob goes, so the tree
    beside the pane loses everything but the open path — and the controls do not move.
    """
    walked = follow(client, f"/session/{SPINE}", "next")
    for page in walked:
        capped = client.get(f"{page.url}?kin=1")
        assert capped.status_code == 200, page.url
        for control in ("previous", "next"):
            assert inside(capped.text, "data-walk", control, "data-node") == inside(
                page.html, "data-walk", control, "data-node"
            ), f"{page.key}: {control}"
