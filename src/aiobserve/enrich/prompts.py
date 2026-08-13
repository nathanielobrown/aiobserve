"""What each level sends the model: the rows it is built from, and the text they render to.

The renders are pure — rows in, prompt text out — so their evidence is a real store built
from the recorded fixtures rather than a client and a network. Every size limit is a
parameter with a default here, because a redacted fixture is two orders of magnitude short
of the real budgets and elision could not otherwise be tested at all.
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from aiobserve.enrich.taxonomy import (
    CATEGORY_DEFINITIONS,
    OUTCOME_DEFINITIONS,
    Category,
    Outcome,
)


class Level(StrEnum):
    """The three things that get an enrichment row, each with its own table and prompt."""

    turn = "turn"
    agent_run = "agent_run"
    session = "session"


# Per level, covering what `input_hash` cannot see: the instructions and the output schema.
# Bump one and that level re-enriches; its parents follow through the hash.
PROMPT_VERSION: dict[Level, int] = {Level.turn: 2, Level.agent_run: 2, Level.session: 2}

# What each level is looking at. The rest of the instructions is the same everywhere, so a
# level reads differently only where it should.
_SUBJECT: dict[Level, str] = {
    Level.turn: (
        "You are reading one turn of a coding session: what the person asked for, and what "
        "the agent did about it. Describe that turn."
    ),
    Level.agent_run: (
        "You are reading one run of a subagent: the task it was given, any later "
        "instructions, and what it did about them. Describe that run."
    ),
    Level.session: (
        "You are reading a summary of one coding session: what it cost, and a description of "
        "each thing it did, in order. Describe the session as a whole."
    ),
}

_ANSWER = """Answer with one JSON object recording what you just read, and say nothing else:

- description: one or two sentences saying what was done, and to what. Name the files, \
commands and subjects concretely. Do not judge the work, and do not restate the category
- category: exactly one of the categories below
- outcome: exactly one of the outcomes below
- friction: one line naming visible struggle — a command that kept failing, a wrong turn \
taken and undone, a tool that would not answer — or null when the records show none

Never quote a credential, key or token, whatever it appears in. Never copy code or file \
contents into the description."""

# The output contract itself: passed to `--json-schema`, so the model cannot answer out of
# vocabulary in the first place. An edit here is a `PROMPT_VERSION` bump, since `input_hash`
# cannot see it.
OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "description": "One or two sentences"},
        "category": {"type": "string", "enum": [str(member) for member in Category]},
        "outcome": {"type": "string", "enum": [str(member) for member in Outcome]},
        "friction": {
            "type": ["string", "null"],
            "description": "One line naming visible struggle, or null when there was none",
        },
    },
    # `friction` included: the model must decide there was none, not forget to say.
    "required": ["description", "category", "outcome", "friction"],
}


def instructions(level: Level) -> str:
    """The system prompt for one level. Versioned by `PROMPT_VERSION`, not by `input_hash`."""
    vocabulary = "\n".join(
        [
            "Categories:",
            *(f"- {member}: {text}" for member, text in CATEGORY_DEFINITIONS.items()),
            "",
            "Outcomes:",
            *(f"- {member}: {text}" for member, text in OUTCOME_DEFINITIONS.items()),
        ]
    )
    return "\n\n".join([_SUBJECT[level], _ANSWER, vocabulary])


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


TURN_BUDGETS = Budgets(total=30_000)
# The same cap: a run holds the same kind of work a main turn does, and 209 of 2,458 recorded
# runs reach it.
RUN_BUDGETS = Budgets(total=30_000)
# Smaller: a session carries one line per child rather than a transcript. Sessions average 3.1
# children and the longest recorded one has 92.
SESSION_BUDGETS = Budgets(total=24_000)


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


def level_of(key: str) -> Level:
    """The level of an item key, so a caller holding keys alone can still tell them apart."""
    return Level(key.split("|", 1)[0])


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


def render_turn(item: TurnItem, budgets: Budgets = TURN_BUDGETS) -> str:
    """One main turn as the model sees it: what was asked, and what the session then did."""
    head = ["# Main turn", ""]
    if item.command_name is not None:
        # The `prompt` column keeps the command tags; forwarding it would spend budget on
        # markup and read as content.
        head += ["## Command", " ".join(filter(None, (item.command_name, item.command_args)))]
    else:
        head += ["## Prompt", _cap(item.prompt, budgets.prompt)]
    lines: list[str] = []
    for call in item.api_calls:
        lines += ["", "## Response"]
        text = _cap(call.text.strip(), budgets.text)
        if text:
            lines.append(text)
        lines += [_tool_line(tool, budgets) for tool in call.tool_calls]
    return _fit("\n".join(head), lines, budgets.total)


@dataclass(frozen=True)
class RunSection:
    """One stretch of a run's transcript: one instruction, and the calls it drove."""

    # None for the calls a run made before any turn of its own — a fork continuing a
    # conversation whose prompt lives in another transcript.
    prompt: str | None
    api_calls: tuple[ApiCallRow, ...]


