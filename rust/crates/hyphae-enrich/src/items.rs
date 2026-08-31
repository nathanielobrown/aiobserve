//! What one enrichable thing looks like once the store has assembled it.
//!
//! Ported from the row half of `src/hyphae/enrich/prompts.py`. The render that turns one of
//! these into prompt text stays Python for now; what is here is the shape a row-reader
//! produces and a writer keys on.

use crate::schema::Level;

/// One thing that gets one enrichment row.
pub trait Item {
    fn level(&self) -> Level;

    /// The item's primary key, in the enrichment table's column order.
    fn key_values(&self) -> Vec<String>;

    /// The key as one string — what a request, a call log and a failure record carry.
    fn key(&self) -> String {
        let mut parts = vec![self.level().word().to_owned()];
        parts.extend(self.key_values());
        parts.join("|")
    }
}

/// The level of an item key, so a caller holding keys alone can still tell them apart.
pub fn level_of(key: &str) -> Option<Level> {
    Level::of(key.split('|').next().unwrap_or_default())
}

/// One tool call as a prompt sees it: what was asked, and how big the answer was.
///
/// The result text itself never travels — 390 MB corpus-wide — except the tail of a failed
/// one, which is where friction shows.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolCallRow {
    pub name: String,
    pub input: String,
    /// None when the call was never answered, which is what `incomplete` means.
    pub result: Option<String>,
    pub is_error: bool,
    pub incomplete: bool,
    /// The description of the agent run this call spawned — the one way a child's work
    /// reaches a parent's prompt. None when the call spawned nothing, when the run it spawned
    /// is not enriched yet, and when the run is the one being rendered: a fork's transcript
    /// holds a copy of its own spawning call, and a run does not embed itself.
    pub spawned: Option<String>,
}

/// One model response and the tools it asked for. `thinking` is deliberately absent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiCallRow {
    pub text: String,
    /// Why generation stopped, as recorded. None is a real recorded state — 26 of the 69
    /// stop reasons in the fixtures are null — and renders as "not recorded", never as
    /// absence.
    pub stop_reason: Option<String>,
    pub tool_calls: Vec<ToolCallRow>,
}

/// One main turn: the prompt a person wrote, and the work it drove.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TurnItem {
    pub session_id: String,
    pub source: String,
    pub turn_id: String,
    pub index: i32,
    /// As recorded, tags included. A slash turn renders `command_name`/`command_args` instead.
    pub prompt: String,
    pub command_name: Option<String>,
    pub command_args: Option<String>,
    /// What the CLI itself printed for a slash command. None means no record archived an
    /// answer; `""` means one did and it printed nothing. Most command turns drive no model
    /// response, so this is the only thing the render can say about what happened.
    pub command_result: Option<String>,
    pub api_calls: Vec<ApiCallRow>,
}

impl Item for TurnItem {
    fn level(&self) -> Level {
        Level::Turn
    }

    fn key_values(&self) -> Vec<String> {
        vec![
            self.session_id.clone(),
            self.source.clone(),
            self.turn_id.clone(),
        ]
    }
}

/// One stretch of a run's transcript: one instruction, and the calls it drove.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunSection {
    /// None for the calls a run made before any turn of its own — a fork continuing a
    /// conversation whose prompt lives in another transcript.
    pub prompt: Option<String>,
    pub api_calls: Vec<ApiCallRow>,
}

/// One subagent run: what it was asked, in sequence, and what it did about it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentRunItem {
    pub session_id: String,
    pub agent_run_id: String,
    pub agent_type: String,
    pub sections: Vec<RunSection>,
}

impl Item for AgentRunItem {
    fn level(&self) -> Level {
        Level::AgentRun
    }

    fn key_values(&self) -> Vec<String> {
        vec![self.session_id.clone(), self.agent_run_id.clone()]
    }
}

/// One thing a session did, as that thing's own enrichment described it.
///
/// No transcript text reaches a session prompt: a child that has not been described yet
/// renders as undescribed, which moves the session's hash again once it has been.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionChild {
    /// `Turn` for a main turn, `AgentRun` for a run nothing else in the session embeds.
    pub level: Level,
    /// The run's type — `architect`, `Explore`. None for a main turn.
    pub agent_type: Option<String>,
    pub description: Option<String>,
    pub category: Option<String>,
    pub outcome: Option<String>,
}

/// One whole session: what it cost, and what its children were described as doing.
#[derive(Debug, Clone, PartialEq)]
pub struct SessionItem {
    pub session_id: String,
    /// As Claude Code recorded them; either can be absent from an older transcript.
    pub title: Option<String>,
    pub git_branch: Option<String>,
    /// Wall time is the whole span, gaps included; active is what Claude Code reported
    /// working. Wall is None when the session's records carry no end.
    pub wall_ms: Option<i64>,
    pub active_ms: Option<i64>,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cache_read_tokens: i64,
    pub cache_creation_tokens: i64,
    /// Sums only the api calls the extractor could price.
    pub cost_usd: f64,
    /// The session's main turns and its rootless runs, in the order they started.
    pub children: Vec<SessionChild>,
}

impl SessionItem {
    /// A session item with nothing but its key — for a session the store hands none out for.
    ///
    /// The enrichment tables key on the session id alone, so this is everything an upsert or
    /// a sweep needs. No `Default`: every other field is a measurement, and a caller that
    /// wants one asks the store.
    pub fn bare(session_id: &str) -> Self {
        Self {
            session_id: session_id.to_owned(),
            title: None,
            git_branch: None,
            wall_ms: None,
            active_ms: None,
            input_tokens: 0,
            output_tokens: 0,
            cache_read_tokens: 0,
            cache_creation_tokens: 0,
            cost_usd: 0.0,
            children: Vec::new(),
        }
    }
}

impl Item for SessionItem {
    fn level(&self) -> Level {
        Level::Session
    }

    fn key_values(&self) -> Vec<String> {
        vec![self.session_id.clone()]
    }
}
