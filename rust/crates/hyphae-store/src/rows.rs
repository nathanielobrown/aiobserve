//! One trace, as the rows each table takes.
//!
//! This is what the design's "write the insert columns out beside the DDL" costs: Python
//! reads a dataclass's fields at run time, so its insert never disagrees with its model,
//! while here the order is written down twice — once in [`schema::TABLES`], once in the
//! builder below. `Store::check_columns` holds the first against the DDL; the builders here
//! are held against the first by row width, and by the round trip every store test makes.

use chrono::{DateTime, Utc};
use duckdb::types::{TimeUnit, Value};
use hyphae_model::{
    AgentRun, ApiCall, Compaction, OffloadFile, PrLink, RawRecord, Session, SessionTrace, ToolCall,
    Turn,
};

/// Every session-owned table's rows, in [`crate::schema::TABLES`] order.
///
/// Built as one list rather than table by table so the export loop cannot skip a table: a
/// table deleted from and never inserted into loses a session's rows on every re-extraction.
pub fn of(trace: &SessionTrace) -> Vec<(&'static str, Vec<Vec<Value>>)> {
    vec![
        ("sessions", vec![session(&trace.session)]),
        ("turns", trace.turns.iter().map(turn).collect()),
        ("api_calls", trace.api_calls.iter().map(api_call).collect()),
        (
            "tool_calls",
            trace.tool_calls.iter().map(tool_call).collect(),
        ),
        (
            "agent_runs",
            trace.agent_runs.iter().map(agent_run).collect(),
        ),
        (
            "compactions",
            trace.compactions.iter().map(compaction).collect(),
        ),
        ("pr_links", trace.pr_links.iter().map(pr_link).collect()),
        (
            "offload_files",
            trace.offload_files.iter().map(offload_file).collect(),
        ),
        (
            "raw_records",
            trace.raw_records.iter().map(raw_record).collect(),
        ),
    ]
}

/// The `extract_state` row that stamps what was read and when.
pub fn extract_state(trace: &SessionTrace, fingerprint: &str, at: DateTime<Utc>) -> Vec<Value> {
    vec![
        text(&trace.session.id),
        text(fingerprint),
        text(&trace.session.transcript_path),
        instant(at),
        text(&trace.extractor),
        text(&trace.extractor_version),
    ]
}

fn session(row: &Session) -> Vec<Value> {
    vec![
        text(&row.id),
        maybe_text(&row.project_dir),
        maybe_text(&row.git_branch),
        maybe_text(&row.version),
        maybe_text(&row.entrypoint),
        maybe_instant(row.started_at),
        maybe_instant(row.ended_at),
        Value::BigInt(row.active_ms),
        text(&row.transcript_path),
        maybe_text(&row.title),
        maybe_text(&row.agent_name),
    ]
}

fn turn(row: &Turn) -> Vec<Value> {
    vec![
        text(&row.id),
        text(&row.session_id),
        text(&row.source),
        Value::Int(row.index),
        text(&row.prompt),
        maybe_text(&row.command_name),
        maybe_text(&row.command_args),
        instant(row.started_at),
        instant(row.ended_at),
        Value::Boolean(row.replayed),
    ]
}

fn api_call(row: &ApiCall) -> Vec<Value> {
    vec![
        text(&row.id),
        text(&row.session_id),
        text(&row.source),
        maybe_text(&row.turn_id),
        Value::Int(row.index),
        text(&row.model),
        maybe_text(&row.fallback_from),
        maybe_text(&row.effort),
        maybe_text(&row.stop_reason),
        maybe_text(&row.attribution_skill),
        maybe_text(&row.request_id),
        instant(row.started_at),
        instant(row.ended_at),
        Value::BigInt(row.input_tokens),
        Value::BigInt(row.output_tokens),
        Value::BigInt(row.cache_read_tokens),
        Value::BigInt(row.cache_creation_tokens),
        maybe_big(row.cache_5m_tokens),
        maybe_big(row.cache_1h_tokens),
        text(&row.text),
        text(&row.thinking),
        row.cost_usd.map_or(Value::Null, Value::Double),
        Value::Boolean(row.synthetic),
        Value::Boolean(row.replayed),
    ]
}

fn tool_call(row: &ToolCall) -> Vec<Value> {
    vec![
        text(&row.id),
        text(&row.session_id),
        text(&row.source),
        text(&row.api_call_id),
        Value::Int(row.index),
        text(&row.name),
        Value::Boolean(row.server_side),
        text(&row.input),
        maybe_text(&row.result),
        maybe_text(&row.offload_file),
        Value::Boolean(row.is_error),
        Value::Boolean(row.incomplete),
        instant(row.started_at),
        maybe_instant(row.ended_at),
        Value::Boolean(row.duration_synthetic),
        Value::Boolean(row.replayed),
    ]
}

fn agent_run(row: &AgentRun) -> Vec<Value> {
    vec![
        text(&row.id),
        text(&row.session_id),
        maybe_text(&row.parent_agent_id),
        maybe_text(&row.tool_use_id),
        text(&row.agent_type),
        maybe_text(&row.brief),
        maybe_text(&row.model),
        maybe_text(&row.workflow_id),
        row.spawn_depth.map_or(Value::Null, Value::Int),
        Value::Boolean(row.is_fork),
        maybe_text(&row.fork_context_uuid),
        maybe_instant(row.started_at),
        maybe_instant(row.ended_at),
    ]
}

fn compaction(row: &Compaction) -> Vec<Value> {
    vec![
        text(&row.id),
        text(&row.session_id),
        text(&row.source),
        instant(row.timestamp),
        text(&row.trigger),
        Value::BigInt(row.pre_tokens),
        Value::BigInt(row.post_tokens),
        Value::BigInt(row.duration_ms),
    ]
}

fn pr_link(row: &PrLink) -> Vec<Value> {
    vec![
        text(&row.session_id),
        Value::Int(row.line_no),
        Value::Int(row.pr_number),
        text(&row.pr_url),
        text(&row.pr_repository),
        instant(row.timestamp),
    ]
}

fn offload_file(row: &OffloadFile) -> Vec<Value> {
    vec![
        text(&row.session_id),
        text(&row.name),
        text(&row.content),
        Value::Boolean(row.lossy_decode),
        Value::BigInt(row.size_bytes),
    ]
}

fn raw_record(row: &RawRecord) -> Vec<Value> {
    vec![
        text(&row.session_id),
        text(&row.source),
        Value::Int(row.line_no),
        maybe_text(&row.uuid),
        maybe_instant(row.timestamp),
        text(&row.r#type),
        text(&row.raw),
    ]
}

fn text(value: &str) -> Value {
    Value::Text(value.to_owned())
}

fn maybe_text(value: &Option<String>) -> Value {
    value.as_deref().map_or(Value::Null, text)
}

fn maybe_big(value: Option<i64>) -> Value {
    value.map_or(Value::Null, Value::BigInt)
}

/// An instant as the store keeps it: microseconds since the epoch, UTC. The column is
/// TIMESTAMPTZ and every connection runs `SET TimeZone='UTC'`, so no zone travels with it.
fn instant(value: DateTime<Utc>) -> Value {
    Value::Timestamp(TimeUnit::Microsecond, value.timestamp_micros())
}

fn maybe_instant(value: Option<DateTime<Utc>>) -> Value {
    value.map_or(Value::Null, instant)
}
