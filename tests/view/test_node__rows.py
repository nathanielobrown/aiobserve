"""What a row of a children log says, and the one name a node carries wherever it is printed.

A row is the whole of a child a reader gets without opening it, so what each cell prints is the
subject here: what a tool was asked, what a call said and which tools it went on to call, and
the title that has to read the same in a NavTree row, a crumb and a log cell however it is cut.
"""

import json
import re

import duckdb
from fastapi.testclient import TestClient

from hyphae.analyze import queries
from hyphae.view import formatters, nodes
from hyphae.view.app import build_app
from hyphae.view.format import ELLIPSIS, cut
from hyphae.view.nodes import LEAD_SEPARATOR
from tests.conftest import MYCELIA
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
    tool the viewer knows reads its own field under its own glyph (`view/formatters.py:FORMATTERS`):
    a file tool is its path, and a path inside the session's own project reads relative to it —
    the repository is the frame the reader is holding, and an absolute path spends the width of
    the column saying where the machine keeps it. A command is what ran, with what it was for
    under it. A tool the registry does not name falls to the shape rule the store applies to
    any input at all: a `file_path`, else a `description`, else the head of the input as stored.

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
    )
    with TestClient(build_app(guarded)) as planted:
        edges = planted.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
    beside = {tool_id: fields(edges, "data-child", f"tool:{tool_id}") for tool_id in tools}
    assert beside[tools[0]]["title"] == f"📖 {sibling}"
    assert beside[tools[1]]["title"] == "⚡ cat notes.md"
    assert beside[tools[1]]["about"] == "Read the notes"
    assert beside[tools[2]]["title"] == "⚡ ls"
    assert "about" not in beside[tools[2]]
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
    plant: Planter, store: duckdb.DuckDBPyConnection
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
    # the re-indexed call asked for last comes last, under the count of them.
    assert row["tool_titles"] == "Read the tree, src/hyphae/view/app.py"
    assert row["tool_calls"] == str(held)

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
    assert cut["tool_titles"] == long_path[: queries.LOG_CHARS] + ELLIPSIS

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


