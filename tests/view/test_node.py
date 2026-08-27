"""The node page: one node of a session served whole, with the pane beside its tree.

Every node has a URL that renders cold as a full page, and a tree click is an `hx-get` of that
same URL — so the leaves here fetch node URLs both ways and read the pane through `data-*`.
The pane is three parts: the node's own facts, the one or two fat values it previews with the
way to the whole of each, and a page of its children as links. The first part is this file; the
previews are `test_node__details.py` and the children log is `test_node__logs.py`.

Which node each leaf reads is picked out of the store by `tests/view/selections.py`.
"""

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.view.columns import COLUMNS, Shape
from hyphae.view.nodes import BODY_URL
from tests.conftest import ANCESTOR, DENSE_TURN, MAIN, SPINE
from tests.view.conftest import (
    MISSING,
    fields,
    icons,
    inside,
    one,
    values,
)
from tests.view.selections import (
    KINDS,
    LEVELS,
    TURN,
    node_url,
)

# What htmx puts on the request a tree click makes. The node URL is the same either way,
# which is the point of the leaf that sends them.
HTMX = {
    "HX-Request": "true",
    "HX-Target": "pane",
    "HX-Current-URL": f"http://testserver{TURN}",
}


# The mark each kind carries wherever a page names one of its nodes. Written out here rather
# than read from `nodes.GLYPHS`: these are the viewer's whole visual vocabulary, and a test
# that imported the table would agree with any edit to it. Three of them are shared with the
# heading of the column that counts the kind, which the leaf below the log sweep holds.
MARKS = {
    "session": "❖",
    "turn": "❯",
    "run": "◎",
    "call": "⇄",
    "tool": "⚒",
    "compaction": "⊟",
    # One mark for both buckets: each holds what the transcript could not attach, and a reader
    # meets them as one kind of hole rather than two.
    "unattributed": "∅",
    "unattached": "∅",
}


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
    # The crumbs run outermost first and end at the selection, which is the row the NavTree marks.
    crumbs = values(page.text, "data-crumb")
    (selected,) = values(page.text, "data-selected")
    assert crumbs[0].startswith("session:")
    assert crumbs[-1] == selected
    # And the selection's own row links to the URL that was asked for.
    assert inside(page.text, "data-nav-tree", selected, "href")[0] == url
    # Four places on this page name the node, and every one of them says what kind it is with
    # the same character: the pane's heading, the browser tab, the last crumb, and the row the
    # tree marks. A reader learns eight marks once and then reads a tree without reading a
    # title — which is the whole of what the mark buys, so a surface missing it is a surface
    # where the same node looks like something else.
    mark = MARKS[kind]
    assert icons(page.text, "data-body", kind) == [mark], url
    assert page.text.count(f"<title>{mark} ") == 1, url
    assert icons(page.text, "data-crumb", crumbs[-1]) == [mark], url
    assert icons(page.text, "data-nav-tree", selected) == [mark], url
    # And a crumb above the selection is marked as what *it* is, not as what the page is
    # about: the chain says the kind of every step down to here.
    assert icons(page.text, "data-crumb", crumbs[0]) == [MARKS["session"]], url
    # Every one of those marks is decoration and the markup says so. It stands for a word
    # already on the page — the pane's kind, the crumb's field name, the row's class — so a
    # screen reader passes over it and reads the title instead of announcing a character it
    # has no word for (`.claude/rules/viewer-ui.md`).
    for where, key in (
        ("data-body", kind),
        ("data-crumb", crumbs[-1]),
        ("data-nav-tree", selected),
    ):
        assert inside(page.text, where, key, "aria-hidden") == ["true"], (url, where)


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


