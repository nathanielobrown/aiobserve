//! The canonical trace model: what one recorded agent session looks like, whatever wrote it.
//!
//! A [`SessionTrace`] is flat lists keyed by natural ids — a mirror of the relational schema
//! an exporter writes, not a nested tree. Ids come from the data (a message id, a record
//! uuid), so they survive re-extraction and enrichment rows keyed on them survive with them.
//! None of them is globally unique: a resume copies its ancestor's records verbatim into a
//! new session file, so every row is scoped by `session_id` and by `source` — the transcript
//! inside the session that recorded it.
//!
//! Ported field-for-field from `src/hyphae/model.py`, which stays the authority. The field
//! order of each struct is the DDL order the store inserts by
//! (`hyphae_store::schema::TABLES`); a field moved here without moving there is caught by
//! `Store::check_columns`.

pub mod clock;

use chrono::{DateTime, Utc};

/// The `source` value for records that came from the session's own transcript rather than
/// from a subagent's. Subagent records carry their agentId instead.
pub const MAIN_SOURCE: &str = "main";

/// One recorded session: the main transcript plus everything its subagents wrote.
#[derive(Debug, Clone, PartialEq)]
pub struct Session {
    /// The session UUID, taken from the transcript's filename.
    pub id: String,
    /// From the first record carrying `cwd` — the file opens on bookkeeping records that
    /// carry none. All four are `None` for a transcript that holds only such records.
    pub project_dir: Option<String>,
    pub git_branch: Option<String>,
    pub version: Option<String>,
    pub entrypoint: Option<String>,
    /// Earliest and latest timestamp in the **main transcript**, which is not written in
    /// timestamp order. Subagent work can run past `ended_at`; reading the subagents in
    /// would let a fork's copied records move a session's clock.
    pub started_at: Option<DateTime<Utc>>,
    pub ended_at: Option<DateTime<Utc>>,
    /// Time Claude Code reported working, summed over `system/turn_duration` records. Well
    /// below `ended_at - started_at`, which includes every gap the user spent away.
    pub active_ms: i64,
    pub transcript_path: String,
    /// What the session is called, from the last `custom-title` record, or the last
    /// `ai-title` when the user never renamed it. `None` when it was never titled.
    pub title: Option<String>,
    /// The persona name the session ran under, from the last `agent-name` record.
    pub agent_name: Option<String>,
}

/// One prompt and the work it drove, from the prompt until the next one.
#[derive(Debug, Clone, PartialEq)]
pub struct Turn {
    /// The prompt record's uuid.
    pub id: String,
    pub session_id: String,
    pub source: String,
    /// Position within this transcript, from 0.
    pub index: i32,
    /// The prompt as recorded, including the command tags when it is a slash command.
    pub prompt: String,
    /// Both `None` unless the prompt was a slash command, e.g. "/model" and its arguments.
    pub command_name: Option<String>,
    pub command_args: Option<String>,
    pub started_at: DateTime<Utc>,
    /// The latest timestamp among the records this turn drove.
    pub ended_at: DateTime<Utc>,
    /// This transcript is a fork replaying a turn another one opened. The row stays — the
    /// fork's file recorded it — but every rollup counts it under the first transcript to
    /// hold it.
    pub replayed: bool,
}

