//! What a test drives an enrichment pass with, and how it reads back what the pass wrote.
//!
//! Ported from `tests/enrich/passes.py`. [`FakeClient`] records what it was asked and answers
//! from a script, so every claim about rounds, staleness and failure handling is checked
//! against real rows without a request leaving the machine. Its answers are invented, as model
//! output must be — there is no recorded session to draw them from. The readers below turn the
//! store back into the keys and stamps a leaf asserts on.

use std::collections::HashMap;
use std::sync::Mutex;

use hyphae_enrich::client::{Answer, BatchClient, EnrichRequest, RoundError};
use hyphae_enrich::{EnrichmentStore, Item, Level};
use serde_json::{Map, Value, json};

use crate::fake_cli::MODEL;

/// An invented credential, in a shape the screen knows, for the answer that must be refused.
pub const FAKE_SECRET: &str = "AKIAIOSFODNN7EXAMPLE";

/// A sentinel inside an out-of-vocabulary answer: if it reaches the crash summary, so would
/// whatever a real answer had said there.
pub const FAKE_CATEGORY: &str = "SENTINEL-5c1a-out-of-vocabulary";

/// Answers every request, records every round, and never starts a process.
///
/// `answers` overrides the reply for one key — a failure, or an answer the validator will
/// refuse. Everything else gets a well-formed description naming its own key, so a row can be
/// traced back to the request that wrote it.
pub struct FakeClient {
    model: String,
    answers: HashMap<String, Answer>,
    rounds: Mutex<Vec<Vec<EnrichRequest>>>,
}

impl FakeClient {
    /// A client that answers everything well-formedly, as the corpus model.
    pub fn new() -> Self {
        Self::answering(MODEL, [])
    }

    /// The same, under another model — what `--model` switching looks like from the store.
    pub fn as_model(model: &str) -> Self {
        Self::answering(model, [])
    }

    /// A client whose named keys answer as scripted and whose others answer well-formedly.
    pub fn answering(model: &str, answers: impl IntoIterator<Item = Answer>) -> Self {
        Self {
            model: model.to_owned(),
            answers: answers
                .into_iter()
                .map(|answer| (answer.key().to_owned(), answer))
                .collect(),
            rounds: Mutex::new(Vec::new()),
        }
    }

    /// What the client was asked, one entry per round, in the order the rounds went out.
    pub fn rounds(&self) -> Vec<Vec<EnrichRequest>> {
        self.rounds.lock().expect("the rounds read").clone()
    }

    /// Every key the client was asked about, in the order it was asked.
    pub fn keys(&self) -> Vec<String> {
        self.rounds()
            .iter()
            .flatten()
            .map(|request| request.key.clone())
            .collect()
    }

    /// The keys of one round, as a set: within a round the order is the store's, not the
    /// ordering rule's.
    pub fn round_keys(&self) -> Vec<std::collections::BTreeSet<String>> {
        self.rounds()
            .iter()
            .map(|sent| sent.iter().map(|request| request.key.clone()).collect())
            .collect()
    }
}

impl Default for FakeClient {
    fn default() -> Self {
        Self::new()
    }
}

impl BatchClient for FakeClient {
    fn model(&self) -> &str {
        &self.model
    }

    fn submit(&self, requests: &[EnrichRequest]) -> Result<Vec<Answer>, RoundError> {
        self.rounds
            .lock()
            .expect("the rounds record")
            .push(requests.to_vec());
        Ok(requests
            .iter()
            .map(|request| {
                self.answers
                    .get(&request.key)
                    .cloned()
                    .unwrap_or_else(|| Answer::Succeeded {
                        key: request.key.clone(),
                        output: answer(&request.key),
                    })
            })
            .collect())
    }
}

/// A well-formed model answer (invented) for one item.
pub fn answer(key: &str) -> Map<String, Value> {
    answering(key, json!({}))
}

/// The same answer with fields replaced — the door to an answer the validator refuses.
pub fn answering(key: &str, overrides: Value) -> Map<String, Value> {
    let mut built = json!({
        "description": format!("Described {key}."),
        "category": "test",
        "outcome": "completed",
        "friction": Value::Null,
    });
    let built = built.as_object_mut().expect("the answer is an object");
    for (field, value) in overrides.as_object().expect("the overrides are an object") {
        built.insert(field.clone(), value.clone());
    }
    built.clone()
}

/// A scripted success for one key, with fields replaced.
pub fn succeeding(key: &str, overrides: Value) -> Answer {
    Answer::Succeeded {
        key: key.to_owned(),
        output: answering(key, overrides),
    }
}

/// The item key one agent run is sent and stored under.
pub fn run_key(store: &EnrichmentStore, agent_run_id: &str) -> String {
    keyed(store, Level::AgentRun, |item| {
        item.key().ends_with(&format!("|{agent_run_id}"))
    })
}

/// The item key of the one main turn whose id starts with `prefix`.
pub fn turn_key(store: &EnrichmentStore, session_id: &str, prefix: &str) -> String {
    keyed(store, Level::Turn, |item| {
        item.key()
            .starts_with(&format!("turn|{session_id}|main|{prefix}"))
    })
}

/// The item key one session is sent and stored under.
pub fn session_key(session_id: &str) -> String {
    format!("session|{session_id}")
}

/// Every item key of one level, in the order a pass reads them out of the store.
pub fn level_keys(store: &EnrichmentStore, level: Level) -> Vec<String> {
    store
        .items(level, None)
        .expect("the level reads")
        .iter()
        .map(|item| item.key())
        .collect()
}

fn keyed(store: &EnrichmentStore, level: Level, matches: impl Fn(&dyn Item) -> bool) -> String {
    let mut found: Vec<String> = store
        .items(level, None)
        .expect("the level reads")
        .iter()
        .filter(|item| matches(item.as_ref()))
        .map(|item| item.key())
        .collect();
    assert_eq!(found.len(), 1, "{level} named {} items", found.len());
    found.remove(0)
}
