//! The dry run: what a send would ship, counted by shaping every session and sending nothing.
//!
//! The count is the one number an operator sees before spending an hour and a backend's
//! ingest quota, so it is the mapper's own answer rather than a SQL approximation of it.
//!
//! Ported from `src/hyphae/export/otlp_delivery.py`, which stays the authority.

use std::collections::HashMap;

use hyphae_model::SessionTrace;

use crate::otlp::{COMPACTION_SPAN, ShapeError, TextPolicy, copied_compaction, session_spans};

/// What a run would ship, counted by shaping every session and sending nothing.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Census {
    pub sessions: usize,
    pub spans: usize,
    /// Compactions that survive the copied-prefix replay rule. `live_compactions` does not
    /// reproduce this number — it keeps the copies a fork inherited — so a census that read
    /// the view would over-report every fork copy in the corpus.
    pub compactions: usize,
}

/// What counting refuses.
#[derive(Debug, thiserror::Error)]
pub enum CensusError {
    /// One compaction appears twice in a session and the copied-prefix rule keeps both.
    ///
    /// A duplicated id is a fork's copy of its parent's compaction, so exactly one copy is
    /// the live one. Two would ship one compaction as two spans, which is a rule we can no
    /// longer apply rather than a count to fudge.
    #[error(
        "session {session_id} holds compaction {compaction_id} {held} time(s) and the \
         copied-prefix rule keeps {live} of them. Exactly one copy is live; a fork shape this \
         rule cannot separate has landed."
    )]
    Ambiguous {
        session_id: String,
        compaction_id: String,
        held: usize,
        live: usize,
    },
    /// Boxed for the reason [`crate::delivery::DeliveryError`] boxes it: a bare `ShapeError`
    /// would put every `Result` here over the size clippy refuses.
    #[error(transparent)]
    Shape(Box<ShapeError>),
}

impl From<ShapeError> for CensusError {
    fn from(error: ShapeError) -> Self {
        CensusError::Shape(Box::new(error))
    }
}

impl Census {
    /// Count one more session, shaped exactly as `export()` shapes it.
    ///
    /// Crashes on a session whose duplicated compactions the replay rule cannot separate.
    pub fn add(&mut self, trace: &SessionTrace, text: &TextPolicy) -> Result<(), CensusError> {
        check_one_live_copy(trace)?;
        let shaped = session_spans(trace, text)?;
        self.sessions += 1;
        self.spans += shaped.len();
        self.compactions += shaped
            .iter()
            .filter(|span| span.name == COMPACTION_SPAN)
            .count();
        Ok(())
    }
}

/// Count what a send would put on the wire, without sending it.
///
/// Takes what the caller already holds; a run over a whole corpus folds through
/// [`Census::add`] instead, one session at a time.
pub fn census(traces: &[SessionTrace], text: &TextPolicy) -> Result<Census, CensusError> {
    let mut counted = Census::default();
    for trace in traces {
        counted.add(trace, text)?;
    }
    Ok(counted)
}

/// Every compaction id a session holds twice must keep exactly one live copy.
fn check_one_live_copy(trace: &SessionTrace) -> Result<(), CensusError> {
    let runs: HashMap<&str, &hyphae_model::AgentRun> = trace
        .agent_runs
        .iter()
        .map(|run| (run.id.as_str(), run))
        .collect();
    let mut held: HashMap<&str, usize> = HashMap::new();
    let mut live: HashMap<&str, usize> = HashMap::new();
    for compaction in &trace.compactions {
        *held.entry(compaction.id.as_str()).or_default() += 1;
        if !copied_compaction(compaction, runs.get(compaction.source.as_str()).copied())? {
            *live.entry(compaction.id.as_str()).or_default() += 1;
        }
    }
    for (compaction_id, count) in held {
        let living = live.get(compaction_id).copied().unwrap_or_default();
        if count > 1 && living != 1 {
            return Err(CensusError::Ambiguous {
                session_id: trace.session.id.clone(),
                compaction_id: compaction_id.to_owned(),
                held: count,
                live: living,
            });
        }
    }
    Ok(())
}
