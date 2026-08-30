#![allow(dead_code)] // Each test binary uses a different part of this.

//! A store built the way the pipeline builds one: the recorded fixture corpus, extracted
//! through the Rust path and exported into a tempdir.
//!
//! No leaf here reads the canonical store at `data/traces.duckdb`. Session data is private
//! (`CLAUDE.md`), and the redacted excerpts under `tests/fixtures/` carry every shape the
//! store tier needs — so the corpus these tests query is one nobody has to be careful with.
//!
//! The comparison helpers below never print a value. A failing row assertion names the
//! table, the row and the column; what was in that column stays out of the log.

use std::path::{Path, PathBuf};

use duckdb::types::{ToSql, Value};
use hyphae_extract::sessions::SessionFiles;
use hyphae_extract::{Extractor, SessionSource};
use hyphae_store::{Store, schema};
use tempfile::TempDir;

/// The two `invented/` transcripts that export cleanly. The other six carry unknown record
/// shapes and crash by design, which is what `hyphae-extract`'s walk tier proves.
const CLEAN_INVENTED: &[&str] = &["invented-no-cache-creation", "invented-truncated-tail"];

/// The repository root, from this crate's own location.
pub fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("the crate sits three levels under the repository root")
}

/// `tests/fixtures/` in the repo.
pub fn fixtures() -> PathBuf {
    repo().join("tests/fixtures")
}

/// Every fixture transcript that exports cleanly, discovered rather than listed — the twin
/// of `tests/conftest.py:corpus_transcripts`.
pub fn corpus_transcripts() -> Vec<PathBuf> {
    let mut directories: Vec<PathBuf> = std::fs::read_dir(fixtures())
        .expect("the fixture corpus is readable")
        .map(|entry| entry.expect("the entry is readable").path())
        .filter(|path| path.is_dir() && path.file_name().is_some_and(|name| name != "invented"))
        .collect();
    directories.sort();
    let mut transcripts: Vec<PathBuf> = directories.iter().flat_map(|dir| jsonl(dir)).collect();
    transcripts.extend(
        CLEAN_INVENTED
            .iter()
            .map(|stem| fixtures().join("invented").join(format!("{stem}.jsonl"))),
    );
    transcripts
}

fn jsonl(directory: &Path) -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = std::fs::read_dir(directory)
        .expect("a fixture directory is readable")
        .map(|entry| entry.expect("the entry is readable").path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "jsonl")
        })
        .collect();
    found.sort();
    found
}

/// One transcript as discovery would have handed it over. The fingerprint is a placeholder:
/// it belongs to discovery, and `extract` never reads it.
pub fn source(transcript: &Path) -> SessionSource {
    let stem = transcript
        .file_stem()
        .expect("a transcript path ends in a file name")
        .to_string_lossy()
        .into_owned();
    let session = SessionFiles {
        id: stem.clone(),
        transcript: transcript.to_owned(),
    };
    SessionSource {
        id: stem,
        files: session.files().expect("the fixture's files are readable"),
        fingerprint: "fixture".to_owned(),
    }
}

/// A fresh store in a tempdir holding the whole clean fixture corpus.
///
/// The `TempDir` comes back with it: dropping it deletes the file the `Store` is reading.
pub fn fixture_store() -> (TempDir, Store) {
    let scratch = TempDir::new().expect("a tempdir for the store");
    let store = Store::create(&scratch.path().join("traces.duckdb")).expect("a fresh store");
    let extractor = Extractor::new(fixtures());
    for transcript in corpus_transcripts() {
        let source = source(&transcript);
        let trace = extractor
            .extract(&source)
            .unwrap_or_else(|error| panic!("{} extracts: {error}", source.id));
        store
            .export(&trace, &source.fingerprint)
            .unwrap_or_else(|error| panic!("{} exports: {error}", source.id));
    }
    (scratch, store)
}

/// Every row one session owns in one table, in the crate's own column order.
pub fn session_rows(store: &Store, session_id: &str, table: &str) -> Vec<Vec<Value>> {
    let columns = schema::columns(table).expect("a table this crate declares");
    let selected = columns
        .iter()
        .map(|column| format!("\"{column}\""))
        .collect::<Vec<_>>()
        .join(", ");
    let key = schema::session_key(table);
    let session: &dyn ToSql = &session_id;
    store
        .fetch(
            &format!("SELECT {selected} FROM {table} WHERE {key} = $session_id"),
            &[("session_id", session)],
        )
        .expect("the store answers a table read")
        .into_iter()
        .map(|row| row.values().to_vec())
        .collect()
}

/// Assert two row sets are equal, naming where they differ and never what differed.
///
/// The whole point: `assert_eq!` on rows lifted from a transcript would print prompts, tool
/// output and file contents into the test log the moment a port regressed.
pub fn assert_rows_equal(table: &str, left: &[Vec<Value>], right: &[Vec<Value>]) {
    assert_eq!(left.len(), right.len(), "`{table}` row count");
    let columns = schema::columns(table).expect("a table this crate declares");
    for (at, (one, other)) in left.iter().zip(right).enumerate() {
        assert_eq!(one.len(), other.len(), "`{table}` row {at} width");
        for (column, (a, b)) in columns.iter().zip(one.iter().zip(other)) {
            assert!(a == b, "`{table}` row {at} differs at column `{column}`");
        }
    }
}

/// How many rows the store holds per table, for a count-only comparison.
pub fn table_counts(store: &Store) -> Vec<(&'static str, i64)> {
    schema::TABLES
        .iter()
        .map(|(table, _)| {
            let rows = store
                .fetch(&format!("SELECT count(*) AS n FROM {table}"), &[])
                .expect("the store counts a table");
            (*table, rows[0].i64("n").expect("count(*) is an integer"))
        })
        .collect()
}
