"""The tree beside a node page: one open path through the session, and nothing else open.

The tree is served with the pane in one response, so these leaves fetch the same node URLs
`tests/view/test_node.py` does and read the rows instead. `data-tree` carries a row's node
key — `kind:id`, the key its URL is built from — so the whole tree reads as a list in
document order, and `data-more` marks a row standing for children the cap left out.

Over `SPINE`, whose main thread holds four turns with api calls under some of them: the one
session recorded with enough shape for a level to have siblings, a level below it, and a cap
that can bite. Which turn is read from the store rather than pinned, so a re-recorded fixture
moves the selection instead of reddening the tier.
"""

import duckdb
from fastapi.testclient import TestClient

from aiobserve.view import bounds
from tests.conftest import MAIN, SPINE
from tests.view.conftest import fields, inside, one, values


def url(turn_id: str) -> str:
    """The node URL of one turn of `SPINE`'s main thread."""
    return f"/session/{SPINE}/turn/{MAIN}/{turn_id}"


def main_turns(store: duckdb.DuckDBPyConnection) -> list[str]:
    """`SPINE`'s main-thread turns, in the order the tree renders them."""
    return [
        str(row[0])
        for row in store.execute(
            'SELECT id FROM live_turns WHERE session_id = ? AND source = ? ORDER BY "index"',
            [SPINE, MAIN],
        ).fetchall()
    ]


def open_turn(store: duckdb.DuckDBPyConnection) -> str:
    """The turn these leaves select: one with more than one api call, and not its level's first.

    Both halves matter. Two calls under it give the level below the selection something for a
    cap to cut, and a turn that is not its level's first row is one a cap of a single child
    would drop — which is what the rescue rule exists to stop.
    """
    (turn_id,) = one(
        store,
        'SELECT t.id FROM live_turns t WHERE t.session_id = ? AND t.source = ? AND t."index" > 0'
        " AND (SELECT count(*) FROM live_api_calls c WHERE c.session_id = t.session_id"
        "   AND c.source = t.source AND c.turn_id = t.id) > 1"
        ' ORDER BY t."index" LIMIT 1',
        [SPINE, MAIN],
    )
    return str(turn_id)


def turn_calls(store: duckdb.DuckDBPyConnection, turn_id: str) -> list[str]:
    """The api calls under one turn, in the order the tree renders them."""
    return [
        str(row[0])
        for row in store.execute(
            "SELECT id FROM live_api_calls WHERE session_id = ? AND source = ? AND turn_id = ?"
            ' ORDER BY "index"',
            [SPINE, MAIN, turn_id],
        ).fetchall()
    ]


def test_the_tree_opens_the_selections_path_and_leaves_the_rest_shut(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """One open path: the chain down to the selection, expanded, and no other subtree.

    The chain here is the session and the turn under it, so the rows are the session, every
    turn of its main thread, and — under the selected one alone — the api calls it made. A
    tree that expanded a sibling would show that sibling's calls too, which is the difference
    this reads: the rows are compared as a whole list, in order, not searched for.
    """
    selection = open_turn(store)
    html = client.get(url(selection)).text
    expected: list[str] = [f"session:{SPINE}"]
    for turn_id in main_turns(store):
        expected.append(f"turn:{turn_id}")
        # The selection is the one node whose children render — every other turn is a row.
        if turn_id == selection:
            expected += [f"call:{call_id}" for call_id in turn_calls(store, turn_id)]
    assert values(html, "data-tree") == expected
    # And the tree says which row the pane is about, once.
    assert values(html, "data-selected") == [f"turn:{selection}"]


def test_every_tree_row_swaps_the_pane_from_its_own_node_url(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Each row is an `hx-get` of the URL it links to, swapping the pane and the tree.

    The server-side half of a tree click: the same URL a reader can paste, fetched by htmx,
    with the pane taken out of the response and the rows swapped out of band. Both targets
    have to be unique in the document or the swap lands somewhere else.
    """
    html = client.get(url(open_turn(store))).text
    rows = values(html, "data-tree")
    assert len(rows) > 1
    for key in rows:
        wiring = {
            name: inside(html, "data-tree", key, name)
            for name in ("href", "hx-get", "hx-select", "hx-select-oob", "hx-push-url")
        }
        # A row fetches what it links to: one URL, however the reader gets there.
        assert wiring["hx-get"] == wiring["href"], key
        assert wiring["hx-select"] == ["#pane"], key
        assert wiring["hx-select-oob"] == ["#tree-rows"], key
        assert wiring["hx-push-url"] == ["true"], key
    # The two ids the swap aims at, each written exactly once.
    assert html.count('id="pane"') == 1
    assert html.count('id="tree-rows"') == 1


def test_the_kin_cap_cuts_the_children_but_never_the_open_path(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Children are capped per level, with a row saying how many the cap left out.

    Driven below the fixture corpus's fan-out rather than planted up to the production cap:
    no recorded session comes near 25 children, and the knob exists for exactly this. The cap
    bites twice here — once on the turns beside the selection, once on the calls under it —
    and the selection survives it either way. A cut that hid the open path would leave the
    pane describing a node the tree does not show.
    """
    selection = open_turn(store)
    html = client.get(url(selection), params={"kin": 1}).text
    turns, calls = main_turns(store), turn_calls(store, selection)
    assert len(turns) > 2 and len(calls) > 1, "the cap has to have something to cut"
    # The cap admits one turn, and the path through the selection is kept beside it...
    shown = [key for key in values(html, "data-tree") if key.startswith("turn:")]
    assert shown == [f"turn:{turns[0]}", f"turn:{selection}"]
    # ...with a tail saying how many turns are off the tree, linking to the page that pages
    # them rather than capping them: the session's own.
    assert fields(html, "data-more", f"session:{SPINE}")["cut"] == str(len(turns) - len(shown))
    assert inside(html, "data-more", f"session:{SPINE}", "href") == [f"/session/{SPINE}"]
    # And the level under the selection takes the same cap, where no rescue is owed: one call
    # of the several it made, and a tail for the rest.
    assert [key for key in values(html, "data-tree") if key.startswith("call:")] == [
        f"call:{calls[0]}"
    ]
    assert fields(html, "data-more", f"turn:{selection}")["cut"] == str(len(calls) - 1)


def test_a_size_above_its_ceiling_is_refused(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The two sizes a node URL carries only go down — the ceiling is the production default."""
    at = url(open_turn(store))
    for knob, bound in (("kin", bounds.KIN), ("log", bounds.LOG)):
        assert client.get(at, params={knob: bound.ceiling + 1}).status_code == 400, knob
        assert client.get(at, params={knob: bound.ceiling}).status_code == 200, knob
