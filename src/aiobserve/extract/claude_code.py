"""Reading Claude Code transcripts.

The record shapes here belong to Claude Code and change without notice, so this module is
closed-world on purpose: every record type, every `system` subtype, and every tag a prompt
can lead with is registered, and anything else stops the run. A type we quietly skip today
is a wrong count months from now.

What each field means, and the session that proves it, is in `docs/schema.md`.
"""

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePath
from typing import Any, NamedTuple

from aiobserve.model import (
    MAIN_SOURCE,
    ApiCall,
    OffloadFile,
    RawRecord,
    Session,
    SessionTrace,
    ToolCall,
    Turn,
)
from aiobserve.pipeline import SessionSource
from aiobserve.sessions import (
    AGENT_PREFIX,
    DEFAULT_PROJECTS_ROOT,
    JOURNAL_NAME,
    META_SUFFIX,
    SUBAGENTS_DIR,
    TOOL_RESULTS_DIR,
    TRANSCRIPT_SUFFIX,
    WORKFLOW_PREFIX,
    WORKFLOWS_DIR,
    encode_project_path,
    find_sessions,
)

EXTRACTOR_NAME = "claude_code"
# The `source` a workflow journal records under, after its `wf_<id>/` directory.
JOURNAL_SOURCE = "journal"
# Bump on any change to what this parser produces: the version is folded into every
# fingerprint, so bumping it re-extracts the whole corpus on the next refresh.
EXTRACTOR_VERSION = "3"


class TranscriptSchemaError(Exception):
    """A transcript held a shape this parser does not know.

    Never carries record content: transcripts are private, and this message reaches logs.
    """


class RecordType(StrEnum):
    """Record types this parser reads. Anything outside both registries crashes."""

    ASSISTANT = "assistant"
    USER = "user"
    SYSTEM = "system"
    CUSTOM_TITLE = "custom-title"
    # The title record before v2.1.187 renamed it.
    AI_TITLE = "ai-title"
    AGENT_NAME = "agent-name"
    PR_LINK = "pr-link"
    # Opens a by-reference fork's transcript, naming the conversation it continues.
    FORK_CONTEXT_REF = "fork-context-ref"


class ArchiveRecordType(StrEnum):
    """Record types kept verbatim in `raw_records` and read by nothing.

    Each is either bookkeeping (editor state, file backups) or content the subagent's own
    transcript states better. Archiving rather than parsing keeps them recoverable.
    """

    ATTACHMENT = "attachment"
    LAST_PROMPT = "last-prompt"
    MODE = "mode"
    PERMISSION_MODE = "permission-mode"
    BRIDGE_SESSION = "bridge-session"
    FILE_HISTORY_SNAPSHOT = "file-history-snapshot"
    FILE_HISTORY_DELTA = "file-history-delta"
    AGENT_SETTING = "agent-setting"
    QUEUE_OPERATION = "queue-operation"
    SUMMARY = "summary"
    # Worktree sessions only.
    WORKTREE_STATE = "worktree-state"
    RELOCATED = "relocated"
    # Workflow journals only (`subagents/workflows/wf_<id>/journal.jsonl`).
    STARTED = "started"
    RESULT = "result"


class SystemSubtype(StrEnum):
    """Every `system` subtype the corpus holds. An unregistered one crashes."""

    TURN_DURATION = "turn_duration"
    COMPACT_BOUNDARY = "compact_boundary"
    AWAY_SUMMARY = "away_summary"
    LOCAL_COMMAND = "local_command"
    INFORMATIONAL = "informational"
    SCHEDULED_TASK_FIRE = "scheduled_task_fire"
    API_ERROR = "api_error"
    AGENTS_KILLED = "agents_killed"
    STOP_HOOK_SUMMARY = "stop_hook_summary"


class ResultBlock(StrEnum):
    """Block kinds a block-form `tool_result` is built from. An unregistered one crashes.

    Only text carries into `ToolCall.result`: an image has none, and a tool reference names
    a tool the result pointed at rather than saying anything.
    """

    TEXT = "text"
    IMAGE = "image"
    TOOL_REFERENCE = "tool_reference"


class TurnTag(StrEnum):
    """Leading tags on a prompt string that still make it a prompt."""

    COMMAND_NAME = "command-name"
    COMMAND_MESSAGE = "command-message"
    # An instruction from another agent — drives work exactly as a user prompt does.
    # Subagent transcripts only.
    TEAMMATE_MESSAGE = "teammate-message"


class MachineTag(StrEnum):
    """Leading tags Claude Code writes to itself. Archived, never turns.

    Counting these as prompts inflates the turn count roughly 3.6x on this corpus.
    """

    TASK_NOTIFICATION = "task-notification"
    LOCAL_COMMAND_STDOUT = "local-command-stdout"
    BASH_STDOUT = "bash-stdout"
    BASH_INPUT = "bash-input"


