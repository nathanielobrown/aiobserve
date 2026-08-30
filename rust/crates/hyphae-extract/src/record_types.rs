//! The closed world of a Claude Code transcript: every record type, subtype, block and tag.
//!
//! These registries are what makes the reader closed-world. Claude Code owns these shapes and
//! changes them without notice, so anything not registered here stops the run: a type we
//! quietly skip today is a wrong count months from now.
//!
//! Ported name-for-name from `src/hyphae/extract/record_types.py`, which stays the authority.
//! What each shape holds, and the recording that proves it, is `docs/schema.md`.

/// Record types this parser reads. Anything outside both registries crashes.
pub mod record {
    pub const ASSISTANT: &str = "assistant";
    pub const USER: &str = "user";
    pub const SYSTEM: &str = "system";
    pub const CUSTOM_TITLE: &str = "custom-title";
    /// The title Claude Code wrote for the session itself, still current beside
    /// `custom-title` — which is the one the operator typed.
    pub const AI_TITLE: &str = "ai-title";
    pub const AGENT_NAME: &str = "agent-name";
    pub const PR_LINK: &str = "pr-link";
    /// Opens a by-reference fork's transcript, naming the conversation it continues.
    pub const FORK_CONTEXT_REF: &str = "fork-context-ref";
}

pub const RECORD_TYPES: &[&str] = &[
    record::ASSISTANT,
    record::USER,
    record::SYSTEM,
    record::CUSTOM_TITLE,
    record::AI_TITLE,
    record::AGENT_NAME,
    record::PR_LINK,
    record::FORK_CONTEXT_REF,
];

/// Record types kept verbatim in `raw_records` and read by nothing.
///
/// Each is either bookkeeping (editor state, file backups) or content the subagent's own
/// transcript states better. Archiving rather than parsing keeps them recoverable.
pub const ARCHIVE_RECORD_TYPES: &[&str] = &[
    "attachment",
    "last-prompt",
    "mode",
    "permission-mode",
    "bridge-session",
    "file-history-snapshot",
    "file-history-delta",
    "agent-setting",
    "queue-operation",
    "summary",
    // Worktree sessions only.
    "worktree-state",
    "relocated",
    // Workflow journals only (`subagents/workflows/wf_<id>/journal.jsonl`).
    "started",
    "result",
];

/// Every `system` subtype the corpus holds. An unregistered one crashes.
pub mod system {
    pub const TURN_DURATION: &str = "turn_duration";
    pub const COMPACT_BOUNDARY: &str = "compact_boundary";
}

pub const SYSTEM_SUBTYPES: &[&str] = &[
    system::TURN_DURATION,
    system::COMPACT_BOUNDARY,
    "away_summary",
    "local_command",
    "informational",
    "scheduled_task_fire",
    "api_error",
    "agents_killed",
    "stop_hook_summary",
    // The harness ran the session on another model because the one asked for was unavailable.
    "model_consent_fallback",
];

/// Every kind of block a `message.content` list holds. An unregistered one crashes.
///
/// Leaving the set open is how server-side tool calls stayed invisible: they produced no row,
/// no text and no crash, so a session that used one looked like a session that had not.
pub mod block {
    pub const TEXT: &str = "text";
    pub const THINKING: &str = "thinking";
    pub const TOOL_USE: &str = "tool_use";
    /// A tool Anthropic runs server-side. Recorded like `tool_use`, but answered by an
    /// `advisor_tool_result` block in the same message rather than by a user record.
    pub const SERVER_TOOL_USE: &str = "server_tool_use";
    pub const ADVISOR_TOOL_RESULT: &str = "advisor_tool_result";
    /// Claude Code retried the request on another model; `from`/`to` name both.
    pub const FALLBACK: &str = "fallback";
    pub const IMAGE: &str = "image";
    pub const TOOL_RESULT: &str = "tool_result";
}

pub const CONTENT_BLOCKS: &[&str] = &[
    block::TEXT,
    block::THINKING,
    block::TOOL_USE,
    block::SERVER_TOOL_USE,
    block::ADVISOR_TOOL_RESULT,
    block::FALLBACK,
    block::IMAGE,
    block::TOOL_RESULT,
];

/// What an `advisor_tool_result` block's own content says. An unregistered one crashes.
///
/// Neither shape carries readable output: an error names its code, and a completed call comes
/// back encrypted.
pub mod advisor {
    pub const ERROR: &str = "advisor_tool_result_error";
    pub const REDACTED: &str = "advisor_redacted_result";
}

pub const ADVISOR_RESULTS: &[&str] = &[advisor::ERROR, advisor::REDACTED];

/// Block kinds a block-form `tool_result` is built from. An unregistered one crashes.
///
/// Only text carries into `ToolCall::result`: an image has none, and a tool reference names a
/// tool the result pointed at rather than saying anything.
pub const RESULT_BLOCKS: &[&str] = &[block::TEXT, block::IMAGE, "tool_reference"];

/// Leading tags on a prompt string that still make it a prompt.
pub const TURN_TAGS: &[&str] = &[
    "command-name",
    "command-message",
    // An instruction from another agent — drives work exactly as a user prompt does.
    // Subagent transcripts only.
    "teammate-message",
];

/// Leading tags Claude Code writes to itself. Archived, never turns.
///
/// Counting these as prompts inflates the turn count roughly 3.6x on this corpus.
pub const MACHINE_TAGS: &[&str] = &[
    "task-notification",
    "local-command-stdout",
    "bash-stdout",
    "bash-input",
];
