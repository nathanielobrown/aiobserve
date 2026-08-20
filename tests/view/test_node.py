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
from aiobserve.view.app import build_app, continued
from aiobserve.view.format import ELLIPSIS
from aiobserve.view.labels import LABELS
from aiobserve.view.nodes import BODY_URL
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


# What column of a node's own facts counts the children its expansion links to instead of
# listing. A kind absent from here has none — a tool call ends the tree.
CHILDREN = {"turn": "api_calls", "call": "tool_calls", "run": "turns"}


@pytest.mark.parametrize("named", ["client", "enriched_client"])
def test_a_log_row_expands_to_the_body_its_own_page_wraps(
    request: pytest.FixtureRequest, store: duckdb.DuckDBPyConnection, named: str
) -> None:
    """A children-log row opens the child's body alone: one body, two mounts.

    The full view wraps that body with the crumbs above it, the log under it and prev/next
    beside it; the expansion adds none of them, and the child's own children are a count and a
    link rather than a second accordion. Swept over every kind of page so every shape of log
    row is opened, because an expansion is built from the child's kind, not the parent's.

    Run over the described store as well as the plain one: a label is the model's words where a
    pass reached the node, and a body that read enrichment differently from the page wrapping
    it would tell a reader two things about one node.
    """
    client: TestClient = request.getfixturevalue(named)
    opened = set()
    # Every kind's own page, plus the corpus's densest session: the first session by id holds
    # no turns of its own, so without it no turn expansion is ever opened.
    urls = [node_url(store, kind) for kind in KINDS] + [f"/session/{ANCESTOR}"]
    for url in urls:
        page = client.get(url).text
        for key in values(page, "data-child"):
            child, _, _ = key.partition(":")
            # The mount rides the row rather than carrying a label of its own, so it is the
            # fetch under the body URL among the row's two.
            (mount,) = [
                url for url in inside(page, "data-child", key, "hx-get") if url.startswith(BODY_URL)
            ]
            served = client.get(mount)
            assert served.status_code == 200, mount
            # The body is the one the child's own page wraps, fact for fact.
            (own,) = inside(page, "data-child", key, "href")
            assert fields(served.text, "data-body", child) == fields(
                client.get(own).text, "data-body", child
            ), mount
            # And it is only the body: everything the full view wraps it in is absent.
            for wrapper in ("data-crumb", "data-tree", "data-walk", "data-log", "data-detail"):
                assert not values(served.text, wrapper), (mount, wrapper)
            # What is under the child is a count and the way to its own page, and the count is
            # the one the body itself reports.
            (link,) = inside(served.text, "data-children", child, "href")
            assert link == own, mount
            counted = fields(served.text, "data-children", child)
            if child in CHILDREN:
                assert (
                    counted["children"] == fields(served.text, "data-body", child)[CHILDREN[child]]
                ), mount
            else:
                assert "children" not in counted, mount
            opened.add(child)
    # Every kind a log lists was opened: a shape the sweep never reached is a mount nothing
    # proved serves.
    assert opened == {"turn", "call", "tool", "run"}


