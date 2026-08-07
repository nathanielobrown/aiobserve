"""What each level sends the model: the rows it is built from, and the text they render to.

The renders are pure — rows in, prompt text out — so their evidence is a real store built
from the recorded fixtures rather than a client and a network. Every size limit is a
parameter with a default here, because a redacted fixture is two orders of magnitude short
of the real budgets and elision could not otherwise be tested at all.
"""

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from anthropic.types import ToolParam

from aiobserve.enrich.taxonomy import (
    CATEGORY_DEFINITIONS,
    OUTCOME_DEFINITIONS,
    Category,
    Outcome,
)

# The one tool the model may call. Both clients force it, so an answer is a JSON object or
# it is a failure — there is no prose to parse.
OUTPUT_TOOL_NAME = "record_enrichment"


class Level(StrEnum):
    """The three things that get an enrichment row, each with its own table and prompt."""

    turn = "turn"
    agent_run = "agent_run"
    session = "session"


# Per level, covering what `input_hash` cannot see: the instructions and the output schema.
# Bump one and that level re-enriches; its parents follow through the hash.
PROMPT_VERSION: dict[Level, int] = {Level.turn: 1, Level.agent_run: 1, Level.session: 1}

# What each level is looking at. The rest of the instructions is the same everywhere, so a
# level reads differently only where it should.
_SUBJECT: dict[Level, str] = {
    Level.turn: (
        "You are reading one turn of a coding session: what the person asked for, and what "
        "the agent did about it. Describe that turn."
    )
}

_ANSWER = f"""Answer by calling `{OUTPUT_TOOL_NAME}` once, and say nothing else:

- description: one or two sentences saying what was done, and to what. Name the files, \
commands and subjects concretely. Do not judge the work, and do not restate the category
- category: exactly one of the categories below
- outcome: exactly one of the outcomes below
- friction: one line naming visible struggle — a command that kept failing, a wrong turn \
taken and undone, a tool that would not answer — or null when the records show none

Never quote a credential, key or token, whatever it appears in. Never copy code or file \
contents into the description."""

# The tool the answer arrives as. Its input schema *is* the output contract, so the model
# cannot answer out of vocabulary in the first place — and an edit here is a
# `PROMPT_VERSION` bump, since `input_hash` cannot see it.
OUTPUT_TOOL = ToolParam(
    name=OUTPUT_TOOL_NAME,
    description="Record what the item you just read was doing.",
    input_schema={
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
    },
)


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


def input_hash(rendered: str) -> str:
    """The staleness hash: the rendered content and nothing else.

    Not the instructions and not the output schema — `PROMPT_VERSION` covers those, so an
    instruction edit does not have to pretend the content changed.
    """
    return hashlib.sha256(rendered.encode()).hexdigest()


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
    return line


def _cap(text: str, limit: int) -> str:
    """The head of `text`, saying how much was left behind."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}[+{len(text) - limit} chars]"


def _tail(text: str, limit: int) -> str:
    """The end of `text` — where an error message says what failed."""
    if len(text) <= limit:
        return text
    return f"[+{len(text) - limit} chars]{text[-limit:]}"


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
