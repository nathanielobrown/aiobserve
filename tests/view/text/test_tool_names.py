"""Each tool the viewer names its own calls by, one case per rule.

A unit table rather than served HTML: every fixture README redacts the strings under a tool
`input`, so a served row can prove the registry fired but not what it read
(`plans/viewer-polish/testing_plan.md`). The six names the corpus does record are read off
pages in `tests/view/test_node__titles.py`; the rest are here and nowhere else.

Each case says where its input came from — a recorded fixture, this project's own store, or
invented for a tool no recording of ours has ever called.
"""

import pytest

from hyphae.view.text.tool_names import FORMATTERS, Formatted, name_tool

# Every field `tool_fields` extracts, all NULL: what the store hands a formatter for a tool
# call carrying none of them. Each case below fills in the ones its own tool recorded.
EMPTY_FIELDS: dict[str, object] = dict.fromkeys(
    (
        "path",
        "command",
        "description",
        "subagent_type",
        "skill",
        "args",
        "to",
        "addressed",
        "summary",
        "pattern",
        "url",
        "query",
        "message",
        "todos",
        "input_head",
    )
)


# What the store hands a formatter, per tool: the fields `analyze/macros.py:tool_fields`
# extracts, with everything the tool did not carry NULL. Written out as one table because
# fourteen small rules are where a registry drifts from the design that specified it
# (`plans/viewer-polish/design.md`, the formatter table).
#
# Every value here but four is lifted from a recorded session: `Read`, `Bash`, `Agent`,
# `SendMessage` and `ToolSearch` from the fixtures (`tests/fixtures/spine`,
# `tests/fixtures/parallel_tools`), the rest from this project's own sessions in the canonical
# store, which is where the field names came from too. `Grep`, `Glob` and `TodoWrite` have no
# row anywhere in that store, so those three cases are **invented** and the fields they read
# are the ones those tools document rather than ones a recording proved. The fourth is the
# `PushNotification` message: what the session recorded is an agent's prose about private work,
# replaced in the fixture by an invented sentence of the same shape
# (`tests/fixtures/spine/README.md`), and this row reads the replacement.
FORMATTED = [
    # `spine`'s three main-thread reads, relativized against the session's project already:
    # what the formatter gets is the path the macro cut, not the path the record held.
    ("Read", {"path": "docs/handoffs.md"}, "📖", "docs/handoffs.md"),
    ("Write", {"path": "data/migrate_project_rename.py"}, "✏️", "data/migrate_project_rename.py"),
    ("Edit", {"path": "tests/enrich/test_prompts.py"}, "📝", "tests/enrich/test_prompts.py"),
    # A `Bash` call carries both, and the row shows what ran rather than what it was called:
    # a column of descriptions is a column of an agent's own summaries of itself.
    (
        "Bash",
        {"command": "date; ls /Users/nob/repos/mycelia/issues/", "description": "List issues"},
        "⚡",
        "date; ls /Users/nob/repos/mycelia/issues/",
    ),
    # And only its first line: a heredoc or a `&&` chain is a screenful, and the row has one.
    ("Bash", {"command": "python3 - <<'PY'\nimport json\nprint(1)\nPY"}, "⚡", "python3 - <<'PY'"),
    # `spine`'s two delegations, which is the shape the brackets were chosen for: a tree of
    # `Agent` rows reads as a column of types with a task line beside each.
    (
        "Agent",
        {"subagent_type": "Explore", "description": "Research 0149 multi-instance pg0"},
        "👉",
        "[Explore] Research 0149 multi-instance pg0",
    ),
    # A delegation that named no type — Claude Code writes `subagent_type` only where the
    # caller picked one — is the task line alone rather than an empty bracket.
    (
        "Agent",
        {"description": "Grill doc: needs-design pair"},
        "👉",
        "Grill doc: needs-design pair",
    ),
    # A skill invoked bare, and one invoked with arguments, which ride after the name.
    ("Skill", {"skill": "design"}, "📕", "design"),
    (
        "Skill",
        {"skill": "writing", "args": "PR body for the viewer node-browser branch"},
        "📕",
        "writing PR body for the viewer node-browser branch",
    ),
    # The one formatter that reads beyond its own tool call: `to` holds either a run id or a
    # name already fit to print, and `addressed` is the agent type the id resolved to.
    (
        "SendMessage",
        {"to": "aa52d3fe48cec7f58", "addressed": "auditor", "summary": "Request the doc-sync"},
        "📬",
        "to auditor: Request the doc-sync",
    ),
    # Nothing resolved, so the row prints what was recorded — the teammate-name population.
    (
        "SendMessage",
        {"to": "architect", "addressed": None, "summary": "Grill the plan"},
        "📬",
        "to architect: Grill the plan",
    ),
    # A send with no summary is the address alone, rather than a dangling colon.
    ("SendMessage", {"to": "team-lead", "addressed": None}, "📬", "to team-lead"),
    # The two search tools, invented: both document one `pattern`, and neither has a row.
    ("Grep", {"pattern": "def tool_node"}, "🔎", "def tool_node"),
    ("Glob", {"pattern": "**/*.sql"}, "🗂", "**/*.sql"),
    (
        "WebFetch",
        {"url": "https://mise.jdx.dev/tasks/task-arguments.html"},
        "🌐",
        "https://mise.jdx.dev/tasks/task-arguments.html",
    ),
    (
        "WebSearch",
        {"query": "mutmut 3 pyproject.toml config paths_to_mutate 2026"},
        "🔍",
        "mutmut 3 pyproject.toml config paths_to_mutate 2026",
    ),
    # The two tools slice 2 added, both confirmed against session `4208c1bd` before they were
    # written down (`plans/viewer-polish/design.md`). A tool search reads the query it ran, not
    # the `max_results` beside it: what tells two searches apart is what was searched for.
    ("ToolSearch", {"query": "select:PushNotification"}, "🧰", "select:PushNotification"),
    # And a notification reads the message it sent — the only thing it carries besides a status.
    (
        "PushNotification",
        {"message": "Invented for this fixture: the run finished and the report is written up"},
        "🔔",
        "Invented for this fixture: the run finished and the report is written up",
    ),
    # A todo list is the one row named by a count: the items are the model's own plan, and a
    # row of the first one says less than how many there are. Invented, like the two above.
    ("TodoWrite", {"todos": 3}, "☑️", "3 todos"),
    ("TodoWrite", {"todos": 1}, "☑️", "1 todo"),
]


