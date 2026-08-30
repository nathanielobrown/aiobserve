"""What one transcript holds: the file read into lines, and what those lines say.

One thread at a time — the session's own transcript or a subagent's — and no knowledge of
which files make up a session (`extract/layout.py`) or of how a refresh is driven
(`extract/claude_code.py`). What a record *is* lives here and nowhere else: `_read` parses a
file into lines and rejects any shape outside the registries, `_resolve_duplicates` collapses a
uuid the file wrote twice, and `_parse` turns what survives into turns, api calls, tool calls
and compactions.

Every field name these readers reach for is Claude Code's own, and the meaning of each is
declared on a model in `extract/records/` with the session that proved it (`docs/schema.md`).
"""

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any, NamedTuple

from hyphae.extract.pricing import SYNTHETIC_MODEL, TokenUsage, compute_cost
from hyphae.extract.records.registry import (
    AdvisorResult,
    ArchiveRecordType,
    ContentBlock,
    MachineTag,
    RecordType,
    ResultBlock,
    SystemSubtype,
    TranscriptSchemaError,
    TurnTag,
)
from hyphae.model import (
    MAIN_SOURCE,
    ApiCall,
    Compaction,
    PrLink,
    RawRecord,
    Session,
    ToolCall,
    Turn,
)

# A leading tag, with or without attributes: `<teammate-message teammate_id="...">` names
# who sent it, so the name ends at whitespace as well as at the closing bracket.
_LEADING_TAG = re.compile(r"<([A-Za-z0-9_-]+)(?=[\s>])")
_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Line:
    """One line of a transcript, parsed but not yet interpreted."""

    line_no: int
    record: dict[str, Any]
    raw: str

    @property
    def uuid(self) -> str | None:
        """The record's own id, absent on the bookkeeping types — a documented absence."""
        return self.record.get("uuid")


class _Parsed(NamedTuple):
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


