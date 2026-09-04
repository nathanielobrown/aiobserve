"""What one transcript holds: the file read into lines, and what each line says on its own.

One thread at a time — the session's own transcript or a subagent's — and no knowledge of
which files make up a session (`extract/layout.py`) or of how a refresh is driven
(`extract/claude_code.py`). `read_lines` parses a file into lines and validates each against
its record model, `resolve_duplicates` collapses a uuid the file wrote twice, and the readers
below answer what the file says about the session as a whole: where it ran, what it was
called, the pull requests it touched. Turning those lines into turns, api calls, tool calls
and compactions is `extract/parse.py`.

Every field these readers reach for is declared on a model in `extract/records/` with the
session that proved it (`docs/schema.md`).
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hyphae.extract.errors import TranscriptSchemaError, invalid_record
from hyphae.extract.records.base import Identified, Record, SessionContext, Timestamped
from hyphae.extract.records.bookkeeping import (
    AgentNameRecord,
    AiTitleRecord,
    CustomTitleRecord,
    ForkContextRefRecord,
    PrLinkRecord,
)
from hyphae.extract.records.registry import ContentBlock
from hyphae.extract.records.shapes import model_for
from hyphae.extract.records.system import TurnDurationRecord
from hyphae.extract.records.unknown import UnknownFields
from hyphae.model import PrLink, RawRecord, Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Line:
    """One line of a transcript, validated against its model but not yet interpreted."""

    line_no: int
    record: Record
    # The same line as a dict, for the readers that have not moved onto the model yet. It goes
    # when the last of them does (`plans/records-as-parser/design.md`).
    fields: dict[str, Any]
    raw: str

    @property
    def uuid(self) -> str | None:
        """The record's own id, absent on the bookkeeping types — a documented absence."""
        return self.record.uuid if isinstance(self.record, Identified) else None


def read_lines(path: Path, session_id: str, unknown_fields: UnknownFields) -> list[Line]:
    """Every line of a transcript, parsed as JSON and validated against its record model.

    `unknown_fields` is where a field no model declares goes: a crash in a test run, a tally
    in an extract. It belongs to the extraction run, not the file, so the caller owns it.

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
        lines.append(_validated(record, raw, session_id, line_no, unknown_fields))
    return lines


def _validated(
    record: dict[str, Any],
    raw: str,
    session_id: str,
    line_no: int,
    unknown_fields: UnknownFields,
) -> Line:
    """One parsed line through its model: the kind, the shape, then the undeclared fields.

    Each step names the session and the line, because a transcript is the only place a reader
    can check what went wrong and neither the model nor pydantic knows where the record came
    from.
    """
    try:
        model = model_for(record)
    except TranscriptSchemaError as error:
        raise TranscriptSchemaError(f"{error} in session {session_id}, line {line_no}") from error
    try:
        parsed = model.model_validate(record)
    except ValidationError as error:
        raise invalid_record(error, model, session_id, line_no) from error
    unknown_fields.note(parsed, session_id, line_no)
    return Line(line_no=line_no, record=parsed, fields=record, raw=raw)


def resolve_duplicates(lines: list[Line], session_id: str) -> list[Line]:
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


def _content(line: Line) -> Any:
    """What the record said, if it said anything — the one field a duplicate may not change."""
    message = line.fields.get("message")
    return message.get("content") if isinstance(message, dict) else None


def raw_record(session_id: str, source: str, line: Line) -> RawRecord:
    """One line as the archive keeps it: the whole text, plus the fields a query filters on."""
    return RawRecord(
        session_id=session_id,
        source=source,
        line_no=line.line_no,
        uuid=line.uuid,
        timestamp=timestamp_of(line.record),
        type=line.record.type,
        raw=line.raw,
    )


def timestamp_of(record: Record) -> datetime | None:
    """A record's timestamp. Absent on the bookkeeping types, which carry no time at all."""
    if not isinstance(record, Timestamped) or record.timestamp is None:
        return None
    return datetime.fromisoformat(record.timestamp)


