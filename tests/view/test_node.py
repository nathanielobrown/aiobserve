"""The node page: one node of a session served whole, with the pane beside its tree.

Every node has a URL that renders cold as a full page, and a tree click is an `hx-get` of
that same URL — so the leaves here fetch a turn's URL both ways and read the pane through
`data-*`. The body a full view wraps is the body the `/fragment/body/...` mount serves on its
own, which is what keeps an inline expansion and a node page from telling two stories.
"""

import duckdb
from fastapi.testclient import TestClient

from tests.conftest import ANCESTOR, DENSE_TURN, MAIN
from tests.view.conftest import MISSING, fields, inside, one, values

# The corpus's densest main-thread turn — 4 api calls under it — so the pane's children log
# has more than one row and the tree has a level under the selection worth rendering.
TURN = f"/session/{ANCESTOR}/turn/{MAIN}/{DENSE_TURN}"
BODY = f"/fragment/body/turn/{ANCESTOR}/{MAIN}/{DENSE_TURN}"

# What htmx puts on the request a tree click makes. The node URL is the same either way,
# which is the point of the leaf that sends them.
HTMX = {
    "HX-Request": "true",
    "HX-Target": "pane",
    "HX-Current-URL": f"http://testserver{TURN}",
}


def test_a_turn_node_serves_the_turn_the_store_holds(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The pane says what the store says about the turn it was asked for."""
    response = client.get(TURN)
    assert response.status_code == 200
    index, calls, tools = one(
        store,
        'SELECT t."index",'
        " (SELECT count(*) FROM live_api_calls c WHERE c.session_id = t.session_id"
        "   AND c.source = t.source AND c.turn_id = t.id),"
        " (SELECT count(*) FROM live_tool_calls tc JOIN live_api_calls c"
        "   ON c.session_id = tc.session_id AND c.source = tc.source AND c.id = tc.api_call_id"
        "   WHERE tc.session_id = t.session_id AND tc.source = t.source AND c.turn_id = t.id)"
        " FROM live_turns t WHERE t.session_id = ? AND t.source = ? AND t.id = ?",
        [ANCESTOR, MAIN, DENSE_TURN],
    )
    shown = fields(response.text, "data-body", "turn")
    # The turn's own place in its thread, and the two counts under it — the api calls it
    # made, and the tool calls those made.
    assert shown["turn_index"] == str(index)
    assert shown["api_calls"] == str(calls)
    assert shown["tool_calls"] == str(tools)
    # And the log under the pane lists those api calls, one row each.
    assert len(values(response.text, "data-child")) == calls


def test_a_node_the_store_does_not_hold_is_a_404(client: TestClient) -> None:
    """A turn id, a thread or a session the store never held: nothing at that URL.

    Three separate misses because the route reads three keys, and a page that answered on
    two of them would be a page about some other session's turn.
    """
    for url in (
        f"/session/{ANCESTOR}/turn/{MAIN}/{MISSING}",
        f"/session/{ANCESTOR}/turn/{MISSING}/{DENSE_TURN}",
        f"/session/{MISSING}/turn/{MAIN}/{DENSE_TURN}",
    ):
        assert client.get(url).status_code == 404, url


def test_the_same_node_url_serves_the_same_bytes_cold_and_warm(client: TestClient) -> None:
    """A tree click and a pasted link produce one response, byte for byte.

    The click is an `hx-get` of the node's own URL, cut down to `#pane` by the browser rather
    than by the server, so the response cannot depend on the htmx headers that came with it.
    That is what lets one entry in the payload sweep price both ways of arriving.
    """
    cold = client.get(TURN)
    warm = client.get(TURN, headers=HTMX)
    assert warm.status_code == cold.status_code == 200
    assert warm.content == cold.content


def test_the_body_a_page_wraps_is_the_body_the_fragment_serves(client: TestClient) -> None:
    """One body, two mounts: the full view wraps it, the fragment serves it alone.

    An expansion in a children log fetches the body mount, so the two have to agree about the
    node — and the fragment stops there rather than nesting another log inside an accordion:
    its children are a count and a link to the node's own page.
    """
    full = client.get(TURN).text
    body = client.get(BODY)
    assert body.status_code == 200
    # The same node, said the same way, whichever way it was fetched...
    assert fields(body.text, "data-body", "turn") == fields(full, "data-body", "turn")
    # ...while what wraps it — the crumbs above and the children log below — is the page's,
    # and the fragment carries neither.
    assert fields(full, "data-crumbs", "turn") and fields(full, "data-log", "turn")
    assert fields(body.text, "data-crumbs", "turn") == {}
    assert fields(body.text, "data-log", "turn") == {}
    # What the fragment says instead: how many children the node has, and where to read them.
    assert fields(body.text, "data-children", "turn")["children"] == str(
        len(values(full, "data-child"))
    )
    assert inside(body.text, "data-children", "turn", "href") == [TURN]
