"""How a tool call is named, wherever the viewer names one.

A tool call's title is read from the input field that tells two of that tool's calls apart —
a path for a file tool, the command for `Bash` — under a glyph that stands for the tool, so a
NavTree row says which tool ran without spending the width on its name. The store extracts the
fields (`analyze/macros.py:tool_fields`) and this module composes the name out of them: SQL
ships fields, and the name a reader reads is Python's.

`name_tool` is the entry point and `view/builders.py` its only caller, so the surfaces that
print a tool call — its own page, a NavTree row, a crumb, a children log, the errors list, and
the api call above it — read one derivation. A tool absent from `FORMATTERS` is not a gap: it
takes the shape rule below, which names a tool nobody here has heard of.
"""

from collections.abc import Callable, Mapping
from typing import NamedTuple

# What the store extracts from a tool call's input for the formatters below
# (`analyze/macros.py:tool_fields`): every member present on every row, NULL where the call
# carried nothing under that name.
Fields = Mapping[str, object]


class Formatted(NamedTuple):
    """A tool call named by its own tool: the glyph that stands for the tool, and the words."""

    mark: str
    words: str


# One tool's rule for naming its calls, or None where this call carried nothing to name it by.
Formatter = Callable[[Fields], Formatted | None]


def _field(fields: Fields, key: str) -> str:
    """One extracted field as words, whatever the query left NULL."""
    value = fields.get(key)
    return str(value) if value else ""


def _one(mark: str, key: str) -> Formatter:
    """The common rule: a glyph for the tool, and the one field the design names for it."""

    def formatter(fields: Fields) -> Formatted | None:
        words = _field(fields, key)
        return Formatted(mark, words) if words else None

    return formatter


def _bash(fields: Fields) -> Formatted | None:
    """What ran, not what it was called: `description` is the agent's summary of itself."""
    command = _field(fields, "command")
    # And the first line of it. A heredoc or a chained pipeline is a screenful, and the row
    # that has to hold it is one line — so the cut is at the newline rather than at a width.
    return Formatted("⚡", command.split("\n", 1)[0]) if command else None


def _agent(fields: Fields) -> Formatted | None:
    """The type the run was spawned as, then the brief: a tree of runs reads as a column."""
    kind = _field(fields, "subagent_type")
    said = _field(fields, "description")
    words = f"[{kind}] {said}".strip() if kind else said
    return Formatted("👉", words) if words else None


def _skill(fields: Fields) -> Formatted | None:
    """The skill invoked, and what it was invoked with where the caller passed anything."""
    skill = _field(fields, "skill")
    args = _field(fields, "args")
    return Formatted("📕", f"{skill} {args}".strip()) if skill else None


def _send_message(fields: Fields) -> Formatted | None:
    """Who it went to and what it said.

    `to` holds either an agent run's id or a name the caller typed. The query resolves the id
    against the session's runs and leaves `addressed` NULL where nothing matched — one lookup
    and one fallback, because a name that resolves to nothing is already fit to print.
    """
    who = _field(fields, "addressed") or _field(fields, "to")
    summary = _field(fields, "summary")
    return Formatted("📬", f"to {who}: {summary}" if summary else f"to {who}") if who else None


def _todo_write(fields: Fields) -> Formatted | None:
    """How many items the list holds. The items are the model's plan; the first one alone
    says less about the call than the count does."""
    count = fields.get("todos")
    if not isinstance(count, int):
        return None
    return Formatted("☑️", f"{count} todo{'' if count == 1 else 's'}")


# What each tool the viewer knows names its calls by (`plans/viewer-polish/design.md`). A tool
# absent here is not a gap: its calls take the shape rule below, which names any input at all
# and is what a registry keyed by name cannot do. So this holds the tools whose input we have
# read enough of to beat that default.
FORMATTERS: dict[str, Formatter] = {
    "Read": _one("📖", "path"),
    "Write": _one("✏️", "path"),
    "Edit": _one("📝", "path"),
    "Bash": _bash,
    "Agent": _agent,
    "Skill": _skill,
    "SendMessage": _send_message,
    "Grep": _one("🔎", "pattern"),
    "Glob": _one("🗂", "pattern"),
    "WebFetch": _one("🌐", "url"),
    "WebSearch": _one("🔍", "query"),
    "TodoWrite": _todo_write,
}


# What a call the registry has no rule for is named by, in the order the arms are tried: the
# fields that say what a call was whichever tool made it. `input_head` is the head of the input
# as the store holds it — JSON for every tool we have seen — so the last arm names a call whose
# input carried none of the names above it.
_SHAPE = ("path", "description", "input_head")


def _shaped(fields: Fields) -> str:
    """The shape-driven name: the first of `_SHAPE` the record answers.

    A field the record left out falls through; one it carried empty does not. That is the
    `coalesce` this was ported from — a caller who sent an empty description described the
    call as nothing, and printing its raw input instead would be the viewer overruling it.
    """
    for key in _SHAPE:
        value = fields.get(key)
        if value is not None:
            return str(value)
    return ""


def name_tool(name: str, fields: Fields) -> Formatted:
    """What one tool call is called: its tool's own rule, else the shape of its input.

    A `Formatted` whose `mark` is empty is the second — no glyph stands for the tool, so the
    caller leads the row with the tool's name instead (`view/builders.py`).
    """
    formatter = FORMATTERS.get(name)
    named = formatter(fields) if formatter else None
    return named or Formatted("", _shaped(fields))