/// One model response, reassembled from the records it was written across.
///
/// Claude Code writes one record per content block, all sharing `message.id`, so a
/// per-record reading triples the call count.
#[derive(Debug, Clone, PartialEq)]
pub struct ApiCall {
    /// `message.id`.
    pub id: String,
    pub session_id: String,
    pub source: String,
    /// `None` when the call precedes the transcript's first prompt — a by-reference fork
    /// opens mid-conversation.
    pub turn_id: Option<String>,
    pub index: i32,
    pub model: String,
    /// The model Claude Code asked for first, when it retried the request on `model`
    /// instead. `None` — the usual case — means no retry.
    pub fallback_from: Option<String>,
    /// Opaque: the effort setting's values and meaning are not established.
    pub effort: Option<String>,
    pub stop_reason: Option<String>,
    /// The skill that was driving when the call was made, e.g. "night-run".
    pub attribution_skill: Option<String>,
    pub request_id: Option<String>,
    /// When the record this call answers was written. Falls back to the call's own first
    /// chunk when that record is not in this transcript — true after a compaction, and for
    /// a by-reference fork's opening call.
    pub started_at: DateTime<Utc>,
    pub ended_at: DateTime<Utc>,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cache_read_tokens: i64,
    pub cache_creation_tokens: i64,
    /// The cache-creation total split by TTL. `None` — not 0 — when the reply reported no
    /// split at all, so a query can tell a missing split from an empty one.
    pub cache_5m_tokens: Option<i64>,
    pub cache_1h_tokens: Option<i64>,
    /// Every text and thinking block of the message, concatenated in order. Block
    /// interleaving is lost; `raw_records` keeps it.
    pub text: String,
    pub thinking: String,
    /// USD, from our own price table — not from the transcript, which records no cost.
    /// `None` when the table does not price `model`, which is a gap in our list to fill.
    pub cost_usd: Option<f64>,
    /// A placeholder reply Claude Code wrote itself rather than a model response. It reports
    /// zero tokens and costs nothing, so counting it as a call inflates call counts.
    pub synthetic: bool,
    /// A fork's copy of a call another transcript made. See [`Turn::replayed`].
    pub replayed: bool,
}

/// One tool the model asked for, and the result that came back.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolCall {
    /// The `tool_use` block's id, which the answering `tool_result` block quotes.
    pub id: String,
    pub session_id: String,
    pub source: String,
    /// `message.id` of the call that issued it.
    pub api_call_id: String,
    /// Position within this transcript, from 0.
    pub index: i32,
    pub name: String,
    /// Anthropic ran this tool server-side and returned its result inside the same message
    /// (`server_tool_use`), rather than Claude Code running it locally.
    pub server_side: bool,
    /// The tool's arguments, as recorded, serialised back to JSON.
    pub input: String,
    /// The result flattened to text: the block's string, or its text blocks joined. Images
    /// and tool references contribute nothing. `None` while the call is incomplete, and the
    /// short on-disk preview when `offload_file` names the full output.
    pub result: Option<String>,
    /// The `tool-results/` file holding the full output, when Claude Code moved it out of
    /// the transcript. Its content is in [`OffloadFile`].
    pub offload_file: Option<String>,
    pub is_error: bool,
    /// No result record — the session ended, or was interrupted, mid-call.
    pub incomplete: bool,
    pub started_at: DateTime<Utc>,
    /// `None` while incomplete.
    pub ended_at: Option<DateTime<Utc>>,
    /// The start is shared with the rest of its batch rather than measured: one message
    /// issuing several calls writes their records in execution order, not issue order, so
    /// `ended_at - started_at` reads longer than the tool ran.
    pub duration_synthetic: bool,
    /// A fork's copy of a call another transcript made. See [`Turn::replayed`].
    pub replayed: bool,
}

/// One subagent the session ran, and what is known about why it ran.
///
/// The run's own work is in the turns, calls and tools carrying its id as their `source`;
/// this row says who asked for it. Everything but the timestamps comes from the
/// `agent-<id>.meta.json` Claude Code writes beside the transcript.
#[derive(Debug, Clone, PartialEq)]
pub struct AgentRun {
    /// The agentId, which is the file stem after `agent-` and the `source` its rows carry.
    pub id: String,
    pub session_id: String,
    /// The agent that spawned this one, when it was a subagent's subagent.
    pub parent_agent_id: Option<String>,
    /// The `Agent` (or `Workflow`) tool call that asked for this run. `None` makes the run
    /// an orphan: a teammate the team mechanism started, with no tool call behind it.
    pub tool_use_id: Option<String>,
    /// Which agent definition ran: "general-purpose", "auditor", a session-defined name.
    /// Not a closed set — sessions name their own.
    pub agent_type: String,
    /// The one-line brief the spawning call gave the run. Absent on some runs.
    pub brief: Option<String>,
    /// The model alias the caller asked for, e.g. "opus". Absent when the caller named none.
    pub model: Option<String>,
    /// The `wf_<id>` fan-out this run belonged to, from the directory it sits in.
    pub workflow_id: Option<String>,
    /// 1 for a run the session itself spawned, deeper for a subagent's subagent, 0 for a
    /// teammate. `None` when the meta left the key out, which one recorded meta does.
    pub spawn_depth: Option<i32>,
    /// The run continues a conversation another transcript started, either by replaying its
    /// records or by pointing at them.
    pub is_fork: bool,
    /// The record a by-reference fork picked up from, in the session its transcript names.
    /// `None` for every other run, including a fork that copied the history instead.
    pub fork_context_uuid: Option<String>,
    /// The run's own work: its first record that no earlier transcript already held, and the
    /// last record of its transcript. `started_at` is `None` when the run copied everything
    /// it holds; `ended_at` when the transcript carries no timestamps at all.
    pub started_at: Option<DateTime<Utc>>,
    pub ended_at: Option<DateTime<Utc>>,
}

