"""What enrichment describes: the three levels, and the rows each item is built from.

An item is one thing that gets one enrichment row. The store reads these out of the trace
store (`enrich/store.py`), the renders turn them into prompt text (`enrich/prompts.py`), and
the enricher carries them between the two. Nothing here renders or queries — so the module
the persistence side reads its row types from holds no prompt text and no SQL.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import override

# Between an item key's fields. Absent from every value it joins: a session id and a run id are
# uuids, a source is one of those or `main`, and a turn id is an integer.
SEPARATOR = "|"


class Level(StrEnum):
    """The three things that get an enrichment row, each with its own table and prompt."""

    turn = "turn"
    agent_run = "agent_run"
    session = "session"


@dataclass(frozen=True)
class Budgets:
    """Every size limit one render obeys, in characters.

    Passed rather than read from a constant so the elision paths can be exercised: every
    string in a redacted fixture is ten characters long, so no recorded row comes within two
    orders of magnitude of `total`.
    """

    # The whole rendered prompt. Differs per level, so there is no sensible default.
    total: int
    prompt: int = 4_000
    # The assistant's text per api call. Enough for the narration, not for a file dump.
    text: int = 1_500
    # The head of a tool's input — the file read, the command run, the URL fetched.
    input_head: int = 120
    # The tail of a *failed* tool result. No other result content travels at all.
    error_tail: int = 300
    # A slash command's own printed output — for most command turns, the whole of what
    # happened. 315 of the 316 recorded bodies fit it; the median is 71 characters.
    command_result: int = 2_000


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
    # The description of the agent run this call spawned — the one way a child's work
    # reaches a parent's prompt. None when the call spawned nothing, when the run it spawned
    # is not enriched yet, and when the run is the one being rendered: a fork's transcript
    # holds a copy of its own spawning call, and a run does not embed itself.
    spawned: str | None


@dataclass(frozen=True)
class ApiCallRow:
    """One model response and the tools it asked for. `thinking` is deliberately absent."""

    text: str
    # Why generation stopped, as recorded. None is a real recorded state — 26 of the 69 stop
    # reasons in the fixtures are null — and renders as "not recorded", never as absence.
    stop_reason: str | None
    tool_calls: tuple[ToolCallRow, ...]


@dataclass(frozen=True)
class RunSection:
    """One stretch of a run's transcript: one instruction, and the calls it drove."""

    # None for the calls a run made before any turn of its own — a fork continuing a
    # conversation whose prompt lives in another transcript.
    prompt: str | None
    api_calls: tuple[ApiCallRow, ...]


@dataclass(frozen=True)
class SessionChild:
    """One thing a session did, as that thing's own enrichment described it.

    No transcript text reaches a session prompt: a child that has not been described yet
    renders as undescribed, which moves the session's hash again once it has been.
    """

    # `turn` for a main turn, `agent_run` for a run nothing else in the session embeds.
    level: Level
    # The run's type — `architect`, `Explore`. None for a main turn.
    agent_type: str | None
    description: str | None
    category: str | None
    outcome: str | None


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
        return item_key(self.level, *self.key_values)


def item_key(level: Level, *values: str) -> str:
    """One item's key: its level and its primary key, in the enrichment table's column order.

    The one place the format is written. A caller that builds a key from parts — the parent
    links, the stored stamps — must come through here, because nothing compares the two
    spellings at runtime: a key built differently matches no stored row and names no item, and
    the pass reports fresh work rather than an error.
    """
    return SEPARATOR.join((level, *values))


def level_of(key: str) -> Level:
    """The level of an item key — `item_key` read back, for a caller holding keys alone."""
    return Level(key.split(SEPARATOR, 1)[0])


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
    # What the CLI itself printed for a slash command. None means no record archived an
    # answer; "" means one did and it printed nothing. Most command turns drive no model
    # response, so this is the only thing the render can say about what happened.
    command_result: str | None
    api_calls: tuple[ApiCallRow, ...]

    @property
    @override
    def level(self) -> Level:
        return Level.turn

    @property
    @override
    def key_values(self) -> tuple[str, ...]:
        return (self.session_id, self.source, self.turn_id)


@dataclass(frozen=True)
class AgentRunItem(Item):
    """One subagent run: what it was asked, in sequence, and what it did about it."""

    session_id: str
    agent_run_id: str
    agent_type: str
    sections: tuple[RunSection, ...]

    @property
    @override
    def level(self) -> Level:
        return Level.agent_run

    @property
    @override
    def key_values(self) -> tuple[str, ...]:
        return (self.session_id, self.agent_run_id)


@dataclass(frozen=True)
class SessionItem(Item):
    """One whole session: what it cost, and what its children were described as doing."""

    session_id: str
    # As Claude Code recorded them; either can be absent from an older transcript.
    title: str | None
    git_branch: str | None
    # Wall time is the whole span, gaps included; active is what Claude Code reported working.
    # Wall is None when the session's records carry no end.
    wall_ms: int | None
    active_ms: int | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    # Sums only the api calls the extractor could price.
    cost_usd: float
    # The session's main turns and its rootless runs, in the order they started.
    children: tuple[SessionChild, ...]

    @property
    @override
    def level(self) -> Level:
        return Level.session

    @property
    @override
    def key_values(self) -> tuple[str, ...]:
        return (self.session_id,)
