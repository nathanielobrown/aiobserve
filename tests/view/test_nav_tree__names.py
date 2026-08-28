"""What each kind of NavTree row is named from, and how an address resolves to a name.

A row's title is the whole of what it says, so these leaves read every one back against the
column its kind is named by — a turn's prompt, a tool call's own fields, a run's agent type and
brief — with the expectations restated from the store in the test's own SQL rather than read
off the code that composes them.

How a row is laid out and what it carries beside its title is `test_nav_tree__rows.py`.
"""

import json
from collections.abc import Sequence

import duckdb
from fastapi.testclient import TestClient

from hyphae.analyze import queries
from hyphae.view.app import build_app
from hyphae.view.format import cut
from hyphae.view.nodes import LEAD_SEPARATOR, Kind
from hyphae.view.store import Page, open_store, page_rows
from tests.view.conftest import Planter, fields, one, values
from tests.view.nav_trees import candidates, node_url


def _shaped(given: str | None, project: str | None, chars: int) -> str:
    """What a tool call the viewer knows no rule for is called, restated from its input.

    Restated rather than imported (`view/formatters.py`): an oracle sharing the implementation
    would agree with itself whatever it said. Each field is cut before it is chosen, the way
    the query cuts it, so a path longer than the column loses its repository prefix off an
    already-bounded head.

    Every input the corpus holds is a JSON object of strings; a fixture holding anything else
    reads as no title here and goes red rather than passing quietly.
    """
    try:
        asked = json.loads(given) if given is not None else None
    except json.JSONDecodeError:
        asked = None
    fields = asked if isinstance(asked, dict) else {}

    def head(key: str, room: int = 0) -> str | None:
        value = fields.get(key)
        return value[: chars + 1 + room] if isinstance(value, str) else None

    # A path is read with the project directory on top of the width, because the prefix comes
    # off before the cut: what the column shows is a whole width of the part that tells two
    # paths apart. A path the project does not contain takes the plain width instead.
    path = head("file_path", room=len(project) + 1 if project else 0)
    if path is not None:
        if project and path.startswith(f"{project}/"):
            return path[len(project) + 1 :]
        return path[: chars + 1]
    if (described := head("description")) is not None:
        return described
    return (given or "")[: chars + 1]


# The tools the fixture corpus records that the viewer names by their own field, restated from
# `plans/viewer-polish/design.md` rather than read off `view/formatters.py:FORMATTERS`. The leaf
# below asserts which registered names this corpus exercises, so a name added to the design
# without a recorded call is a rule this sweep never sees rather than one it silently blesses.
_MARKS = {
    "Read": "📖",
    "Bash": "⚡",
    "Agent": "👉",
    "SendMessage": "📬",
    "ToolSearch": "🧰",
    "PushNotification": "🔔",
}


def _named(
    name: str, given: str | None, project: str | None, addressed: str | None, chars: int
) -> str | None:
    """What a tool that names its own calls is called, or None where it carried nothing to
    name it by and falls back to the shape rule above."""
    try:
        asked = json.loads(given) if given is not None else None
    except json.JSONDecodeError:
        asked = None
    fields = asked if isinstance(asked, dict) else {}

    def head(key: str) -> str:
        value = fields.get(key)
        return value[: chars + 1] if isinstance(value, str) else ""

    match name:
        # A path, read against the project the way the shape rule reads one.
        case "Read":
            words = _shaped(given, project, chars) if head("file_path") else ""
        # What ran, and only its first line: the row is one line and a heredoc is a screenful.
        case "Bash":
            words = head("command").split("\n", 1)[0]
        # The definition the run was spawned as, in brackets, then the brief it was given.
        case "Agent":
            kind, said = head("subagent_type"), head("description")
            words = f"[{kind}] {said}".strip() if kind else said
        # Who it went to and what it said. `to` holds a run id or a name the caller typed, and
        # `addressed` is the agent type the id resolved to where the session holds that run.
        case "SendMessage":
            who = (addressed or "")[: chars + 1] or head("to")
            summary = head("summary")
            words = "" if not who else f"to {who}: {summary}" if summary else f"to {who}"
        # What was searched for, and what the notification said: the one field each carries.
        case "ToolSearch":
            words = head("query")
        case "PushNotification":
            words = head("message")
        case _:
            return None
    return f"{_MARKS[name]} {words}" if words else None


