"""What every level sends the model: the shared instructions, and each level's render.

The renders are pure — items in (`enrich/items.py`), prompt text out — so their evidence is a
real store built from the recorded fixtures rather than a client and a network. Every size
limit is a parameter rather than a constant read here, because a redacted fixture is two
orders of magnitude short of the real budgets and elision could not otherwise be tested at
all; `enrich/levels.py` holds the budgets a pass really runs on.
"""

import re
from collections.abc import Sequence

from hyphae.enrich.items import (
    AgentRunItem,
    ApiCallRow,
    Budgets,
    SessionChild,
    SessionItem,
    ToolCallRow,
    TurnItem,
)
from hyphae.enrich.taxonomy import (
    CATEGORY_DEFINITIONS,
    OUTCOME_DEFINITIONS,
    Category,
    Outcome,
)

_ANSWER = """Answer with one JSON object recording what you just read, and say nothing else:

- description: one or two sentences saying what was done, and to what. Name the files, \
commands and subjects concretely. Do not judge the work, and do not restate the category
- category: exactly one of the categories below
- outcome: exactly one of the outcomes below
- friction: one line naming visible struggle — a command that kept failing, a wrong turn \
taken and undone, a tool that would not answer — or null when the records show none

Never quote a credential, key or token, whatever it appears in. Never copy code or file \
contents into the description."""

# The ties a QC pass over described items found the model getting wrong, each settled the way
# the sampled records read. Guidance rather than vocabulary: editing `CATEGORY_DEFINITIONS`
# would record a taxonomy change that did not happen, and cost a `TAXONOMY_VERSION` bump that
# would make every stored row incomparable rather than merely stale.
_CHOOSING = """Choosing between them:

- implement over design when the item produced the working thing. design is for work that \
produced a decision or a plan for one, even when that plan is written down
- configure for a turn the CLI handled by itself — /model, /effort, /clear — which changes \
how the agent is set up, not what it is working on
- review over debug when the work judges a change someone else made, even while it hunts \
defects in that change
- the records say how each item ended. end_turn means the model finished its answer: do not \
report partial or failed unless the records name what did not land
- tool_use means the last call asked for a tool and the records stop there, so the item did \
not finish. Name what did not land, and call it abandoned, not completed"""

# A session render is one line per child, each written by an earlier pass over the records; a
# QC pass found the model reading those lines as a plan and reporting that the session did
# what it set out to do. Carried by `Level.session` alone, which `enrich/levels.py` says.
RELAYING = """Each line below is another reader's description of one thing the session did. \
Say what those lines say happened, not what the session set out to do. Do not name a result \
no line names: if a line says a cause was found, the session found a cause — it did not fix \
it."""

# The output contract itself: passed to `--json-schema`, so the model cannot answer out of
# vocabulary in the first place. An edit here is a `prompt_version` bump on every level,
# since `input_hash` cannot see it.
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


def instructions(subject: str, *riders: str) -> str:
    """The system prompt: what to read, how to answer, the vocabulary, and how to choose.

    `subject` says what the level is looking at and `riders` are the paragraphs it alone
    carries; `enrich/levels.py` holds both. Everything between them is the same at every level.
    """
    vocabulary = "\n".join(
        [
            "Categories:",
            *(f"- {member}: {text}" for member, text in CATEGORY_DEFINITIONS.items()),
            "",
            "Outcomes:",
            *(f"- {member}: {text}" for member, text in OUTCOME_DEFINITIONS.items()),
        ]
    )
    return "\n\n".join([subject, _ANSWER, vocabulary, _CHOOSING, *riders])


def _ended_line(calls: Sequence[ApiCallRow]) -> str:
    """How an item ended, in the one line that keeps the model from inferring it.

    Last, once, and never per response: `tool_use` is what a call requesting a tool always
    says, so 51 of 69 recorded values would be noise beside every response.
    """
    if not calls:
        return "## Ended: no model response"
    reason = calls[-1].stop_reason
    return f"## Ended: {reason if reason is not None else 'not recorded'}"


def _command_result_block(result: str | None, budgets: Budgets) -> str:
    """What the CLI printed, or which of the two ways it printed nothing.

    Three deliberately distinguished states: an unsaid one reads as an unanswered command,
    which is the inference this block exists to remove.
    """
    if result is None:
        return "## Command result: not recorded"
    if not result:
        return "## Command result: the command printed nothing"
    return f"## Command result\n{_cap(result, budgets.command_result)}"


def render_turn(item: TurnItem, budgets: Budgets) -> str:
    """One main turn as the model sees it: what was asked, and what the session then did."""
    head = ["# Main turn", ""]
    if item.command_name is not None:
        # The `prompt` column keeps the command tags; forwarding it would spend budget on
        # markup and read as content.
        head += ["## Command", " ".join(filter(None, (item.command_name, item.command_args)))]
        head += ["", _command_result_block(item.command_result, budgets)]
    else:
        head += ["## Prompt", _cap(item.prompt, budgets.prompt)]
    lines: list[str] = []
    for call in item.api_calls:
        lines += ["", "## Response"]
        text = _cap(call.text.strip(), budgets.text)
        if text:
            lines.append(text)
        lines += [_tool_line(tool, budgets) for tool in call.tool_calls]
    # In the elidable sequence, not the head: `_fit` protects both of its ends, so the line
    # survives elision without spending head budget a long turn needs.
    lines += ["", _ended_line(item.api_calls)]
    return _fit("\n".join(head), lines, budgets.total)


def render_run(item: AgentRunItem, budgets: Budgets) -> str:
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
    # Once, after the last section — the run's last call, wherever it sat.
    lines += ["", _ended_line([call for section in item.sections for call in section.api_calls])]
    return _fit("\n".join(head), lines, budgets.total)


def render_session(item: SessionItem, budgets: Budgets) -> str:
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