@dataclass(frozen=True)
class AgentRunItem(Item):
    """One subagent run: what it was asked, in sequence, and what it did about it."""

    session_id: str
    agent_run_id: str
    agent_type: str
    sections: tuple[RunSection, ...]

    @property
    def level(self) -> Level:
        return Level.agent_run

    @property
    def key_values(self) -> tuple[str, ...]:
        return (self.session_id, self.agent_run_id)


def render_run(item: AgentRunItem, budgets: Budgets = RUN_BUDGETS) -> str:
    """One agent run as the model sees it: every instruction it got, and the work each drove.

    The title and the run's first section survive any budget; the sequence after them elides.
    """
    head = [f"# Agent run: {item.agent_type}"]
    lines: list[str] = []
    task_seen = False
    for index, section in enumerate(item.sections):
        if section.prompt is None:
            opening = ["## Continuation", _CONTINUATION]
        else:
            opening = [
                "## Task" if not task_seen else "## Instruction",
                _cap(_unwrap_teammate(section.prompt), budgets.prompt),
            ]
            task_seen = True
        # Only the opening of the first section is protected from elision; its calls, and
        # everything after it, are the sequence `_fit` trims.
        (head if index == 0 else lines).extend(["", *opening])
        for call in section.api_calls:
            lines += ["", "## Response"]
            text = _cap(call.text.strip(), budgets.text)
            if text:
                lines.append(text)
            lines += [_tool_line(tool, budgets) for tool in call.tool_calls]
    return _fit("\n".join(head), lines, budgets.total)


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
    def level(self) -> Level:
        return Level.session

    @property
    def key_values(self) -> tuple[str, ...]:
        return (self.session_id,)


def render_session(item: SessionItem, budgets: Budgets = SESSION_BUDGETS) -> str:
    """One session as the model sees it: what it cost, and a line per thing it did."""
    head = [
        f"# Session: {item.title or 'untitled'}",
        "",
        "## Metrics",
        f"branch {item.git_branch or 'unknown'}",
        f"wall {_duration(item.wall_ms)}, active {_duration(item.active_ms)}",
        f"tokens {item.input_tokens:,} in, {item.output_tokens:,} out, "
        f"{item.cache_read_tokens:,} cache read, {item.cache_creation_tokens:,} cache write",
        f"cost ${item.cost_usd:.2f}",
        "",
        "## Work",
    ]
    return _fit("\n".join(head), [_child_line(child) for child in item.children], budgets.total)


def render(item: Item) -> str:
    """One item as its level's prompt, at that level's default budgets.

    The enricher's one door into the renders. Take a `render_*` function directly to pass
    budgets, as the tests do.
    """
    match item:
        case TurnItem():
            return render_turn(item)
        case AgentRunItem():
            return render_run(item)
        case SessionItem():
            return render_session(item)
    raise ValueError(f"nothing renders a {type(item).__name__}")


def input_hash(rendered: str) -> str:
    """The staleness hash: the rendered content and nothing else.

    Not the instructions and not the output schema — `PROMPT_VERSION` covers those, so an
    instruction edit does not have to pretend the content changed.
    """
    return hashlib.sha256(rendered.encode()).hexdigest()


# What a run with no prompt of its own says in place of a task. All 41 zero-turn runs of the
# corpus are forks, and the conversation they continue is not in their transcript.
_CONTINUATION = "This run continues a conversation another transcript holds; its task is not here."

# The one turn opener that carries attributes. Its wrapper is markup — forwarding it would
# spend budget on the tag and read as content, exactly as a slash command's tags would.
_TEAMMATE_MESSAGE = re.compile(
    r"\A<teammate-message\b[^>]*>(.*)</teammate-message>\Z", re.DOTALL | re.IGNORECASE
)