def _tallied(names: Sequence[str]) -> str:
    """The count of each tool after the first, restated from the tool names the store holds.

    In the order each tool first appears among them, which is the order the calls were made.
    No cut: no recorded call invokes enough distinct tools to reach `nodes.TALLY_CHARS`, and
    a corpus that grew one would go red here rather than pass on a shortened count.
    """
    counted: dict[str, int] = {}
    for name in names:
        counted[name] = counted.get(name, 0) + 1
    return "".join(f" +{made}({name})" for name, made in counted.items())


def titled(store: duckdb.DuckDBPyConnection, session_id: str) -> dict[str, tuple[str, str]]:
    """Every row of one session whose title the store composes, keyed the way a row is.

    Read off the columns the design names a node from, not off the page: a tool call named by
    its input alone, or a run named by the definition it ran where its own brief was recorded,
    is a row pointing at a node the reader did not ask for.

    Each title comes back in two halves: what a surface cuts, and the part that survives the
    cut — an api call's tool count, which the width is budgeted around rather than spent on.
    """
    said: dict[str, str] = {}
    # What must still be there after the cut, per row, empty for every title that is all one
    # piece. Only an api call named by its tool calls carries one.
    kept: dict[str, str] = {}
    for tool_id, name, given, project, addressed in store.execute(
        "SELECT t.id, t.name, t.input, s.project_dir, a.agent_type FROM live_tool_calls t"
        " LEFT JOIN sessions s ON s.id = t.session_id"
        # The one lookup a title reaches outside its own row for: a `SendMessage` addressed by
        # an agent run's id reads as that run's type.
        " LEFT JOIN live_agent_runs a ON a.session_id = t.session_id"
        "  AND a.id = json_extract_string(t.input, '$.to')"
        " WHERE t.session_id = ?",
        [session_id],
    ).fetchall():
        # A tool the viewer knows names its own calls, under the glyph that stands for it and
        # with no name beside it — the glyph is what a reader picks the call out of a tree by.
        # Any other tool leads with its name, and after it the title that tells two `Read`
        # rows apart.
        if (named := _named(name, given, project, addressed, queries.NAV_CHARS)) is not None:
            said[f"{Kind.TOOL}:{tool_id}"] = named
            continue
        titled = _shaped(given, project, queries.NAV_CHARS)
        said[f"{Kind.TOOL}:{tool_id}"] = f"{name}{LEAD_SEPARATOR}{titled}" if titled else name
    for call_id, spoken, model, tools, given, project, addressed in store.execute(
        # The tool calls a call went on to make, in the order it made them: their names, and
        # the input of the first, which is the only one whose own title is shown — with the
        # same address lookup the tool rows above take, because the first call names itself
        # here exactly as it names itself on its own row.
        "SELECT c.id, c.text, c.model,"
        ' list(t.name ORDER BY t."index") FILTER (t.id IS NOT NULL),'
        ' min_by(t.input, t."index"), any_value(s.project_dir),'
        ' min_by(a.agent_type, t."index")'
        " FROM live_api_calls c"
        " LEFT JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.api_call_id = c.id"
        " LEFT JOIN sessions s ON s.id = c.session_id"
        " LEFT JOIN live_agent_runs a ON a.session_id = t.session_id"
        "  AND a.id = json_extract_string(t.input, '$.to')"
        " WHERE c.session_id = ? GROUP BY c.id, c.text, c.model",
        [session_id],
    ).fetchall():
        # What the call said. Where it said nothing, what it did instead: the tool it called
        # first and that call's own title, then how many of each tool followed. A call that
        # neither spoke nor called a tool is named by the model that was asked.
        if spoken:
            # Marked as the model's own words, which is the one thing on the row the rest of
            # the page does not say — and marked whether or not the call also ran tools.
            said[f"{Kind.CALL}:{call_id}"] = f"💭 {spoken}"
        elif tools:
            # Named by the same two rules the tool row under it takes, in the same order: the
            # tool's own rule under its glyph, else its name leading the shape of its input.
            # One derivation for both rows is the point — a reader following a call into the
            # tool it called must not meet a different name at the bottom.
            if (named := _named(tools[0], given, project, addressed, queries.NAV_CHARS)) is None:
                asked = _shaped(given, project, queries.NAV_CHARS)
                named = f"{tools[0]}{LEAD_SEPARATOR}{asked}" if asked else tools[0]
            said[f"{Kind.CALL}:{call_id}"] = named
            kept[f"{Kind.CALL}:{call_id}"] = _tallied(tools[1:])
        else:
            said[f"{Kind.CALL}:{call_id}"] = model
    for compaction_id, trigger in store.execute(
        "SELECT id, trigger FROM live_compactions WHERE session_id = ?", [session_id]
    ).fetchall():
        said[f"{Kind.COMPACTION}:{compaction_id}"] = f"compaction · {trigger}"
    for turn_id, prompt, command_name, command_args in store.execute(
        "SELECT id, prompt, command_name, command_args FROM live_turns WHERE session_id = ?",
        [session_id],
    ).fetchall():
        # The command a turn ran and what followed it, else the prompt as the reader typed it.
        said[f"{Kind.TURN}:{turn_id}"] = (
            f"{command_name} {command_args or ''}".strip() if command_name is not None else prompt
        )
    for run_id, brief, agent_type in store.execute(
        "SELECT id, brief, agent_type FROM live_agent_runs WHERE session_id = ?",
        [session_id],
    ).fetchall():
        # The definition it ran, always first and in brackets — which agent this was is what a
        # reader picks a run out of a tree by — and after it the brief it was given, where one
        # was recorded. The brackets close the lead, so no dash stands between the two.
        said[f"{Kind.RUN}:{run_id}"] = f"[{agent_type}] {brief}" if brief else f"[{agent_type}]"
    return {key: (value, kept.get(key, "")) for key, value in said.items()}


