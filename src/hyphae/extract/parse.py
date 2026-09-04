"""One transcript's lines as the session's entities: turns, api calls, tool calls, compactions.

`parse` drives the four readers below, each walking the same lines for what it is looking for.
The lines arrive already validated against their record models (`extract/transcript.py`), so a
reader here reads attributes and never guesses at a shape.

Every field these readers reach for is declared on a model in `extract/records/` with the
session that proved it (`docs/schema.md`).
"""

import json
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePath
from typing import NamedTuple

from hyphae.extract.errors import TranscriptSchemaError
from hyphae.extract.pricing import SYNTHETIC_MODEL, TokenUsage, compute_cost
from hyphae.extract.records.blocks import (
    AdvisorToolResultBlock,
    Block,
    FallbackBlock,
    ResultPart,
    ServerToolUseBlock,
    TextBlock,
    TextResult,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from hyphae.extract.records.conversation import AssistantRecord, UserRecord
from hyphae.extract.records.messages import AssistantMessage, ToolUseResult
from hyphae.extract.records.registry import (
    AdvisorResult,
    ContentBlock,
    MachineTag,
    TurnTag,
)
from hyphae.extract.records.system import CompactBoundaryRecord
from hyphae.extract.transcript import Line, required, required_timestamp, timestamp_of
from hyphae.model import MAIN_SOURCE, ApiCall, Compaction, ToolCall, Turn

# A leading tag, with or without attributes: `<teammate-message teammate_id="...">` names
# who sent it, so the name ends at whitespace as well as at the closing bracket.
_LEADING_TAG = re.compile(r"<([A-Za-z0-9_-]+)(?=[\s>])")
_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


class Parsed(NamedTuple):
    """What one transcript — the session's own or a subagent's — yielded."""

    turns: list[Turn]
    api_calls: list[ApiCall]
    tool_calls: list[ToolCall]
    compactions: list[Compaction]


@dataclass(frozen=True)
class _Result:
    """What a `tool_result` record said about the call it answered."""

    # None when the answer carried nothing readable — an encrypted server-side result.
    text: str | None
    is_error: bool
    offload_file: str | None
    ended_at: datetime | None


@dataclass(frozen=True)
class _Prompt:
    """A user record that opens a turn."""

    text: str
    command_name: str | None
    command_args: str | None


class _Chunk(NamedTuple):
    """One assistant record: one block of one reply, with what every reader of it needs.

    A reply is written across as many records as it has blocks, so nothing here is the whole
    call — `_api_calls` groups the chunks that share a `call_id` into one.
    """

    line: Line
    record: AssistantRecord
    # `message.id`: the reply this record holds a piece of, and the id its `ApiCall` takes.
    call_id: str
    message: AssistantMessage
    # The record's own content list, which is the only form an assistant message is written in.
    blocks: Sequence[Block]


def _replies(lines: Iterable[Line], session_id: str) -> Iterator[_Chunk]:
    """Every assistant record of a transcript, narrowed to what a reply is read through.

    The three required fields are the ones a record cannot be placed without: with no message,
    no reply id or no content list, there is no call to group it into and no blocks to read.
    """
    for line in lines:
        record = line.record
        if not isinstance(record, AssistantRecord):
            continue
        message = required(record.message, line, session_id, "message")
        yield _Chunk(
            line=line,
            record=record,
            call_id=required(message.id, line, session_id, "message.id"),
            message=message,
            blocks=required(message.content, line, session_id, "message.content"),
        )


def parse(lines: list[Line], session_id: str, source: str, replayed: set[int]) -> Parsed:
    """Turns, calls and tools from one transcript, keyed to the source that wrote it.

    `replayed` holds the line numbers this transcript copied from an earlier one; a row
    opened by such a line is a replay of that transcript's work.
    """
    turns, turn_by_line = _turns(lines, session_id, source, replayed)
    return Parsed(
        turns=turns,
        api_calls=_api_calls(lines, turn_by_line, session_id, source, replayed),
        tool_calls=_tool_calls(lines, session_id, source, replayed),
        compactions=_compactions(lines, session_id, source, replayed),
    )


def _turns(
    lines: list[Line], session_id: str, source: str, replayed: set[int]
) -> tuple[list[Turn], dict[int, str | None]]:
    """The session's turns, and which turn each line belongs to.

    A turn runs from its prompt until the next one, so its end is the latest timestamp
    among the records in between — records are not written in timestamp order.
    """
    turn_by_line: dict[int, str | None] = {}
    open_turn: str | None = None
    spans: dict[str, list[datetime]] = {}
    turns: list[Turn] = []
    for line in lines:
        prompt = _prompt(line, session_id, source)
        if prompt is not None:
            open_turn = required(line.uuid, line, session_id, "uuid")
            spans[open_turn] = []
            turns.append(
                Turn(
                    id=open_turn,
                    session_id=session_id,
                    source=source,
                    index=len(turns),
                    prompt=prompt.text,
                    command_name=prompt.command_name,
                    command_args=prompt.command_args,
                    started_at=required_timestamp(line, session_id),
                    # Replaced below, once the turn's span is known.
                    ended_at=required_timestamp(line, session_id),
                    replayed=line.line_no in replayed,
                )
            )
        turn_by_line[line.line_no] = open_turn
        moment = timestamp_of(line.record)
        if open_turn is not None and moment is not None:
            spans[open_turn].append(moment)
    # Every span holds at least the prompt's own timestamp, which the loop above required.
    return [replace(turn, ended_at=max(spans[turn.id])) for turn in turns], turn_by_line


def _prompt(line: Line, session_id: str, source: str) -> _Prompt | None:
    """Whether this record opens a turn, and the prompt if it does.

    The filters run before the tag registry: `isMeta` records carry tags of their own that
    the registry deliberately does not list.
    """
    record = line.record
    if not isinstance(record, UserRecord):
        return None
    # These flags are absent on ordinary prompts — absence means "no". `isSidechain` marks
    # delegated work, and excludes it only in the main transcript, where the subagent's own
    # file states it better; inside that file every record is sidechain.
    if record.isMeta or record.isCompactSummary:
        return None
    if source == MAIN_SOURCE and record.isSidechain:
        return None
    message = required(record.message, line, session_id, "message")
    content = required(message.content, line, session_id, "message.content")
    if isinstance(content, list):
        return _block_prompt(content)
    text = content.strip()
    if not text:
        return None
    if not text.startswith("<"):
        return _Prompt(text=content, command_name=None, command_args=None)
    match = _LEADING_TAG.match(text)
    tag = match.group(1) if match else text[:40]
    if tag in MachineTag:
        return None
    if tag not in TurnTag:
        raise TranscriptSchemaError(
            f"Unknown leading prompt tag <{tag}> in session {session_id}, line {line.line_no}"
        )
    return _Prompt(
        text=content,
        command_name=_captured(_COMMAND_NAME, content),
        command_args=_captured(_COMMAND_ARGS, content),
    )


def _block_prompt(blocks: Sequence[Block]) -> _Prompt | None:
    """A block-content user record is a prompt unless it is carrying a tool result back."""
    kinds = {block.BLOCK for block in blocks}
    if ContentBlock.TOOL_RESULT in kinds:
        return None
    if not kinds & {ContentBlock.TEXT, ContentBlock.IMAGE}:
        return None
    text = "".join(block.text or "" for block in blocks if isinstance(block, TextBlock))
    return _Prompt(text=text, command_name=None, command_args=None)


def _captured(pattern: re.Pattern[str], content: str) -> str | None:
    match = pattern.search(content)
    return match.group(1).strip() if match else None


def _api_calls(
    lines: list[Line],
    turn_by_line: dict[int, str | None],
    session_id: str,
    source: str,
    replayed: set[int],
) -> list[ApiCall]:
    """One `ApiCall` per assistant message, however many records it was written across.

    Claude Code writes one record per content block, all sharing `message.id` and chained
    by `parentUuid`, interleaved with the tool results they triggered. Two thirds of the
    messages in the corpus span several records, so grouping is not optional.
    """
    at_uuid = {line.uuid: line for line in lines if line.uuid}
    chunks: dict[str, list[_Chunk]] = {}
    for chunk in _replies(lines, session_id):
        chunks.setdefault(chunk.call_id, []).append(chunk)
    calls = []
    for index, (call_id, group) in enumerate(chunks.items()):
        first, last = group[0], group[-1]
        # The chunks of one message repeat the same usage and the rest of the reply's envelope;
        # the last is the file's final word on all of it.
        model = required(last.message.model, last.line, session_id, "message.model")
        tokens = _tokens(last, session_id)
        # None when the record this call answers is not in this file — after a compaction, or
        # in a fork that opens mid-conversation — and when the reply answers nothing at all,
        # which 23 records in the store say with a null `parentUuid` (scanned 2026-09-04).
        parent = first.record.parentUuid
        answered = at_uuid.get(parent) if parent is not None else None
        calls.append(
            ApiCall(
                id=call_id,
                session_id=session_id,
                source=source,
                turn_id=turn_by_line[first.line.line_no],
                index=index,
                model=model,
                fallback_from=_fallback_from(group, session_id),
                effort=last.record.effort,
                stop_reason=last.message.stop_reason,
                # Present only while a skill was driving.
                attribution_skill=last.record.attributionSkill,
                request_id=last.record.requestId,
                started_at=required_timestamp(answered or first.line, session_id),
                ended_at=required_timestamp(last.line, session_id),
                input_tokens=tokens.input,
                output_tokens=tokens.output,
                cache_read_tokens=tokens.cache_read,
                cache_creation_tokens=tokens.cache_creation,
                cache_5m_tokens=tokens.cache_5m,
                cache_1h_tokens=tokens.cache_1h,
                cost_usd=compute_cost(model, tokens),
                synthetic=model == SYNTHETIC_MODEL,
                text=_blocks(group, TextBlock, lambda block: block.text),
                thinking=_blocks(group, ThinkingBlock, lambda block: block.thinking),
                # The message belongs to whichever transcript wrote it first, so its first
                # chunk decides — a fork copies a message whole.
                replayed=first.line.line_no in replayed,
            )
        )
    return calls


def _tokens(chunk: _Chunk, session_id: str) -> TokenUsage:
    """What one reply reported spending, as the pricing table charges it.

    Every chunk of a reply repeats the counts, so the caller passes the one it is reading from.
    A count that is missing is not a zero — it is a reply nothing can be priced from.
    """

    def counted(value: int | None, field: str) -> int:
        return required(value, chunk.line, session_id, f"message.usage.{field}")

    usage = required(chunk.message.usage, chunk.line, session_id, "message.usage")
    split = usage.cache_creation
    return TokenUsage(
        input=counted(usage.input_tokens, "input_tokens"),
        output=counted(usage.output_tokens, "output_tokens"),
        cache_read=counted(usage.cache_read_input_tokens, "cache_read_input_tokens"),
        cache_creation=counted(usage.cache_creation_input_tokens, "cache_creation_input_tokens"),
        cache_5m=split.ephemeral_5m_input_tokens if split else None,
        cache_1h=split.ephemeral_1h_input_tokens if split else None,
    )


def _fallback_from(group: Iterable[_Chunk], session_id: str) -> str | None:
    """The model this message was first asked of, when Claude Code retried on another.

    A `fallback` block names both ends; the one that answered is already `message.model`,
    which every recorded fallback agrees with, so only the model asked for first is new.
    """
    for chunk in group:
        for block in chunk.blocks:
            if isinstance(block, FallbackBlock):
                asked = required(block.from_, chunk.line, session_id, "fallback.from")
                return asked.model
    return None


def _tool_calls(
    lines: list[Line], session_id: str, source: str, replayed: set[int]
) -> list[ToolCall]:
    """Every tool the transcript asked for, paired with the record that answered it.

    A message issuing several calls usually writes one record per call, in the order Claude
    Code got round to running them — so the batch shares the earliest of those timestamps
    and says the start is synthetic, rather than reporting an execution order as a duration.

    A server-side call sits in that same stream but is not part of that batch: its record
    is the request itself, so it keeps its own start.
    """
    results = _tool_results(lines, session_id) | _advisor_results(lines, session_id)
    issued = [
        (chunk, block)
        for chunk in _replies(lines, session_id)
        for block in chunk.blocks
        if isinstance(block, ToolUseBlock | ServerToolUseBlock)
    ]
    # A batch is the *records* a message issued its calls from, not the calls: several
    # `tool_use` blocks in one record were issued together and share that record's real
    # timestamp, so counting blocks would call a measured start synthetic (both shapes are
    # recorded — `docs/schema.md`, `tool_use block`).
    batches: dict[str, dict[int, datetime]] = {}
    for chunk, block in issued:
        if isinstance(block, ToolUseBlock):
            moment = required_timestamp(chunk.line, session_id)
            batches.setdefault(chunk.call_id, {})[chunk.line.line_no] = moment
    calls = []
    for index, (chunk, block) in enumerate(issued):
        server_side = isinstance(block, ServerToolUseBlock)
        batch = [] if server_side else list(batches[chunk.call_id].values())
        tool_use_id = required(block.id, chunk.line, session_id, "tool_use.id")
        result = results.get(tool_use_id)
        calls.append(
            ToolCall(
                id=tool_use_id,
                session_id=session_id,
                source=source,
                api_call_id=chunk.call_id,
                index=index,
                name=required(block.name, chunk.line, session_id, "tool_use.name"),
                server_side=server_side,
                input=json.dumps(required(block.input, chunk.line, session_id, "tool_use.input")),
                result=result.text if result else None,
                offload_file=result.offload_file if result else None,
                is_error=result.is_error if result else False,
                incomplete=result is None,
                started_at=(
                    required_timestamp(chunk.line, session_id) if server_side else min(batch)
                ),
                ended_at=result.ended_at if result else None,
                duration_synthetic=len(batch) > 1,
                # The issuing record decides: a fork copies the call and its answer together.
                replayed=chunk.line.line_no in replayed,
            )
        )
    return calls


def _tool_results(lines: list[Line], session_id: str) -> dict[str, _Result]:
    """What each tool said back, keyed by the call it answered.

    A rewind can record an answer twice under one call id; the later record wins, as it
    does everywhere else in the file.
    """
    results: dict[str, _Result] = {}
    for line in lines:
        record = line.record
        if not isinstance(record, UserRecord):
            continue
        message = required(record.message, line, session_id, "message")
        if not isinstance(message.content, list):
            continue
        # Present on tool-result records only, and a string or a list on a few older ones,
        # which carry no offload pointer.
        details = record.toolUseResult
        path = details.persistedOutputPath if isinstance(details, ToolUseResult) else None
        for block in message.content:
            if not isinstance(block, ToolResultBlock):
                continue
            answered = required(block.tool_use_id, line, session_id, "tool_result.tool_use_id")
            results[answered] = _Result(
                text=_result_text(required(block.content, line, session_id, "tool_result.content")),
                # Absent on most results — absence means the tool succeeded.
                is_error=bool(block.is_error),
                offload_file=PurePath(path).name if path else None,
                ended_at=timestamp_of(record),
            )
    return results


def _advisor_results(lines: list[Line], session_id: str) -> dict[str, _Result]:
    """What each server-side tool said back, keyed by the call it answered.

    Unlike a local tool, the answer rides inside the same assistant message as the request,
    and it is never readable: a refusal names its error code, and a completed call comes
    back encrypted. So the row records that the advisor answered, and what it cost in time.

    An unregistered result kind never reaches here: `AdvisorContent.type` is the registry
    itself, so validating the record is what rejects one.
    """
    results: dict[str, _Result] = {}
    for chunk in _replies(lines, session_id):
        for block in chunk.blocks:
            if not isinstance(block, AdvisorToolResultBlock):
                continue
            line = chunk.line
            content = required(block.content, line, session_id, "advisor_tool_result.content")
            error = content.type == AdvisorResult.ERROR
            answered = required(block.tool_use_id, line, session_id, "advisor_tool_result.id")
            results[answered] = _Result(
                text=content.error_code if error else None,
                is_error=error,
                offload_file=None,
                ended_at=timestamp_of(chunk.record),
            )
    return results


def _result_text(content: str | Sequence[ResultPart]) -> str:
    """A result flattened to text, whether it was recorded as a string or as blocks.

    Only a `text` part carries words; an image and a tool reference carry none. A part of an
    unregistered kind never reaches here either — the union `ToolResultBlock.content` declares
    is what a block-form result validates against.
    """
    if isinstance(content, str):
        return content
    return "".join(part.text or "" for part in content if isinstance(part, TextResult))


def _blocks[B: Block](
    group: Iterable[_Chunk], kind: type[B], words: Callable[[B], str | None]
) -> str:
    """Every block of one kind across a message's records, concatenated in order.

    `words` reads the text off the block, which the kind decides rather than the caller: a
    `thinking` block holds its words at `thinking`, a `text` block at `text`. A block that
    carries none contributes nothing, which is what an empty one contributes too.
    """
    return "".join(
        words(block) or "" for chunk in group for block in chunk.blocks if isinstance(block, kind)
    )


def _compactions(
    lines: list[Line], session_id: str, source: str, replayed: set[int]
) -> list[Compaction]:
    """Every point this transcript summarised itself to free context.

    Each boundary is written alongside the summary that replaced the history, so one row
    here is one `isCompactSummary` user record in the same file.
    """
    compactions = []
    for line in lines:
        record = line.record
        if not isinstance(record, CompactBoundaryRecord):
            continue
        # All 1,510 boundaries in the store carry the object and its four numbers (scanned
        # 2026-09-04), so a boundary missing one is a shape nobody has recorded.
        meta = required(record.compactMetadata, line, session_id, "compactMetadata")
        compactions.append(
            Compaction(
                id=required(record.uuid, line, session_id, "uuid"),
                session_id=session_id,
                source=source,
                timestamp=required_timestamp(line, session_id),
                trigger=required(meta.trigger, line, session_id, "compactMetadata.trigger"),
                pre_tokens=required(meta.preTokens, line, session_id, "compactMetadata.preTokens"),
                post_tokens=required(
                    meta.postTokens, line, session_id, "compactMetadata.postTokens"
                ),
                duration_ms=required(
                    meta.durationMs, line, session_id, "compactMetadata.durationMs"
                ),
                replayed=line.line_no in replayed,
            )
        )
    return compactions