def test_one_tool_call_is_titled_the_same_way_wherever_it_is_named(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """The five surfaces that name a tool call agree, because one derivation names it.

    The pane's own heading, the NavTree row beside it, the crumb chain leading it, the row in
    its parent's children log, and the session's errors list. They read five different queries
    at four different widths, so the agreement is a fact about the derivation rather than about
    the page: before it was shared, three of these showed the input JSON as stored and the
    fourth showed the path.

    A tool the registry names leads with its glyph and no name, and the glyph rides in the
    node's words rather than its lead — which is what carries it into the children log, the one
    surface that drops a lead because it heads the tool's name in a column of its own.

    Planted for the reason the leaf above is: redaction left no recorded input with a path in
    it, and no failure whose input says what it was asked.
    """
    session_id, source, call_id = one(
        store,
        "SELECT session_id, source, api_call_id FROM live_tool_calls"
        " GROUP BY 1, 2, 3 ORDER BY count(*) DESC, 1, 2, 3 LIMIT 1",
    )
    tool_id = one(
        store,
        "SELECT id FROM live_tool_calls WHERE session_id = ? AND source = ? AND api_call_id = ?"
        ' ORDER BY "index" LIMIT 1',
        [session_id, source, call_id],
    )[0]
    project = "/Users/planted/repos/hyphae"
    # A failed read of a file inside the session's own project: one row every one of the five
    # surfaces has a reason to name — the errors list only lists what failed.
    path = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [project, session_id]),
        (
            "UPDATE tool_calls SET name = ?, input = ?, is_error = true WHERE id = ?",
            ["Read", f'{{"file_path": "{project}/src/hyphae/view/nodes.py"}}', tool_id],
        ),
    )
    with TestClient(build_app(path)) as planted:
        pane = planted.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
        parent = planted.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
        listed = planted.get(f"/session/{session_id}/errors").text
    # The title the derivation composes: the glyph that stands for the tool, then what it was
    # asked. Short enough that the narrowest of the five surfaces still prints it whole.
    titled = "📖 src/hyphae/view/nodes.py"
    assert len(titled) < queries.CRUMB_CHARS
    # Its own pane heads it, the NavTree row it stands on carries it, the crumb chain that
    # leads the pane ends on it, and the errors list — which reads a query of its own, over
    # every thread of the session — carries the same string.
    assert fields(pane, "data-body", "tool")["title"] == titled
    assert fields(pane, "data-nav-tree", f"tool:{tool_id}")["title"] == titled
    assert fields(pane, "data-crumb", f"tool:{tool_id}")["tool"] == titled
    assert fields(listed, "data-error", f"tool:{tool_id}")["title"] == titled
    # And so does the children log under the parent call, which prints the words alone in its
    # `Title` column. The glyph is not the tool's name, so nothing is said twice: `Read` stands
    # in the `Tool` column beside it.
    row = fields(parent, "data-child", f"tool:{tool_id}")
    assert row["title"] == titled
    assert row["name"] == "Read"

    # A path long enough that cutting the project directory off it matters. The five surfaces
    # have four widths between them, so the same call is shown four lengths — and each one
    # is the head of the *relative* path, marked where it stopped. A derivation that cut the
    # absolute path first would hand every surface the same short string, unmarked and one
    # project directory shorter than the width it was asked for.
    long_path = f"src/hyphae/{'v' * 380}.sql"
    reach = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [project, session_id]),
        (
            "UPDATE tool_calls SET name = ?, input = ?, is_error = true WHERE id = ?",
            ["Read", json.dumps({"file_path": f"{project}/{long_path}"}), tool_id],
        ),
    )
    with TestClient(build_app(reach)) as planted:
        pane = planted.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
        parent = planted.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
        listed = planted.get(f"/session/{session_id}/errors").text
    # The glyph is spent out of the width like any other character: it leads the words, so
    # every surface pays two characters for it and cuts the path two characters earlier.
    whole = f"📖 {long_path}"
    assert fields(pane, "data-body", "tool")["title"] == whole[: queries.HEADER_CHARS] + ELLIPSIS
    assert (
        fields(pane, "data-crumb", f"tool:{tool_id}")["tool"]
        == whole[: queries.CRUMB_CHARS] + ELLIPSIS
    )
    for shown, where in ((pane, "data-nav-tree"), (listed, "data-error")):
        assert (
            fields(shown, where, f"tool:{tool_id}")["title"]
            == whole[: queries.NAV_CHARS] + ELLIPSIS
        )
    assert (
        fields(parent, "data-child", f"tool:{tool_id}")["title"]
        == whole[: queries.LOG_CHARS] + ELLIPSIS
    )
    # And a path that fits every width reaches every surface whole, extension and all: the
    # pane has the least room of the four widths that cut a whole title and 30 characters of
    # project directory is what decides whether a reader sees the end of the name or a cut
    # that says nothing.
    fits = f"src/hyphae/{'v' * 72}.sql"
    assert len(f"📖 {fits}") < queries.HEADER_CHARS
    snug = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [project, session_id]),
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Read", json.dumps({"file_path": f"{project}/{fits}"}), tool_id],
        ),
    )
    with TestClient(build_app(snug)) as planted:
        pane = planted.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
    assert fields(pane, "data-body", "tool")["title"] == f"📖 {fits}"


# The tools the fixture corpus records under a name the registry knows, each with the glyph
# that leads its rows and the input field its title is read from. Restated from
# `plans/viewer-polish/design.md` rather than read off `view/formatters.py:FORMATTERS`, which is the
# thing under test. The eight names not here have no recorded call to serve, and the leaf below
# says so out loud.
RECORDED_FORMATTERS = {
    "Read": ("📖", "file_path"),
    "Bash": ("⚡", "command"),
    "Agent": ("👉", "subagent_type"),
    "SendMessage": ("📬", "to"),
}