def test_every_row_is_named_from_the_column_its_kind_is_named_by(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A row's title is the whole of what it says, so it is read back against its own column.

    A NavTree row carries `NAV_TREE_ROW_BYTES` and no more, so the title is where a kind spends what
    it has to say — and every kind spends it differently. Every node the store names is read
    on its own page, where its row is on the open path whichever preset the reader picked: a
    column that only one recorded row exercises — a slash turn with no arguments after it —
    is one a sample would step over.
    """
    # Keyed by session as well as by row: two sessions of the corpus record an api call under
    # the same id, and a row carries the id alone.
    # Composed at a NavTree row's width the way the surfaces compose it: the count of an api
    # call's tool calls is taken out of the width first, so the row is cut around it.
    said = {
        (str(at), key): (cut(head, queries.NAV_CHARS - len(kept)) + kept).strip()
        for (at,) in store.execute("SELECT id FROM sessions").fetchall()
        for key, (head, kept) in titled(store, str(at)).items()
    }
    read: set[tuple[str, str]] = set()
    for kind in (Kind.TOOL, Kind.CALL, Kind.COMPACTION, Kind.RUN, Kind.TURN):
        for session_id, source, node_id in candidates(store, kind):
            # A page holds more than the node it opens, so the ones already read are skipped.
            if (session_id, f"{kind}:{node_id}") in read:
                continue
            page = client.get(node_url(kind, session_id, source, node_id)).text
            assert f"{kind}:{node_id}" in values(page, "data-nav-tree"), node_id
            for key in values(page, "data-nav-tree"):
                if (at := (session_id, key)) in said:
                    assert fields(page, "data-nav-tree", key)["title"] == said[at], at
                    read.add(at)
    # Every row the store names a title for was reached. A sweep that missed one would pass on
    # a title built from any column at all.
    assert read == set(said)


# What the planted run of another session was spawned as. Invented, and unlike anything the
# corpus records, so a page that printed it could only have got it from the plant.
COLLIDED = "planted-collision"


def test_an_address_names_a_run_of_the_sending_session_and_no_other(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A `SendMessage` addresses a run by an id, and an id is one session's word.

    Claude Code mints a run id per session and nothing makes one unique across a store, so
    every lookup that turns a `to` into an agent type is scoped to the sending session. No two
    sessions of the corpus collide — 17 hex characters rarely do — which is why the collision is
    planted: against the corpus as recorded, a lookup matching on the id alone reads exactly
    like one that matches on the session too.

    Four queries resolve an address, one per surface a send is named on, so the plant is read
    on all four: the NavTree row and the pane's heading on the call's own page, the row in its
    api call's children log, and the row on the session's errors page — which the second plant
    reaches by failing the call, the one way onto that page.
    """
    session_id, source, tool_id, call_id, run_id, agent_type = one(
        store,
        "SELECT t.session_id, t.source, t.id, t.api_call_id, a.id, a.agent_type"
        " FROM live_tool_calls t"
        " JOIN live_agent_runs a ON a.session_id = t.session_id"
        "  AND a.id = json_extract_string(t.input, '$.to')"
        " WHERE t.name = 'SendMessage' ORDER BY t.session_id, t.source, t.\"index\" LIMIT 1",
    )
    (elsewhere,) = one(
        store, "SELECT id FROM sessions WHERE id <> ? ORDER BY id LIMIT 1", [str(session_id)]
    )
    collided = plant(
        # The same run id under another session, spawned as something else — the row a lookup
        # that forgot whose id it was reading would find...
        (
            "INSERT INTO agent_runs (SELECT * REPLACE (? AS session_id, ? AS agent_type)"
            " FROM agent_runs WHERE session_id = ? AND id = ?)",
            [str(elsewhere), COLLIDED, str(session_id), str(run_id)],
        ),
        # ...and the send failed, so the errors page has this call to name.
        (
            "UPDATE tool_calls SET is_error = true WHERE session_id = ? AND id = ?",
            [str(session_id), str(tool_id)],
        ),
    )
    with TestClient(build_app(collided)) as served:
        pane = served.get(f"/session/{session_id}/thread/{source}/tool/{tool_id}").text
        parent = served.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
        failures = served.get(f"/session/{session_id}/errors").text
    # Every surface still prints the run this session spawned...
    named = f"📬 to {agent_type}"
    assert fields(pane, "data-nav-tree", f"{Kind.TOOL}:{tool_id}")["title"].startswith(named)
    assert fields(pane, "data-body", "tool")["title"].startswith(named)
    assert fields(parent, "data-child", f"{Kind.TOOL}:{tool_id}")["title"].startswith(named)
    assert fields(failures, "data-error", f"{Kind.TOOL}:{tool_id}")["title"].startswith(named)
    # ...and the other session's word reaches none of them. Read across the whole page rather
    # than off the row: an unscoped join matches twice, and which of the two answers a row —
    # or whether the row is drawn twice — is the database's business and not a contract.
    for page in (pane, parent, failures):
        assert COLLIDED not in page
    # The heading is the one of the four that reads its query's first row and drops the rest,
    # so a second row it should never have had leaves nothing on the page to see. That query
    # is read as rows instead: one call, one header.
    with open_store(collided) as reading:
        header = page_rows(
            reading,
            Page.TOOL_HEADER,
            session_id=str(session_id),
            source=str(source),
            tool_call_id=str(tool_id),
            head_chars=queries.HEADER_CHARS,
            detail_chars=queries.DETAIL_CHARS,
        )
    assert len(header) == 1