@pytest.mark.parametrize(("name", "fields", "mark", "words"), FORMATTED)
def test_a_named_tool_is_titled_by_the_field_the_design_gives_it(
    name: str, fields: dict[str, object], mark: str, words: str
) -> None:
    """Each tool the registry names reads its own field, marked with its own glyph."""
    assert name_tool(name, {**EMPTY_FIELDS, **fields}) == Formatted(mark, words)


# What a call the registry has no rule for is named by: the shape of its input, checked in
# order — a path, else a description, else the head of the input as stored — and the glyph is
# empty, because a shape says which tool ran to nobody. Invented inputs: the arms
# are the subject, and the rows a fixture records take these same arms through served HTML
# in `tests/view/test_node__titles.py`.
FELL_THROUGH = [
    # A path wins, and it reaches here relativized and cut already (`macros.py:tool_path`).
    ({"path": "docs/viewer.md", "description": "Read the doc"}, "docs/viewer.md"),
    # Else what the caller said the call was for.
    ({"description": "Run the deep research"}, "Run the deep research"),
    # Else the head of the input as the store holds it, which is JSON for every tool we have
    # seen. A call carrying none of the names above still names its own row.
    (
        {"input_head": '{"schema": "Findings", "strict": true}'},
        '{"schema": "Findings", "strict": true}',
    ),
]


@pytest.mark.parametrize(("fields", "words"), FELL_THROUGH)
def test_a_tool_the_registry_does_not_name_is_named_by_the_shape_of_its_input(
    fields: dict[str, object], words: str
) -> None:
    """An unnamed tool takes the shape-driven title, under no glyph of its own."""
    assert name_tool("StructuredOutput", {**EMPTY_FIELDS, **fields}) == Formatted("", words)


def test_an_empty_field_is_a_value_the_record_carried_and_not_an_absence() -> None:
    """The arms fall through on NULL, the way the SQL this ports from coalesces.

    A description recorded as an empty string is a description: the row it names is blank,
    and a rule that skipped it would print the input JSON under a tool whose caller said
    the call was for nothing.
    """
    empty = {"description": "", "input_head": '{"description": ""}'}
    assert name_tool("StructuredOutput", {**EMPTY_FIELDS, **empty}) == Formatted("", "")


@pytest.mark.parametrize("name", sorted(FORMATTERS))
def test_a_named_tool_whose_field_the_record_lacks_falls_through_too(name: str) -> None:
    """A registered tool is only formatted where the record carried what its lead reads.

    A malformed input, or one whose fields are all named something else, is a row the page
    still has to draw — and the shape-driven title says more about it than a bare glyph.
    """
    head = '{"unexpected": 1}'
    assert name_tool(name, {**EMPTY_FIELDS, "input_head": head}) == Formatted("", head)