def session_of(lines: list[Line], session_id: str, transcript: Path) -> Session:
    """Session metadata, gathered from the records that carry it."""
    # The file opens on bookkeeping records with no `cwd`, so the first record that has
    # one is what says where this session ran.
    sited = (line.record for line in lines if isinstance(line.record, SessionContext))
    context = next((record for record in sited if record.cwd is not None), None)
    moments = [t for t in (timestamp_of(line.record) for line in lines) if t is not None]
    active_ms = sum(
        line.record.durationMs or 0 for line in lines if isinstance(line.record, TurnDurationRecord)
    )
    custom_title = _last_of(lines, CustomTitleRecord)
    ai_title = _last_of(lines, AiTitleRecord)
    agent_name = _last_of(lines, AgentNameRecord)
    return Session(
        id=session_id,
        project_dir=context.cwd if context else None,
        # Absent when the project is not a git repository.
        git_branch=context.gitBranch if context else None,
        version=context.version if context else None,
        # Absent on sessions older than the field — the corpus has 1.0.128 sessions.
        entrypoint=context.entrypoint if context else None,
        started_at=min(moments) if moments else None,
        ended_at=max(moments) if moments else None,
        active_ms=active_ms,
        transcript_path=str(transcript),
        # Claude Code appends a fresh title record on every rename, so the last one is the
        # name the session ended up with. Both spellings are current; 13 of the 398 titled
        # mycelia sessions hold both (scanned 2026-08-07), and there the operator's wins.
        title=(custom_title.customTitle if custom_title else None)
        or (ai_title.aiTitle if ai_title else None),
        agent_name=agent_name.agentName if agent_name else None,
    )


def _last_of[R: Record](lines: list[Line], kind: type[R]) -> R | None:
    """The file's last record of one kind, or None when it holds none."""
    found = [line.record for line in lines if isinstance(line.record, kind)]
    return found[-1] if found else None


def pr_links(lines: list[Line], session_id: str) -> list[PrLink]:
    """Every pull request the session recorded touching.

    These records carry no uuid, and a session that pushes repeatedly links the same PR
    once per push, so the line number is what separates two of them.
    """
    return [
        PrLink(
            session_id=session_id,
            line_no=line.line_no,
            pr_number=required(line.record.prNumber, line, session_id, "prNumber"),
            pr_url=required(line.record.prUrl, line, session_id, "prUrl"),
            pr_repository=required(line.record.prRepository, line, session_id, "prRepository"),
            timestamp=required_timestamp(line, session_id),
        )
        for line in lines
        if isinstance(line.record, PrLinkRecord)
    ]


def fork_context(lines: list[Line]) -> str | None:
    """The record a by-reference fork continues from, when its file opens on one.

    Only that variant carries it: a fork that copied its history states the same thing by
    holding the records themselves. Every one of the 25 in the corpus leads the file
    (scanned 2026-08-07), but the search does not depend on that.
    """
    for line in lines:
        if isinstance(line.record, ForkContextRefRecord):
            return line.record.parentLastUuid
    return None


def workflow_launches(lines: list[Line]) -> dict[str, str]:
    """Which tool call launched each fan-out: `runId` from the result, to its call's id.

    A `Workflow` call answers with the run it started, and the run id is the name of the
    directory its agents write into — the only join between a fan-out's transcripts and the
    call that asked for them.
    """
    launches = {}
    for line in lines:
        details = line.fields.get("toolUseResult")
        if not isinstance(details, dict) or "runId" not in details:
            continue
        for block in line.fields["message"]["content"]:
            if block["type"] == ContentBlock.TOOL_RESULT:
                launches[details["runId"]] = block["tool_use_id"]
    return launches


def required_timestamp(line: Line, session_id: str) -> datetime:
    """The record's timestamp, for the entities that cannot be placed in time without one."""
    return required(timestamp_of(line.record), line, session_id, "timestamp")


def required[V](value: V | None, line: Line, session_id: str, field: str) -> V:
    """A declared field an entity cannot be built without, or a crash naming where it was.

    Every field on a model is optional, so this is where a reader that cannot proceed without
    one says so. The kind comes off the record rather than the caller: eight parse paths reach
    this raise, and a caller naming the wrong one sends the reader to the wrong records.
    """
    if value is None:
        raise TranscriptSchemaError(
            f"Session {session_id}, line {line.line_no}: "
            f"a {line.record.type} record with no {field}"
        )
    return value