# A leading tag, with or without attributes: `<teammate-message teammate_id="...">` names
# who sent it, so the name ends at whitespace as well as at the closing bracket.
_LEADING_TAG = re.compile(r"<([A-Za-z0-9_-]+)(?=[\s>])")
_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


@dataclass(frozen=True)
class _Line:
    """One line of a transcript, parsed but not yet interpreted."""

    line_no: int
    record: dict[str, Any]
    raw: str


class _SessionFiles(NamedTuple):
    """One session's files, sorted by what reads them."""

    transcript: Path
    # Each subagent's transcript, paired with the agentId it records under.
    agents: list[tuple[str, Path]]
    # Each workflow journal, paired with its `wf_<id>/journal` source. Archive only: the
    # runs it logs write their own transcripts.
    journals: list[tuple[str, Path]]
    offloads: list[Path]


class _Parsed(NamedTuple):
    """What one transcript — the session's own or a subagent's — yielded."""

    turns: list[Turn]
    api_calls: list[ApiCall]
    tool_calls: list[ToolCall]


@dataclass(frozen=True)
class _Result:
    """What a `tool_result` record said about the call it answered."""

    text: str
    is_error: bool
    offload_file: str | None
    ended_at: datetime | None


@dataclass(frozen=True)
class _Prompt:
    """A user record that opens a turn."""

    text: str
    command_name: str | None
    command_args: str | None


class ClaudeCodeExtractor:
    """Discovers and parses Claude Code sessions for one project."""

    def __init__(self, *, projects_root: Path = DEFAULT_PROJECTS_ROOT) -> None:
        self.projects_root = projects_root

    def sessions(self, project: Path) -> list[SessionSource]:
        """Every session recorded for `project`, with the fingerprint of its files."""
        project_dir = self.projects_root / encode_project_path(project)
        sources = []
        for session in find_sessions(project, projects_root=self.projects_root):
            files = session.files()
            sources.append(
                SessionSource(
                    id=session.id,
                    files=tuple(files),
                    fingerprint=fingerprint(files, project_dir),
                )
            )
        return sources

    def extract(self, source: SessionSource) -> SessionTrace:
        """Parse every file of one session into a trace.

        Every transcript the session wrote — its own and each subagent's — runs through the
        same parser, distinguished only by the `source` its rows carry.
        """
        files = _classify(source)
        transcripts = [(MAIN_SOURCE, files.transcript), *files.agents]
        lines = {name: _read(path, source.id) for name, path in transcripts}
        journals = {name: _read(path, source.id) for name, path in files.journals}
        # The archive keeps every line of every file, duplicates included; the normalized
        # tables below read each transcript's deduplicated view.
        raw_records = [
            _raw_record(source.id, name, line)
            for name, rows in (*lines.items(), *journals.items())
            for line in rows
        ]
        main = _resolve_duplicates(lines[MAIN_SOURCE], source.id)
        parsed = [_parse(rows, source.id, name) for name, rows in lines.items()]
        return SessionTrace(
            extractor=EXTRACTOR_NAME,
            extractor_version=EXTRACTOR_VERSION,
            session=_session(main, source.id, files.transcript),
            turns=[turn for one in parsed for turn in one.turns],
            api_calls=[call for one in parsed for call in one.api_calls],
            tool_calls=[call for one in parsed for call in one.tool_calls],
            offload_files=[_offload_file(path, source.id) for path in files.offloads],
            raw_records=raw_records,
        )


def _parse(lines: list[_Line], session_id: str, source: str) -> _Parsed:
    """Turns, calls and tools from one transcript, keyed to the source that wrote it."""
    kept = _resolve_duplicates(lines, session_id)
    turns, turn_by_line = _turns(kept, session_id, source)
    return _Parsed(
        turns=turns,
        api_calls=_api_calls(kept, turn_by_line, session_id, source),
        tool_calls=_tool_calls(kept, session_id, source),
    )


def _classify(source: SessionSource) -> _SessionFiles:
    """Sort a session's files by what reads them.

    The layout is closed-world like the record types: a file whose place we cannot name is
    a Claude Code change we need to see, not a file to skip.
    """
    transcript = _transcript_of(source)
    directory = transcript.with_suffix("")
    agents: list[tuple[str, Path]] = []
    journals: list[tuple[str, Path]] = []
    offloads: list[Path] = []
    for path in source.files:
        if path == transcript:
            continue
        parts = path.relative_to(directory).parts
        if parts[:1] == (TOOL_RESULTS_DIR,) and len(parts) == 2:
            offloads.append(path)
            continue
        name = _archived_source(parts, source.id)
        if name is None:
            continue
        (journals if name.endswith(f"/{JOURNAL_SOURCE}") else agents).append((name, path))
    return _SessionFiles(transcript=transcript, agents=agents, journals=journals, offloads=offloads)


