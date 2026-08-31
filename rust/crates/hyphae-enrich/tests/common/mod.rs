//! The fixture keys and the scratch store both halves of the enrichment suite read.
//!
//! `tests/enrich/test_store.py` is one file; the Rust port is over the repo's length budget as
//! one, so it is two binaries over one set of helpers. Nothing here is a leaf.
//!
//! Every leaf takes a writable copy: enrichment opens a store for writing, and a write lock on
//! the shared cache would refuse every read-only open running beside it
//! (`hyphae_testsupport::cache`). The counts either binary asserts differ from the Python
//! file's — that suite picks 11 fixture directories and this corpus is the whole clean 18 — so
//! each one was recounted against this store rather than lifted.

// Each binary uses part of this, and cargo compiles it into both.
#![allow(dead_code)]

use std::collections::BTreeMap;

use duckdb::params;
use hyphae_enrich::{
    AgentRunItem, Enrichment, EnrichmentStore, Item, Level, SessionItem, Stamp, TurnItem,
};
use hyphae_testsupport::{cache, metadata, passes};
use tempfile::TempDir;

// The recorded sessions this file names, declared once in `hyphae_testsupport::landmarks` and
// bound here under the names this suite reads them by. Aliases rather than a `pub use`: cargo
// compiles this module into both binaries, and a re-export the other one happens not to name is
// an unused import.
use hyphae_testsupport::landmarks;

pub const MYCELIA: &str = landmarks::MYCELIA;
pub const SPINE: &str = landmarks::SPINE;
pub const SPINE_RUN: &str = landmarks::SPINE_RUN;
pub const SPINE_LEAF: &str = landmarks::SPINE_LEAF;
pub const SPINE_MODEL_TURN: &str = landmarks::SPINE_MODEL_TURN;
pub const SPINE_MODEL_LINE: i64 = landmarks::SPINE_MODEL_LINE;
pub const STOP_REASON_TURN: &str = landmarks::STOP_REASON_TURN;
pub const RESUME: &str = landmarks::RESUME;
pub const MODEL_ONLY: &str = landmarks::MODEL_ONLY;
pub const DUP_UUID: &str = landmarks::DUP_UUID;
pub const WORKTREE_SESSION: &str = landmarks::WORKTREE_SESSION;
pub const SERVER_TOOLS: &str = landmarks::SERVER_TOOLS;
pub const TEAMMATE: &str = landmarks::TEAMMATE;
pub const TEAMMATE_RUN: &str = landmarks::TEAMMATE_RUN;
pub const BYREF_FORK: &str = landmarks::BYREF_FORK;
pub const FORK_ORIGIN_RUN: &str = landmarks::FORK_ORIGIN_RUN;
pub const FORK_RUN: &str = landmarks::FORK_RUN;
pub const DEEP_RESEARCH_SESSION: &str = landmarks::DEEP_RESEARCH_SESSION;
pub const WORKFLOW_AGENT: &str = landmarks::WORKFLOW_AGENT;

/// `resume_pair/`'s ancestor and the plain turn its stdout record hangs off.
pub const RESUME_ANCESTOR: &str = landmarks::ANCESTOR;
pub const RESUME_PLAIN_TURN: &str = landmarks::DENSE_TURN;
/// The invented fixture with neither a turn nor a run: the third session with nothing to
/// describe, and the one the Python suite's corpus leaves out.
pub const INVENTED_EMPTY: &str = landmarks::INVENTED_PROJECT_SESSION;
/// A fork whose only work is a subagent's — no turn of its own, and every call under the run.
pub const FORK_BYREF: &str = landmarks::NO_PROJECT_SESSION;
/// The second session the project filter re-homes.
pub const NEIGHBOUR_SESSION: &str = landmarks::TEAMMATE;

/// The sessions this corpus hands out to describe: 18 recorded, three with nothing in them.
pub const DESCRIBABLE: usize = 13;

/// A private copy of the cached corpus, open for enrichment.
///
/// The `TempDir` comes back with the store: dropping it deletes the file the store is on.
pub fn open_copy() -> (TempDir, EnrichmentStore) {
    let (scratch, path) = cache::writable_copy(&cache::corpus_store());
    let store = EnrichmentStore::open(&path).expect("the copy opens for enrichment");
    (scratch, store)
}

/// What a planted row says. Invented, as any model answer in a test must be.
pub fn enrichment(description: &str) -> Enrichment {
    let vocabulary = metadata::enrichment();
    Enrichment {
        description: description.to_owned(),
        // From the bridged vocabulary rather than a third hand copy of the taxonomy.
        category: vocabulary.categories[0].clone(),
        outcome: vocabulary.outcomes[0].clone(),
        friction: None,
    }
}

/// What a planted row was written under, at a version nothing reads as drift.
pub fn stamp(input_hash: &str) -> Stamp {
    Stamp {
        input_hash: input_hash.to_owned(),
        prompt_version: 1,
        taxonomy_version: metadata::enrichment().taxonomy_version,
        model: "claude-haiku-4-5-20251001".to_owned(),
    }
}