def test_a_tool_call_that_spawned_a_run_leads_with_the_way_to_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A `Task` call's body opens with a link to the run it started.

    The tool call is where a run begins, and the run is what a reader came to the call to
    reach — so it leads the body rather than sitting under the facts. Read out of the store's
    own spawning edge, and followed: a link to a page that does not serve is not a way there.
    """
    session_id, source, tool_id, run_id = one(
        store,
        "SELECT tc.session_id, tc.source, tc.id, a.id FROM live_tool_calls tc"
        " JOIN live_agent_runs a ON a.session_id = tc.session_id AND a.tool_use_id = tc.id"
        # A fork copies the call that spawned it into its own thread; that copy spawned
        # nothing, and this is the rule every other query reads the edge by.
        "  AND tc.source <> a.id"
        " ORDER BY tc.session_id, tc.id LIMIT 1",
    )
    page = client.get(f"/session/{session_id}/tool/{source}/{tool_id}").text
    (href,) = inside(page, "data-spawned", run_id, "href")
    assert href == f"/session/{session_id}/run/{run_id}"
    assert client.get(href).status_code == 200
    # It leads: the link is above the tool's own facts, not under them.
    assert page.index(f'data-spawned="{run_id}"') < page.index('data-field="tool_index"')
    # And a call that started no run says nothing about one, rather than linking nowhere.
    plain = one(
        store,
        "SELECT tc.session_id, tc.source, tc.id FROM live_tool_calls tc"
        " LEFT JOIN live_agent_runs a ON a.session_id = tc.session_id AND a.tool_use_id = tc.id"
        "  AND tc.source <> a.id"
        " WHERE a.id IS NULL ORDER BY tc.session_id, tc.id LIMIT 1",
    )
    quiet, thread, call = plain
    assert not values(client.get(f"/session/{quiet}/tool/{thread}/{call}").text, "data-spawned")


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
        # The pane shows the width it budgeted for, marked where the value went on, and says
        # how many characters it left.
        head = prompt[: bounds.DETAIL.ceiling] + ELLIPSIS
        assert fields(page, "data-detail", "prompt")["prompt"] == head
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
        assert fields(narrow, "data-detail", "prompt")["prompt"] == prompt[:10] + ELLIPSIS


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
    (nxt,) = inside(first.text, "data-page", "calls", "href")
    assert "after=" in nxt and "offset" not in nxt.lower()
    assert values(client.get(nxt).text, "data-child") == children[1:2]
    # Walking the log to its end lands on every child exactly once, in the level's own order.
    walked: list[str] = []
    at: str | None = f"{TURN}?log=1"
    while at is not None:
        page = client.get(at).text
        walked += values(page, "data-child")
        following = inside(page, "data-page", "calls", "href")
        at = following[0] if following else None
    assert walked == children
    # And a page past the last one is nothing, rather than an empty log that reads as a node
    # with no children.
    assert client.get(TURN, params={"after": 10_000}).status_code == 404
    # Every page above turned the log down with `?log=`, and it had to: the corpus's fullest
    # level is five children against a production log of twelve, so no recorded node overflows
    # and no page mints this link with the cursor as its *first* query parameter. That arm is
    # read on the helper the template links with — a `&` where a `?` belongs is a 404.
    assert continued(TURN, "", 3) == f"{TURN}?after=3"
    assert continued(TURN, "?log=1", 3) == f"{TURN}?log=1&after=3"


def test_the_bucket_that_pages_in_memory_walks_the_same_way_the_query_does(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The unattached bucket's log pages by slicing, and owes what the keyset log owes.

    Its runs arrive with the session's, which every level of the tree needs anyway, so this one
    level cuts a list it already holds instead of asking the store for a page. Read on the one
    recorded bucket that holds more than one run: the pages have to concatenate to the level,
    the "+N more" has to count what was left, and the cursor has to resume on the row it
    stopped at rather than repeat or skip one.
    """
    sessions = [str(row[0]) for row in store.execute("SELECT id FROM sessions").fetchall()]
    paged = [
        (f"/session/{session_id}/unattached", page.text)
        for session_id in sessions
        if (page := client.get(f"/session/{session_id}/unattached")).status_code == 200
        and len(values(page.text, "data-child")) > 1
    ]
    assert paged, "the corpus has a bucket holding more than one unattached run"
    at, whole = paged[0]
    children = values(whole, "data-child")
    first = client.get(at, params={"log": 1}).text
    assert values(first, "data-child") == children[:1]
    assert fields(first, "data-log", "runs")["cut"] == str(len(children) - 1)
    # Walking to the end lands on every run exactly once, in the level's own order...
    walked: list[str] = []
    visited: list[str] = []
    following: str | None = f"{at}?log=1"
    while following is not None and len(visited) <= len(children):
        visited.append(following)
        page = client.get(following).text
        walked += values(page, "data-child")
        onward = inside(page, "data-page", "runs", "href")
        following = onward[0] if onward else None
    assert walked == children
    # ...in one page each and not one page more. A last page that still offered a way on would
    # send the reader to an empty page, or round the same one again.
    assert len(visited) == len(children)
    assert not inside(
        client.get(at, params={"log": len(children)}).text, "data-page", "runs", "href"
    )


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
