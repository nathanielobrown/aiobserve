"""The node page: one node of a session served whole, with the pane beside its tree.

Every node has a URL that renders cold as a full page, and a tree click is an `hx-get` of that
same URL — so the leaves here fetch node URLs both ways and read the pane through `data-*`.
The pane is three parts: the node's own facts, the one or two fat values it previews with the
way to the whole of each, and a page of its children as links.

The node of each kind is read from the store rather than pinned, so a re-recorded fixture moves
the selection instead of reddening the tier.
"""

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view import bounds
from aiobserve.view.app import build_app
from aiobserve.view.labels import LABELS
from tests.conftest import ANCESTOR, DENSE_TURN, MAIN, SPINE
from tests.view.conftest import MISSING, Planter, fields, inside, one, values

# The corpus's densest main-thread turn — 4 api calls under it — so the pane's children log
# has more than one row and the tree has a level under the selection worth rendering.
TURN = f"/session/{ANCESTOR}/turn/{MAIN}/{DENSE_TURN}"

# What htmx puts on the request a tree click makes. The node URL is the same either way,
# which is the point of the leaf that sends them.
HTMX = {
    "HX-Request": "true",
    "HX-Target": "pane",
    "HX-Current-URL": f"http://testserver{TURN}",
}

# One node of every kind a URL can name, read out of the store: the SQL that finds one, and the
# URL template it fills. Every kind is here on purpose — the pane dispatches on the kind, and a
# kind missing from the sweep is a kind whose page nothing renders.
KINDS: dict[str, tuple[str, str]] = {
    "session": ("SELECT id FROM sessions ORDER BY id LIMIT 1", "/session/{0}"),
    "turn": (
        'SELECT session_id, source, id FROM live_turns ORDER BY session_id, source, "index"'
        " LIMIT 1",
        "/session/{0}/turn/{1}/{2}",
    ),
    "run": (
        "SELECT session_id, id FROM live_agent_runs ORDER BY session_id, id LIMIT 1",
        "/session/{0}/run/{1}",
    ),
    "call": (
        'SELECT session_id, source, id FROM live_api_calls ORDER BY session_id, source, "index"'
        " LIMIT 1",
        "/session/{0}/call/{1}/{2}",
    ),
    "tool": (
        "SELECT session_id, source, id FROM live_tool_calls ORDER BY session_id, source, id"
        " LIMIT 1",
        "/session/{0}/tool/{1}/{2}",
    ),
    "compaction": (
        "SELECT session_id, source, id FROM live_compactions ORDER BY session_id, source, id"
        " LIMIT 1",
        "/session/{0}/compaction/{1}/{2}",
    ),
    # The two buckets, each found by what puts a row in it: a call answering no turn of its own
    # thread, and a run whose spawning call resolves to nothing at all.
    "unattributed": (
        "SELECT c.session_id, c.source FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE t.id IS NULL ORDER BY c.session_id, c.source LIMIT 1",
        "/session/{0}/unattributed/{1}",
    ),
    "unattached": (
        "SELECT a.session_id FROM live_agent_runs a"
        " LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id"
        "  AND tc.id = a.tool_use_id AND tc.source <> a.id"
        " LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "  AND c.id = tc.api_call_id"
        " WHERE c.id IS NULL ORDER BY a.session_id LIMIT 1",
        "/session/{0}/unattached",
    ),
}


def node_url(store: duckdb.DuckDBPyConnection, kind: str) -> str:
    """The URL of one recorded node of `kind`, whichever the store answers with."""
    sql, shape = KINDS[kind]
    return shape.format(*one(store, sql))


