"""What each level sends the model: the rows it is built from, and the text they render to.

The renders are pure — rows in, prompt text out — so their evidence is a real store built
from the recorded fixtures rather than a client and a network. Every size limit is a
parameter with a default here, because a redacted fixture is two orders of magnitude short
of the real budgets and elision could not otherwise be tested at all.
"""

from dataclasses import dataclass
from enum import StrEnum


class Level(StrEnum):
    """The three things that get an enrichment row, each with its own table and prompt."""

    turn = "turn"
    agent_run = "agent_run"
    session = "session"


# Per level, covering what `input_hash` cannot see: the instructions and the output schema.
# Bump one and that level re-enriches; its parents follow through the hash.
PROMPT_VERSION: dict[Level, int] = {Level.turn: 1, Level.agent_run: 1, Level.session: 1}


@dataclass(frozen=True)
class ToolCallRow:
    """One tool call as a prompt sees it: what was asked, and how big the answer was.

    The result text itself never travels — 390 MB corpus-wide — except the tail of a failed
    one, which is where friction shows.
    """

    name: str
    input: str
    # None when the call was never answered, which is what `incomplete` means.
    result: str | None
    is_error: bool
    incomplete: bool


@dataclass(frozen=True)
class ApiCallRow:
    """One model response and the tools it asked for. `thinking` is deliberately absent."""

    text: str
    tool_calls: tuple[ToolCallRow, ...]


class Item:
    """One thing that gets one enrichment row."""

    @property
    def level(self) -> Level:
        raise NotImplementedError

    @property
    def key_values(self) -> tuple[str, ...]:
        """The item's primary key, in the enrichment table's column order."""
        raise NotImplementedError

    @property
    def key(self) -> str:
        """The key as one string — what a request, a call log, and a failure record carry."""
        return "|".join((self.level, *self.key_values))


@dataclass(frozen=True)
class TurnItem(Item):
    """One main turn: the prompt a person wrote, and the work it drove."""

    session_id: str
    source: str
    turn_id: str
    index: int
    # As recorded, tags included. A slash turn renders `command_name`/`command_args` instead.
    prompt: str
    command_name: str | None
    command_args: str | None
    api_calls: tuple[ApiCallRow, ...]

    @property
    def level(self) -> Level:
        return Level.turn

    @property
    def key_values(self) -> tuple[str, ...]:
        return (self.session_id, self.source, self.turn_id)