def _archived_source(parts: tuple[str, ...], session_id: str) -> str | None:
    """The `source` name for one companion file, or None when nothing reads it.

    A subagent's transcript is sourced by its bare agentId, which its file name carries
    after the `agent-` prefix; a workflow's journal by its directory, as `wf_<id>/journal`.
    """
    unknown = TranscriptSchemaError(
        f"Session {session_id}: unknown file {'/'.join(parts)} in its directory"
    )
    # A workflow's definition and the script that ran it, beside the runs they drove.
    if parts[:1] == (WORKFLOWS_DIR,):
        return None
    workflow = None
    if parts[:2] == (SUBAGENTS_DIR, WORKFLOWS_DIR):
        if len(parts) != 4 or not parts[2].startswith(WORKFLOW_PREFIX):
            raise unknown
        workflow = parts[2]
    elif parts[:1] != (SUBAGENTS_DIR,) or len(parts) != 2:
        raise unknown
    name = parts[-1]
    # A subagent's metadata: its spawning call and its type, which agent runs read (slice 3).
    if name.endswith(META_SUFFIX):
        return None
    if workflow and name == JOURNAL_NAME:
        return f"{workflow}/{JOURNAL_SOURCE}"
    if name.startswith(AGENT_PREFIX) and name.endswith(TRANSCRIPT_SUFFIX):
        return name[len(AGENT_PREFIX) : -len(TRANSCRIPT_SUFFIX)]
    raise unknown


def _offload_file(path: Path, session_id: str) -> OffloadFile:
    """One `tool-results/` file, read whole — it is the only copy once Claude Code prunes."""
    data = path.read_bytes()
    try:
        return OffloadFile(
            session_id=session_id,
            name=path.name,
            content=data.decode(),
            lossy_decode=False,
            size_bytes=len(data),
        )
    except UnicodeDecodeError:
        # Not text at all — a fetched PDF — or text cut mid-character. Archived anyway:
        # the file is gone in a few weeks, and its size and name still say what ran.
        return OffloadFile(
            session_id=session_id,
            name=path.name,
            content=data.decode(errors="replace"),
            lossy_decode=True,
            size_bytes=len(data),
        )


def _transcript_of(source: SessionSource) -> Path:
    """The session's own transcript, among the files discovery collected."""
    name = f"{source.id}{TRANSCRIPT_SUFFIX}"
    for path in source.files:
        if path.name == name:
            return path
    raise TranscriptSchemaError(f"Session {source.id}: no {name} among its files")


def _read(path: Path, session_id: str) -> list[_Line]:
    """Every line of a transcript, parsed as JSON.

    Split on "\\n" rather than `splitlines()`: real records contain U+2028 and U+2029
    inside string values, which `splitlines()` treats as line breaks and so cuts records
    in half.
    """
    lines = []
    for line_no, raw in enumerate(path.read_text().split("\n"), start=1):
        if not raw.strip():
            continue
        record = json.loads(raw)
        _check_type(record, session_id, line_no)
        lines.append(_Line(line_no=line_no, record=record, raw=raw))
    return lines


def _check_type(record: dict[str, Any], session_id: str, line_no: int) -> None:
    """Reject a record type or `system` subtype outside the registries."""
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


def _resolve_duplicates(lines: list[_Line], session_id: str) -> list[_Line]:
    """Collapse repeated uuids to their last occurrence.

    A rewind or an in-file fork rewrites a record's envelope under the uuid it already
    used. The last write is the state the session continued from. A rewrite that changes
    what was *said* is a different animal and stops the run.
    """
    last_at: dict[str, int] = {}
    for index, line in enumerate(lines):
        # Bookkeeping types carry no uuid — a documented absence, and nothing to dedup.
        uuid = line.record.get("uuid")
        if uuid is None:
            continue
        if uuid in last_at and _content(lines[last_at[uuid]]) != _content(line):
            raise TranscriptSchemaError(
                f"Duplicate uuid {uuid} with differing message content in session "
                f"{session_id}, lines {lines[last_at[uuid]].line_no} and {line.line_no}"
            )
        last_at[uuid] = index
    survivors = set(last_at.values())
    return [
        line
        for index, line in enumerate(lines)
        if line.record.get("uuid") is None or index in survivors
    ]


def _content(line: _Line) -> Any:
    """What the record said, if it said anything — the one field a duplicate may not change."""
    message = line.record.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _raw_record(session_id: str, source: str, line: _Line) -> RawRecord:
    return RawRecord(
        session_id=session_id,
        source=source,
        line_no=line.line_no,
        uuid=line.record.get("uuid"),
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
    )


