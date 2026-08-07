"""The canonical trace model: what one recorded agent session looks like, whatever wrote it.

A `SessionTrace` is flat lists keyed by natural ids — a mirror of the relational schema an
exporter writes, not a nested tree. Ids come from the data (a message id, a record uuid), so
they survive re-extraction and enrichment rows keyed on them survive with them. None of them
is globally unique: a resume copies its ancestor's records verbatim into a new session file,
so every row is scoped by `session_id` and by `source` — the transcript inside the session
that recorded it.

Slices 1 to 3 of `plans/trace-pipeline/design.md` cover sessions, turns, API calls, tool
calls, agent runs, and the archive — every line of every file the session wrote, plus the
tool outputs it moved out of the transcript. Compactions, PR links, cost, and the
`replayed` flag arrive with the slices that populate them.
"""

from dataclasses import dataclass
from datetime import datetime

# The `source` value for records that came from the session's own transcript rather than
# from a subagent's. Subagent records carry their agentId instead.
MAIN_SOURCE = "main"


@dataclass(frozen=True)
class Session:
    """One recorded session: the main transcript plus everything its subagents wrote."""

    # The session UUID, taken from the transcript's filename.
    id: str
    # From the first record carrying `cwd` — the file opens on bookkeeping records that
    # carry none. All four are None for a transcript that holds only such records.
    project_dir: str | None
    git_branch: str | None
    version: str | None
    entrypoint: str | None
    # Earliest and latest record timestamp. Records are not written in timestamp order.
    started_at: datetime | None
    ended_at: datetime | None
    # Time Claude Code reported working, summed over `system/turn_duration` records. Well
    # below `ended_at - started_at`, which includes every gap the user spent away.
    active_ms: int
    transcript_path: str


@dataclass(frozen=True)
class Turn:
    """One prompt and the work it drove, from the prompt until the next one."""

    # The prompt record's uuid.
    id: str
    session_id: str
    source: str
    # Position within this transcript, from 0.
    index: int
    # The prompt as recorded, including the command tags when it is a slash command.
    prompt: str
    # Both None unless the prompt was a slash command, e.g. "/model" and its arguments.
    command_name: str | None
    command_args: str | None
    started_at: datetime
    # The latest timestamp among the records this turn drove.
    ended_at: datetime


@dataclass(frozen=True)
class ApiCall:
    """One model response, reassembled from the records it was written across.

    Claude Code writes one record per content block, all sharing `message.id`, so a
    per-record reading triples the call count.
    """

    # `message.id`.
    id: str
    session_id: str
    source: str
    # None when the call precedes the transcript's first prompt — a by-reference fork
    # opens mid-conversation.
    turn_id: str | None
    index: int
    model: str
    # Opaque: the effort setting's values and meaning are not established (docs/schema.md).
    effort: str | None
    stop_reason: str | None
    # The skill that was driving when the call was made, e.g. "night-run".
    attribution_skill: str | None
    request_id: str | None
    # When the record this call answers was written. Falls back to the call's own first
    # chunk when that record is not in this transcript — true after a compaction, and for
    # a by-reference fork's opening call.
    started_at: datetime
    ended_at: datetime
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    # The cache-creation total split by TTL. None — not 0 — when the reply reported no
    # split at all, so a query can tell a missing split from an empty one.
    cache_5m_tokens: int | None
    cache_1h_tokens: int | None
    # Every text and thinking block of the message, concatenated in order. Block
    # interleaving is lost; `raw_records` keeps it.
    text: str
    thinking: str


