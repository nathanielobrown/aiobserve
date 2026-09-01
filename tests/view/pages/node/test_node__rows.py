"""What one row of a children log says, cell by cell.

A row is the whole of a child a reader gets without opening it, so what each cell prints is the
subject here: what a tool was asked, what a call said and which tools it went on to call, and
how much of each the column it sits in will hold. The name itself — the one string every
surface printing this node has to agree on — is `test_node__titles.py`.
"""

import json
import re

import duckdb
from fastapi.testclient import TestClient

from hyphae.analyze import queries
from hyphae.view.app import build_app
from hyphae.view.text.format import ELLIPSIS
from tests.conftest import (
    MAIN,
    SEARCH_TOOL,
    SPINE,
)
from tests.view.conftest import (
    Planter,
    fields,
    one,
)
from tests.view.selections import (
    TURN,
)


def test_a_tool_row_says_what_the_tool_was_asked(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A tool call is titled by the input that identifies it, not by its size.

    What identifies one differs by tool, so the title reads the field rather than the name. A
    tool the viewer knows reads its own field under its own glyph
    (`view/text/tool_names.py:FORMATTERS`): a file tool is its path, and a path inside the session's
    own project reads relative to it —
    the repository is the frame the reader is holding, and an absolute path spends the width of
    the column saying where the machine keeps it. A command is what ran, with what it was for
    under it — unless the title says that already, and then nothing reads under it. A tool the
    registry does not name falls to the shape rule the store applies to any input at all: a
    `file_path`, else a `description`, else the head of the input as stored.

    Derived once and read by every surface that names the call — that is the leaf below the
    edge cases here, and it is what the title convention is for.

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
    project = "/Users/planted/repos/hyphae"
    asked = {
        # A file the session's own project holds, and one it does not.
        tools[0]: ("Read", f'{{"file_path": "{project}/src/hyphae/view/app.py"}}'),
        tools[1]: ("Read", '{"file_path": "/etc/hosts"}'),
        # A command, which carries both what it was for and what it ran.
        tools[2]: ("Bash", '{"command": "git status --short", "description": "Read the tree"}'),
        # And a tool the registry does not name, whose input carries none of the fields the
        # shape rule reads either — so it falls back to the input as stored, with the tool's
        # own name still leading the row.
        tools[3]: ("StructuredOutput", '{"schema": "Findings", "strict": true}'),
    }
    path = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [project, session_id]),
        *(
            ("UPDATE tool_calls SET name = ?, input = ? WHERE id = ?", [name, sent, tool_id])
            for tool_id, (name, sent) in asked.items()
        ),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
    rows = {tool_id: fields(page, "data-child", f"tool:{tool_id}") for tool_id in tools}
    # The project's own file reads from the project root, and the one outside it in full.
    assert rows[tools[0]]["title"] == "📖 src/hyphae/view/app.py"
    assert rows[tools[1]]["title"] == "📖 /etc/hosts"
    # The command reads as what ran, with what it was for under it.
    assert rows[tools[2]]["title"] == "⚡ git status --short"
    assert rows[tools[2]]["about"] == "Read the tree"
    # And the unnamed tool shows the input as stored, under no glyph: the registry has no
    # rule for it, so the row keeps the tool's name in the column beside the title.
    assert rows[tools[3]]["title"] == '{"schema": "Findings", "strict": true}'
    assert rows[tools[3]]["name"] == "StructuredOutput"
    assert "about" not in rows[tools[3]]
    # A directory whose name merely starts with the project's reads absolute: `hyphae2` is
    # not inside `hyphae`, and without the separator the guard carries it would relativise
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
        # A `Bash` call that also names a file. The tool's own rule wins over the shape rule
        # the store would have applied — a `Bash` call is what it ran, whatever else the input
        # carries — and what it was for reads underneath.
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
        # And a command with nothing saying what it was for, which heads the same way and
        # prints no second line rather than a dash under it.
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Bash", '{"command": "ls"}', tools[2]],
        ),
        # An `Agent` call, whose title is the type the run was spawned as and then the brief —
        # which is the same `description` a second line would print.
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            [
                "Agent",
                json.dumps({"subagent_type": "implementer", "description": "Close the audit nits"}),
                tools[3],
            ],
        ),
    )
    with TestClient(build_app(guarded)) as planted:
        edges = planted.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
    beside = {tool_id: fields(edges, "data-child", f"tool:{tool_id}") for tool_id in tools}
    assert beside[tools[0]]["title"] == f"📖 {sibling}"
    assert beside[tools[1]]["title"] == "⚡ cat notes.md"
    assert beside[tools[1]]["about"] == "Read the notes"
    assert beside[tools[2]]["title"] == "⚡ ls"
    assert "about" not in beside[tools[2]]
    # And the row whose title already says what the call was for prints nothing under it: the
    # brief is inside the title an `Agent` row heads with, so a second line would be the same
    # sentence twice on one row. The `Bash` rows above are the other side of the rule — there
    # the description says something the command does not, which is why the line exists at all.
    assert beside[tools[3]]["title"] == "👉 [implementer] Close the audit nits"
    assert "about" not in beside[tools[3]]
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
    assert fields(loose, "data-child", f"tool:{tools[0]}")["title"] == (
        f"📖 {project}/src/hyphae/view/app.py"
    )