def _read(path: Path, session_id: str) -> list[_Line]:
    """Every line of a transcript, parsed as JSON.

    Split on "\\n" rather than `splitlines()`: real records contain U+2028 and U+2029
    inside string values, which `splitlines()` treats as line breaks and so cuts records
    in half.

    A transcript read while Claude Code is writing it can end mid-record. That last line is
    dropped with a warning, because the session is live rather than corrupt and the next
    refresh will pick it up whole. Anywhere earlier, unparseable JSON is real damage and
    stops the run.
    """
    raws = path.read_text().split("\n")
    lines = []
    for line_no, raw in enumerate(raws, start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as error:
            if any(later.strip() for later in raws[line_no:]):
                raise TranscriptSchemaError(
                    f"Unparseable record in session {session_id}, line {line_no}"
                ) from error
            logger.warning(
                "Session %s: dropped an incomplete final line (%d), still being written",
                session_id,
                line_no,
            )
            continue
        _check_type(record, session_id, line_no)
        lines.append(_Line(line_no=line_no, record=record, raw=raw))
    return lines


def _resolve_duplicates(lines: list[_Line], session_id: str) -> list[_Line]:
    """Collapse repeated uuids to their last occurrence.

    A rewind or an in-file fork rewrites a record's envelope under the uuid it already
    used. The last write is the state the session continued from. A rewrite that changes
    what was *said* is a different animal and stops the run.
    """
    last_at: dict[str, int] = {}
    for index, line in enumerate(lines):
        # Bookkeeping types carry no uuid — a documented absence, and nothing to dedup.
        if line.uuid is None:
            continue
        if line.uuid in last_at and _content(lines[last_at[line.uuid]]) != _content(line):
            raise TranscriptSchemaError(
                f"Duplicate uuid {line.uuid} with differing message content in session "
                f"{session_id}, lines {lines[last_at[line.uuid]].line_no} and {line.line_no}"
            )
        last_at[line.uuid] = index
    survivors = set(last_at.values())
    return [line for index, line in enumerate(lines) if line.uuid is None or index in survivors]


def _parse(lines: list[_Line], session_id: str, source: str, replayed: set[int]) -> _Parsed:
    """Turns, calls and tools from one transcript, keyed to the source that wrote it.

    `replayed` holds the line numbers this transcript copied from an earlier one; a row
    opened by such a line is a replay of that transcript's work.
    """
    turns, turn_by_line = _turns(lines, session_id, source, replayed)
    return _Parsed(
        turns=turns,
        api_calls=_api_calls(lines, turn_by_line, session_id, source, replayed),
        tool_calls=_tool_calls(lines, session_id, source, replayed),
        compactions=_compactions(lines, session_id, source, replayed),
    )


def _check_type(record: dict[str, Any], session_id: str, line_no: int) -> None:
    """Reject a record type, `system` subtype or content block outside the registries."""
    kind = record["type"]
    if kind == RecordType.SYSTEM:
        subtype = record["subtype"]
        if subtype not in SystemSubtype:
            raise TranscriptSchemaError(
                f"Unknown system subtype {subtype!r} in session {session_id}, line {line_no}"
            )
        return
    if kind not in RecordType and kind not in ArchiveRecordType:
        raise TranscriptSchemaError(
            f"Unknown record type {kind!r} in session {session_id}, line {line_no}"
        )
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    # A string content is a bare prompt, which has no blocks to register.
    for block in content if isinstance(content, list) else []:
        if block["type"] not in ContentBlock:
            raise TranscriptSchemaError(
                f"Unknown content block {block['type']!r} in session {session_id}, line {line_no}"
            )


def _content(line: _Line) -> Any:
    """What the record said, if it said anything — the one field a duplicate may not change."""
    message = line.record.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _raw_record(session_id: str, source: str, line: _Line) -> RawRecord:
    return RawRecord(
        session_id=session_id,
        source=source,
        line_no=line.line_no,
        uuid=line.uuid,
        timestamp=_timestamp(line.record),
        type=line.record["type"],
        raw=line.raw,
    )


def _timestamp(record: dict[str, Any]) -> datetime | None:
    """A record's timestamp. Absent on the bookkeeping types, which carry no time at all."""
    moment = record.get("timestamp")
    return datetime.fromisoformat(moment) if moment else None


def _session(lines: list[_Line], session_id: str, transcript: Path) -> Session:
    """Session metadata, gathered from the records that carry it."""
    # The file opens on bookkeeping records with no `cwd`, so the first record that has
    # one is what says where this session ran.
    context = next((line.record for line in lines if "cwd" in line.record), None)
    moments = [t for t in (_timestamp(line.record) for line in lines) if t is not None]
    active_ms = sum(
        line.record["durationMs"]
        for line in lines
        if line.record["type"] == RecordType.SYSTEM
        and line.record["subtype"] == SystemSubtype.TURN_DURATION
    )
    return Session(
        id=session_id,
        project_dir=context["cwd"] if context else None,
        # Absent when the project is not a git repository.
        git_branch=context.get("gitBranch") if context else None,
        version=context["version"] if context else None,
        # Absent on sessions older than the field — the corpus has 1.0.128 sessions.
        entrypoint=context.get("entrypoint") if context else None,
        started_at=min(moments) if moments else None,
        ended_at=max(moments) if moments else None,
        active_ms=active_ms,
        transcript_path=str(transcript),
        # Claude Code appends a fresh title record on every rename, so the last one is the
        # name the session ended up with. Both spellings are current; 13 of the 398 titled
        # mycelia sessions hold both (scanned 2026-08-07), and there the operator's wins.
        title=_last_field(lines, RecordType.CUSTOM_TITLE, "customTitle")
        or _last_field(lines, RecordType.AI_TITLE, "aiTitle"),
        agent_name=_last_field(lines, RecordType.AGENT_NAME, "agentName"),
    )


def _last_field(lines: list[_Line], kind: RecordType, field: str) -> str | None:
    """The last value of a single-field record type, or None when the file holds none."""
    values = [line.record[field] for line in lines if line.record["type"] == kind]
    return values[-1] if values else None


def _compactions(
    lines: list[_Line], session_id: str, source: str, replayed: set[int]
) -> list[Compaction]:
    """Every point this transcript summarised itself to free context.

    Each boundary is written alongside the summary that replaced the history, so one row
    here is one `isCompactSummary` user record in the same file.
    """
    return [
        Compaction(
            id=line.record["uuid"],
            session_id=session_id,
            source=source,
            timestamp=_required_timestamp(line, session_id),
            trigger=line.record["compactMetadata"]["trigger"],
            pre_tokens=line.record["compactMetadata"]["preTokens"],
            post_tokens=line.record["compactMetadata"]["postTokens"],
            duration_ms=line.record["compactMetadata"]["durationMs"],
            replayed=line.line_no in replayed,
        )
        for line in lines
        if line.record["type"] == RecordType.SYSTEM
        and line.record["subtype"] == SystemSubtype.COMPACT_BOUNDARY
    ]


def _pr_links(lines: list[_Line], session_id: str) -> list[PrLink]:
    """Every pull request the session recorded touching.

    These records carry no uuid, and a session that pushes repeatedly links the same PR
    once per push, so the line number is what separates two of them.
    """
    return [
        PrLink(
            session_id=session_id,
            line_no=line.line_no,
            pr_number=line.record["prNumber"],
            pr_url=line.record["prUrl"],
            pr_repository=line.record["prRepository"],
            timestamp=_required_timestamp(line, session_id),
        )
        for line in lines
        if line.record["type"] == RecordType.PR_LINK
    ]


def _fork_context(lines: list[_Line]) -> str | None:
    """The record a by-reference fork continues from, when its file opens on one.

    Only that variant carries it: a fork that copied its history states the same thing by
    holding the records themselves. Every one of the 25 in the corpus leads the file
    (scanned 2026-08-07), but the search does not depend on that.
    """
    for line in lines:
        if line.record["type"] == RecordType.FORK_CONTEXT_REF:
            return line.record["parentLastUuid"]
    return None


def _workflow_launches(lines: list[_Line]) -> dict[str, str]:
    """Which tool call launched each fan-out: `runId` from the result, to its call's id.

    A `Workflow` call answers with the run it started, and the run id is the name of the
    directory its agents write into — the only join between a fan-out's transcripts and the
    call that asked for them.
    """
    launches = {}
    for line in lines:
        details = line.record.get("toolUseResult")
        if not isinstance(details, dict) or "runId" not in details:
            continue
        for block in line.record["message"]["content"]:
            if block["type"] == ContentBlock.TOOL_RESULT:
                launches[details["runId"]] = block["tool_use_id"]
    return launches


def _turns(
    lines: list[_Line], session_id: str, source: str, replayed: set[int]
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
            open_turn = line.record["uuid"]
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
                    started_at=_required_timestamp(line, session_id),
                    # Replaced below, once the turn's span is known.
                    ended_at=_required_timestamp(line, session_id),
                    replayed=line.line_no in replayed,
                )
            )
        turn_by_line[line.line_no] = open_turn
        moment = _timestamp(line.record)
        if open_turn is not None and moment is not None:
            spans[open_turn].append(moment)
    # Every span holds at least the prompt's own timestamp, which the loop above required.
    return [replace(turn, ended_at=max(spans[turn.id])) for turn in turns], turn_by_line