@dataclass(frozen=True)
class ToolCall:
    """One tool the model asked for, and the result that came back."""

    # The `tool_use` block's id, which the answering `tool_result` block quotes.
    id: str
    session_id: str
    source: str
    # `message.id` of the call that issued it.
    api_call_id: str
    # Position within this transcript, from 0.
    index: int
    name: str
    # The tool's arguments, as recorded, serialised back to JSON.
    input: str
    # The result flattened to text: the block's string, or its text blocks joined. Images
    # and tool references contribute nothing. None while the call is incomplete, and the
    # short on-disk preview when `offload_file` names the full output.
    result: str | None
    # The `tool-results/` file holding the full output, when Claude Code moved it out of
    # the transcript. Its content is in `OffloadFile`.
    offload_file: str | None
    is_error: bool
    # No result record — the session ended, or was interrupted, mid-call.
    incomplete: bool
    started_at: datetime
    # None while incomplete.
    ended_at: datetime | None
    # The start is shared with the rest of its batch rather than measured: one message
    # issuing several calls writes their records in execution order, not issue order, so
    # `ended_at - started_at` reads longer than the tool ran.
    duration_synthetic: bool


@dataclass(frozen=True)
class AgentRun:
    """One subagent the session ran, and what is known about why it ran.

    The run's own work is in the turns, calls and tools carrying its id as their `source`;
    this row says who asked for it. Everything but the timestamps comes from the
    `agent-<id>.meta.json` Claude Code writes beside the transcript.
    """

    # The agentId, which is the file stem after `agent-` and the `source` its rows carry.
    id: str
    session_id: str
    # The agent that spawned this one, when it was a subagent's subagent.
    parent_agent_id: str | None
    # The `Agent` (or `Workflow`) tool call that asked for this run. None makes the run an
    # orphan: a teammate the team mechanism started, with no tool call behind it.
    tool_use_id: str | None
    # Which agent definition ran: "general-purpose", "auditor", "workflow-subagent", a
    # session-defined name. Not a closed set — sessions name their own.
    agent_type: str
    # The one-line summary of the task, from the spawning call. Absent on some runs.
    description: str | None
    # The model alias the caller asked for, e.g. "opus". Absent when the caller named none.
    model: str | None
    # The `wf_<id>` fan-out this run belonged to, from the directory it sits in.
    workflow_id: str | None
    # 1 for a run the session itself spawned, deeper for a subagent's subagent, 0 for a
    # teammate.
    spawn_depth: int
    # First and last record of its transcript.
    started_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True)
class OffloadFile:
    """A tool output Claude Code wrote to a file instead of into the transcript.

    These hold the largest outputs a session produced, and Claude Code prunes them with
    the transcript, so the archive is the only durable copy.
    """

    session_id: str
    # The file's name under the session's `tool-results/`, as `ToolCall.offload_file`
    # quotes it. The recorded path is absolute on the machine that wrote it, so only the
    # name travels.
    name: str
    # The file decoded as UTF-8.
    content: str
    # The file was not valid UTF-8 — a fetched PDF, or output cut mid-character — and
    # `content` carries replacement characters where the bytes were.
    lossy_decode: bool
    # Size on disk, which `content` stops measuring once the decode was lossy.
    size_bytes: int


@dataclass(frozen=True)
class RawRecord:
    """One line of one transcript, kept verbatim.

    Claude Code prunes transcripts from disk after a few weeks, so this is the archive —
    every line, including duplicates the normalized tables resolve away, and including
    types no parser reads yet.
    """

    session_id: str
    # "main", a subagent's agentId, or "wf_<id>/journal".
    source: str
    # 1-based line number in the file this record came from.
    line_no: int
    # Both None on the bookkeeping types that carry neither.
    uuid: str | None
    timestamp: datetime | None
    type: str
    raw: str


@dataclass(frozen=True)
class SessionTrace:
    """Everything extracted from one session, ready to hand to an exporter."""

    # Which extractor produced this, and at what version — provenance follows the rows into
    # the sink, and the version is folded into the fingerprint so a parser upgrade
    # re-extracts the corpus without a manual purge.
    extractor: str
    extractor_version: str
    session: Session
    turns: list[Turn]
    api_calls: list[ApiCall]
    tool_calls: list[ToolCall]
    agent_runs: list[AgentRun]
    offload_files: list[OffloadFile]
    raw_records: list[RawRecord]