def _turns(
    lines: list[_Line], session_id: str, source: str
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
    if "tool_result" in kinds:
        return None
    if not kinds & {"text", "image"}:
        return None
    text = "".join(block["text"] for block in blocks if block["type"] == "text")
    return _Prompt(text=text, command_name=None, command_args=None)


def _captured(pattern: re.Pattern[str], content: str) -> str | None:
    match = pattern.search(content)
    return match.group(1).strip() if match else None


def _api_calls(
    lines: list[_Line], turn_by_line: dict[int, str | None], session_id: str, source: str
) -> list[ApiCall]:
    """One `ApiCall` per assistant message, however many records it was written across.

    Claude Code writes one record per content block, all sharing `message.id` and chained
    by `parentUuid`, interleaved with the tool results they triggered. Two thirds of the
    messages in the corpus span several records, so grouping is not optional.
    """
    at_uuid = {line.record["uuid"]: line for line in lines if line.record.get("uuid")}
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
        calls.append(
            ApiCall(
                id=message_id,
                session_id=session_id,
                source=source,
                turn_id=turn_by_line[first.line_no],
                index=index,
                model=message["model"],
                effort=last.record.get("effort"),
                stop_reason=message["stop_reason"],
                # Present only while a skill was driving.
                attribution_skill=last.record.get("attributionSkill"),
                request_id=last.record.get("requestId"),
                started_at=_required_timestamp(answered or first, session_id),
                ended_at=_required_timestamp(last, session_id),
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read_tokens=usage["cache_read_input_tokens"],
                cache_creation_tokens=usage["cache_creation_input_tokens"],
                cache_5m_tokens=split["ephemeral_5m_input_tokens"] if split else None,
                cache_1h_tokens=split["ephemeral_1h_input_tokens"] if split else None,
                text=_blocks(group, "text", "text"),
                thinking=_blocks(group, "thinking", "thinking"),
            )
        )
    return calls


def _tool_calls(lines: list[_Line], session_id: str, source: str) -> list[ToolCall]:
    """Every tool the transcript asked for, paired with the record that answered it.

    A message issuing several calls writes one record per call, in the order Claude Code
    got round to running them — so the batch shares the earliest of those timestamps and
    says the start is synthetic, rather than reporting an execution order as a duration.
    """
    results = _tool_results(lines, session_id)
    issued = [
        (line, block)
        for line in lines
        if line.record["type"] == RecordType.ASSISTANT
        for block in line.record["message"]["content"]
        if block["type"] == "tool_use"
    ]
    batches: dict[str, list[datetime]] = {}
    for line, _ in issued:
        message_id = line.record["message"]["id"]
        batches.setdefault(message_id, []).append(_required_timestamp(line, session_id))
    calls = []
    for index, (line, block) in enumerate(issued):
        batch = batches[line.record["message"]["id"]]
        result = results.get(block["id"])
        calls.append(
            ToolCall(
                id=block["id"],
                session_id=session_id,
                source=source,
                api_call_id=line.record["message"]["id"],
                index=index,
                name=block["name"],
                input=json.dumps(block["input"]),
                result=result.text if result else None,
                offload_file=result.offload_file if result else None,
                is_error=result.is_error if result else False,
                incomplete=result is None,
                started_at=min(batch),
                ended_at=result.ended_at if result else None,
                duration_synthetic=len(batch) > 1,
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
            if block["type"] != "tool_result":
                continue
            results[block["tool_use_id"]] = _Result(
                text=_result_text(block["content"], session_id, line.line_no),
                # Absent on most results — absence means the tool succeeded.
                is_error=bool(block.get("is_error")),
                offload_file=PurePath(path).name if path else None,
                ended_at=_timestamp(record),
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


def _blocks(group: Iterable[_Line], kind: str, field: str) -> str:
    """Every block of one kind across a message's records, concatenated in order."""
    return "".join(
        block[field]
        for line in group
        for block in line.record["message"]["content"]
        if block["type"] == kind
    )


def fingerprint(files: Iterable[Path], relative_to: Path) -> str:
    """A session's state, as one digest over the files that hold it.

    Covers every file, not just the main transcript: a subagent transcript or an offloaded
    tool result changes without the transcript changing. Folds in the extractor version so
    a parser upgrade re-extracts the corpus rather than leaving old rows parsed by old
    logic. Uses mtime, so copying the tree re-extracts everything — idempotent, just slow.
    """
    digest = hashlib.sha256(EXTRACTOR_VERSION.encode())
    for path in sorted(files):
        stat = path.stat()
        entry = f"{path.relative_to(relative_to)}\0{stat.st_size}\0{stat.st_mtime_ns}\0"
        digest.update(entry.encode())
    return digest.hexdigest()