@pytest.mark.parametrize("kind", list(KINDS))
def test_every_kind_of_node_serves_a_page_that_says_what_it_is(
    client: TestClient, store: duckdb.DuckDBPyConnection, kind: str
) -> None:
    """One page per kind, cold, carrying the pane for that kind and the crumbs down to it.

    Swept per kind rather than over one node because the pane dispatches on the kind and each
    arm renders different facts. What is checked is the frame every page shares: the right
    pane, a chain that ends at the selection, and a tree whose selected row is the same node.
    """
    url = node_url(store, kind)
    page = client.get(url)
    assert page.status_code == 200, url
    # The pane is the one for this kind, and it carries the node's own facts.
    assert values(page.text, "data-body") == [kind], url
    assert fields(page.text, "data-body", kind), url
    # The crumbs run outermost first and end at the selection, which is the row the tree marks.
    crumbs = values(page.text, "data-crumb")
    (selected,) = values(page.text, "data-selected")
    assert crumbs[0].startswith("session:")
    assert crumbs[-1] == selected
    # And the selection's own row links to the URL that was asked for.
    assert inside(page.text, "data-tree", selected, "href")[0] == url


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_node_the_store_does_not_hold_is_a_404(
    client: TestClient, store: duckdb.DuckDBPyConnection, kind: str
) -> None:
    """Every key a node URL carries is read, so a miss on any one of them is nothing.

    The session is swapped on every kind and the node's own id on every kind that has one: a
    page that answered on the session alone would be a page about some other session's turn.
    An empty bucket is a miss too — it is a node that is not there rather than an empty one.
    """
    url = node_url(store, kind)
    session_id = url.split("/")[2]
    assert client.get(url.replace(session_id, MISSING, 1)).status_code == 404, url
    if (tail := url.rsplit("/", 1)[1]) != session_id:
        assert client.get(url.replace(tail, MISSING)).status_code == 404, url


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


