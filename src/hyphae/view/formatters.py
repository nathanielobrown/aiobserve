"""How each tool the viewer knows names its own calls.

A tool call's title is read from the input field that tells two of that tool's calls apart —
a path for a file tool, the command for `Bash` — under a glyph that stands for the tool, so a
NavTree row says which tool ran without spending the width on its name. The store extracts the
fields (`analyze/macros.py:tool_fields`); this module holds the rule per name and nothing else,
because SQL cannot dispatch on a name without a `CASE` arm per tool.

`view/builders.py:tool_node` is the caller. A tool absent from `FORMATTERS` keeps the shape-driven
title the store composes for any input at all.
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
# absent here is not a gap: its calls take the shape-driven title the store composes for any
# tool at all (`analyze/macros.py:tool_title`), which is what a registry keyed by name cannot
# do. So this holds the tools whose input we have read enough of to beat that default.
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


def formatted(name: str, fields: Fields) -> Formatted | None:
    """How this tool names its own calls, or None to leave the call to the store's default."""
    formatter = FORMATTERS.get(name)
    return formatter(fields) if formatter else None
