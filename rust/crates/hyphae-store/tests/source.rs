//! Reading the trace store back as traces: the round trip, and the source filter.
//!
//! The port of `tests/extract/test_store.py`. The OTLP export ships the store rather than the
//! transcripts on disk, so [`StoreSource`] is the extractor that pipeline runs on. Everything
//! the exporter can send is only as true as this rebuild — a column silently dropped here
//! ships a corpus missing a field nobody notices — so the round trip compares whole traces
//! rather than fields.

use std::path::Path;

use hyphae_extract::SessionSource;
use hyphae_model::SessionTrace;
use hyphae_store::source::{SourceError, StoreSource};
use hyphae_store::{Param, Store, rows as built, schema};
use hyphae_testsupport::landmarks::{
    MYCELIA, NO_PROJECT_SESSION, SIBLING_SESSION, SPINE, WORKTREE_SESSION,
};
use hyphae_testsupport::{cache, corpus, rows};
use tempfile::TempDir;

/// A worktree of the analyzed repository, and a repository whose path merely starts with the
/// same characters. Planted onto two recorded sessions, because the recorded corpus holds no
/// sibling of `MYCELIA`.
const UNDER_PROJECT: &str = "/Users/nob/repos/mycelia/worktrees/paging";
const SIBLING_PROJECT: &str = "/Users/nob/repos/mycelia-other";

/// What `refresh()` hands `extract()`: an id and a fingerprint, and no files.
fn source(session_id: &str) -> SessionSource {
    SessionSource {
        id: session_id.to_owned(),
        files: Vec::new(),
        fingerprint: "fixture".to_owned(),
    }
}

/// The same trace with every list in [`StoreSource`]'s order.
///
/// List order carries no meaning: the model's lists are keyed by natural ids, and the
/// extractor emits the main transcript's rows before each subagent's while the store orders
/// by primary key. Sorting both sides leaves the comparison over every row and every field of
/// every row.
fn canonical(mut trace: SessionTrace) -> SessionTrace {
    trace
        .turns
        .sort_by(|a, b| (&a.source, &a.id).cmp(&(&b.source, &b.id)));
    trace
        .api_calls
        .sort_by(|a, b| (&a.source, &a.id).cmp(&(&b.source, &b.id)));
    trace
        .tool_calls
        .sort_by(|a, b| (&a.source, &a.id).cmp(&(&b.source, &b.id)));
    trace.agent_runs.sort_by(|a, b| a.id.cmp(&b.id));
    trace
        .compactions
        .sort_by(|a, b| (&a.source, &a.id).cmp(&(&b.source, &b.id)));
    trace.pr_links.sort_by_key(|row| row.line_no);
    trace.offload_files.sort_by(|a, b| a.name.cmp(&b.name));
    trace
        .raw_records
        .sort_by(|a, b| (&a.source, a.line_no).cmp(&(&b.source, b.line_no)));
    trace
}

/// Assert two traces hold the same rows, naming where they differ and never what differed.
///
/// Compared as the rows each trace becomes rather than with `assert_eq!` over the structs:
/// a failing struct comparison prints both whole traces, which puts prompts, tool output and
/// file contents in the log (`hyphae_testsupport::rows`). The column lists `rows::of` writes
/// by are the DDL's own, held there by `Store::check_columns`, so nothing is left out of the
/// comparison by going through them.
fn assert_round_trip(label: &str, actual: SessionTrace, expected: SessionTrace) {
    assert_eq!(
        (&actual.extractor, &actual.extractor_version),
        (&expected.extractor, &expected.extractor_version),
        "{label} provenance"
    );
    let (actual, expected) = (canonical(actual), canonical(expected));
    for ((table, left), (_, right)) in built::of(&actual).iter().zip(built::of(&expected)) {
        let columns = schema::columns(table).expect("a table the schema declares");
        rows::assert_columns_equal(&format!("{label} {table}"), columns, left, &right);
    }
}

/// A copy of the exportable corpus with `project_dir` planted onto named sessions.
fn planted(rows: &[(&str, &str)]) -> (TempDir, Store) {
    let (scratch, path) = cache::writable_copy(&cache::exportable_store());
    let store = Store::open_for_write(&path).expect("the copy opens for writing");
    for (session_id, project) in rows {
        store
            .connection()
            .execute(
                "UPDATE sessions SET project_dir = ? WHERE id = ?",
                [*project, *session_id],
            )
            .expect("the plant lands");
    }
    (scratch, store)
}

