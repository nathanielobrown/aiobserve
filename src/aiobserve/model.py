"""The canonical trace model: what one recorded agent session looks like, whatever wrote it.

A `SessionTrace` is flat lists keyed by natural ids — a mirror of the relational schema an
exporter writes, not a nested tree. Ids come from the data (a message id, a record uuid), so
they survive re-extraction and enrichment rows keyed on them survive with them. None of them
is globally unique: a resume copies its ancestor's records verbatim into a new session file,
so every row is scoped by `session_id` and by `source` — the transcript inside the session
that recorded it.

Slice 1 of `plans/trace-pipeline/design.md` covers sessions, turns, API calls, and the raw
archive. Tool calls, agent runs, compactions, PR links, offload files, cost, and the
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
    raw_records: list[RawRecord]