def test_a_call_row_says_what_the_call_said_and_which_tools_it_called(
    client: TestClient, plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A turn's calls log carries each call's own words and the tools that call went on to make.

    A page of api calls used to be a page of model names and counts: every row said the same
    model, and the only way to learn what a call did was to open it. The row now carries the
    head of what the call itself said — its own text, not a description of it — and the titles
    of the tool calls it made, in the order it made them, under the count that says how many.
    The titles are the shared derivation the tools log reads, so a call's row and the log
    inside it name the same tool the same way.

    The call is picked from a turn whose other calls made tool calls too, and its two tools are
    dressed in reverse order of their index. A row that named the turn's tools rather than the
    call's, or named the call's in the order the store happens to hold them, prints a different
    string here.

    Planted: redaction leaves a recorded call's text trimmed and no recorded tool call with a
    path or a description in its input.
    """
    session_id, source, turn_id, call_id, held = one(
        store,
        "SELECT c.session_id, c.source, c.turn_id, c.id, count(*) FROM live_api_calls c"
        " JOIN live_tool_calls t"
        "   ON t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id"
        " JOIN live_turns u ON u.session_id = c.session_id AND u.source = c.source"
        "  AND u.id = c.turn_id"
        # A sibling call on the same turn that called tools of its own, so that a row naming
        # the turn's tools instead of the call's has something extra to name.
        " WHERE EXISTS (SELECT 1 FROM live_api_calls o JOIN live_tool_calls ot"
        "   ON ot.session_id = o.session_id AND ot.source = o.source AND ot.api_call_id = o.id"
        "  WHERE o.session_id = c.session_id AND o.source = c.source AND o.turn_id = c.turn_id"
        "   AND o.id <> c.id)"
        " GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC, 1, 2, 3, 4 LIMIT 1",
    )
    assert held == 2, "the plant names both of the call's tools, so it needs exactly two"
    tools = [
        row[0]
        for row in store.execute(
            "SELECT id FROM live_tool_calls WHERE session_id = ? AND source = ? AND api_call_id = ?"
            ' ORDER BY "index" LIMIT 2',
            [session_id, source, call_id],
        ).fetchall()
    ]
    project = "/Users/planted/repos/hyphae"
    said = "I will read the app and then check what the NavTree is standing on."
    dressed = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [project, session_id]),
        ("UPDATE api_calls SET text = ? WHERE id = ?", [said, call_id]),
        # Every tool the turn's *other* calls made, named so that a row reaching past its own
        # call would print the word.
        (
            "UPDATE tool_calls SET name = 'Bash', input = ?"
            " WHERE session_id = ? AND source = ? AND api_call_id <> ? AND api_call_id IN"
            " (SELECT id FROM api_calls WHERE session_id = ? AND source = ? AND turn_id = ?)",
            [
                json.dumps({"command": "git log", "description": "Another call asked"}),
                session_id,
                source,
                call_id,
                session_id,
                source,
                turn_id,
            ],
        ),
        # A file inside the session's own project, and a command that says what it was for:
        # the two derivations the tools log's own rows show.
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Read", f'{{"file_path": "{project}/src/hyphae/view/app.py"}}', tools[0]],
        ),
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Bash", '{"command": "git status", "description": "Read the tree"}', tools[1]],
        ),
        # And the first of them is moved to the end of the call's order, so that the order the
        # store holds the two rows in and the order the call made them in disagree. A row that
        # printed the tools in the order they came back names them the other way round.
        ('UPDATE tool_calls SET "index" = 90000 WHERE id = ?', [tools[0]]),
    )
    with TestClient(build_app(dressed)) as planted:
        page = planted.get(f"/session/{session_id}/thread/{source}/turn/{turn_id}").text
    row = fields(page, "data-child", f"call:{call_id}")
    # What the call said stands in the row beside the model that said it...
    assert row["text"] == said
    # ...and the tools it called are named, in the order it called them and no others: what
    # the re-indexed call asked for last comes last, under the count of them. Each is named by
    # its own tool's rule, glyph and all, so the words here and the words on the tool's own row
    # are one derivation (`view/text/tool_names.py`) — the `Bash` row says what ran rather than what
    # the caller said it was for.
    assert row["tool_titles"] == "⚡ git status, 📖 src/hyphae/view/app.py"
    assert row["tool_calls"] == str(held)

    # The same column over the recording rather than a plant, because a plant can only show
    # what this test dressed: `SPINE` holds one api call that asked for two different tools at
    # once, and each is named under its own tool's glyph rather than the first one's
    # (`tests/fixtures/spine/README.md`).
    recorded_turn, recorded_call = one(
        store,
        "SELECT c.turn_id, c.id FROM live_api_calls c JOIN live_tool_calls t"
        "  ON t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id"
        " WHERE t.id = ?",
        [SEARCH_TOOL],
    )
    served = client.get(f"/session/{SPINE}/thread/{MAIN}/turn/{recorded_turn}").text
    named = fields(served, "data-child", f"call:{recorded_call}")["tool_titles"]
    # The command it ran leads, because that is the order it asked in, and the search reads as
    # what was searched for — the field the registry names a `ToolSearch` call by.
    assert named.startswith("⚡ ls -la ")
    assert named.endswith(", 🧰 select:PushNotification")

    # Both are cut to the column's width and marked where they were cut, like every other
    # string a row of a hundred prints: a call that talked for a page and called forty tools
    # is a row, not a page of one.
    long_said = "s" * (queries.LOG_CHARS + 40)
    long_path = f"src/hyphae/{'v' * queries.LOG_CHARS}.sql"
    reach = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [project, session_id]),
        ("UPDATE api_calls SET text = ? WHERE id = ?", [long_said, call_id]),
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Read", json.dumps({"file_path": f"{project}/{long_path}"}), tools[0]],
        ),
    )
    with TestClient(build_app(reach)) as planted:
        wide = planted.get(f"/session/{session_id}/thread/{source}/turn/{turn_id}").text
    cut = fields(wide, "data-child", f"call:{call_id}")
    assert cut["text"] == long_said[: queries.LOG_CHARS] + ELLIPSIS
    assert cut["tool_titles"] == f"📖 {long_path}"[: queries.LOG_CHARS] + ELLIPSIS

    # A call that answered with tool calls and no text prints nothing rather than the dash a
    # missing value takes: `api_calls.text` is NOT NULL, so a call that said nothing holds the
    # empty string, and the column beside it already names what answered.
    silent = plant(("UPDATE api_calls SET text = '' WHERE id = ?", [call_id]))
    with TestClient(build_app(silent)) as planted:
        quiet = planted.get(f"/session/{session_id}/thread/{source}/turn/{turn_id}").text
    assert fields(quiet, "data-child", f"call:{call_id}")["text"] == ""


def test_the_two_prose_columns_of_a_calls_log_are_bounded_by_the_stylesheet(
    client: TestClient,
) -> None:
    """What a call said and which tools it called are held to their columns by CSS alone.

    Both columns carry model prose in a table a browser sizes by its content, and neither the
    query nor the template can bound what that does to a row: the cut those two values arrive
    under is 300 characters, which is four lines of a wide column and a row as tall as a
    paragraph. So the shape is the stylesheet's, and nothing renders CSS — a rule dropped here
    is a page that still serves, still passes, and reads like a wall.

    The floor under the words is the half of this a fixture cannot show. Most api calls say
    nothing at all, so a column sized by its content collapses to the width of the few rows
    that filled it, and the two lines it is meant to show arrive one word wide. Found in a
    browser; pinned here.
    """
    # A turn page whose calls log has both columns, read the way a browser reads them: by the
    # class the cell carries.
    page = client.get(TURN).text
    assert 'class="said"' in page and 'class="called"' in page
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.DOTALL)
    rules = [
        (selector.strip(), body)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style)
        if "td.said" in selector or "td.called" in selector
    ]

    def declared(cell: str) -> dict[str, str]:
        """Every property the sheet sets on one of the two columns, or on the span inside it."""
        return {
            name.strip(): value.strip()
            for selector, body in rules
            if f"td.{cell}" in selector
            for name, _, value in (part.partition(":") for part in body.split(";"))
            if name.strip()
        }

    said, called = declared("said"), declared("called")
    # Both are capped, so a column of prose cannot push the numbers a reader counts by off
    # the side of the pane, and both are dim, so the row still scans as a row.
    assert said["max-width"] == called["max-width"] == "26rem"
    assert said["color"] == called["color"] == "var(--dim)"
    # The words wrap and stop at two lines, however long the 300 characters run...
    assert said["display"] == "-webkit-box" and said["overflow"] == "hidden"
    assert said["-webkit-line-clamp"] == said["line-clamp"] == "2"
    # ...they never collapse to the width of the calls that said nothing...
    assert said["min-width"] == "16rem"
    # ...and the list of tool titles is one line, cut with an ellipsis rather than wrapped.
    assert called["white-space"] == "nowrap"
    assert called["text-overflow"] == "ellipsis" and called["overflow"] == "hidden"
