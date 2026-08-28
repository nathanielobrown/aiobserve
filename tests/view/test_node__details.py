"""What a pane previews of a fat value, and what the whole of it costs to open.

A pane shows the head of one or two fat values with the way to the rest of each: `?detail=`
widens the preview and a fragment URL serves the value whole. These leaves read both halves,
and the marking each kind of value arrives with — the source a record named, or the Markdown a
person or a model wrote, shown as what it was written in rather than rendered.
"""

import json

import duckdb
from fastapi.testclient import TestClient

from hyphae.view import bounds
from hyphae.view.app import build_app
from hyphae.view.format import ELLIPSIS
from hyphae.view.labels import LABELS
from tests.conftest import ANCESTOR, DENSE_TURN, MAIN, SLASH_TURN, SPINE
from tests.view.conftest import (
    Planter,
    block,
    classed,
    fields,
    inside,
    one,
    plain,
    prose,
    values,
    walled,
)
from tests.view.selections import (
    TURN,
)


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
    # What a preview is marked up as is a property of the value and not of the mount it lands
    # in. A tool call's arguments are JSON — all 49 the fixture corpus records parse — so the
    # pane marks them up as JSON, which is what the fetch under the preview already did: a
    # head printed flat beside a rest highlighted is the divergence this one rule closes.
    source, tool_id = one(
        store,
        "SELECT source, id FROM live_tool_calls WHERE session_id = ? AND json_valid(input)"
        ' ORDER BY source, "index" LIMIT 1',
        [SPINE],
    )
    arguments = client.get(f"/session/{SPINE}/thread/{source}/tool/{tool_id}").text
    assert walled(arguments, "input") == "code json"
    assert classed(block(arguments, "input"))


# A prompt in the markdown a person or an agent writes one in: a heading, a list, a link and
# a fenced block. Planted rather than recorded — redaction flattened every fixture prompt to a
# line of its own — and real in the shape that matters here: it is how the briefs in `plans/`
# are written.
MARKDOWN_PROMPT = """# The task

Read `docs/viewer.md`, then:

- price it
- land it

```py
budget = 1
```
"""


