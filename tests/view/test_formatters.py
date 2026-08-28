"""Each tool the viewer names its own calls by, one case per rule.

A unit table rather than served HTML: every fixture README redacts the strings under a tool
`input`, so a served row can prove the registry fired but not what it read
(`plans/viewer-polish/testing_plan.md`). The four names the corpus does record are read off
pages in `tests/view/test_node__titles.py`; the rest are here and nowhere else.

Each case says where its input came from — a recorded fixture, this project's own store, or
invented for a tool no recording of ours has ever called.
"""

import pytest

from hyphae.view.formatters import FORMATTERS, Formatted, formatted

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
        "todos",
    )
)


# What the store hands a formatter, per tool: the fields `analyze/macros.py:tool_fields`
# extracts, with everything the tool did not carry NULL. Written out as one table because
# twelve small rules are where a registry drifts from the design that specified it
# (`plans/viewer-polish/design.md`, the formatter table).
#
# Every value here but three is lifted from a recorded session: `Read`, `Bash`, `Agent` and
# `SendMessage` from the fixtures (`tests/fixtures/spine`, `tests/fixtures/parallel_tools`),
# the rest from this project's own sessions in the canonical store, which is where the field
# names came from too. `Grep`, `Glob` and `TodoWrite` have no row anywhere in that store, so
# those three cases are **invented** and the fields they read are the ones those tools
# document rather than ones a recording proved.
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
    assert formatted(name, {**EMPTY_FIELDS, **fields}) == Formatted(mark, words)


def test_a_tool_the_registry_does_not_name_is_formatted_by_nothing() -> None:
    """An unnamed tool falls through to the shape-driven title, which is the store's own."""
    assert formatted("StructuredOutput", {**EMPTY_FIELDS, "path": "docs/viewer.md"}) is None


@pytest.mark.parametrize("name", sorted(FORMATTERS))
def test_a_named_tool_whose_field_the_record_lacks_falls_through_too(name: str) -> None:
    """A registered tool is only formatted where the record carried what its lead reads.

    A malformed input, or one whose fields are all named something else, is a row the page
    still has to draw — and the shape-driven title says more about it than a bare glyph.
    """
    assert formatted(name, EMPTY_FIELDS) is None