pub fn spine_turns(store: &EnrichmentStore) -> Vec<TurnItem> {
    store
        .turn_items(None)
        .expect("the turns read")
        .into_iter()
        .filter(|item| item.session_id == SPINE)
        .collect()
}

pub fn spine_runs(store: &EnrichmentStore) -> Vec<AgentRunItem> {
    store
        .run_items(None)
        .expect("the runs read")
        .into_iter()
        .filter(|item| item.session_id == SPINE)
        .collect()
}

/// Add one raw transcript record to a session's archive, at a line of its own.
pub fn plant_record(store: &EnrichmentStore, session_id: &str, line_no: i64, record: &str) {
    store
        .connection()
        .execute(
            "INSERT INTO raw_records (session_id, source, line_no, uuid, timestamp, type, raw)
             VALUES (?, 'main', ?, ?, now(), 'user', ?)",
            params![session_id, line_no, format!("planted-{line_no}"), record],
        )
        .expect("the record plants");
}

/// One column of a table, as strings, in the order the query names.
pub fn column(store: &EnrichmentStore, sql: &str) -> Vec<Option<String>> {
    store
        .store()
        .fetch(sql, &[])
        .expect("the query runs")
        .iter()
        .map(|row| {
            row.opt_str(row.columns()[0].as_str())
                .expect("the column reads")
                .map(str::to_owned)
        })
        .collect()
}

/// The one main turn of `session_id` whose id starts with `prefix`.
///
/// Each picker asserts it named exactly one item: a fixture that stops carrying the shape fails
/// here rather than rendering something else.
pub fn turn(store: &EnrichmentStore, session_id: &str, prefix: &str) -> TurnItem {
    let mut found: Vec<TurnItem> = store
        .turn_items(None)
        .expect("the turns read")
        .into_iter()
        .filter(|item| item.session_id == session_id && item.turn_id.starts_with(prefix))
        .collect();
    assert_eq!(found.len(), 1, "{prefix} named {} turns", found.len());
    found.remove(0)
}

/// The store's one agent run with this id.
pub fn run(store: &EnrichmentStore, agent_run_id: &str) -> AgentRunItem {
    let mut found: Vec<AgentRunItem> = store
        .run_items(None)
        .expect("the runs read")
        .into_iter()
        .filter(|item| item.agent_run_id == agent_run_id)
        .collect();
    assert_eq!(found.len(), 1, "{agent_run_id} named {} runs", found.len());
    found.remove(0)
}

/// The store's one enrichable session with this id.
pub fn session(store: &EnrichmentStore, session_id: &str) -> SessionItem {
    let mut found: Vec<SessionItem> = store
        .session_items(None)
        .expect("the sessions read")
        .into_iter()
        .filter(|item| item.session_id == session_id)
        .collect();
    assert_eq!(
        found.len(),
        1,
        "{session_id} named {} sessions",
        found.len()
    );
    found.remove(0)
}

/// The render's last line — the one that says how the item ended.
pub fn ended(rendered: &str) -> &str {
    rendered
        .rsplit('\n')
        .next()
        .expect("a render has a last line")
}

/// Enrich one item, so a render of its parent has a child description to embed.
///
/// `explore`/`completed` rather than [`enrichment`]'s first-of-the-vocabulary pair: a render
/// prints the two members, so the leaves reading them name what they expect to see.
pub fn describe(store: &EnrichmentStore, item: &dyn Item, description: &str) {
    let vocabulary = metadata::enrichment();
    let (category, outcome) = ("explore", "completed");
    for member in [category, outcome] {
        assert!(
            vocabulary
                .categories
                .iter()
                .chain(&vocabulary.outcomes)
                .any(|held| held == member),
            "the taxonomy no longer carries `{member}`"
        );
    }
    let said = Enrichment {
        description: description.to_owned(),
        category: category.to_owned(),
        outcome: outcome.to_owned(),
        friction: None,
    };
    // The stamp decides re-enrichment, which no render reads.
    store
        .upsert(item, &said, &stamp("unused"))
        .expect("the description writes");
}

/// The item ids one level's enrichment rows carry, in key order.
pub fn stored_ids(store: &EnrichmentStore, level: Level) -> Vec<String> {
    let last = *level.keys().last().expect("a level has a key column");
    column(
        store,
        &format!("SELECT {last} FROM {} ORDER BY 1", level.table()),
    )
    .into_iter()
    .map(|held| held.expect("a key column is never null"))
    .collect()
}

/// Every enrichment row of every level: the item's own id against what it says.
///
/// Keyed by the id alone — one map across three tables, which is what a cascade moves through.
pub fn described(store: &EnrichmentStore) -> BTreeMap<String, String> {
    read_pairs(store, "description")
}

/// The same, against the input hash each row was written under.
pub fn hashes(store: &EnrichmentStore) -> BTreeMap<String, String> {
    read_pairs(store, "input_hash")
}

/// The same, against the moment each row was written — what a rewrite moves and a skip does not.
pub fn written_at(store: &EnrichmentStore) -> BTreeMap<String, String> {
    read_pairs(store, "CAST(enriched_at AS VARCHAR)")
}

fn read_pairs(store: &EnrichmentStore, said: &str) -> BTreeMap<String, String> {
    passes::read_pairs(store, said)
}