def _unwrap_teammate(prompt: str) -> str:
    """An instruction from another agent, without the XML the transcript stores it in."""
    match = _TEAMMATE_MESSAGE.fullmatch(prompt.strip())
    return match.group(1).strip() if match else prompt


def _tool_line(tool: ToolCallRow, budgets: Budgets) -> str:
    """One tool call on one line: what ran, on what, and how big the answer was."""
    if tool.incomplete:
        # Not "0 chars": the session ended or was interrupted mid-call.
        result = "unanswered"
    elif tool.result is None:
        result = "no result"
    else:
        result = f"result {len(tool.result)} chars" + (", ERROR" if tool.is_error else "")
    line = (
        f"- {tool.name} (input {len(tool.input)} chars, {result})"
        f" {_one_line(_cap(tool.input, budgets.input_head))}"
    )
    if tool.is_error and tool.result:
        line += f" | error tail: {_one_line(_tail(tool.result, budgets.error_tail))}"
    if tool.spawned is not None:
        line += f" | subagent: {_one_line(tool.spawned)}"
    return line


def _child_line(child: SessionChild) -> str:
    """One child of a session on one line: what kind of thing it was, and what it did."""
    label = "Main turn" if child.agent_type is None else f"Agent run ({child.agent_type})"
    if child.description is None:
        return f"- {label} [not described yet]"
    return f"- {label} [{child.category}/{child.outcome}] {_one_line(child.description)}"


def _duration(ms: int | None) -> str:
    """A span of time in the two largest units that carry it — what a reader compares."""
    if ms is None:
        return "unknown"
    seconds, minutes = ms // 1000, ms // 60_000
    if seconds < 60:
        return f"{seconds}s"
    if minutes < 60:
        return f"{minutes}m {seconds % 60}s"
    if minutes < 1440:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes // 1440}d {minutes % 1440 // 60}h"


def _cap(text: str, limit: int) -> str:
    """The head of `text` and how much was left behind, in `limit` characters or fewer."""
    if len(text) <= limit:
        return text
    kept = _room(len(text), limit)
    return f"{text[:kept]}{_dropped(len(text) - kept)}" if kept > 0 else text[:limit]


def _tail(text: str, limit: int) -> str:
    """The end of `text` — where an error message says what failed — within the same limit."""
    if len(text) <= limit:
        return text
    kept = _room(len(text), limit)
    return f"{_dropped(len(text) - kept)}{text[-kept:]}" if kept > 0 else text[-limit:]


def _dropped(count: int) -> str:
    return f"[+{count} chars]"


def _room(length: int, limit: int) -> int:
    """How much text a cap keeps once room for its marker is paid for.

    Reserved against the whole length, so the marker finally written — which counts something
    shorter — always fits. Zero or less means the limit holds no marker at all, and the count
    is then what goes: a reader who cannot have both wants the text.
    """
    return limit - len(_dropped(length))


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _fit(head: str, lines: list[str], budget: int) -> str:
    """`head` plus as many of `lines` as fit, dropping the middle and saying how many.

    The head and tail of the sequence are what a description is built from — how the work
    started and how it ended. The middle is where a long grind repeats itself.
    """
    whole = "\n".join([head, *lines])
    if len(whole) <= budget:
        return whole
    if len(head) >= budget:
        return head[:budget]
    # The longest the marker can be, so the room reserved for it is always enough.
    room = budget - len(head) - 1 - len(_elision(len(lines), len(lines))) - 1
    kept_head: list[str] = []
    kept_tail: list[str] = []
    low, high, from_head = 0, len(lines) - 1, True
    while low <= high:
        index = low if from_head else high
        if len(lines[index]) + 1 > room:
            # One end no longer fits; try the other before giving up.
            other = high if from_head else low
            if low == high or len(lines[other]) + 1 > room:
                break
            from_head = not from_head
            continue
        room -= len(lines[index]) + 1
        if from_head:
            kept_head.append(lines[low])
            low += 1
        else:
            kept_tail.insert(0, lines[high])
            high -= 1
        from_head = not from_head
    return "\n".join([head, *kept_head, _elision(high - low + 1, len(lines)), *kept_tail])


def _elision(elided: int, total: int) -> str:
    return f"[… {elided} of {total} lines elided …]"
