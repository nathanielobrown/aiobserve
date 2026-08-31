//! What one recorded session becomes on the wire: a trace, its spans, and their ids.
//!
//! The ids, the span envelope and the attribute encoding are here; what each kind of store
//! row becomes is [`spans`]. Ported from `src/hyphae/export/otlp.py`, which stays the
//! authority.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use chrono::{DateTime, TimeDelta, Utc};
use hyphae_model::{
    AgentRun, ApiCall, Compaction, MAIN_SOURCE, PrLink, Session, SessionTrace, ToolCall, Turn,
};
use opentelemetry_proto::tonic::common::v1::{AnyValue, KeyValue, any_value};
use opentelemetry_proto::tonic::resource::v1::Resource;
use opentelemetry_proto::tonic::trace::v1::{Span, span};
use sha2::{Digest, Sha256};

/// The span-shaping version. A row in `otlp_delivery` recorded under an older one is treated
/// as undelivered, so a shaping change re-sends the corpus the way an extractor upgrade
/// re-extracts it. Bump it whenever what a session becomes changes.
pub const MAPPER_VERSION: &str = "1";

/// The two OTLP span kinds this exporter emits.
pub const INTERNAL: i32 = span::SpanKind::Internal as i32;
pub const CLIENT: i32 = span::SpanKind::Client as i32;

/// The span name every compaction gets, which is also how a census recognizes one.
pub const COMPACTION_SPAN: &str = "claude_code.compaction";

/// The delimiter the id keys join on, and the invariant every component must hold to.
pub const DELIMITER: char = '/';

/// A span with no positive duration renders as an invisible sliver, and the store holds
/// plenty (a turn whose only record is its prompt). One millisecond is the floor.
pub const MINIMUM_DURATION_MS: i64 = 1;

/// How much of an opted-in text field ships. Attributes are an ingest and a context cost, and
/// a whole tool result can be megabytes. Truncation is not redaction — a credential fits in
/// 200 characters — which is why text is opt-in rather than truncated-by-default.
pub const DEFAULT_MAX_CHARS: usize = 500;

/// The `kind` slot of a span id key — the store table the span came from.
///
/// Its values are part of every span id, so renaming one re-ids every span of that kind and
/// re-sends the corpus as a second copy. Change one only with [`MAPPER_VERSION`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SpanKey {
    Session,
    Turn,
    ApiCall,
    ToolCall,
    AgentRun,
    Compaction,
}

impl SpanKey {
    /// The string that enters the hash. These are the Python `StrEnum`'s values, and they are
    /// the wire contract rather than a display detail.
    pub fn as_str(self) -> &'static str {
        match self {
            SpanKey::Session => "session",
            SpanKey::Turn => "turn",
            SpanKey::ApiCall => "api_call",
            SpanKey::ToolCall => "tool_call",
            SpanKey::AgentRun => "agent_run",
            SpanKey::Compaction => "compaction",
        }
    }
}

/// Why a recorded session could not be shaped.
///
/// Every variant is a shape the source filter is supposed to have excluded or that no
/// recorded session holds, so each one stops the run rather than guessing. None carries
/// transcript text — only ids, keys and timestamps.
#[derive(Debug, thiserror::Error)]
pub enum ShapeError {
    #[error(
        "{component:?} holds {DELIMITER:?}, which the span-id key joins on. An id that carries \
         the delimiter is schema drift we need to see."
    )]
    AmbiguousKey { component: String },
    #[error(
        "Session {session_id} records no timestamps but reached the mapper. The source filter \
         excludes the sessions that hold none, so this is schema drift."
    )]
    TimelessSession { session_id: String },
    #[error(
        "Session {session_id} records no project_dir, so it has no service name. The source \
         filter excludes those, so this is schema drift."
    )]
    PlacelessSession { session_id: String },
    #[error(
        "Agent run {run_id} of session {session_id} records no timestamps, so its span cannot \
         be timed. The model permits it and no recorded run does it."
    )]
    TimelessRun { run_id: String, session_id: String },
    #[error(
        "Compaction {compaction_id} of session {session_id} is timestamped {timestamp}, before \
         its non-fork run {thread} started at {started_at}. Only a fork can hold a copy, so \
         this is schema drift."
    )]
    CompactionBeforeRun {
        compaction_id: String,
        session_id: String,
        timestamp: String,
        /// The `source` column, spelled otherwise: `source` is thiserror's own field name
        /// for a wrapped error.
        thread: String,
        started_at: String,
    },
    #[error(
        "Api call {call_id} of session {session_id} names turn {turn_id} in source {thread}, \
         which the trace does not hold."
    )]
    UnparentedCall {
        call_id: String,
        session_id: String,
        turn_id: String,
        thread: String,
    },
}

