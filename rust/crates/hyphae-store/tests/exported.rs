//! What an exported trace holds once it is in the store: every field, and the keys that keep
//! two rows apart.
//!
//! The port of the write-path half of `tests/export/test_duckdb.py`. What the exporter
//! refuses, and what a store of another vintage does, is `store.rs`; the views over these
//! rows are `rollups.rs`. Nothing here invents a row — the traces come from the redacted
//! recordings — but three leaves plant a collision the corpus does not record, and say so.

use duckdb::types::Value;
use hyphae_store::{Store, rows as built};
use hyphae_testsupport::landmarks::{COMPACTED, CONFIG_ONLY, DUP_UUID, SPINE};
use hyphae_testsupport::{corpus, rows};
use tempfile::TempDir;

/// A store of its own, and the tempdir that outlives it.
fn store() -> (TempDir, Store) {
    let scratch = TempDir::new().expect("a tempdir");
    let store = Store::create(&scratch.path().join("traces.duckdb")).expect("a fresh store");
    (scratch, store)
}

/// How many rows one table holds, across every session.
fn count(store: &Store, table: &str) -> i64 {
    store
        .fetch(&format!("SELECT count(*) AS n FROM {table}"), &[])
        .expect("the store counts a table")[0]
        .i64("n")
        .expect("count(*) is an integer")
}

/// Rows in an order two reads agree on. Nothing is printed: the key is built and dropped.
fn ordered(mut held: Vec<Vec<Value>>) -> Vec<Vec<Value>> {
    held.sort_by_key(|row| format!("{row:?}"));
    held
}

/// Every column of an exported trace reads back as it was written, nulls included.
#[test]
fn an_exported_trace_reads_back_field_for_field() {
    let (_scratch, store) = store();
    let spine = corpus::trace("spine", SPINE);
    // The spine session never compacted, so the compactions come from the session that did.
    let compacted = corpus::trace("compaction", COMPACTED);

    // If two traces are exported...
    store
        .export(&spine, "fingerprint-1")
        .expect("spine exports");
    store
        .export(&compacted, "fingerprint-2")
        .expect("the compacted session exports");

    // ...then every table holds exactly the rows the exporter built for them, field for
    // field — including the `command_name`/`command_args` nulls on a plain prompt.
    let mut carried = 0;
    for trace in [&spine, &compacted] {
        for (table, rows_built) in built::of(trace) {
            let held = rows::session_rows(&store, &trace.session.id, table);
            rows::assert_rows_equal(table, &ordered(held), &ordered(rows_built.clone()));
            if !rows_built.is_empty() {
                carried += 1;
            }
        }
    }
    // Two sessions between them fill more than a couple of tables, or the loop is vacuous.
    assert!(carried >= 12, "only {carried} tables held rows");
}

/// Each exported session leaves a fingerprint, its path, and the extractor that ran.
#[test]
fn extract_state_records_what_produced_the_rows() {
    let (_scratch, store) = store();
    let spine = corpus::trace("spine", SPINE);

    store
        .export(&spine, "fingerprint-1")
        .expect("spine exports");

    let state = store
        .fetch(
            "SELECT session_id, fingerprint, transcript_path, extractor, extractor_version \
             FROM extract_state",
            &[],
        )
        .expect("extract_state reads");
    assert_eq!(state.len(), 1);
    let row = &state[0];
    assert_eq!(row.str("session_id").expect("a session id"), SPINE);
    assert_eq!(
        row.str("fingerprint").expect("a fingerprint"),
        "fingerprint-1"
    );
    assert_eq!(
        row.str("transcript_path").expect("a path"),
        spine.session.transcript_path
    );
    assert_eq!(row.str("extractor").expect("a name"), spine.extractor);
    assert_eq!(
        row.str("extractor_version").expect("a version"),
        spine.extractor_version
    );
    // ...and `fingerprints()` is exactly the map the extract loop reads to skip work.
    assert_eq!(
        store.fingerprints().expect("the fingerprints read"),
        std::collections::HashMap::from([(SPINE.to_owned(), "fingerprint-1".to_owned())])
    );
}

