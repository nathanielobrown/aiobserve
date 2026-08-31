//! The enrichment tables and the views over them, and which level owns which.
//!
//! Ported statement-for-statement from `src/hyphae/enrich/store.py`, which stays the
//! authority until the Python pass retires. These tables sit in the trace store's own file
//! but outside the pipeline's per-session replace, so a re-extraction never touches them.

/// Every enrichment table holds the same columns; only the primary key differs.
const ENRICHMENT_COLUMNS: &str = r#"
  description VARCHAR NOT NULL,
  category VARCHAR NOT NULL,
  outcome VARCHAR NOT NULL,
  -- One line of visible struggle, NULL when the records showed none.
  friction VARCHAR,
  -- The four fields that decide staleness: sha256 of the rendered prompt content, the
  -- level's prompt version, the taxonomy version, and the model that answered.
  input_hash VARCHAR NOT NULL,
  prompt_version INTEGER NOT NULL,
  taxonomy_version INTEGER NOT NULL,
  model VARCHAR NOT NULL,
  enriched_at TIMESTAMPTZ NOT NULL,
"#;

/// The three tables and the four views, in the order they must be created.
pub fn ddl() -> String {
    format!(
        r#"
CREATE TABLE IF NOT EXISTS turn_enrichments (
  session_id VARCHAR NOT NULL, source VARCHAR NOT NULL, turn_id VARCHAR NOT NULL,
  {ENRICHMENT_COLUMNS}
  PRIMARY KEY (session_id, source, turn_id)
);
CREATE TABLE IF NOT EXISTS agent_run_enrichments (
  session_id VARCHAR NOT NULL, agent_run_id VARCHAR NOT NULL,
  {ENRICHMENT_COLUMNS}
  PRIMARY KEY (session_id, agent_run_id)
);
CREATE TABLE IF NOT EXISTS session_enrichments (
  session_id VARCHAR NOT NULL,
  {ENRICHMENT_COLUMNS}
  PRIMARY KEY (session_id)
);
-- The sessions enrichment describes, named once so the reader and the sweep cannot drift
-- apart: a session with no main turn and no agent run has nothing to describe, and one whose
-- turns drove no api call has no model response to describe. 45 recorded sessions are in the
-- second state — `/model` and `/effort` turns the CLI answered by itself — and the QC pass
-- found the model inventing work for them rather than reporting none.
CREATE OR REPLACE VIEW describable_sessions AS
SELECT * FROM session_rollups WHERE (turns > 0 OR agent_runs > 0) AND api_calls > 0;
-- LEFT join, so an un-enriched turn still appears and coverage reads honestly. The
-- enrichment's own model is renamed: `agent_runs` carries a `model` of its own, and the
-- three views answer the same question the same way.
CREATE OR REPLACE VIEW enriched_turns AS
SELECT t.*, e.description, e.category, e.outcome, e.friction, e.input_hash,
       e.prompt_version, e.taxonomy_version, e.model AS enrichment_model, e.enriched_at
FROM live_turns t
LEFT JOIN turn_enrichments e
  ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id;
-- `agent_runs` carries a `model` of its own — the model that ran it — so it keeps its
-- meaning under a name that says whose it is, and `description` means the enrichment's in
-- all three views. The run's own brief needs no such rename: it is `brief`.
CREATE OR REPLACE VIEW enriched_agent_runs AS
SELECT r.* EXCLUDE (model),
       r.model AS agent_model, e.description, e.category, e.outcome, e.friction, e.input_hash,
       e.prompt_version, e.taxonomy_version, e.model AS enrichment_model, e.enriched_at
FROM live_agent_runs r
LEFT JOIN agent_run_enrichments e
  ON e.session_id = r.session_id AND e.agent_run_id = r.id;
CREATE OR REPLACE VIEW enriched_sessions AS
SELECT r.*, e.description, e.category, e.outcome, e.friction, e.input_hash,
       e.prompt_version, e.taxonomy_version, e.model AS enrichment_model, e.enriched_at
FROM session_rollups r
LEFT JOIN session_enrichments e ON e.session_id = r.session_id;
"#
    )
}

/// The payload every level's insert writes, in the order the insert binds them.
pub const PAYLOAD_COLUMNS: &[&str] = &[
    "description",
    "category",
    "outcome",
    "friction",
    "input_hash",
    "prompt_version",
    "taxonomy_version",
    "model",
    "enriched_at",
];

/// The three things that get an enrichment row, each with its own table and prompt.
///
/// Closed set: a level without a table above cannot be written, and a table above with no
/// level here would never be swept.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Level {
    Turn,
    AgentRun,
    Session,
}

impl Level {
    /// Every level, in the order a pass walks them: children before the parents that embed
    /// their descriptions.
    pub const ALL: [Self; 3] = [Self::Turn, Self::AgentRun, Self::Session];

    /// The word an item key and a `level` column carry.
    pub fn word(self) -> &'static str {
        match self {
            Self::Turn => "turn",
            Self::AgentRun => "agent_run",
            Self::Session => "session",
        }
    }

    /// The level one word names, or nothing where the text says something else.
    pub fn of(word: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|level| level.word() == word)
    }

    /// What `input_hash` cannot see: the instructions and the output schema this level's
    /// rows were written under (`enrich/prompts.py:PROMPT_VERSION`).
    pub fn prompt_version(self) -> i64 {
        match self {
            Self::Turn | Self::AgentRun | Self::Session => 4,
        }
    }

    /// The table a pass writes this level's rows to.
    pub fn table(self) -> &'static str {
        match self {
            Self::Turn => "turn_enrichments",
            Self::AgentRun => "agent_run_enrichments",
            Self::Session => "session_enrichments",
        }
    }

    /// The enrichment table's primary key columns, in order.
    pub fn keys(self) -> &'static [&'static str] {
        match self {
            Self::Turn => &["session_id", "source", "turn_id"],
            Self::AgentRun => &["session_id", "agent_run_id"],
            Self::Session => &["session_id"],
        }
    }

    /// The view holding the rows this level describes.
    ///
    /// `live_turns`, not `turns`: a fork's replay of another transcript's turn is a copy, and
    /// the turn it copied is enriched under the transcript that ran it. `describable_sessions`,
    /// not `sessions`: a row for a session the pass will never refresh again is a zombie by
    /// the same definition as one whose session is gone.
    pub fn base(self) -> &'static str {
        match self {
            Self::Turn => "live_turns",
            Self::AgentRun => "live_agent_runs",
            Self::Session => "describable_sessions",
        }
    }

    /// The base view's columns matching [`Level::keys`], in the same order.
    pub fn base_keys(self) -> &'static [&'static str] {
        match self {
            Self::Turn => &["session_id", "source", "id"],
            Self::AgentRun => &["session_id", "id"],
            Self::Session => &["session_id"],
        }
    }
}

impl std::fmt::Display for Level {
    fn fmt(&self, into: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        into.write_str(self.word())
    }
}