/// A trace read back out of the store is the trace that was written into it.
#[test]
fn a_recorded_trace_round_trips_through_the_store() {
    // If the deepest recorded session — four main turns, two nested subagent runs — was
    // extracted into the store...
    let store = cache::corpus_reader();
    let expected = corpus::trace("spine", SPINE);
    // ...when it is rebuilt from the rows rather than from the transcript...
    let trace = StoreSource::new(&store)
        .extract(&source(SPINE))
        .expect("the spine session rebuilds");
    // ...then every entity list comes back whole, down to the last field of the last row...
    assert_round_trip("spine", trace.clone(), expected);
    // ...and the columns the session left NULL come back as None rather than as a default
    // that would read as a recorded value: no api call retried a model, and two of the seven
    // carry no stop reason.
    assert!(
        trace
            .api_calls
            .iter()
            .all(|call| call.fallback_from.is_none()),
        "no recorded call fell back to another model"
    );
    assert!(
        trace
            .api_calls
            .iter()
            .any(|call| call.stop_reason.is_none()),
        "a call with no stop reason comes back with none, not with an empty string"
    );
}

/// Every recorded session survives the store, not just the one the leaf above names.
#[test]
fn every_fixture_session_round_trips() {
    let store = cache::corpus_reader();
    let reader = StoreSource::new(&store);
    for transcript in corpus::corpus_transcripts() {
        let directory = transcript
            .parent()
            .and_then(Path::file_name)
            .expect("a fixture sits in a directory")
            .to_string_lossy()
            .into_owned();
        let stem = transcript
            .file_stem()
            .expect("a transcript has a stem")
            .to_string_lossy()
            .into_owned();
        let expected = corpus::trace(&directory, &stem);
        let trace = reader
            .extract(&source(&stem))
            .unwrap_or_else(|error| panic!("{stem} rebuilds: {error}"));
        assert_round_trip(&format!("{directory}/{stem}"), trace, expected);
    }
}

/// A rebuilt trace credits the extractor whose rows it is, never `StoreSource` itself.
#[test]
fn provenance_names_the_parser_not_the_reader() {
    let store = cache::corpus_reader();
    let trace = StoreSource::new(&store)
        .extract(&source(SPINE))
        .expect("the spine session rebuilds");
    let held = store
        .fetch(
            "SELECT extractor, extractor_version FROM extract_state WHERE session_id = $id",
            &[("id", Param::Text(SPINE.to_owned()))],
        )
        .expect("the store answers");
    assert_eq!(trace.extractor, held[0].str("extractor").unwrap());
    assert_eq!(
        trace.extractor_version,
        held[0].str("extractor_version").unwrap()
    );
    assert!(
        !trace.extractor.contains("StoreSource"),
        "the reader is not the parser"
    );
}

/// Discovery reports each session's recorded fingerprint and no files to read.
#[test]
fn sessions_carry_the_fingerprint_the_store_holds() {
    // If the store holds the sessions of the analyzed repository...
    let store = Store::open_read_only(&cache::exportable_store()).expect("the store opens");
    let expected: Vec<SessionSource> = store
        .fetch(
            "SELECT e.session_id, e.fingerprint FROM extract_state e \
             JOIN sessions s ON s.id = e.session_id \
             WHERE s.project_dir = $project ORDER BY e.session_id",
            &[("project", Param::Text(MYCELIA.to_owned()))],
        )
        .expect("the store answers")
        .iter()
        .map(|row| SessionSource {
            id: row.str("session_id").unwrap().to_owned(),
            files: Vec::new(),
            fingerprint: row.str("fingerprint").unwrap().to_owned(),
        })
        .collect();
    assert!(
        !expected.is_empty(),
        "the fixture corpus should hold sessions under MYCELIA"
    );
    // ...then discovery lists exactly those, each with the fingerprint `extract_state`
    // recorded and an empty `files` — the store is the source, so there is nothing to stat.
    let listed = StoreSource::new(&store)
        .sessions(Path::new(MYCELIA))
        .expect("the corpus lists");
    assert_eq!(listed, expected);
}