def test_a_pane_reads_what_a_person_or_a_model_wrote_as_the_markdown_it_was_written_in(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A pane renders prose as markdown, the same way the fetch that replaces it already did.

    The preview and the whole value are one value shown twice — the fetch swaps into the block
    the preview sat in — so a pane that printed the characters and a fetch that rendered them
    told a reader the head and the rest were written in different things.

    Every value a pane previews that prose was written into is swept, because the choice is a
    flag per value: what a turn was asked and what followed its slash command, and what an api
    call said and what it thought. A run's two are swept where the run's own leaf plants them.
    The rest of what a pane previews is not prose — a tool call's arguments, its result and the
    command it ran are marked up in the syntax the record names, and `test_a_read_of_a_markdown
    _file_shows_the_source_marked_up_and_not_rendered` is what keeps them there.
    """
    call_id = one(
        store,
        "SELECT id FROM live_api_calls WHERE session_id = ? AND source = ? AND turn_id = ?"
        ' ORDER BY "index" LIMIT 1',
        [ANCESTOR, MAIN, DENSE_TURN],
    )[0]
    call = f"/session/{ANCESTOR}/thread/{MAIN}/call/{call_id}"
    slash = f"/session/{SPINE}/thread/{MAIN}/turn/{SLASH_TURN}"
    path = plant(
        (
            "UPDATE turns SET prompt = ? WHERE session_id = ? AND source = ? AND id = ?",
            [MARKDOWN_PROMPT, ANCESTOR, MAIN, DENSE_TURN],
        ),
        (
            "UPDATE turns SET command_args = ? WHERE session_id = ? AND source = ? AND id = ?",
            [MARKDOWN_PROMPT, SPINE, MAIN, SLASH_TURN],
        ),
        (
            "UPDATE api_calls SET text = ?, thinking = ? WHERE session_id = ? AND source = ?"
            " AND id = ?",
            [MARKDOWN_PROMPT, MARKDOWN_PROMPT, ANCESTOR, MAIN, call_id],
        ),
    )
    # Each value beside the page that previews it and the fetch that opens it whole.
    previewed = {
        "prompt": (TURN, f"/fragment/prompt/session/{ANCESTOR}/thread/{MAIN}/turn/{DENSE_TURN}"),
        "command_args": (slash, f"/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{SLASH_TURN}"),
        "text": (call, f"/fragment/text/session/{ANCESTOR}/thread/{MAIN}/call/{call_id}"),
        "thinking": (call, f"/fragment/thinking/session/{ANCESTOR}/thread/{MAIN}/call/{call_id}"),
    }
    with TestClient(build_app(path)) as written:
        for field, (page, fetch) in previewed.items():
            pane = prose(written.get(page).text, field)
            # The heading is a heading, the list is a list, and the fenced block is marked up in
            # the language a lexer read it as — the same lexers the viewer reads code with...
            assert "<h1>The task</h1>" in pane, field
            assert pane.count("<li>") == 2, field
            assert '<pre class="code python">' in pane, field
            # ...so none of the marks a reader wrote are left standing in the text.
            assert "#" not in plain(pane) and "```" not in plain(pane), field
            # And the value the fetch brings back is rendered the same, because it is the same
            # value: this prompt fits the pane's width, so the head is the whole of it.
            assert prose(written.get(fetch).text, field) == pane, field


# What a subagent sends back to the agent that spawned it: prose, written in markdown, with a
# heading and a list in it. Planted for the reason the prompt above is — redaction flattened
# every recorded report to `[redacted]` — and real in shape: it is the report this repository
# asks its own implementer runs for.
REPORT = """## Done

- landed the branch
- `mise run check` is green
"""


def test_a_run_page_reads_the_call_that_spawned_it_for_the_ask_and_the_answer(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A run's page says what it was asked and what it sent back, off the call that spawned it.

    Neither fact is on the run's own row. Claude Code records the ask in the spawning `Agent`
    call's `prompt` and what the parent received as that call's `result`, so the page reads
    both from there. The answer is deliberately what the parent got rather than the run's last
    turn: a run that stopped without reporting told its parent nothing, and a page that showed
    its last turn instead would put words in the parent's mouth.
    """
    session_id, run_id = one(
        store,
        "SELECT a.session_id, a.id FROM live_agent_runs a JOIN live_tool_calls t"
        " ON t.session_id = a.session_id AND t.id = a.tool_use_id AND t.source <> a.id"
        " WHERE json_extract_string(t.input, '$.prompt') IS NOT NULL AND t.result IS NOT NULL"
        " ORDER BY 1, 2 LIMIT 1",
    )
    path = plant(
        (
            "UPDATE tool_calls SET input = json_merge_patch(input, ?), result = ?"
            " WHERE session_id = ? AND id = (SELECT tool_use_id FROM agent_runs"
            "   WHERE session_id = ? AND id = ?)",
            [json.dumps({"prompt": MARKDOWN_PROMPT}), REPORT, session_id, session_id, run_id],
        )
    )
    run = f"/session/{session_id}/run/{run_id}"
    with TestClient(build_app(path)) as spawned:
        pane = spawned.get(run).text
        asked = spawned.get(f"/fragment/prompt{run}")
        answered = spawned.get(f"/fragment/result{run}")
    # Both are on the pane, rendered as the markdown they were written in rather than as the
    # JSON the ask was stored inside — and beside the brief, which is a third thing: the line
    # the spawning agent typed to name the run, not the instructions it gave.
    assert "<h1>The task</h1>" in prose(pane, "prompt")
    assert "<h2>Done</h2>" in prose(pane, "result")
    assert values(pane, "data-detail") == ["brief", "prompt", "result"]
    # And each has a route of its own that answers with the whole value, filed under the same
    # name the preview sat under, so the fetch swaps into its own block.
    assert values(asked.text, "data-detail") == ["prompt"]
    assert values(answered.text, "data-detail") == ["result"]
    assert prose(asked.text, "prompt") == prose(pane, "prompt")
    assert prose(answered.text, "result") == prose(pane, "result")


def test_a_run_nobody_asked_in_words_shows_no_ask_and_serves_none(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run whose spawning call carried no prompt has no ask to show, and its route 404s.

    Two ways a run reaches the store without one: spawned by a tool that takes something other
    than a prompt — a `Workflow` names a workflow — and recorded with no spawning call at all,
    which is what a resumed or forked transcript replays. Neither is an empty value: nothing on
    the pane links to the route, so a request for it is a URL somebody kept.
    """
    for named, sql in (
        (
            "spawned by a tool that takes no prompt",
            "SELECT a.session_id, a.id FROM live_agent_runs a JOIN live_tool_calls t"
            " ON t.session_id = a.session_id AND t.id = a.tool_use_id AND t.source <> a.id"
            " WHERE json_extract_string(t.input, '$.prompt') IS NULL ORDER BY 1, 2 LIMIT 1",
        ),
        (
            "recorded with no spawning call",
            "SELECT a.session_id, a.id FROM live_agent_runs a WHERE NOT EXISTS ("
            "  SELECT 1 FROM live_tool_calls t WHERE t.session_id = a.session_id"
            "   AND t.id = a.tool_use_id AND t.source <> a.id) ORDER BY 1, 2 LIMIT 1",
        ),
    ):
        session_id, run_id = one(store, sql)
        run = f"/session/{session_id}/run/{run_id}"
        pane = client.get(run).text
        assert "prompt" not in values(pane, "data-detail"), named
        assert client.get(f"/fragment/prompt{run}").status_code == 404, named


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
    the NavTree reaches — an id copied out of a transcript may name a record a later line
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
        # ...nowhere in the preview, and nowhere else on the page either: the pane heads itself
        # with an `<h1>` and this file's `#` must not have made a second one.
        assert "<h1>" not in marked
        assert page.count("<h1>") == 1
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


# What a tool answering in structured data returns. Planted: redaction flattened the result of
# all 49 tool calls the fixture corpus records to `[redacted]`, so no recorded row can take
# either arm below. Real in the shape that matters — an object with a list under a key is what
# an MCP tool and a `TodoWrite` both hand back.
JSON_RESULT = '{"ok": true, "rows": [1, 2, 3]}'


# And what a tool answering in words returns, with a brace in it so that the arm below is a
# page falling back rather than a page finding nothing to parse.
PLAIN_RESULT = "Found 3 matches in {src}/hyphae, none in the viewer."


def test_a_result_no_file_names_is_json_where_it_parses_and_the_stored_characters_where_it_does_not(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A tool's answer is read as JSON when it is JSON, and printed as stored when it is not.

    The third arm of one rule. A result whose file the record names keeps that file's syntax
    (the leaf above); everything else is tried as JSON, because that is what a tool that does
    not answer in prose answers in. The fallback is what makes trying safe: a value that does
    not parse comes back whole and unmarked rather than lexed as broken JSON, so nothing a
    reader opened the call for is lost to a guess about what it was.

    Planted on a recorded `Bash` call, which names no file: what a `Bash` call ran is beside
    the result on the same page, so the plant also holds the two apart.
    """
    session_id, source, tool_id = call_to(store, "Bash")
    at = f"/session/{session_id}/thread/{source}/tool/{tool_id}"
    fetch = f"/fragment/result/session/{session_id}/thread/{source}/tool/{tool_id}"
    structured = plant(
        (
            "UPDATE tool_calls SET result = ? WHERE session_id = ? AND source = ? AND id = ?",
            [JSON_RESULT, session_id, source, tool_id],
        )
    )
    with TestClient(build_app(structured)) as answered:
        page = answered.get(at).text
        # Marked up as the JSON it is, and indented for reading: the store holds one line and
        # a reader opening a result wants the shape of it.
        assert walled(page, "result") == "code json"
        assert json.loads(plain(block(page, "result"))) == json.loads(JSON_RESULT)
        assert "\n" in plain(block(page, "result"))
        # And the fetch that replaces the preview reads the same way, off the same rule.
        served = answered.get(fetch).text
        assert walled(served, "value") == "code json"
        assert classed(block(served, "value")) == classed(block(page, "result"))
    words = plant(
        (
            "UPDATE tool_calls SET result = ? WHERE session_id = ? AND source = ? AND id = ?",
            [PLAIN_RESULT, session_id, source, tool_id],
        )
    )
    with TestClient(build_app(words)) as said:
        page = said.get(at).text
        # No class, because nothing on the page claims to know what this is — and every
        # character of it, which is what proves the parse that failed swallowed nothing.
        assert walled(page, "result") == ""
        assert block(page, "result") == PLAIN_RESULT
        assert block(said.get(fetch).text, "value") == PLAIN_RESULT


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
        # And a value that is not prose comes back marked up the way the preview was. The
        # fragment files it under `value` and the pane under the column's own name, so the
        # two `<pre>` classes are what compare — one rule in `view/node_pages.py` decides
        # both, and this is the reading that would see them part again.
        if name in ("input", "result"):
            assert walled(served.text, "value") == walled(page, name), name
            assert classed(block(served.text, "value")) == classed(block(page, name)), name
    # And a run's brief, which is the one fat column that hangs off the session rather than a
    # thread, so its route takes no source.
    session_id, run_id, held = one(
        store,
        "SELECT session_id, id, length(brief) FROM live_agent_runs"
        " WHERE length(brief) > 0 ORDER BY length(brief) DESC LIMIT 1",
    )
    page = client.get(f"/session/{session_id}/run/{run_id}").text
    assert fields(page, "data-detail", "brief")["brief"]
    served = client.get(f"/fragment/brief/session/{session_id}/run/{run_id}")
    assert values(served.text, "data-value") == [str(held)]
    assert values(served.text, "data-detail") == ["brief"]
    # The brief is what a run was asked to do, so it is labelled as a brief and not as a
    # description of the run — the word the enrichment pass owns.
    assert (LABELS["brief"], LABELS["description"]) == ("Task brief", "Description")