type Result<T> = std::result::Result<T, ShapeError>;

/// Whether transcript-derived text ships, and how much of each field.
///
/// Text is untrusted and POSTing it to a third party publishes it, so the default policy
/// sends none of it. `--include-text` swaps in an including one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TextPolicy {
    pub include: bool,
    /// Characters kept per field, applied only when `include` is set.
    pub max_chars: usize,
}

pub const METADATA_ONLY: TextPolicy = TextPolicy {
    include: false,
    max_chars: DEFAULT_MAX_CHARS,
};

/// The 16-byte trace id of a session. Digest bytes, never hex characters.
pub fn trace_id(session_id: &str) -> Vec<u8> {
    Sha256::digest(session_id.as_bytes())[..16].to_vec()
}

/// The 8-byte span id of one row, from the composite key the store already holds.
///
/// `source` is empty for rows keyed without one — an agent run, or the session itself.
/// Crashes when a component holds `/`: no shipped row across the canonical store does, and
/// absorbing one would silently collapse two rows into one span.
pub fn span_id(session_id: &str, kind: SpanKey, source: &str, natural_id: &str) -> Result<Vec<u8>> {
    let components = [session_id, kind.as_str(), source, natural_id];
    for component in components {
        if component.contains(DELIMITER) {
            return Err(ShapeError::AmbiguousKey {
                component: component.to_owned(),
            });
        }
    }
    Ok(Sha256::digest(components.join("/").as_bytes())[..8].to_vec())
}

/// Every span one session becomes, root first.
///
/// Replayed rows emit nothing: a fork's copy of its parent's transcript would double-count in
/// every backend aggregation. Compactions carry no such flag, so [`copied_compaction`] derives
/// one. A tool call that started a subagent becomes that subagent's span rather than a span of
/// its own.
pub fn session_spans(trace: &SessionTrace, text: &TextPolicy) -> Result<Vec<Span>> {
    let session = &trace.session;
    let (Some(started_at), Some(ended_at)) = (session.started_at, session.ended_at) else {
        return Err(ShapeError::TimelessSession {
            session_id: session.id.clone(),
        });
    };
    let turns: HashMap<(&str, &str), &Turn> = trace
        .turns
        .iter()
        .map(|turn| ((turn.source.as_str(), turn.id.as_str()), turn))
        .collect();
    let runs: HashMap<&str, &AgentRun> = trace
        .agent_runs
        .iter()
        .map(|run| (run.id.as_str(), run))
        .collect();
    let live_tools: Vec<&ToolCall> = trace.tool_calls.iter().filter(|c| !c.replayed).collect();
    // The live tool call each run named as its launch, if this trace holds one. Replayed
    // copies are excluded first: matching one would collapse a span that never ships.
    let launched: HashSet<&str> = trace
        .agent_runs
        .iter()
        .filter_map(|run| run.tool_use_id.as_deref())
        .collect();
    let spawns: HashMap<&str, &ToolCall> = live_tools
        .iter()
        .filter(|call| launched.contains(call.id.as_str()))
        .map(|call| (call.id.as_str(), *call))
        .collect();
    let mut children = Vec::new();
    for turn in trace.turns.iter().filter(|turn| !turn.replayed) {
        children.push(turn_span(session, turn, text)?);
    }
    for call in trace.api_calls.iter().filter(|call| !call.replayed) {
        children.push(chat_span(session, call, &turns, text)?);
    }
    for call in live_tools
        .iter()
        .filter(|call| !spawns.contains_key(call.id.as_str()))
    {
        children.push(tool_span(session, call, text)?);
    }
    for run in &trace.agent_runs {
        let spawn = run
            .tool_use_id
            .as_deref()
            .and_then(|id| spawns.get(id).copied());
        children.push(run_span(session, run, spawn, &runs, text)?);
    }
    for compaction in &trace.compactions {
        if !copied_compaction(compaction, runs.get(compaction.source.as_str()).copied())? {
            children.push(compaction_span(session, compaction)?);
        }
    }
    let root = root_span(trace, started_at, ended_at, &children, text)?;
    let mut spans = Vec::with_capacity(children.len() + 1);
    spans.push(root);
    spans.extend(children);
    Ok(spans)
}

