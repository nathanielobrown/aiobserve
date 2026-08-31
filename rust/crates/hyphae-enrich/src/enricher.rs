//! One enrichment run: what is stale, what gets sent, what comes back, and what failed.
//!
//! Ported from `src/hyphae/enrich/enricher.py`. Rerunning is the retry. A failed item writes no
//! row, so it is still stale next time, and a succeeded one is not — there is no resume state
//! to keep, and nothing to clean up after a crash.

use std::collections::{HashMap, HashSet};

use crate::client::{Answer, BatchClient, EnrichRequest, RoundError};
use crate::items::{Item, level_of};
use crate::prompts::{input_hash, instructions};
use crate::schema::Level;
use crate::store::{EnrichError, EnrichmentStore, Stamp};
use crate::taxonomy;
use crate::validation::{ItemFailure, validate};

/// One item that would be sent: what it renders to, and what its row would be stamped.
pub struct PlannedItem {
    pub item: Box<dyn Item>,
    pub rendered: String,
    pub stamp: Stamp,
}

/// What one run did. Returned only when every item succeeded.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EnrichReport {
    pub swept: usize,
    pub enriched: usize,
}

/// Why a pass wrote less than it was asked to.
///
/// [`PassError::Items`] names the failed keys and their kinds and has nowhere to put prose:
/// the summary is keys-only by construction rather than by discipline.
#[derive(Debug, thiserror::Error)]
pub enum PassError {
    /// Some items failed. Names their keys and how they failed — never what they said.
    #[error("{} item(s) failed, wrote nothing:\n{}", .0.len(), listed(.0))]
    Items(Vec<ItemFailure>),
    /// Every remaining run waits on another: a run spawned by its own descendant.
    #[error("agent run parentage has a cycle among {0} run(s)")]
    Cycle(usize),
    /// A key the round did not send, or one sent back twice: the client lost track of the
    /// batch, and the rows it would write belong to some other item.
    #[error("the client answered {0}, which it was not asked")]
    Unasked(String),
    #[error("the client left {0} request(s) unanswered")]
    Unanswered(usize),
    #[error(transparent)]
    Store(#[from] EnrichError),
    #[error(transparent)]
    Round(#[from] RoundError),
}

fn listed(failures: &[ItemFailure]) -> String {
    failures
        .iter()
        .map(|failure| format!("  {}: {}", failure.kind, failure.key))
        .collect::<Vec<_>>()
        .join("\n")
}

/// The levels a run describes, in the order it describes them: bottom-up, because every prompt
/// embeds its children's descriptions rather than their text. The agent runs are themselves
/// split into rounds by parentage. Every level [`crate::store`] can write has a round here —
/// one missing would be a level nothing ever describes.
pub const ROUND_ORDER: [Level; 3] = [Level::AgentRun, Level::Turn, Level::Session];

/// Every item a run would send now — an upper bound, for a dry run.
///
/// Hash-stale items plus every ancestor of one: a child's new description restates its
/// parents' prompts, and no read can tell in advance whether the new description will differ
/// from the old. A child re-described in the same words stops the cascade there and costs
/// less than this quotes.
pub fn plan(
    store: &EnrichmentStore,
    model: &str,
    project: Option<&str>,
    limit: Option<usize>,
) -> Result<Vec<PlannedItem>, PassError> {
    let parents = store.item_parents(project)?;
    let mut planned: Vec<PlannedItem> = Vec::new();
    let mut reached: HashSet<String> = HashSet::new();
    for level in ROUND_ORDER {
        let entries = plan_level(store, model, level, project)?;
        for key in store.stale_keys(level, &stamps(&entries))? {
            reached.extend(ancestors(&key, &parents));
            reached.insert(key);
        }
        planned.extend(entries);
    }
    planned.retain(|entry| reached.contains(&entry.item.key()));
    if let Some(limit) = limit {
        planned.truncate(limit);
    }
    Ok(planned)
}

/// Describe every stale item, write what came back, and crash if anything failed.
///
/// Runs go out deepest-first, one round per level of the spawn forest, and main turns last.
/// Every round re-reads and re-hashes its level *after* the previous round's upserts: that is
/// what carries a new child description up the tree, and planning the rounds up front would
/// look identical until the day a description changed.
pub fn enrich(
    store: &EnrichmentStore,
    client: &dyn BatchClient,
    project: Option<&str>,
    limit: Option<usize>,
) -> Result<EnrichReport, PassError> {
    let swept = store.sweep_zombies()?;
    let parents = store.item_parents(project)?;
    let mut rounds: Vec<(Level, Option<HashSet<String>>)> = run_rounds(&parents)?
        .into_iter()
        .map(|keys| (Level::AgentRun, Some(keys)))
        .collect();
    // None: every item of the level. Turns and sessions are one round each — no turn embeds
    // another turn, and no session embeds another session.
    rounds.push((Level::Turn, None));
    rounds.push((Level::Session, None));
    let (mut enriched, mut remaining) = (0, limit);
    let mut failures: Vec<ItemFailure> = Vec::new();
    // Items whose prompts embed something that failed. Writing one bakes a hole into a
    // description that the hash then calls current forever — the one failure a rerun cannot
    // heal, so a blocked item writes nothing and stays stale.
    let mut blocked: HashSet<String> = HashSet::new();
    for (level, keys) in rounds {
        if remaining == Some(0) {
            break;
        }
        let entries = plan_level(store, client.model(), level, project)?;
        let stale = store.stale_keys(level, &stamps(&entries))?;
        let mut by_key: HashMap<String, PlannedItem> = entries
            .into_iter()
            .map(|entry| (entry.item.key(), entry))
            .collect();
        let mut sending: Vec<PlannedItem> = stale
            .into_iter()
            .filter(|key| {
                !blocked.contains(key) && keys.as_ref().is_none_or(|round| round.contains(key))
            })
            .map(|key| by_key.remove(&key).expect("a stale key was planned"))
            .collect();
        if let Some(left) = remaining {
            sending.truncate(left);
            remaining = Some(left - sending.len());
        }
        let (count, round_failures) = round(store, client, &sending)?;
        enriched += count;
        for failure in &round_failures {
            blocked.extend(ancestors(&failure.key, &parents));
        }
        failures.extend(round_failures);
    }
    if !failures.is_empty() {
        return Err(PassError::Items(failures));
    }
    Ok(EnrichReport { swept, enriched })
}

/// One level's items, rendered and stamped as they stand right now.
///
/// Reads and renders only — a dry run calls this and writes nothing. Call it when the round
/// starts, never earlier: a child's new description changes what its parents render to.
fn plan_level(
    store: &EnrichmentStore,
    model: &str,
    level: Level,
    project: Option<&str>,
) -> Result<Vec<PlannedItem>, EnrichError> {
    Ok(store
        .items(level, project)?
        .into_iter()
        .map(|item| {
            let rendered = item.render();
            PlannedItem {
                stamp: Stamp {
                    input_hash: input_hash(&rendered),
                    prompt_version: level.prompt_version(),
                    taxonomy_version: taxonomy::enrichment().taxonomy_version,
                    model: model.to_owned(),
                },
                item,
                rendered,
            }
        })
        .collect())
}

/// What [`EnrichmentStore::stale_keys`] compares against, in the order the items came back.
fn stamps(entries: &[PlannedItem]) -> Vec<(String, Stamp)> {
    entries
        .iter()
        .map(|entry| (entry.item.key(), entry.stamp.clone()))
        .collect()
}

/// The agent runs grouped so that every run follows the runs it spawned.
///
/// Grouped by height rather than depth: a leaf goes in the first round whatever tree it
/// belongs to, so the whole store's forest is described in as many rounds as its deepest
/// branch has levels. Only the runs: a run whose parent is a turn or a session waits for
/// nothing, because those levels come after every round here.
fn run_rounds(parents: &HashMap<String, String>) -> Result<Vec<HashSet<String>>, PassError> {
    let mut waiting: HashMap<&str, HashSet<&str>> = parents
        .keys()
        .filter(|key| level_of(key) == Some(Level::AgentRun))
        .map(|key| (key.as_str(), HashSet::new()))
        .collect();
    for (key, parent) in parents {
        if let Some(children) = waiting.get_mut(parent.as_str()) {
            children.insert(key.as_str());
        }
    }
    let mut rounds: Vec<HashSet<String>> = Vec::new();
    let mut described: HashSet<&str> = HashSet::new();
    while !waiting.is_empty() {
        let ready: HashSet<&str> = waiting
            .iter()
            .filter(|(_, children)| children.is_subset(&described))
            .map(|(key, _)| *key)
            .collect();
        if ready.is_empty() {
            return Err(PassError::Cycle(waiting.len()));
        }
        waiting.retain(|key, _| !ready.contains(key));
        rounds.push(ready.iter().map(|key| (*key).to_owned()).collect());
        described.extend(ready);
    }
    Ok(rounds)
}

/// Every item whose prompt embeds `key`, directly or through another item.
fn ancestors(key: &str, parents: &HashMap<String, String>) -> HashSet<String> {
    let mut found: HashSet<String> = HashSet::new();
    let mut parent = parents.get(key);
    while let Some(held) = parent {
        if !found.insert(held.clone()) {
            break;
        }
        parent = parents.get(held);
    }
    found
}

/// Send one level's stale items and write the answers, one row per success.
fn round(
    store: &EnrichmentStore,
    client: &dyn BatchClient,
    planned: &[PlannedItem],
) -> Result<(usize, Vec<ItemFailure>), PassError> {
    if planned.is_empty() {
        return Ok((0, Vec::new()));
    }
    let by_key: HashMap<String, &PlannedItem> = planned
        .iter()
        .map(|entry| (entry.item.key(), entry))
        .collect();
    let requests: Vec<EnrichRequest> = planned
        .iter()
        .map(|entry| EnrichRequest {
            key: entry.item.key(),
            instructions: instructions(entry.item.level()),
            content: entry.rendered.clone(),
        })
        .collect();
    let results = client.submit(&requests)?;
    let mut answered: HashSet<&str> = HashSet::new();
    let mut failures: Vec<ItemFailure> = Vec::new();
    for result in &results {
        let key = result.key();
        // A key we did not send, or one sent back twice, means the client lost track of the
        // batch — the rows it would write belong to some other item.
        let Some(entry) = by_key.get(key) else {
            return Err(PassError::Unasked(key.to_owned()));
        };
        if !answered.insert(key) {
            return Err(PassError::Unasked(key.to_owned()));
        }
        match result {
            Answer::Failed { kind, .. } => failures.push(ItemFailure {
                key: key.to_owned(),
                kind: *kind,
            }),
            Answer::Succeeded { output, .. } => match validate(output) {
                Err(invalid) => failures.push(ItemFailure {
                    key: key.to_owned(),
                    kind: invalid.kind,
                }),
                Ok(enrichment) => store.upsert(entry.item.as_ref(), &enrichment, &entry.stamp)?,
            },
        }
    }
    let missing = by_key.len() - answered.len();
    if missing > 0 {
        return Err(PassError::Unanswered(missing));
    }
    Ok((by_key.len() - failures.len(), failures))
}
