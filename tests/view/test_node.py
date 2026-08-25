"""The node page: one node of a session served whole, with the pane beside its tree.

Every node has a URL that renders cold as a full page, and a tree click is an `hx-get` of that
same URL — so the leaves here fetch node URLs both ways and read the pane through `data-*`.
The pane is three parts: the node's own facts, the one or two fat values it previews with the
way to the whole of each, and a page of its children as links.

The node of each kind is read from the store rather than pinned, so a re-recorded fixture moves
the selection instead of reddening the tier.
"""

import json
import re

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.view import bounds
from aiobserve.view.app import build_app, numbered
from aiobserve.view.format import ELLIPSIS
from aiobserve.view.labels import LABELS, label
from aiobserve.view.nodes import BODY_URL, COLUMNS, Shape
from tests.conftest import ANCESTOR, DENSE_TURN, MAIN, SPINE
from tests.view.conftest import MISSING, Planter, block, fields, inside, one, plain, values

# The corpus's densest main-thread turn — 4 api calls under it — so the pane's children log
# has more than one row and the tree has a level under the selection worth rendering.
TURN = f"/session/{ANCESTOR}/thread/{MAIN}/turn/{DENSE_TURN}"

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
        "/session/{0}/thread/{1}/turn/{2}",
    ),
    "run": (
        "SELECT session_id, id FROM live_agent_runs ORDER BY session_id, id LIMIT 1",
        "/session/{0}/run/{1}",
    ),
    "call": (
        'SELECT session_id, source, id FROM live_api_calls ORDER BY session_id, source, "index"'
        " LIMIT 1",
        "/session/{0}/thread/{1}/call/{2}",
    ),
    "tool": (
        "SELECT session_id, source, id FROM live_tool_calls ORDER BY session_id, source, id"
        " LIMIT 1",
        "/session/{0}/thread/{1}/tool/{2}",
    ),
    "compaction": (
        "SELECT session_id, source, id FROM live_compactions ORDER BY session_id, source, id"
        " LIMIT 1",
        "/session/{0}/thread/{1}/compaction/{2}",
    ),
    # The two buckets, each found by what puts a row in it: a call answering no turn of its own
    # thread, and a run whose spawning call resolves to nothing at all.
    "unattributed": (
        "SELECT c.session_id, c.source FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE t.id IS NULL ORDER BY c.session_id, c.source LIMIT 1",
        "/session/{0}/thread/{1}/unattributed",
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

    The click is an `hx-get` of the node's own URL, cut down to `#pane` by the browser rather
    than by the server, so the response cannot depend on the htmx headers that came with it.
    That is what lets one entry in the payload sweep price both ways of arriving.
    """
    cold = client.get(TURN)
    warm = client.get(TURN, headers=HTMX)
    assert warm.status_code == cold.status_code == 200
    assert warm.content == cold.content


def test_a_pane_previews_a_fat_value_and_offers_the_rest_as_its_own_fetch(
    client: TestClient, plant: Planter, store: duckdb.DuckDBPyConnection
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
    # The recorded prompt at that same URL fits, and a value that fits offers nothing: no count
    # of what is left, and no fetch of a rest that is not there.
    fits = client.get(TURN).text
    assert "cut" not in fields(fits, "data-detail", "prompt")
    assert not inside(fits, "data-detail", "prompt", "data-whole")


# A shell command with something for a lexer to find in it: a builtin, an operator, a quoted
# string and a pipe. Planted rather than recorded — redaction flattened every command the
# fixture corpus holds to `[redacted]` — and real in the sense that matters here: it is a line
# this repository's own tasks run.
COMMAND = "cd /tmp && rg -n 'x' *.py | head -3"
# And what a `Read` of a markdown file returns: the source, behind the line-number gutter
# Claude Code adds. Planted for the same reason — a recorded file path reads `[redacted]`.
READ = "1\t# Title\n2\t\n3\t- an item\n"
# What an `Edit` of a python file returns instead: a sentence about the file, which is the
# shape the guards below exist to keep apart from the file itself.
EDITED = "The file /tmp/notes.py has been updated."
# And a command argument passed to a tool that runs no shell, for the same guards read the
# other way round.
NOT_RUN = "ls -la"


def call_to(store: duckdb.DuckDBPyConnection, tool: str) -> tuple[str, str, str]:
    """One recorded call to `tool`: the session, the thread, and the call's id.

    Read out of the store rather than pinned, because a tool call this tier can render is one
    the tree reaches — an id copied out of a transcript may name a record a later line
    replaced, and its page is a 404 an absence assertion cannot tell from an answer.
    """
    return one(
        store,
        "SELECT session_id, source, id FROM live_tool_calls WHERE name = ?"
        " ORDER BY session_id, source, id LIMIT 1",
        [tool],
    )


def test_a_bash_call_reads_the_command_it_ran_as_a_shell_reads_it(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A `Bash` call previews the command itself, marked up as shell.

    The command is in the input JSON, escaped onto one line among the tool's other arguments,
    and a reader who opened the call to read the command should not be reading it out of a
    JSON string. So it is a value of the pane like the input and the result are, with the rest
    of a long command behind its own route.
    """
    session_id, source, tool_id = call_to(store, "Bash")
    read_session, read_source, read_id = call_to(store, "Read")
    path = plant(
        (
            "UPDATE tool_calls SET input = ? WHERE session_id = ? AND source = ? AND id = ?",
            [
                json.dumps({"description": "look for x", "command": COMMAND}),
                session_id,
                source,
                tool_id,
            ],
        ),
        # ...and the same argument on a `Read`, which runs nothing. Real in shape: 86 recorded
        # calls to tools other than `Bash` were passed a `command` of their own (the canonical
        # store, read 2026-08-20), 2 of them to `Read`.
        (
            "UPDATE tool_calls SET input = ? WHERE session_id = ? AND source = ? AND id = ?",
            [
                json.dumps({"file_path": "[redacted]", "command": NOT_RUN}),
                read_session,
                read_source,
                read_id,
            ],
        ),
    )
    with TestClient(build_app(path)) as ran:
        page = ran.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
        marked = block(page, "command")
        # Every character the store holds is still there to read back...
        assert plain(marked) == COMMAND
        # ...and a shell's own words are marked as what they are: `cd` a builtin, `&&` an
        # operator. Which classes those are is `view/highlight.py`'s business; that the pane
        # asked for a shell rather than for JSON is this leaf's.
        assert '<span class="nb">cd</span>' in marked
        assert '<span class="o">&amp;&amp;</span>' in marked
        # The whole of it has a route of its own, marked up the same way — the syntax is
        # spelled once for the preview and once for the fetch, so the fetch is read for the
        # mark too. A route that fell back to JSON would serve the command as a JSON string.
        served = ran.get(f"/fragment/command/session/{session_id}/thread/{source}/tool/{tool_id}")
        assert served.status_code == 200
        assert plain(block(served.text, "value")) == COMMAND
        assert '<span class="nb">cd</span>' in block(served.text, "value")
        assert values(served.text, "data-detail") == ["command"]
        # And the input is still on the page as the record: the command is a reading of it.
        assert json.loads(plain(block(page, "input")))["command"] == COMMAND
        # A call to a tool that runs no command has none to show, though its arguments carry
        # the word: the arm is the tool's name. A page that marked that argument up as shell
        # would be saying a `Read` ran it.
        read = ran.get(f"/session/{read_session}/thread/{read_source}/tool/{read_id}")
        assert read.status_code == 200
        assert "command" not in values(read.text, "data-detail")
        # The argument is still on the page inside the input it was passed in — as the record,
        # not as a shell. And the route the pane would have linked to has no such value to
        # serve: the row is there and the column under it is null, which is not a value of
        # nothing but the absence of one. A 200 would make the pane's missing link a bug
        # rather than the only honest thing the page can do.
        assert NOT_RUN in plain(block(read.text, "input"))
        missing = ran.get(
            f"/fragment/command/session/{read_session}/thread/{read_source}/tool/{read_id}"
        )
        assert missing.status_code == 404


def test_a_read_of_a_markdown_file_shows_the_source_marked_up_and_not_rendered(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A `Read` result is evidence, so a markdown file is marked up rather than rendered.

    Rendering would turn the `#` the file holds into a heading and lose the characters the
    agent was actually shown. What the file was is read off the path it was read from, which
    is the only thing in the record that says so.
    """
    session_id, source, tool_id = call_to(store, "Bash")
    path = plant(
        (
            "UPDATE tool_calls SET name = 'Read', input = ?, result = ?"
            " WHERE session_id = ? AND source = ? AND id = ?",
            [json.dumps({"file_path": "/tmp/notes.md"}), READ, session_id, source, tool_id],
        )
    )
    with TestClient(build_app(path)) as read:
        page = read.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
        marked = block(page, "result")
        # The source, whole, with the heading marked as a heading rather than made one...
        assert plain(marked) == READ
        assert '<span class="gh"># Title</span>' in marked
        assert "<h1>" not in page
        # ...and the line numbers Claude Code prefixes each line with kept out of the lexer's
        # way, because a gutter is not part of the file.
        assert '<span class="lineno">1\t</span>' in marked
        # The whole fetch reads the same way, off the same file name.
        served = read.get(f"/fragment/result/session/{session_id}/thread/{source}/tool/{tool_id}")
        assert '<span class="gh"># Title</span>' in block(served.text, "value")
    # A file this viewer has no lexer for is shown as stored, which is the arm every result
    # took before: nothing claims to know what a `.bin` holds.
    other = plant(
        (
            "UPDATE tool_calls SET name = 'Read', input = ?, result = ?"
            " WHERE session_id = ? AND source = ? AND id = ?",
            [json.dumps({"file_path": "/tmp/notes.bin"}), READ, session_id, source, tool_id],
        )
    )
    with TestClient(build_app(other)) as binary:
        page = binary.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
        assert plain(block(page, "result")) == READ
        assert "<span" not in block(page, "result")
    # And a tool that names a file without returning one is shown as stored too. `Edit` and
    # `Write` name a file whose suffix this viewer has a lexer for in 30,491 recorded calls
    # (the canonical store, read 2026-08-20), and what they return is a sentence about the
    # file — a page that marked it up as python would be claiming it is the file.
    edited = plant(
        (
            "UPDATE tool_calls SET name = 'Edit', input = ?, result = ?"
            " WHERE session_id = ? AND source = ? AND id = ?",
            [json.dumps({"file_path": "/tmp/notes.py"}), EDITED, session_id, source, tool_id],
        )
    )
    with TestClient(build_app(edited)) as edit:
        page = edit.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
        assert plain(block(page, "result")) == EDITED
        assert "<span" not in block(page, "result")
        # The rule is spelled once for the preview and once for the whole fetch, so both are
        # read here: the second query answers off the same file name as the first.
        served = edit.get(f"/fragment/result/session/{session_id}/thread/{source}/tool/{tool_id}")
        assert "<span" not in block(served.text, "value")


def test_every_value_a_pane_previews_is_fetchable_whole_from_its_own_url(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The five fat columns a node page previews each round-trip through a value route.

    One route per column rather than one per row: a tool call's input and its result are two
    values a reader opens apart, and a route that served the row whole would send the other
    one every time. Each is checked against the length the store holds, which is what proves
    the fetch is untruncated rather than merely longer than the preview.
    """
    columns = {
        # The node URL that previews it, the value route, and where the store keeps it.
        "command_args": (
            f"/session/{SPINE}/thread/{MAIN}/turn/{{0}}",
            f"/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{{0}}",
            "SELECT id, length(command_args) FROM live_turns WHERE session_id = ? AND source = ?"
            " AND command_name IS NOT NULL AND length(command_args) > 0"
            " ORDER BY length(command_args) DESC LIMIT 1",
        ),
        "prompt": (
            f"/session/{SPINE}/thread/{MAIN}/turn/{{0}}",
            f"/fragment/prompt/session/{SPINE}/thread/{MAIN}/turn/{{0}}",
            # Of a turn that was typed rather than run: a slash turn's prompt is the
            # `<command-…>` wrapper, which the pane shows as the two values inside it instead.
            "SELECT id, length(prompt) FROM live_turns WHERE session_id = ? AND source = ?"
            " AND command_name IS NULL AND length(prompt) > 0"
            " ORDER BY length(prompt) DESC LIMIT 1",
        ),
        "input": (
            f"/session/{SPINE}/thread/{MAIN}/tool/{{0}}",
            f"/fragment/input/session/{SPINE}/thread/{MAIN}/tool/{{0}}",
            "SELECT id, length(input) FROM live_tool_calls WHERE session_id = ? AND source = ?"
            " AND length(input) > 0 ORDER BY length(input) DESC LIMIT 1",
        ),
        "result": (
            f"/session/{SPINE}/thread/{MAIN}/tool/{{0}}",
            f"/fragment/result/session/{SPINE}/thread/{MAIN}/tool/{{0}}",
            "SELECT id, length(result) FROM live_tool_calls WHERE session_id = ? AND source = ?"
            " AND length(result) > 0 ORDER BY length(result) DESC LIMIT 1",
        ),
        "text": (
            f"/session/{SPINE}/thread/{MAIN}/call/{{0}}",
            f"/fragment/text/session/{SPINE}/thread/{MAIN}/call/{{0}}",
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
        # The fetch replaces the section the preview sat in, so it comes back filed under the
        # same name: what a value is styled as — the rail that tells an ask from an answer —
        # hangs off that name, and a fragment that dropped it would open unstyled.
        assert values(served.text, "data-detail") == [name], name
    # And a run's brief, which is the one fat column that hangs off the session rather than a
    # thread, so its route takes no source.
    session_id, run_id, held = one(
        store,
        "SELECT session_id, id, length(description) FROM live_agent_runs"
        " WHERE length(description) > 0 ORDER BY length(description) DESC LIMIT 1",
    )
    page = client.get(f"/session/{session_id}/run/{run_id}").text
    assert fields(page, "data-detail", "description")["description"]
    served = client.get(f"/fragment/brief/session/{session_id}/run/{run_id}")
    assert values(served.text, "data-value") == [str(held)]
    assert values(served.text, "data-detail") == ["description"]
    # The brief is what a run was asked to do, so it is labelled as a brief and not as a
    # description of the run — the word the enrichment pass owns.
    assert LABELS["description"] == "Task brief"


# The widest parent the store holds for each shape a children log takes, and the URL of the page
# that logs it. Every shape is here because the log is assembled per shape — a shape missing from
# the sweep is a shape whose page size and whose count above it nothing reads. Widest because a
# page has to be shorter than its level for either to be legible: against a level of one, a page
# that served an extra row and a heading that counted the page would both look right.
LEVELS: dict[str, tuple[str, str, str]] = {
    "session": (
        "SELECT session_id FROM live_turns WHERE source = 'main' GROUP BY 1"
        " ORDER BY count(*) DESC, 1 LIMIT 1",
        "/session/{0}",
        "turns",
    ),
    "run": (
        "SELECT a.session_id, a.id FROM live_agent_runs a"
        " JOIN live_turns t ON t.session_id = a.session_id AND t.source = a.id"
        " GROUP BY 1, 2 ORDER BY count(*) DESC, 1, 2 LIMIT 1",
        "/session/{0}/run/{1}",
        "turns",
    ),
    "turn": (
        "SELECT session_id, source, turn_id FROM live_api_calls WHERE turn_id IS NOT NULL"
        " GROUP BY 1, 2, 3 ORDER BY count(*) DESC, 1, 2, 3 LIMIT 1",
        "/session/{0}/thread/{1}/turn/{2}",
        "calls",
    ),
    "call": (
        "SELECT session_id, source, api_call_id FROM live_tool_calls"
        " GROUP BY 1, 2, 3 ORDER BY count(*) DESC, 1, 2, 3 LIMIT 1",
        "/session/{0}/thread/{1}/call/{2}",
        "tools",
    ),
    # The two buckets, which page the same way: one out of a query, one out of a list the page
    # already holds.
    "unattributed": (
        "SELECT c.session_id, c.source FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE t.id IS NULL GROUP BY 1, 2 ORDER BY count(*) DESC, 1, 2 LIMIT 1",
        "/session/{0}/thread/{1}/unattributed",
        "calls",
    ),
    "unattached": (
        "SELECT a.session_id FROM live_agent_runs a"
        " LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id"
        "  AND tc.id = a.tool_use_id AND tc.source <> a.id"
        " LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "  AND c.id = tc.api_call_id"
        " WHERE c.id IS NULL GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1",
        "/session/{0}/unattached",
        "runs",
    ),
}


@pytest.mark.parametrize("parent", list(LEVELS))
def test_every_shape_of_log_serves_the_page_asked_for_and_counts_its_level(
    client: TestClient, store: duckdb.DuckDBPyConnection, parent: str
) -> None:
    """A page holds what the URL asked for, and the heading above it counts the level.

    Swept per shape at `?log=1`: the corpus's widest level is five children against a page of a
    hundred, so at the production size every page is its whole level and both clauses read true
    however the code got there. One row a page is what tells a page from the level it came from.
    """
    sql, template, shape = LEVELS[parent]
    url = template.format(*one(store, sql))
    children = values(client.get(url).text, "data-child")
    assert len(children) > 1, f"{url}: the widest {parent} has to hold a level worth paging"
    for number, child in enumerate(children, start=1):
        page = client.get(url, params={"log": 1, "page": number}).text
        # The page is the one row the URL asked for, in the level's own order...
        assert values(page, "data-child") == [child], f"{url} page {number}"
        # ...under a heading counting the level rather than the row beneath it...
        assert fields(page, "data-log", shape)["children"] == str(len(children)), url
        # ...and a pager placing the page in the level.
        place = fields(page, "data-pager", shape)["place"]
        assert place == f"Page {number} of {len(children)}", url


def walked_log(client: TestClient, at: str, held: int) -> list[str]:
    """Every child a log lists, gathered by following its pager from the page given.

    Bounded by the level's own size: a pager that offered a way on from its last page would
    otherwise walk for as long as the store answers.
    """
    found: list[str] = []
    following: str | None = at
    for _ in range(held + 1):
        if following is None:
            return found
        page = client.get(following).text
        found += values(page, "data-child")
        onward = inside(page, "data-page", "next", "href")
        following = onward[0] if onward else None
    raise AssertionError(f"{at}: the pager never reached a last page")


def test_a_children_log_pages_by_number_and_counts_the_whole_level(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The log is one numbered page of a level, and the heading counts the level.

    Driven below the corpus's fan-out with `?log=`, because no recorded turn has more children
    than the production page. What is read is that the pages concatenate to the level exactly
    once, that each says which of how many it is, and that the count above them is the level's
    own — a heading counting the rows in front of the reader says a turn of four calls has one.
    """
    whole = client.get(TURN).text
    children = values(whole, "data-child")
    assert len(children) > 2, "the log has to have something to page"
    # One child to a page: the first page holds the first child...
    first = client.get(TURN, params={"log": 1}).text
    assert values(first, "data-child") == children[:1]
    # ...under a heading counting the whole level rather than the row beneath it...
    assert fields(first, "data-log", "calls")["children"] == str(len(children))
    # ...and a pager saying which page of how many this is.
    assert fields(first, "data-pager", "calls")["place"] == f"Page 1 of {len(children)}"
    # The first page offers no way back, and its way on is numbered rather than a cursor.
    assert not inside(first, "data-page", "previous", "href")
    (onward,) = inside(first, "data-page", "next", "href")
    assert "page=2" in onward and "after=" not in onward
    second = client.get(onward).text
    assert values(second, "data-child") == children[1:2]
    assert fields(second, "data-pager", "calls")["place"] == f"Page 2 of {len(children)}"
    # The way back from the second page lands on the first, which is the page with no number.
    (back,) = inside(second, "data-page", "previous", "href")
    assert back == f"{TURN}?log=1"
    assert values(client.get(back).text, "data-child") == children[:1]
    # Walking forward lands on every child exactly once, in the level's own order.
    assert walked_log(client, f"{TURN}?log=1", len(children)) == children


def test_a_level_divides_into_the_pages_it_has_and_no_empty_one(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The page count is the level's own arithmetic, at any size a URL asks for.

    Read at three sizes against one recorded level: one that divides it, one that leaves a
    remainder, and one that holds the whole thing. The arithmetic is where a paginator goes
    wrong, and the failure is quiet — an off-by-one mints a last page with nothing on it.
    """
    children = values(client.get(TURN).text, "data-child")
    held = len(children)
    for size, count in ((1, held), (held - 1, 2), (held, 1)):
        for number in range(1, count + 1):
            page = client.get(TURN, params={"log": size, "page": number}).text
            assert values(page, "data-child") == children[(number - 1) * size : number * size]
            # Every page of the level says the same total, and its own place in it...
            assert fields(page, "data-log", "calls")["children"] == str(held)
            if count > 1:
                assert fields(page, "data-pager", "calls")["place"] == f"Page {number} of {count}"
        # ...and one page past the last is nothing at all, rather than an empty log that reads
        # as a node with no children.
        assert client.get(TURN, params={"log": size, "page": count + 1}).status_code == 404
    # A level that fits on one page carries no pager: there is no page to go to.
    assert "data-pager" not in client.get(TURN, params={"log": held}).text
    # And a page number below the first is a bad ask rather than a miss: no level has one, so
    # it is the number that is wrong and not the node — the answer every other size a URL
    # carries gives (`checked`).
    assert client.get(TURN, params={"page": 0}).status_code == 400
    # A level with nothing in it counts nothing. The count comes off the page's own rows, so an
    # empty page is the one place it has no row to read it from.
    empty = store.execute(
        "SELECT c.session_id, c.source, c.id FROM live_api_calls c"
        " LEFT JOIN live_tool_calls t"
        " ON t.session_id = c.session_id AND t.api_call_id = c.id"
        " GROUP BY ALL HAVING count(t.id) = 0 ORDER BY 1, 2, 3 LIMIT 1"
    ).fetchone()
    assert empty, "the corpus has to hold an api call that called no tool"
    session_id, source, call_id = empty
    childless = client.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
    assert fields(childless, "data-log", "tools")["children"] == "0"
    assert "data-pager" not in childless


def test_the_bucket_that_pages_in_memory_walks_the_same_way_the_query_does(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The unattached bucket's log pages by slicing, and owes what the queried log owes.

    Its runs arrive with the session's, which every level of the tree needs anyway, so this one
    level cuts a list it already holds instead of asking the store for a page. Read on the one
    recorded bucket that holds more than one run: the pages have to concatenate to the level,
    the heading has to count the level rather than the page, and the last page has to be last.
    """
    sessions = [str(row[0]) for row in store.execute("SELECT id FROM sessions").fetchall()]
    bucketed = [
        (f"/session/{session_id}/unattached", page.text)
        for session_id in sessions
        if (page := client.get(f"/session/{session_id}/unattached")).status_code == 200
        and len(values(page.text, "data-child")) > 1
    ]
    assert bucketed, "the corpus has a bucket holding more than one unattached run"
    at, whole = bucketed[0]
    children = values(whole, "data-child")
    first = client.get(at, params={"log": 1}).text
    assert values(first, "data-child") == children[:1]
    assert fields(first, "data-log", "runs")["children"] == str(len(children))
    assert fields(first, "data-pager", "runs")["place"] == f"Page 1 of {len(children)}"
    # Walking to the end lands on every run exactly once, in the level's own order...
    assert walked_log(client, f"{at}?log=1", len(children)) == children
    # ...and the whole level on one page ends the walk there.
    assert "data-pager" not in client.get(at, params={"log": len(children)}).text
    assert client.get(at, params={"log": len(children), "page": 2}).status_code == 404


def test_the_page_the_log_opens_at_is_the_url_with_no_page_on_it(client: TestClient) -> None:
    """`?page=1` serves the same page the URL without it serves.

    The two have to agree or a reader who pages back to the start gets a different document
    from the one they were linked, and the payload sweep prices only one of them.
    """
    bare = client.get(TURN)
    opened = client.get(TURN, params={"page": 1})
    assert opened.status_code == bare.status_code == 200
    assert values(opened.text, "data-child") == values(bare.text, "data-child")
    # Which is what the helper every pager link is minted through says: the first page is the
    # node's own URL, and a later one hangs off whatever knobs the reader is carrying. A `&`
    # where a `?` belongs is a 404, so both arms are read.
    assert numbered(TURN, "", 1) == TURN
    assert numbered(TURN, "?log=1", 1) == f"{TURN}?log=1"
    assert numbered(TURN, "", 3) == f"{TURN}?page=3"
    assert numbered(TURN, "?log=1", 3) == f"{TURN}?log=1&page=3"


def headings(html: str) -> dict[str, str]:
    """What each column of a children log heads itself with, keyed by the column it heads.

    Whitespace collapsed the way a browser collapses it, so the heading a reader sees is what
    the assertion reads and the template stays free to break a long line.
    """
    return {
        column: " ".join(plain(inner).split())
        for column, inner in re.findall(
            r'<th [^>]*data-column="([^"]*)"[^>]*>(.*?)</th>', html, flags=re.S
        )
    }


@pytest.mark.parametrize("parent", list(LEVELS))
def test_every_children_log_heads_the_columns_its_rows_fill(
    client: TestClient, store: duckdb.DuckDBPyConnection, parent: str
) -> None:
    """The log is a table: one head naming the columns, and every row filling all of them.

    The reason it is a table at all — a row of bare numbers is unreadable, and a reader who
    cannot tell an api-call count from a tool-call count from a time of day is reading nothing.
    So the contract is that head and row agree, column for column, in order: a cell rendered
    under some other column's heading is a number attributed to the wrong question.

    Swept per shape, because the columns are the shape's own — a turn's children are counted
    by what a call did, a call's by what a tool answered.
    """
    sql, template, shape = LEVELS[parent]
    url = template.format(*one(store, sql))
    page = client.get(url).text
    named = [column.field for column in COLUMNS[Shape(shape)]]
    # The head names the shape's columns, in the order the shape declares them...
    assert inside(page, "data-columns", shape, "data-column") == named, url
    # ...each heading an icon over a word from the registry every header on the page reads...
    headed = headings(page)
    # ...each a column heading a screen reader can attribute a cell to...
    assert inside(page, "data-columns", shape, "scope") == ["col"] * len(named), url
    assert headed == {
        column.field: f"{column.icon} {label(column.field)}" for column in COLUMNS[Shape(shape)]
    }, url
    # ...and every row fills every one of them, so no cell sits under a heading not its own.
    children = values(page, "data-child")
    assert children, url
    for key in children:
        assert inside(page, "data-child", key, "data-column") == named, (url, key)
    # And what a row opens spans exactly those columns. `nodes.LISTED` says which shape of log
    # a kind lists in, and the expansion's span is read off it — a kind mapped to the wrong
    # shape opens a row narrower or wider than the table it lands in. Checked here, against
    # the page that did the listing, because this is where the shape is known to be right.
    (mount,) = [
        at for at in inside(page, "data-child", children[0], "hx-get") if at.startswith(BODY_URL)
    ]
    body = client.get(mount)
    assert body.status_code == 200, mount
    assert values(body.text, "colspan") == [str(len(named))], mount


def test_a_log_row_opens_the_body_from_a_button_that_says_so(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The expansion is a labelled control, and what it opens stands in the log's own table.

    A `<details>` summary said `body` and looked like text; a reader has to be able to see
    that a row can be opened. And what arrives is a row of the same table — the fragment is
    swapped in after the row that asked for it, so a body wrapped in anything but a `<tr>`
    lands outside the table the browser is drawing.
    """
    url = LEVELS["call"][1].format(*one(store, LEVELS["call"][0]))
    page = client.get(url).text
    for key in values(page, "data-child"):
        # The control names the row it opens, and it is a button rather than a disclosure.
        assert inside(page, "data-child", key, "data-view") == [key], key
        (mount,) = [
            at for at in inside(page, "data-child", key, "hx-get") if at.startswith(BODY_URL)
        ]
        served = client.get(mount)
        assert served.status_code == 200, mount
        # The body arrives as one row spanning the table it opens under.
        assert served.text.lstrip().startswith("<tr"), mount
        (span,) = inside(served.text, "data-expansion", "tool", "colspan")
        assert span == str(len(COLUMNS[Shape.TOOLS])), mount
    # And the disclosure the button replaced is gone from the log. Scoped to the log because
    # the page footer keeps one for the queries it ran, which no reader has to find to read
    # a row.
    (log,) = re.findall(r'<section class="log".*?</section>', page, flags=re.S)
    assert "<details" not in log


def test_a_tool_row_says_what_the_tool_was_asked(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A tool row leads with the input that identifies the call, not with its size.

    What identifies one differs by tool, so the row reads the field rather than the name: a
    file tool is its path, and a path inside the session's own project reads relative to it —
    the repository is the frame the reader is holding, and an absolute path spends the width
    of the column saying where the machine keeps it. A command is its description, with the
    command itself under it, because a reader scanning a call's tools wants what was intended
    before what was typed. Anything else is the head of the input as stored.

    Planted: the fixture corpus is redacted, so no recorded tool call carries a path, a
    description or a command — only the shape around them survives redaction.
    """
    session_id, source, call_id, held = one(
        store,
        "SELECT session_id, source, api_call_id, count(*) FROM live_tool_calls"
        " GROUP BY 1, 2, 3 ORDER BY 4 DESC, 1, 2, 3 LIMIT 1",
    )
    assert held >= 4, "the plant needs an api call with four tool calls to dress"
    tools = [
        row[0]
        for row in store.execute(
            "SELECT id FROM live_tool_calls WHERE session_id = ? AND source = ? AND api_call_id = ?"
            ' ORDER BY "index" LIMIT 4',
            [session_id, source, call_id],
        ).fetchall()
    ]
    project = "/Users/planted/repos/aiobserve"
    asked = {
        # A file the session's own project holds, and one it does not.
        tools[0]: ("Read", f'{{"file_path": "{project}/src/aiobserve/view/app.py"}}'),
        tools[1]: ("Read", '{"file_path": "/etc/hosts"}'),
        # A command, which carries both what it was for and what it ran.
        tools[2]: ("Bash", '{"command": "git status --short", "description": "Read the tree"}'),
        # And a tool the rule knows no field of, which falls back to the input as stored.
        tools[3]: ("TodoWrite", '{"todos": [{"content": "write the test"}]}'),
    }
    path = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [project, session_id]),
        *(
            ("UPDATE tool_calls SET name = ?, input = ? WHERE id = ?", [name, input, tool_id])
            for tool_id, (name, input) in asked.items()
        ),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
    rows = {tool_id: fields(page, "data-child", f"tool:{tool_id}") for tool_id in tools}
    # The project's own file reads from the project root, and the one outside it in full.
    assert rows[tools[0]]["input_head"] == "src/aiobserve/view/app.py"
    assert rows[tools[1]]["input_head"] == "/etc/hosts"
    # The command reads as what it was for, with what it ran under it.
    assert rows[tools[2]]["input_head"] == "Read the tree"
    assert rows[tools[2]]["command"] == "git status --short"
    # And the tool with no field the rule knows shows the input as stored.
    assert rows[tools[3]]["input_head"] == '{"todos": [{"content": "write the test"}]}'
    assert "command" not in rows[tools[3]]
    # A directory whose name merely starts with the project's reads absolute: `aiobserve2` is
    # not inside `aiobserve`, and without the separator the guard carries it would relativise
    # to `/src/x.py` — a path that looks like it sits at the repository root. Real: 2,053 of
    # the 67,252 `file_path` rows in the recorded store share the project's prefix from
    # outside it.
    sibling = f"{project}2/src/x.py"
    guarded = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [project, session_id]),
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Read", f'{{"file_path": "{sibling}"}}', tools[0]],
        ),
        # A call that carries every field the rule reads. The path wins the head, and the
        # command stays off the row: the line under a head is what the head describes, and
        # this head describes a file.
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            [
                "Bash",
                json.dumps(
                    {
                        "file_path": f"{project}/notes.md",
                        "description": "Read the notes",
                        "command": "cat notes.md",
                    }
                ),
                tools[1],
            ],
        ),
        # And a command with nothing saying what it was for. The head falls back to the input
        # as stored, which already holds the command, so the row does not print it twice.
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Bash", '{"command": "ls"}', tools[2]],
        ),
    )
    with TestClient(build_app(guarded)) as planted:
        edges = planted.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
    beside = {tool_id: fields(edges, "data-child", f"tool:{tool_id}") for tool_id in tools}
    assert beside[tools[0]]["input_head"] == sibling
    assert beside[tools[1]]["input_head"] == "notes.md"
    assert "command" not in beside[tools[1]]
    assert beside[tools[2]]["input_head"] == '{"command": "ls"}'
    assert "command" not in beside[tools[2]]
    # A session whose project the store never recorded has no frame to read a path against,
    # so the path reads absolute rather than against nothing.
    homeless = plant(
        ("UPDATE sessions SET project_dir = NULL WHERE id = ?", [session_id]),
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Read", asked[tools[0]][1], tools[0]],
        ),
    )
    with TestClient(build_app(homeless)) as planted:
        loose = planted.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
    assert fields(loose, "data-child", f"tool:{tools[0]}")["input_head"] == (
        f"{project}/src/aiobserve/view/app.py"
    )