def test_every_registered_tool_the_corpus_records_agrees_across_its_surfaces(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The leaf above pins the widths on a planted row; this one takes the corpus as it is.

    One recorded call of each name the registry knows and the fixtures hold, read on its own
    page: the NavTree row, the crumb ending the chain, the pane's heading and the row in the
    parent call's children log print one string, cut to each surface's own width. Nothing here
    restates what the string should be — `test_nav_tree__rows.py` checks that against the
    store's own columns. What is checked here is that four queries agree on a recorded row.

    Each row is chosen for a field redaction left intact, shortest first, so the title is a
    real one and short enough that the widest surface prints it whole. A fixture re-cut that
    redacts one of these fields again reds this leaf rather than passing on `[redacted]`.
    """
    for name, (glyph, field) in RECORDED_FORMATTERS.items():
        session_id, source, tool_id, call_id = one(
            store,
            "SELECT session_id, source, id, api_call_id FROM live_tool_calls"
            " WHERE name = ? AND json_extract_string(input, ?) NOT IN ('[redacted]', '')"
            " ORDER BY length(input), id LIMIT 1",
            [name, f"$.{field}"],
        )
        pane = client.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
        parent = client.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
        # The NavTree cuts widest, so its row is the whole title when it carries no ellipsis.
        whole = fields(pane, "data-nav-tree", f"tool:{tool_id}")["title"]
        assert whole.startswith(f"{glyph} ") and not whole.endswith(ELLIPSIS), (name, whole)
        assert fields(pane, "data-body", "tool")["title"] == cut(whole, queries.HEADER_CHARS)
        assert fields(pane, "data-crumb", f"tool:{tool_id}")["tool"] == cut(
            whole, queries.CRUMB_CHARS
        )
        assert fields(parent, "data-child", f"tool:{tool_id}")["title"] == cut(
            whole, queries.LOG_CHARS
        )
        # A path is the one field read against something outside the tool call: the session's
        # own project directory comes off the front, so the row spends its width on the part
        # that tells two files apart rather than on where the machine keeps the repository.
        if field == "file_path":
            (project,) = one(store, "SELECT project_dir FROM sessions WHERE id = ?", [session_id])
            (given,) = one(store, "SELECT input FROM live_tool_calls WHERE id = ?", [tool_id])
            assert project == MYCELIA and f"{project}/" in given
            assert whole == f"{glyph} {json.loads(given)[field][len(project) + 1 :]}"
    # Which of the registry's names this corpus records: the four above and no others. The
    # rest are proven by the unit table in `test_format.py` alone, over inputs no fixture
    # holds — so a fixture that gains a `Grep` call reds this line rather than going unread.
    recorded = {
        name for (name,) in store.execute("SELECT DISTINCT name FROM live_tool_calls").fetchall()
    }
    assert recorded & set(formatters.FORMATTERS) == set(RECORDED_FORMATTERS)


def test_a_message_to_a_run_is_titled_by_what_that_run_was_spawned_as(
    client: TestClient, plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """`SendMessage` is the one title that reads past the row it names.

    The tool call carries `to`, which holds either an agent run's id or a name the sender
    typed. An id says nothing to a reader, so a `to` the session holds a run for reads as the
    definition that run was spawned as — and the word the row prints is then one the record
    behind it does not contain, which is what pins the lookup here. An implementation that
    never looked past the tool call would print the id and satisfy every other leaf.

    Anything else prints as it was sent: a name the sender typed is already the useful word,
    and a stale id is better shown than guessed at. Planted, because the fixture corpus records
    `SendMessage` on one session only and every send there addresses a run of it.
    """
    session_id, source, tool_id, sent, agent_type = one(
        store,
        "SELECT t.session_id, t.source, t.id, t.input, a.agent_type FROM live_tool_calls t"
        " JOIN live_agent_runs a ON a.session_id = t.session_id"
        "  AND a.id = json_extract_string(t.input, '$.to')"
        " WHERE t.name = 'SendMessage' ORDER BY t.session_id, t.source, t.\"index\" LIMIT 1",
    )
    at = f"/session/{session_id}/thread/{source}/tool/{tool_id}"
    titled = fields(client.get(at).text, "data-body", "tool")["title"]
    # What the reader is given is the agent, not the id — and it is nowhere in the record the
    # row stands for. The summary beside it is redacted in the fixture, so only the address is
    # read here; the arm below sends one worth printing.
    assert titled.startswith(f"📬 to {agent_type}")
    assert json.loads(sent)["to"] in sent and agent_type not in sent
    # An address the session holds no run for is printed as recorded, with what was said.
    typed = plant(
        (
            "UPDATE tool_calls SET input = ? WHERE id = ?",
            [json.dumps({"to": "architect", "summary": "the ladder is restacked"}), tool_id],
        )
    )
    with TestClient(build_app(typed)) as planted:
        shown = fields(planted.get(at).text, "data-body", "tool")["title"]
    assert shown == "📬 to architect: the ladder is restacked"


def test_an_api_call_that_answered_with_tool_calls_is_named_by_what_it_called(
    client: TestClient, plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A call that said nothing is named by what it did instead, wherever the viewer names it.

    Its text is what a call is normally called, and a call that answered with tool calls and
    no words has none — so the row read as the model that answered, and a turn of them read as
    a column of one repeated string. The tools it called are the record's own answer to which
    call this was: the first one's title, and how many of each tool followed it.

    A call that *did* speak is named by its words instead, under 💭 — the one glyph on a thread
    that says a row is the model talking rather than the viewer describing what it did. Both
    halves are read here because the mark hangs off the words and not off the absence of tools:
    a call that spoke and then ran four tools is marked too.

    Read off the store rather than pinned, like every other selection here. What is pinned is
    the agreement: the pane's heading, the NavTree row beside it and the browser tab print one
    string, because one derivation composes it from two queries at two widths.
    """
    session_id, source, call_id, turn_id, model = one(
        store,
        "SELECT c.session_id, c.source, c.id, c.turn_id, c.model FROM live_api_calls c"
        " JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.api_call_id = c.id"
        " WHERE (c.text IS NULL OR c.text = '') AND c.turn_id IS NOT NULL"
        " GROUP BY 1, 2, 3, 4, 5 ORDER BY count(*) DESC, c.id LIMIT 1",
    )
    (names,) = one(
        store,
        'SELECT list(t.name ORDER BY t."index") FROM live_tool_calls t'
        " WHERE t.session_id = ? AND t.source = ? AND t.api_call_id = ?",
        [session_id, source, call_id],
    )
    page = client.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
    titled = fields(page, "data-body", "call")["title"]
    # The tool it called first leads, the way a run's agent type leads: which tool this was is
    # what a reader picks the call out of a tree by. After it, that call's own title.
    assert titled.startswith(f"{names[0]}{LEAD_SEPARATOR}"), titled
    # And after that, the tools it went on to call, counted once per tool.
    assert titled.endswith("".join(f" +1({name})" for name in dict.fromkeys(names[1:]))), titled
    # The three surfaces that name the node agree, at three widths and off two queries.
    assert fields(page, "data-nav-tree", f"call:{call_id}")["title"] == titled
    assert f"<title>⇄ {titled} ·" in page
    # The one documented exception stands: the children log under the turn names its api-call
    # rows by the model that answered, with what each said in a column of its own beside it.
    log = client.get(f"/session/{session_id}/thread/{source}/turn/{turn_id}").text
    assert fields(log, "data-child", f"call:{call_id}")["model"] == model

    # The other half of the rule, on a call the corpus records rather than a planted one: a
    # call whose answer was words carries the mark that says the row is the model speaking.
    # It is picked from the calls that *also* ran tools, which is the case a mark hung off
    # "this call did nothing else" would miss — and the silent call above carries no mark.
    assert nodes.SPEECH_MARK not in titled
    spoke, spoke_source, spoke_call, spoken = one(
        store,
        "SELECT c.session_id, c.source, c.id, c.text FROM live_api_calls c"
        " JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.api_call_id = c.id"
        " WHERE c.text IS NOT NULL AND c.text <> ''"
        " GROUP BY 1, 2, 3, 4 ORDER BY count(*) DESC, c.id LIMIT 1",
    )
    said = f"{nodes.SPEECH_MARK} {spoken}"
    page = client.get(f"/session/{spoke}/thread/{spoke_source}/call/{spoke_call}").text
    assert fields(page, "data-body", "call")["title"] == cut(said, queries.HEADER_CHARS)
    assert fields(page, "data-nav-tree", f"call:{spoke_call}")["title"] == cut(
        said, queries.NAV_CHARS
    )


def test_the_count_of_a_calls_tools_survives_every_width_the_title_is_cut_to(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """The count is budgeted out of the width first, so a long first title cannot push it off.

    Cut the other way round — title first, count into whatever is left — and the rows that
    most need the count are the rows that lose it: a call whose first tool call has plenty to
    say is a call that made several. What a reader would see is a title that stops, with no
    sign the call did anything after it.

    Planted on a recorded call that called `Bash` once and `Read` twice, by emptying the one
    column that decides which name the derivation falls through to — the store forbids a NULL
    there, and a call that answered with tools alone is recorded with an empty string.
    Redaction left the corpus no call that both said nothing and called one tool twice.
    """
    session_id, source, call_id, tool_id = one(
        store,
        'SELECT c.session_id, c.source, c.id, min_by(t.id, t."index") FROM live_api_calls c'
        " JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.api_call_id = c.id"
        " GROUP BY 1, 2, 3 HAVING count(*) > count(DISTINCT t.name) AND count(DISTINCT t.name) > 1"
        " ORDER BY count(*) DESC, c.id LIMIT 1",
    )
    url = f"/session/{session_id}/thread/{source}/call/{call_id}"
    silent = ("UPDATE api_calls SET text = '' WHERE id = ?", [call_id])
    with TestClient(build_app(plant(silent))) as planted:
        page = planted.get(url).text
    # Two `Read` calls after the `Bash` that leads, counted as one group rather than listed.
    assert fields(page, "data-body", "call")["title"].endswith(" +2(Read)")

    # The same call with a first tool call that fills a title on its own. Every width the
    # viewer cuts a title to is spent on the description less the count, so both ends survive:
    # what the call did first, marked where it was stopped, and how many followed.
    asked = "w" * (queries.NAV_CHARS * 2)
    described = (
        "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
        ["Bash", json.dumps({"description": asked, "command": "true"}), tool_id],
    )
    with TestClient(build_app(plant(silent, described))) as planted:
        page = planted.get(url).text
    tally = " +2(Read)"
    for where, chars in (("data-body", queries.HEADER_CHARS), ("data-nav-tree", queries.NAV_CHARS)):
        key = "call" if where == "data-body" else f"call:{call_id}"
        shown = fields(page, where, key)["title"]
        assert shown == f"Bash{LEAD_SEPARATOR}{asked}"[: chars - len(tally)] + ELLIPSIS + tally

    # The cap is a fit, not a ceiling to stay under: a tally that lands exactly on it keeps
    # every group. Two tools named at half the cap each is the boundary the drop is decided
    # at, and one character either side of it decides differently.
    wide = nodes.TALLY_CHARS // 2 - len(" +1()")
    stem = "mcp__fits_the_cap_".ljust(wide - 1, "_")[: wide - 1]
    fitted = (
        'UPDATE tool_calls SET name = ? || "index" WHERE session_id = ? AND api_call_id = ?',
        [stem, session_id, call_id],
    )
    with TestClient(build_app(plant(silent, fitted))) as planted:
        page = planted.get(url).text
    exactly = "".join(f" +1({stem}{index})" for index in (1, 2))
    assert len(exactly) == nodes.TALLY_CHARS, "the plant does not land on the cap"
    assert fields(page, "data-body", "call")["title"].endswith(exactly)

    # The count is bounded in its turn, because it is the half no width cuts. A call that
    # invoked a handful of tools with names as long as an MCP tool's would otherwise spend a
    # whole NavTree row on counts. Whole groups go rather than half a name: `+1(mcp__…` counts
    # calls of a tool the reader cannot identify.
    named = (
        "UPDATE tool_calls SET name = 'mcp__a_long_server_name__tool_' || \"index\""
        " WHERE session_id = ? AND api_call_id = ?",
        [session_id, call_id],
    )
    with TestClient(build_app(plant(silent, named))) as planted:
        page = planted.get(url).text
    counted = fields(page, "data-body", "call")["title"]
    # The `Bash` that leads is now the first of three long names, and one of the two after it
    # fits under the cap. The other is gone, and the mark says a count was left behind.
    kept, dropped = "mcp__a_long_server_name__tool_1", "mcp__a_long_server_name__tool_2"
    assert counted.startswith("mcp__a_long_server_name__tool_0" + LEAD_SEPARATOR)
    assert counted.endswith(f" +1({kept}){ELLIPSIS}") and dropped not in counted
    assert len(f" +1({kept}) +1({dropped})") > nodes.TALLY_CHARS, "the plant did not overflow"
