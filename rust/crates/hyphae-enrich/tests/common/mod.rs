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

use duckdb::params;
use hyphae_enrich::{AgentRunItem, Enrichment, EnrichmentStore, Stamp, TurnItem};
use hyphae_testsupport::{cache, metadata};
use tempfile::TempDir;

// The recorded sessions this file names, and the runs inside them. `tests/fixtures/*/README.md`
// names the session behind each fixture directory.
pub const SPINE: &str = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b";
pub const SPINE_RUN: &str = "ac461ef46b4bb8e32";
pub const SPINE_LEAF: &str = "af6473ae437c9608d";
/// `spine/`'s `/model` turn: the one whose stdout record the archive read exercises.
pub const SPINE_MODEL_TURN: &str = "5b848af7-f86e-4950-b474-cd98125fad24";
/// The line that record sits on, so a planted one can be ordered against it.
pub const SPINE_MODEL_LINE: i64 = 8;
/// `model_only/`: three turns the CLI answered by itself and no api call under any of them.
pub const MODEL_ONLY: &str = "bec99999-cbb7-4d11-9a58-3ad3d0e1c8cf";
/// `resume_pair/`'s ancestor and the plain turn its stdout record hangs off.
pub const RESUME_ANCESTOR: &str = "2352492b-1437-4427-ad51-70f35c75f663";
pub const RESUME_PLAIN_TURN: &str = "55309e59-0fae-4ef1-9251-877e27487bda";
/// The resume itself: every api call it holds sits under a turn its ancestor ran.
pub const RESUME: &str = "0a76f771-5f5b-447e-852a-664fc972ea7c";
/// The session that records no main turn and no agent run.
pub const DUP_UUID: &str = "8ee00a94-b01a-4394-b447-b065f74b11af";
/// The invented fixture with neither: the third session with nothing to describe, and the
/// one the Python suite's corpus leaves out.
pub const INVENTED_EMPTY: &str = "invented-no-cache-creation";
/// A fork whose only work is a subagent's — no turn of its own, and every call under the run.
pub const FORK_BYREF: &str = "07a769d7-828c-4edb-b3ce-af51e2712aa3";
/// The turn whose three recorded calls stopped `end_turn`, `tool_use` and nothing.
pub const STOP_REASON_TURN: &str = "9ae45aaa-d992-4089-a78d-f65d2f237080";
/// The two sessions the project filter re-homes, and the project every fixture was recorded
/// under.
pub const WORKTREE_SESSION: &str = "0b34d1b8-ebd3-40a6-bd89-f1881e1de2ba";
pub const NEIGHBOUR_SESSION: &str = "10d0349d-0705-4e23-aa64-5b1b97698b2e";
pub const MYCELIA: &str = "/Users/nob/repos/mycelia";

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