def _required_timestamp(line: _Line, session_id: str) -> datetime:
    moment = _timestamp(line.record)
    if moment is None:
        raise TranscriptSchemaError(
            f"Session {session_id}, line {line.line_no}: a prompt record with no timestamp"
        )
    return moment


def _prompt(line: _Line, session_id: str, source: str) -> _Prompt | None:
    """Whether this record opens a turn, and the prompt if it does.

    The filters run before the tag registry: `isMeta` records carry tags of their own that
    the registry deliberately does not list.
    """
    record = line.record
    if record["type"] != RecordType.USER:
        return None
    # These flags are absent on ordinary prompts — absence means "no". `isSidechain` marks
    # delegated work, and excludes it only in the main transcript, where the subagent's own
    # file states it better; inside that file every record is sidechain.
    if record.get("isMeta") or record.get("isCompactSummary"):
        return None
    if source == MAIN_SOURCE and record.get("isSidechain"):
        return None
    content = record["message"]["content"]
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


def _block_prompt(blocks: list[dict[str, Any]]) -> _Prompt | None:
    """A block-content user record is a prompt unless it is carrying a tool result back."""
    kinds = {block["type"] for block in blocks}
    if ContentBlock.TOOL_RESULT in kinds:
        return None
    if not kinds & {ContentBlock.TEXT, ContentBlock.IMAGE}:
        return None
    text = "".join(block["text"] for block in blocks if block["type"] == ContentBlock.TEXT)
    return _Prompt(text=text, command_name=None, command_args=None)