def test_a_pane_previews_a_fat_value_and_offers_the_rest_as_its_own_fetch(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A value past the pane's width is cut, counted, and fetched whole from its own URL.

    Planted rather than recorded: redaction flattened every long string in the corpus, so no
    fixture prompt reaches `bounds.DETAIL` and the cut would never fire. The plant is one
    recorded turn's prompt, grown past the width, and what is read is the arithmetic — the head
    is exactly the width, the count is the rest, and the fetch answers with the whole.
    """
    prompt = "x" * (bounds.DETAIL.ceiling * 2)
    path = plant(
        (
            "UPDATE turns SET prompt = ? WHERE session_id = ? AND source = ? AND id = ?",
            [prompt, ANCESTOR, MAIN, DENSE_TURN],
        )
    )
    with TestClient(build_app(path)) as grown:
        page = grown.get(TURN).text
        # The pane shows the width it budgeted for, and says how many characters it left.
        assert fields(page, "data-detail", "prompt")["prompt"] == prompt[: bounds.DETAIL.ceiling]
        assert (
            fields(page, "data-detail", "prompt")["cut"]
            == f"{len(prompt) - bounds.DETAIL.ceiling:,}"
        )
        # The link beside it fetches the value alone, and that fetch is the whole of it.
        (url,) = inside(page, "data-detail", "prompt", "href")
        whole = grown.get(url)
        assert whole.status_code == 200
        assert values(whole.text, "data-value") == [str(len(prompt))]
        # A reader who asks for less gets less, which is what makes the width a knob.
        narrow = grown.get(TURN, params={"detail": 10}).text
        assert fields(narrow, "data-detail", "prompt")["prompt"] == prompt[:10]


def test_every_value_a_pane_previews_is_fetchable_whole_from_its_own_url(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The four fat columns a node page previews each round-trip through a value route.

    One route per column rather than one per row: a tool call's input and its result are two
    values a reader opens apart, and a route that served the row whole would send the other
    one every time. Each is checked against the length the store holds, which is what proves
    the fetch is untruncated rather than merely longer than the preview.
    """
    columns = {
        # The node URL that previews it, the value route, and where the store keeps it.
        "prompt": (
            f"/session/{SPINE}/turn/{MAIN}/{{0}}",
            f"/fragment/prompt/{SPINE}/{MAIN}/{{0}}",
            "SELECT id, length(prompt) FROM live_turns WHERE session_id = ? AND source = ?"
            " AND length(prompt) > 0 ORDER BY length(prompt) DESC LIMIT 1",
        ),
        "input": (
            f"/session/{SPINE}/tool/{MAIN}/{{0}}",
            f"/fragment/input/{SPINE}/{MAIN}/{{0}}",
            "SELECT id, length(input) FROM live_tool_calls WHERE session_id = ? AND source = ?"
            " AND length(input) > 0 ORDER BY length(input) DESC LIMIT 1",
        ),
        "result": (
            f"/session/{SPINE}/tool/{MAIN}/{{0}}",
            f"/fragment/result/{SPINE}/{MAIN}/{{0}}",
            "SELECT id, length(result) FROM live_tool_calls WHERE session_id = ? AND source = ?"
            " AND length(result) > 0 ORDER BY length(result) DESC LIMIT 1",
        ),
        "text": (
            f"/session/{SPINE}/call/{MAIN}/{{0}}",
            f"/fragment/text/{SPINE}/{MAIN}/{{0}}",
            "SELECT id, length(text) FROM live_api_calls WHERE session_id = ? AND source = ?"
            " AND length(text) > 0 ORDER BY length(text) DESC LIMIT 1",
        ),
    }
    for name, (node, fragment, sql) in columns.items():
        node_id, held = one(store, sql, [SPINE, MAIN])
        # The pane previews it under its own name...
        page = client.get(node.format(node_id)).text
        assert fields(page, "data-detail", name)[name], name
        # ...and its own route answers with every character the store holds. Reached by URL
        # rather than by the pane's link, which the pane only draws when there is a rest to
        # offer — every value this corpus records fits inside the preview.
        served = client.get(fragment.format(node_id))
        assert served.status_code == 200, name
        assert values(served.text, "data-value") == [str(held)], name
    # And a run's brief, which is the one fat column that hangs off the session rather than a
    # thread, so its route takes no source.
    session_id, run_id, held = one(
        store,
        "SELECT session_id, id, length(description) FROM live_agent_runs"
        " WHERE length(description) > 0 ORDER BY length(description) DESC LIMIT 1",
    )
    page = client.get(f"/session/{session_id}/run/{run_id}").text
    assert fields(page, "data-detail", "description")["description"]
    served = client.get(f"/fragment/brief/{session_id}/{run_id}")
    assert values(served.text, "data-value") == [str(held)]
    # The brief is what a run was asked to do, so it is labelled as a brief and not as a
    # description of the run — the word the enrichment pass owns.
    assert LABELS["description"] == "Task brief"


def test_a_children_log_pages_by_keyset_and_says_what_it_left(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The log is one page of children, and "+N more" resumes at the last row rather than an
    offset.

    Driven below the corpus's fan-out with `?log=`, because no recorded turn has more children
    than the production page. What is read is that the second page starts where the first
    stopped and that the two together are the level — a page built on an offset would repeat or
    skip a row when the level is read twice.
    """
    whole = client.get(TURN).text
    children = values(whole, "data-child")
    assert len(children) > 2, "the log has to have something to page"
    first = client.get(TURN, params={"log": 1})
    assert values(first.text, "data-child") == children[:1]
    assert fields(first.text, "data-log", "calls")["cut"] == str(len(children) - 1)
    # The link says where to resume, by the cursor of the row it stopped on and not by a count.
    (nxt,) = inside(first.text, "data-next", "calls", "href")
    assert "after=" in nxt and "offset" not in nxt.lower()
    assert values(client.get(nxt).text, "data-child") == children[1:2]
    # Walking the log to its end lands on every child exactly once, in the level's own order.
    walked: list[str] = []
    at: str | None = f"{TURN}?log=1"
    while at is not None:
        page = client.get(at).text
        walked += values(page, "data-child")
        following = inside(page, "data-next", "calls", "href")
        at = following[0] if following else None
    assert walked == children
    # And a page past the last one is nothing, rather than an empty log that reads as a node
    # with no children.
    assert client.get(TURN, params={"after": 10_000}).status_code == 404


def test_a_page_asked_for_the_first_cursor_is_the_page_with_no_cursor_at_all(
    client: TestClient,
) -> None:
    """`?after=` at the opening cursor serves the same page the URL without it serves.

    The two have to agree or a reader who pages back to the start gets a different document
    from the one they were linked, and the payload sweep prices only one of them.
    """
    bare = client.get(TURN)
    opened = client.get(TURN, params={"after": queries.FIRST_PAGE})
    assert opened.status_code == bare.status_code == 200
    assert values(opened.text, "data-child") == values(bare.text, "data-child")
