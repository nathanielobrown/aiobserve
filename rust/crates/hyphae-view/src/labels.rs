//! What a page calls each field it prints.
//!
//! Ported from `src/hyphae/view/labels.py`. A header is a column of the store read by a
//! person, so the two names it carries answer to different readers: the `data-field` beside
//! every value stays the store's own column, and the word above it is what someone says out
//! loud. Closed on purpose — a field with no entry here panics rather than falling back to
//! the column name, so a page cannot ship a label nobody chose.

const LABELS: &[(&str, &str)] = &[
    // What the thread was, and where it ran.
    ("session_id", "Session"),
    ("run_id", "Run"),
    ("git_branch", "Branch"),
    // Claude Code's own version string, which is what pins a schema fact (`docs/schema.md`).
    ("version", "Version"),
    ("entrypoint", "Entrypoint"),
    // What the spawning agent typed in the Agent tool's `description`, which is the brief the
    // run was given rather than a description of what it did.
    ("brief", "Task brief"),
    ("agent_type", "Agent"),
    ("model", "Model"),
    ("fallback_from", "Fell back from"),
    ("effort", "Effort"),
    ("stop_reason", "Stop reason"),
    ("attribution_skill", "Skill"),
    ("spawn_depth", "Depth"),
    ("is_fork", "Fork"),
    // When it ran and for how long. Both spans print as a duration, so neither label names
    // the milliseconds the column holds.
    ("started_at", "Started"),
    ("wall_ms", "Wall time"),
    ("active_ms", "Active time"),
    // How much it did.
    ("turns", "Turns"),
    ("turn_index", "Turn"),
    ("api_calls", "API calls"),
    ("tool_calls", "Tool calls"),
    ("tool_titles", "Tools"),
    ("tool_errors", "Tool errors"),
    ("agent_runs", "Subagent runs"),
    ("compactions", "Compactions"),
    ("cost_usd", "Cost"),
    ("unpriced_api_calls", "Unpriced calls"),
    ("input_tokens", "Input tokens"),
    ("output_tokens", "Output tokens"),
    ("cache_read_tokens", "Cache read"),
    ("cache_creation_tokens", "Cache written"),
    ("skills", "Skills"),
    ("call_index", "Call"),
    ("tool_index", "Tool call"),
    ("name", "Tool"),
    ("server_side", "Server-side"),
    ("is_error", "Error"),
    ("incomplete", "Incomplete"),
    ("replayed", "Replayed"),
    ("command_args", "Command arguments"),
    ("trigger", "Trigger"),
    ("timestamp", "At"),
    ("pre_tokens", "Tokens before"),
    ("post_tokens", "Tokens after"),
    ("duration_ms", "Took"),
    ("prompt", "Prompt"),
    ("text", "Said"),
    ("text_chars", "Said (chars)"),
    ("thinking", "Thought"),
    ("input", "Arguments"),
    ("command", "Command"),
    ("result", "Result"),
    ("result_chars", "Result (chars)"),
    ("description", "Description"),
    ("friction", "Friction"),
    // The two columns a children log prints that no query returns.
    ("title", "Title"),
    ("body", "Body"),
];

/// What a reader calls the field `name`. Panics for a field no page has named yet.
pub fn label(name: &str) -> &'static str {
    LABELS
        .iter()
        .find(|(field, _)| *field == name)
        .map(|(_, said)| *said)
        .unwrap_or_else(|| panic!("no page has named the field `{name}`"))
}