/// The same message id under two transcripts of one session is two rows, not a clash.
///
/// A subagent inherits ids from its own API stream, so `message.id` repeats across the files
/// of one session on ~2.6% of the corpus. Only the composite key survives that.
#[test]
fn an_id_is_scoped_to_its_transcript() {
    let (_scratch, store) = store();
    let mut trace = corpus::trace("spine", SPINE);
    let call = trace.api_calls[0].clone();

    // If one call is recorded under the main transcript and the same id under a subagent's —
    // planted, since the recording holds each id on one thread...
    let mut elsewhere = call.clone();
    elsewhere.source = "a1d0bc50fe316ed8e".to_owned();
    trace.api_calls = vec![call.clone(), elsewhere];
    store
        .export(&trace, "fingerprint-1")
        .expect("two threads may share an id");

    // ...then both rows are there...
    assert_eq!(count(&store, "api_calls"), 2);

    // ...while a genuine repeat of the whole triple is rejected.
    trace.api_calls = vec![call.clone(), call];
    store
        .export(&trace, "fingerprint-2")
        .expect_err("one thread may not record an id twice");
}

/// One agentId may run under two sessions, but not twice under one.
///
/// A resume copies its ancestor's `subagents/` files into the new session's directory, so the
/// same agentId is extracted under both session ids — two of the 2,764 agent transcripts on
/// this machine (scanned 2026-08-07). Only the composite key holds both.
#[test]
fn an_agent_run_is_keyed_by_session_and_agent_id() {
    let (_scratch, store) = store();
    let mut spine = corpus::trace("spine", SPINE);
    let run = spine.agent_runs[0].clone();
    let mut other = corpus::trace("dup_uuid", DUP_UUID);

    // If one agent run is recorded under the session that spawned it and again under the
    // resume that inherited the file...
    store
        .export(&spine, "fingerprint-1")
        .expect("spine exports");
    let mut inherited = run.clone();
    inherited.session_id = DUP_UUID.to_owned();
    other.agent_runs = vec![inherited];
    store
        .export(&other, "fingerprint-2")
        .expect("two sessions may hold one agentId");

    // ...then both rows are there, each under its own session...
    assert_eq!(
        count(&store, "agent_runs"),
        spine.agent_runs.len() as i64 + 1
    );
    let (_, expected) = built::of(&other)
        .into_iter()
        .find(|(table, _)| *table == "agent_runs")
        .expect("agent_runs is a table the exporter builds");
    rows::assert_rows_equal(
        "agent_runs",
        &rows::session_rows(&store, DUP_UUID, "agent_runs"),
        &expected,
    );

    // ...while one session claiming an agentId twice is rejected: the id names the file that
    // produced the run, and a directory holds it once.
    spine.agent_runs = vec![run.clone(), run];
    store
        .export(&spine, "fingerprint-3")
        .expect_err("one session may not hold an agentId twice");
}

/// A `tool-results/` file is stored whole, and two sessions may hold the same name.
#[test]
fn an_offloaded_output_is_keyed_by_session_and_name() {
    let (_scratch, store) = store();
    let mut offloading = corpus::trace("offload", CONFIG_ONLY);
    let offloaded = offloading.offload_files[0].clone();

    // If two sessions each offloaded a file of the same name — planted: Claude Code names
    // these randomly and none of the 636 on this machine repeats (scanned 2026-08-07)...
    store
        .export(&offloading, "fingerprint-1")
        .expect("the offloading session exports");
    let mut spine = corpus::trace("spine", SPINE);
    let mut elsewhere = offloaded.clone();
    elsewhere.session_id = SPINE.to_owned();
    spine.offload_files = vec![elsewhere];
    store
        .export(&spine, "fingerprint-2")
        .expect("two sessions may hold one name");

    // ...then both survive, each with its content...
    assert_eq!(count(&store, "offload_files"), 2);
    let (_, expected) = built::of(&offloading)
        .into_iter()
        .find(|(table, _)| *table == "offload_files")
        .expect("offload_files is a table the exporter builds");
    rows::assert_rows_equal(
        "offload_files",
        &rows::session_rows(&store, CONFIG_ONLY, "offload_files"),
        &expected,
    );

    // ...while one session claiming a name twice is rejected: a directory cannot hold two
    // files of one name, so a second row would be a parser bug.
    offloading.offload_files = vec![offloaded.clone(), offloaded];
    store
        .export(&offloading, "fingerprint-3")
        .expect_err("one session may not hold a name twice");
}