def test_a_slash_turn_leads_with_the_command_it_ran(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A turn typed as a slash command shows the command, not the block it was expanded into.

    Claude Code stores such a turn's prompt as the `<command-name>`/`<command-args>` wrapper it
    built, and the extractor pulls the two halves into columns of their own. The pane reads
    those columns — the command on a line of its own, and what followed it as a value of the
    turn — and drops the wrapper from the prompt beside them, which otherwise printed the
    command and its arguments a second time in their tags. What was sent stays whole in the
    thread's transcript, which is where the pane links for the record.
    """
    turn_id, name, args = one(
        store,
        "SELECT id, command_name, command_args FROM live_turns"
        " WHERE session_id = ? AND source = ? AND command_name IS NOT NULL"
        ' AND length(command_args) > 0 ORDER BY "index" LIMIT 1',
        [SPINE, MAIN],
    )
    page = client.get(f"/session/{SPINE}/thread/{MAIN}/turn/{turn_id}").text
    # The command, off the store's own column and on the command line the pane leads with
    # rather than among the counts the header rows.
    assert fields(page, "data-command", turn_id)["command_name"] == name
    # What followed it is a value of the turn like the prompt is, so it is previewed under its
    # own heading with the way to the rest of it — arguments run to thousands of characters.
    assert fields(page, "data-detail", "command_args")["command_args"] == args
    # The rest of it comes off a route of its own, rendered as the prose a person typed —
    # like the prompt beside it, and unlike a tool's arguments, which are JSON and are marked
    # up as JSON. A fetch that read the arguments as code would print them in a `<pre>`.
    served = client.get(f"/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{turn_id}").text
    assert "<p>" in served
    assert "<pre" not in served
    # The wrapper itself is gone from the pane: everything inside it is already on the page
    # under the two headings above, and this turn's prompt is nothing else.
    assert "prompt" not in values(page, "data-detail")
    # Gone from the value route under that heading too, and not as an empty page: the column
    # the fragment reads is NULL for this turn, so the URL a reader kept answers nothing.
    assert (
        client.get(f"/fragment/prompt/session/{SPINE}/thread/{MAIN}/turn/{turn_id}").status_code
        == 404
    )
    # It is still what was sent, though, so the record the pane opens beneath holds it whole.
    (line_no,) = values(page, "data-open-record")
    recorded = client.get(f"/fragment/record/session/{SPINE}/thread/{MAIN}/line/{line_no}").text
    assert "&lt;command-name&gt;" in recorded
    # A turn nobody typed a command at has no command line at all: the pane leads with the
    # prompt, and there is no empty heading over a column the store left NULL.
    assert not values(client.get(TURN).text, "data-command")


# What column of a node's own facts counts the children its expansion links to instead of
# listing. A kind absent from here has none — a tool call ends the NavTree.
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

    Run over the described store as well as the plain one: a title is the model's words where a
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
            # The mount rides the row rather than carrying a title of its own, so it is the
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
            # And it is only the body: everything the full view wraps it in is absent. A
            # call's expansion is the one that lists a level under it — the tools it called,
            # which is what the leaf below reads.
            for wrapper in ("data-crumb", "data-nav-tree", "data-walk", "data-detail"):
                assert not values(served.text, wrapper), (mount, wrapper)
            # A call that called none has nothing to list, so it stands the count like the rest.
            called = (
                child == "call" and fields(served.text, "data-body", child)["tool_calls"] != "0"
            )
            assert values(served.text, "data-log") == (["tools"] if called else []), mount
            # What is under the child is the way to its own page, with the count beside it
            # wherever the expansion listed nothing — and that count is the body's own.
            (link,) = inside(served.text, "data-children", child, "href")
            assert link == own, mount
            counted = fields(served.text, "data-children", child)
            if child in CHILDREN and not called:
                assert (
                    counted["children"] == fields(served.text, "data-body", child)[CHILDREN[child]]
                ), mount
            else:
                assert "children" not in counted, mount
            opened.add(child)
    # Every kind a log lists was opened: a shape the sweep never reached is a mount nothing
    # proved serves.
    assert opened == {"turn", "call", "tool", "run"}


def test_a_call_opened_in_its_turn_lists_the_tools_it_called(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """An api call opened in a turn's log lists its tool calls, the way the call's own page does.

    The expansion used to say `4 tools` and stop, which is a number the row above it already
    printed — a reader who wanted to know what the call did had to leave the turn. It now
    mounts the log the call's own page carries, through the same macro rather than a second
    shape, so a tool reads the same in both places.

    One level and no further. The rows carry no opener and the table drops the column that
    holds one: an expansion that opened an expansion is the accordion of accordions the rule
    forbids, and the way past this level is the link to the call's own page under it.
    """
    session_id, source, turn_id = one(store, LEVELS["turn"][0])
    page = client.get(LEVELS["turn"][1].format(session_id, source, turn_id)).text
    # The first call on the turn that called any tools, so the expansion has rows to list.
    called = [
        key
        for key in values(page, "data-child")
        if fields(page, "data-child", key)["tool_calls"] != "0"
    ]
    assert called, "the turn's calls made no tool calls, so no expansion can list one"
    key = called[0]
    (mount,) = [at for at in inside(page, "data-child", key, "hx-get") if at.startswith(BODY_URL)]
    (own,) = inside(page, "data-child", key, "href")
    opened = client.get(mount).text
    listed = client.get(own).text
    # The same tool calls the call's own page lists, in the same order, printing the same
    # values — one derivation, one shape, two mounts.
    rows = values(opened, "data-child")
    assert rows == values(listed, "data-child")
    assert rows, mount
    for row in rows:
        assert fields(opened, "data-child", row) == fields(listed, "data-child", row), row
    # Headed like the log on the page, less the column the opener lives in...
    named = [column.field for column in COLUMNS[Shape.TOOLS] if column.field != "body"]
    assert inside(opened, "data-columns", "tools", "data-column") == named
    for row in rows:
        assert inside(opened, "data-child", row, "data-column") == named, row
    # ...because no row in an expansion opens another one.
    assert "data-view" not in opened
    # And the count of the level stands in the log's own heading, with the link under it left
    # to say the one thing the heading does not: where the rest of this call is.
    assert (
        fields(opened, "data-log", "tools")["children"]
        == fields(page, "data-child", key)["tool_calls"]
    )


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
    page = client.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
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
    assert not values(
        client.get(f"/session/{quiet}/thread/{thread}/tool/{call}").text, "data-spawned"
    )


def test_the_same_node_url_serves_the_same_bytes_cold_and_warm(client: TestClient) -> None:
    """A tree click and a pasted link produce one response, byte for byte.

    The click is an `hx-get` of the node's own URL, cut down to `#reading-pane` by the
    browser rather than by the server, so the response cannot depend on the htmx headers that
    came with it.
    That is what lets one entry in the payload sweep price both ways of arriving.
    """
    cold = client.get(TURN)
    warm = client.get(TURN, headers=HTMX)
    assert warm.status_code == cold.status_code == 200
    assert warm.content == cold.content


def test_the_citation_footer_scrolls_with_the_pane_it_cites(client: TestClient) -> None:
    """A node page's footer sits inside the reading pane, last, rather than beside it.

    The page fills the viewport: the NavTree and the pane each carry a scrollbar and the
    document carries none, so a footer outside both would be a strip pinned under them or a
    line below the fold of a page that does not scroll. Inside the pane it scrolls with the
    node it cites — and a tree click, which takes `#reading-pane` out of the response, now
    brings that node's citations along instead of leaving the last node's behind.

    Containment is what is asserted rather than a class: CSS alone could stand a sibling under
    the pane and look right, while the swap kept serving stale provenance.
    """
    ids = inside(client.get(TURN).text, "id", "reading-pane", "id")
    assert ids[0] == "reading-pane"
    assert ids[-1] == "citation"
