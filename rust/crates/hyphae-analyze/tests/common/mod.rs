//! What the analyze tier's test files need: one library query run over a fixture store, and
//! its rows keyed for reading.
//!
//! The twin of `tests/analyze/conftest.py`'s runner fixtures. The runner is called directly
//! rather than through `hp query`, because the printed forms are `hp`'s business and are
//! pinned there (`hp/tests/query.rs`, `hp/tests/parity.rs`); what these files ask about is
//! the SQL. Values come off [`Row`] typed, so a leaf compares numbers rather than the strings
//! a CSV writer made of them.
//!
//! A directory rather than a file so that cargo folds it into each test target instead of
//! building it as a target of its own. Each file uses a subset, hence the allow.

#![allow(dead_code)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use duckdb::types::Value;
use hyphae_analyze::{QueryResult, Request};
use hyphae_store::{Row, Store};
use hyphae_testsupport::{cache, landmarks};
use indexmap::IndexMap;
use tempfile::TempDir;

/// One corpus-scoped query over a store, at the `$as_of` the caller names.
///
/// Every corpus query is windowed, so `as_of` is spelled at each call rather than defaulted:
/// a leaf that left it to today would pass while the recordings are recent and go red the
/// morning they fall out of the window.
pub fn corpus(db: &Path, name: &str, as_of: &str, params: &[(&str, &str)]) -> QueryResult {
    run(
        db,
        name,
        Request {
            project: Some(landmarks::MYCELIA.into()),
            since: None,
            as_of: hyphae_testsupport::windows::date(as_of),
            params: bound(params),
        },
    )
}

pub fn run(db: &Path, name: &str, request: Request) -> QueryResult {
    hyphae_analyze::run(db, name, &request).unwrap_or_else(|error| panic!("{name}: {error}"))
}

fn bound(params: &[(&str, &str)]) -> IndexMap<String, String> {
    params
        .iter()
        .map(|(key, value)| ((*key).to_owned(), (*value).to_owned()))
        .collect()
}

/// A writable copy of the cached corpus with something planted in it, and the tempdir on it.
///
/// Keep the `TempDir`: dropping it deletes the store the returned path names.
pub fn planted(plant: impl FnOnce(&Store)) -> (TempDir, PathBuf) {
    let (scratch, path) = cache::writable_copy(&cache::corpus_store());
    {
        let store = Store::create(&path).expect("the copy opens for writing");
        plant(&store);
    }
    (scratch, path)
}

/// A result's rows by the value of one text column, which is how a leaf names the row it means.
///
/// Panics on a repeated key: a query answering twice for one period or one week is the shape
/// several leaves here exist to catch, and keeping the last row would hide it.
pub fn by(result: &QueryResult, column: &str) -> BTreeMap<String, Row> {
    let mut keyed = BTreeMap::new();
    for row in &result.rows {
        let key = row.str(column).expect("the key column is text").to_owned();
        assert!(
            keyed.insert(key.clone(), row.clone()).is_none(),
            "two rows for `{key}`"
        );
    }
    keyed
}

/// One numeric column of a row, whatever width DuckDB answered it at.
///
/// A rolled-up count arrives as a HUGEINT and a rounded cost as a DOUBLE, and a leaf
/// comparing a window against the corpus wants both on one scale. `None` is SQL NULL, which
/// is a value the query meant rather than a zero.
pub fn number(row: &Row, column: &str) -> Option<f64> {
    match row.value(column).expect("a column the query selected") {
        Value::Null => None,
        Value::Double(number) => Some(*number),
        Value::Float(number) => Some(f64::from(*number)),
        _ => Some(row.i64(column).expect("an integer") as f64),
    }
}