/// A worktree of the project is in scope; a repository whose name merely starts the same way
/// is not.
#[test]
fn the_filter_takes_the_project_and_what_sits_under_it() {
    // If one recorded session is re-placed into a worktree under the analyzed repository and
    // another into a sibling repository beside it — both planted, since the recorded corpus
    // holds neither shape...
    let (_scratch, store) = planted(&[
        (SIBLING_SESSION, UNDER_PROJECT),
        (WORKTREE_SESSION, SIBLING_PROJECT),
    ]);
    // ...then the worktree ships and the sibling does not: the filter cuts on path
    // components, so a string-prefix filter passes the first half and fails here.
    let listed: Vec<String> = StoreSource::new(&store)
        .sessions(Path::new(MYCELIA))
        .expect("the planted corpus lists")
        .into_iter()
        .map(|found| found.id)
        .collect();
    assert!(listed.iter().any(|id| id == SIBLING_SESSION));
    assert!(!listed.iter().any(|id| id == WORKTREE_SESSION));
}

/// A project given as a relative path lists the same sessions its absolute path does.
#[test]
fn the_filter_places_a_project_named_relative_to_the_working_directory() {
    // If a recorded session sits under a repository beside the working directory — planted,
    // since every fixture was recorded under one absolute path...
    let scratch = TempDir::new().expect("a tempdir");
    let repository = scratch
        .path()
        .canonicalize()
        .expect("the tempdir resolves")
        .join("repo");
    std::fs::create_dir(&repository).expect("the repository directory is creatable");
    let placed = repository.to_string_lossy().into_owned();
    let (_held, store) = planted(&[(SIBLING_SESSION, &placed)]);
    // ...then naming that repository the way a shell would, relative to the directory the
    // command runs in, ships it: `project_dir` is an absolute cwd, so a filter that matched
    // the string as typed would report a successful export of nothing.
    let listed: Vec<String> = StoreSource::new(&store)
        .sessions(&scratch.path().join("repo"))
        .expect("the planted corpus lists")
        .into_iter()
        .map(|found| found.id)
        .collect();
    assert!(listed.iter().any(|id| id == SIBLING_SESSION));
}

/// A session that recorded no working directory and no work is simply not listed.
#[test]
fn a_childless_session_with_no_project_is_excluded() {
    // If a session's main transcript is extracted without the subagent file beside it — a
    // trim of recorded data — the store holds its bookkeeping shell: three archive lines
    // (`permission-mode`, `mode`, `bridge-session`) and not one row of work...
    let scratch = TempDir::new().expect("a tempdir");
    let path = scratch.path().join("childless.duckdb");
    let store = Store::create(&path).expect("a fresh store");
    let planted = corpus::planted("fork_byref", NO_PROJECT_SESSION, &[]);
    let trace = corpus::extractor()
        .extract(&planted.source)
        .expect("the trimmed session extracts");
    store.export(&trace, "fixture").expect("it exports");
    // ...then discovery leaves it out without complaint about the session — there is nothing
    // to lose — and what it refuses is the project, which the store then holds nothing under.
    let refused = StoreSource::new(&store)
        .sessions(Path::new(MYCELIA))
        .expect_err("a project the store holds nothing under is refused");
    assert!(matches!(refused, SourceError::UnknownProject { .. }));
    let message = refused.to_string();
    assert!(message.contains(MYCELIA));
    assert!(!message.contains(NO_PROJECT_SESSION));
}

/// Excluding a session that holds records would drop them silently, so it crashes.
#[test]
fn a_session_with_no_project_but_rows_crashes() {
    // If the same session is extracted whole — its by-reference fork wrote 2 api calls, 2
    // tool calls, 1 agent run and 10 raw records under a NULL `project_dir`...
    let scratch = TempDir::new().expect("a tempdir");
    let path = scratch.path().join("contentful.duckdb");
    let store = Store::create(&path).expect("a fresh store");
    let trace = corpus::extractor()
        .extract(&corpus::fixture_source("fork_byref", NO_PROJECT_SESSION))
        .expect("the whole session extracts");
    store.export(&trace, "fixture").expect("it exports");
    // ...then discovery refuses to place it rather than dropping it...
    let refused = StoreSource::new(&store)
        .sessions(Path::new(MYCELIA))
        .expect_err("an unplaceable session holding work is refused");
    assert!(matches!(refused, SourceError::UnplaceableSession { .. }));
    // ...naming the session and what would have been lost, table by table...
    let message = refused.to_string();
    assert!(message.contains(NO_PROJECT_SESSION));
    for held in [
        "api_calls 2",
        "tool_calls 2",
        "agent_runs 1",
        "raw_records 10",
    ] {
        assert!(message.contains(held), "{message} does not name {held}");
    }
    // ...and quoting none of the transcript, whose every string redaction flattened to this.
    assert!(!message.contains("[redacted]"));
}