def _captured(pattern: re.Pattern[str], content: str) -> str | None:
    match = pattern.search(content)
    return match.group(1).strip() if match else None


def _api_calls(
    lines: list[_Line],
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
    chunks: dict[str, list[_Line]] = {}
    for line in lines:
        if line.record["type"] != RecordType.ASSISTANT:
            continue
        chunks.setdefault(line.record["message"]["id"], []).append(line)
    calls = []
    for index, (message_id, group) in enumerate(chunks.items()):
        first, last = group[0], group[-1]
        # The chunks of one message repeat the same usage; the last is the file's final word.
        message = last.record["message"]
        usage = message["usage"]
        # None when the record this call answers is not in this file — after a compaction,
        # or in a fork that opens mid-conversation.
        answered = at_uuid.get(first.record["parentUuid"])
        split = usage.get("cache_creation")
        tokens = TokenUsage(
            input=usage["input_tokens"],
            output=usage["output_tokens"],
            cache_read=usage["cache_read_input_tokens"],
            cache_creation=usage["cache_creation_input_tokens"],
            cache_5m=split["ephemeral_5m_input_tokens"] if split else None,
            cache_1h=split["ephemeral_1h_input_tokens"] if split else None,
        )
        calls.append(
            ApiCall(
                id=message_id,
                session_id=session_id,
                source=source,
                turn_id=turn_by_line[first.line_no],
                index=index,
                model=message["model"],
                fallback_from=_fallback_from(group),
                effort=last.record.get("effort"),
                stop_reason=message["stop_reason"],
                # Present only while a skill was driving.
                attribution_skill=last.record.get("attributionSkill"),
                request_id=last.record.get("requestId"),
                started_at=_required_timestamp(answered or first, session_id),
                ended_at=_required_timestamp(last, session_id),
                input_tokens=tokens.input,
                output_tokens=tokens.output,
                cache_read_tokens=tokens.cache_read,
                cache_creation_tokens=tokens.cache_creation,
                cache_5m_tokens=tokens.cache_5m,
                cache_1h_tokens=tokens.cache_1h,
                cost_usd=compute_cost(message["model"], tokens),
                synthetic=message["model"] == SYNTHETIC_MODEL,
                text=_blocks(group, ContentBlock.TEXT, "text"),
                thinking=_blocks(group, ContentBlock.THINKING, "thinking"),
                # The message belongs to whichever transcript wrote it first, so its first
                # chunk decides — a fork copies a message whole.
                replayed=first.line_no in replayed,
            )
        )
    return calls


def _fallback_from(group: Iterable[_Line]) -> str | None:
    """The model this message was first asked of, when Claude Code retried on another.

    A `fallback` block names both ends; the one that answered is already `message.model`,
    which every recorded fallback agrees with, so only the model asked for first is new.
    """
    for line in group:
        for block in line.record["message"]["content"]:
            if block["type"] == ContentBlock.FALLBACK:
                return block["from"]["model"]
    return None


def _tool_calls(
    lines: list[_Line], session_id: str, source: str, replayed: set[int]
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
        (line, block)
        for line in lines
        if line.record["type"] == RecordType.ASSISTANT
        for block in line.record["message"]["content"]
        if block["type"] in (ContentBlock.TOOL_USE, ContentBlock.SERVER_TOOL_USE)
    ]
    # A batch is the *records* a message issued its calls from, not the calls: several
    # `tool_use` blocks in one record were issued together and share that record's real
    # timestamp, so counting blocks would call a measured start synthetic (both shapes are
    # recorded — `docs/schema.md`, `tool_use block`).
    batches: dict[str, dict[int, datetime]] = {}
    for line, block in issued:
        if block["type"] == ContentBlock.TOOL_USE:
            message_id = line.record["message"]["id"]
            batches.setdefault(message_id, {})[line.line_no] = _required_timestamp(line, session_id)
    calls = []
    for index, (line, block) in enumerate(issued):
        server_side = block["type"] == ContentBlock.SERVER_TOOL_USE
        batch = list(batches[line.record["message"]["id"]].values()) if not server_side else []
        result = results.get(block["id"])
        calls.append(
            ToolCall(
                id=block["id"],
                session_id=session_id,
                source=source,
                api_call_id=line.record["message"]["id"],
                index=index,
                name=block["name"],
                server_side=server_side,
                input=json.dumps(block["input"]),
                result=result.text if result else None,
                offload_file=result.offload_file if result else None,
                is_error=result.is_error if result else False,
                incomplete=result is None,
                started_at=_required_timestamp(line, session_id) if server_side else min(batch),
                ended_at=result.ended_at if result else None,
                duration_synthetic=len(batch) > 1,
                # The issuing record decides: a fork copies the call and its answer together.
                replayed=line.line_no in replayed,
            )
        )
    return calls


def _tool_results(lines: list[_Line], session_id: str) -> dict[str, _Result]:
    """What each tool said back, keyed by the call it answered.

    A rewind can record an answer twice under one call id; the later record wins, as it
    does everywhere else in the file.
    """
    results: dict[str, _Result] = {}
    for line in lines:
        record = line.record
        if record["type"] != RecordType.USER:
            continue
        content = record["message"]["content"]
        if not isinstance(content, list):
            continue
        # Present on tool-result records only, and a string on a few older ones, which
        # carry no offload pointer.
        details = record.get("toolUseResult")
        path = details.get("persistedOutputPath") if isinstance(details, dict) else None
        for block in content:
            if block["type"] != ContentBlock.TOOL_RESULT:
                continue
            results[block["tool_use_id"]] = _Result(
                text=_result_text(block["content"], session_id, line.line_no),
                # Absent on most results — absence means the tool succeeded.
                is_error=bool(block.get("is_error")),
                offload_file=PurePath(path).name if path else None,
                ended_at=_timestamp(record),
            )
    return results


def _advisor_results(lines: list[_Line], session_id: str) -> dict[str, _Result]:
    """What each server-side tool said back, keyed by the call it answered.

    Unlike a local tool, the answer rides inside the same assistant message as the request,
    and it is never readable: a refusal names its error code, and a completed call comes
    back encrypted. So the row records that the advisor answered, and what it cost in time.
    """
    results: dict[str, _Result] = {}
    for line in lines:
        if line.record["type"] != RecordType.ASSISTANT:
            continue
        for block in line.record["message"]["content"]:
            if block["type"] != ContentBlock.ADVISOR_TOOL_RESULT:
                continue
            content = block["content"]
            kind = content["type"]
            if kind not in AdvisorResult:
                raise TranscriptSchemaError(
                    f"Unknown advisor result {kind!r} in session {session_id}, line {line.line_no}"
                )
            error = kind == AdvisorResult.ERROR
            results[block["tool_use_id"]] = _Result(
                text=content["error_code"] if error else None,
                is_error=error,
                offload_file=None,
                ended_at=_timestamp(line.record),
            )
    return results


def _result_text(content: Any, session_id: str, line_no: int) -> str:
    """A result flattened to text, whether it was recorded as a string or as blocks."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        kind = block["type"]
        if kind not in ResultBlock:
            raise TranscriptSchemaError(
                f"Unknown tool result block {kind!r} in session {session_id}, line {line_no}"
            )
        if kind == ResultBlock.TEXT:
            parts.append(block["text"])
    return "".join(parts)


def _blocks(group: Iterable[_Line], kind: ContentBlock, field: str) -> str:
    """Every block of one kind across a message's records, concatenated in order.

    `field` is the block's own key for the text it carries, which is not the kind: a
    `thinking` block holds its words at `thinking`, a `text` block at `text`.
    """
    return "".join(
        block[field]
        for line in group
        for block in line.record["message"]["content"]
        if block["type"] == kind
    )