/// What every span of a session is attributed to.
///
/// `service.name` is the project directory's name, which is what routes a Honeycomb dataset
/// per project; `--service-name` overrides it for a one-off run.
pub fn session_resource(session: &Session, service_name: Option<&str>) -> Result<Resource> {
    let service = match (service_name, session.project_dir.as_deref()) {
        (Some(named), _) => named.to_owned(),
        (None, Some(directory)) => Path::new(directory)
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_default(),
        (None, None) => {
            return Err(ShapeError::PlacelessSession {
                session_id: session.id.clone(),
            });
        }
    };
    Ok(Resource {
        attributes: attributes(vec![
            ("service.name", Some(Attr::Str(service))),
            (
                "hyphae.exporter.version",
                Some(Attr::Str(MAPPER_VERSION.to_owned())),
            ),
            // These spans were shipped from the store after the fact, not emitted live by the
            // agent — a distinction a backend query cannot recover.
            (
                "hyphae.telemetry.source",
                Some(Attr::Str("store-export".to_owned())),
            ),
        ]),
        ..Default::default()
    })
}

/// What each kind of store row becomes.
mod spans;

pub use spans::copied_compaction;
use spans::{chat_span, compaction_span, root_span, run_span, tool_span, turn_span};

/// One span, with its duration floored and its empty attributes dropped.
#[allow(clippy::too_many_arguments)]
fn build_span(
    session_id: &str,
    span: Vec<u8>,
    parent: Vec<u8>,
    name: &str,
    kind: i32,
    started_at: DateTime<Utc>,
    ended_at: DateTime<Utc>,
    values: Vec<(&str, Option<Attr>)>,
    events: Vec<span::Event>,
) -> Result<Span> {
    let floor = started_at + TimeDelta::milliseconds(MINIMUM_DURATION_MS);
    Ok(Span {
        trace_id: trace_id(session_id),
        span_id: span,
        parent_span_id: parent,
        name: name.to_owned(),
        kind,
        start_time_unix_nano: nanos(started_at),
        end_time_unix_nano: nanos(ended_at.max(floor)),
        attributes: attributes(values),
        events,
        ..Default::default()
    })
}

/// One attribute value, typed. OTLP's other value kinds have no source in the store, so a
/// mapper bug cannot reach the wire as an untyped blob — it will not compile.
#[derive(Debug, Clone, PartialEq)]
pub enum Attr {
    Bool(bool),
    Int(i64),
    Double(f64),
    Str(String),
}

/// The non-empty entries as OTLP attributes.
///
/// `None` is dropped rather than sent: OTLP has no null, and an absent attribute is how the
/// wire says a column held nothing.
fn attributes(values: Vec<(&str, Option<Attr>)>) -> Vec<KeyValue> {
    values
        .into_iter()
        .filter_map(|(key, value)| {
            value.map(|value| KeyValue {
                key: key.to_owned(),
                value: Some(any_value(value)),
                ..Default::default()
            })
        })
        .collect()
}

fn any_value(value: Attr) -> AnyValue {
    let value = match value {
        Attr::Bool(value) => any_value::Value::BoolValue(value),
        Attr::Int(value) => any_value::Value::IntValue(value),
        Attr::Double(value) => any_value::Value::DoubleValue(value),
        Attr::Str(value) => any_value::Value::StringValue(value),
    };
    AnyValue { value: Some(value) }
}

/// A metadata string, which ships whatever the text policy says.
fn str_attr(value: Option<&str>) -> Option<Attr> {
    value.map(|value| Attr::Str(value.to_owned()))
}

/// A boolean column, sent only when it is set — the wire twin of Python's `value or None`.
fn flag(value: bool) -> Option<Attr> {
    value.then_some(Attr::Bool(true))
}

/// One transcript-derived string, truncated — or nothing at all, which is the default.
///
/// The cut counts characters, not bytes: a byte slice would split a multi-byte character and
/// the value would not be UTF-8.
fn text_attr(policy: &TextPolicy, value: Option<&str>) -> Option<Attr> {
    if !policy.include {
        return None;
    }
    value.map(|value| Attr::Str(value.chars().take(policy.max_chars).collect()))
}

/// Nanoseconds since the epoch, at the microsecond resolution the transcripts record.
fn nanos(value: DateTime<Utc>) -> u64 {
    (value.timestamp_micros() * 1_000) as u64
}

fn from_nanos(value: u64) -> DateTime<Utc> {
    DateTime::from_timestamp_micros((value / 1_000) as i64).expect("a shipped span's own end")
}

/// One instant as Python's `datetime.isoformat()` writes it, which is what ships.
///
/// The transcripts are read as UTC-aware, so the offset is always `+00:00`, and Python omits
/// the fractional part entirely when the microseconds are zero.
fn iso(value: DateTime<Utc>) -> String {
    if value.timestamp_subsec_micros() == 0 {
        return value.format("%Y-%m-%dT%H:%M:%S+00:00").to_string();
    }
    value.format("%Y-%m-%dT%H:%M:%S%.6f+00:00").to_string()
}