/// One point where Claude Code summarised the conversation to free context.
///
/// Everything after a compaction is reasoning over a summary, so these mark where the
/// transcript's account of the session gets lossy.
#[derive(Debug, Clone, PartialEq)]
pub struct Compaction {
    /// The `system/compact_boundary` record's uuid.
    pub id: String,
    pub session_id: String,
    pub source: String,
    pub timestamp: DateTime<Utc>,
    /// "auto" when the context window filled, "manual" for an explicit `/compact`.
    pub trigger: String,
    /// Context size either side of the summary, in tokens.
    pub pre_tokens: i64,
    pub post_tokens: i64,
    pub duration_ms: i64,
}

/// A pull request the session opened or touched, as Claude Code recorded it.
#[derive(Debug, Clone, PartialEq)]
pub struct PrLink {
    pub session_id: String,
    /// These records carry no uuid, and one session links the same PR many times, so the
    /// transcript line number is the only thing that tells two of them apart.
    pub line_no: i32,
    pub pr_number: i32,
    pub pr_url: String,
    pub pr_repository: String,
    pub timestamp: DateTime<Utc>,
}

/// A tool output Claude Code wrote to a file instead of into the transcript.
///
/// These hold the largest outputs a session produced, and Claude Code prunes them with the
/// transcript, so the archive is the only durable copy.
#[derive(Debug, Clone, PartialEq)]
pub struct OffloadFile {
    pub session_id: String,
    /// The file's name under the session's `tool-results/`, as [`ToolCall::offload_file`]
    /// quotes it. The recorded path is absolute on the machine that wrote it, so only the
    /// name travels.
    pub name: String,
    /// The file decoded as UTF-8.
    pub content: String,
    /// The file was not valid UTF-8 — a fetched PDF, or output cut mid-character — and
    /// `content` carries replacement characters where the bytes were.
    pub lossy_decode: bool,
    /// Size on disk, which `content` stops measuring once the decode was lossy.
    pub size_bytes: i64,
}

/// One line of one transcript, kept verbatim.
///
/// Claude Code prunes transcripts from disk after a few weeks, so this is the archive —
/// every line, including duplicates the normalized tables resolve away, and including types
/// no parser reads yet.
#[derive(Debug, Clone, PartialEq)]
pub struct RawRecord {
    pub session_id: String,
    /// "main", a subagent's agentId, or "wf_<id>/journal".
    pub source: String,
    /// 1-based line number in the file this record came from.
    pub line_no: i32,
    /// Both `None` on the bookkeeping types that carry neither.
    pub uuid: Option<String>,
    pub timestamp: Option<DateTime<Utc>>,
    pub r#type: String,
    pub raw: String,
}

/// Everything extracted from one session, ready to hand to an exporter.
#[derive(Debug, Clone, PartialEq)]
pub struct SessionTrace {
    /// Which extractor produced this, and at what version — provenance follows the rows into
    /// the sink, and the version is folded into the fingerprint so a parser upgrade
    /// re-extracts the corpus without a manual purge.
    pub extractor: String,
    pub extractor_version: String,
    pub session: Session,
    pub turns: Vec<Turn>,
    pub api_calls: Vec<ApiCall>,
    pub tool_calls: Vec<ToolCall>,
    pub agent_runs: Vec<AgentRun>,
    pub compactions: Vec<Compaction>,
    pub pr_links: Vec<PrLink>,
    pub offload_files: Vec<OffloadFile>,
    pub raw_records: Vec<RawRecord>,
}
