"""The one name a node carries wherever it is printed.

A tool call is named once, in Python, out of the fields the store ships (`view/tool_names.py`),
and every surface that prints it — the pane heading, the NavTree row, the crumb, the parent's
log row, the errors list, the api-call row above it — prints that one string cut to its own
width. These leaves read the string back off each of those surfaces and check they agree.
"""

import json
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from hyphae.analyze import macros, queries
from hyphae.view import nodes, tool_names
from hyphae.view.app import build_app
from hyphae.view.format import ELLIPSIS, cut
from hyphae.view.nodes import LEAD_SEPARATOR
from tests.conftest import MYCELIA
from tests.view.conftest import Planter, fields, one


def test_one_tool_call_is_titled_the_same_way_wherever_it_is_named(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """The six surfaces that name a tool call agree, because one derivation names it.

    The pane's own heading, the NavTree row beside it, the crumb chain leading it, the row in
    its parent's children log, the session's errors list, and the api-call row above it — a
    call that answered with tool calls and no words is named by the first of them. They read
    six different queries at four different widths, so the agreement is a fact about the
    derivation rather than about the page: before it was shared, three of these showed the
    input JSON as stored and the fourth showed the path.

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
    # A failed read of a file inside the session's own project: one row every one of the six
    # surfaces has a reason to name — the errors list only lists what failed, and the api call
    # above it is named by its tools only where it said nothing itself.
    path = plant(
        ("UPDATE sessions SET project_dir = ? WHERE id = ?", [project, session_id]),
        (
            "UPDATE tool_calls SET name = ?, input = ?, is_error = true WHERE id = ?",
            ["Read", f'{{"file_path": "{project}/src/hyphae/view/nodes.py"}}', tool_id],
        ),
        ("UPDATE api_calls SET text = '' WHERE id = ?", [call_id]),
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
    # And the api-call row above it, on the same page: a call that spoke no words is named by
    # its first tool call, so the string leads its title with the count of what followed after.
    above = fields(pane, "data-nav-tree", f"call:{call_id}")["title"]
    assert above.startswith(titled), above

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
# `plans/viewer-polish/design.md` rather than read off `view/tool_names.py:FORMATTERS`, which is the
# thing under test. The six names not here have no recorded call to serve, and the leaf below
# says so out loud.
RECORDED_FORMATTERS = {
    "Read": ("📖", "file_path"),
    "Bash": ("⚡", "command"),
    "Agent": ("👉", "subagent_type"),
    "SendMessage": ("📬", "to"),
    "ToolSearch": ("🧰", "query"),
    "PushNotification": ("🔔", "message"),
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
    # Which of the registry's names this corpus records: the six above and no others. The
    # rest are proven by the unit table in `test_tool_names.py` alone, over inputs no fixture
    # holds — so a fixture that gains a `Grep` call reds this line rather than going unread.
    recorded = {
        name for (name,) in store.execute("SELECT DISTINCT name FROM live_tool_calls").fetchall()
    }
    assert recorded & set(tool_names.FORMATTERS) == set(RECORDED_FORMATTERS)


def test_a_tool_the_registry_does_not_name_keeps_the_title_the_store_composed(
    client: TestClient, corpus_db: Path
) -> None:
    """Every recorded call of a tool with no registry entry, against the rule it used to take.

    The shape-driven title was the store's: one `coalesce` over a relativized path, a
    description, and the head of the input as stored. Composing it in Python instead is the
    move no leaf above can see — those read the four tools the registry names, and this rule
    is what names every other tool there is or ever will be.

    So the expectation is that `coalesce` itself, run here over the same rows through the two
    macros that survive, and the sweep is every unnamed call the corpus holds rather than one
    of them. Each of the three arms is asserted to have fired: a port that dropped one would
    otherwise pass on the rows taking the arms it kept.
    """
    reading = duckdb.connect(str(corpus_db), read_only=True)
    macros.install(reading)
    known = sorted(tool_names.FORMATTERS)
    unnamed = reading.execute(
        "SELECT t.session_id, t.source, t.id, t.name,"
        "       tool_path(t.input, s.project_dir, ?),"
        "       tool_asked(t.input, 'description', ?),"
        "       substr(t.input, 1, ? + 1)"
        " FROM live_tool_calls t LEFT JOIN sessions s ON s.id = t.session_id"
        f" WHERE t.name NOT IN ({', '.join('?' * len(known))})"
        ' ORDER BY t.session_id, t.source, t."index"',
        [queries.HEADER_CHARS] * 3 + known,
    ).fetchall()
    reading.close()
    assert unnamed, "the corpus records no call of a tool the registry leaves unnamed"

    took = set()
    for session_id, source, tool_id, name, path, about, head in unnamed:
        # The first arm the record answers, which is what `coalesce` means: an empty string is
        # a value the caller sent and not an absence.
        arm, words = next(
            (arm, value)
            for arm, value in (("path", path), ("about", about), ("head", head))
            if value is not None
        )
        took.add(arm)
        page = client.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
        # The tool's own name still leads the row, because no glyph stands in for it.
        assert fields(page, "data-body", "tool")["title"] == cut(
            f"{name}{LEAD_SEPARATOR}{words}", queries.HEADER_CHARS
        ), (name, tool_id)
    assert took == {"path", "about", "head"}


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
    known = sorted(tool_names.FORMATTERS)
    registered = f"({', '.join('?' * len(known))})"
    session_id, source, call_id, turn_id, model = one(
        store,
        "SELECT c.session_id, c.source, c.id, c.turn_id, c.model FROM live_api_calls c"
        " JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.api_call_id = c.id"
        " WHERE (c.text IS NULL OR c.text = '') AND c.turn_id IS NOT NULL"
        f' GROUP BY 1, 2, 3, 4, 5 HAVING min_by(t.name, t."index") NOT IN {registered}'
        " ORDER BY count(*) DESC, c.id LIMIT 1",
        known,
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
    # what a reader picks the call out of a tree by. After it, that call's own title. The call
    # is chosen for a first tool the registry does not name, which is what leaves the name in
    # the lead — the arm below is the other one.
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

    # And where the registry does name that first tool, its glyph leads the call's title in
    # place of the tool's name — the api call is named by the derivation that names the tool
    # row under it, so the mark a reader picks a `Read` out of a tree by survives the hop up
    # one level. The shortest recorded input, so both surfaces print the title whole.
    glyph_session, glyph_source, glyph_call, first_tool, first_name = one(
        store,
        'SELECT c.session_id, c.source, c.id, min_by(t.id, t."index"),'
        ' min_by(t.name, t."index")'
        " FROM live_api_calls c"
        " JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.api_call_id = c.id"
        " WHERE (c.text IS NULL OR c.text = '')"
        f' GROUP BY 1, 2, 3 HAVING min_by(t.name, t."index") IN {registered}'
        ' ORDER BY length(min_by(t.input, t."index")), c.id LIMIT 1',
        known,
    )
    thread = f"/session/{glyph_session}/thread/{glyph_source}"
    named = fields(client.get(f"{thread}/tool/{first_tool}").text, "data-body", "tool")["title"]
    leading = fields(client.get(f"{thread}/call/{glyph_call}").text, "data-body", "call")["title"]
    assert not named.endswith(ELLIPSIS), named
    # The tool's own title, whole, then whatever the call went on to do after it...
    assert leading.startswith(named), (named, leading)
    # ...and what leads both is the glyph, not the name the row above spells out.
    assert named.startswith(f"{RECORDED_FORMATTERS[first_name][0]} "), named
    assert not leading.startswith(first_name), leading

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

    # The same call with a first tool call that fills a title on its own — a command long
    # enough to run past every width. Each of those widths is spent on the command less the
    # count, so both ends survive: what the call did first, marked where it was stopped, and
    # how many followed.
    asked = "w" * (queries.NAV_CHARS * 2)
    described = (
        "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
        ["Bash", json.dumps({"command": asked, "description": "Run the long one"}), tool_id],
    )
    with TestClient(build_app(plant(silent, described))) as planted:
        page = planted.get(url).text
    tally = " +2(Read)"
    for where, chars in (("data-body", queries.HEADER_CHARS), ("data-nav-tree", queries.NAV_CHARS)):
        key = "call" if where == "data-body" else f"call:{call_id}"
        shown = fields(page, where, key)["title"]
        assert shown == f"⚡ {asked}"[: chars - len(tally)] + ELLIPSIS + tally

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
