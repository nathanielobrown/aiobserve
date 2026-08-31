//! Reading rows back out of a store, and comparing two sets of them.
//!
//! Nothing here prints a value. A failing row assertion names the table, the row and the
//! column; what was in that column stays out of the log, because a store row lifted from a
//! transcript is one `assert_eq!` away from putting prompts, tool output and file contents
//! into CI.

use std::path::Path;

use duckdb::types::Value;
use hyphae_store::{Param, Row, Store, schema};

/// The one row a query the test wrote answers with, off a store opened for the asking.
///
/// The port of `tests/view/conftest.py`'s `one`: a leaf names the node it is about in SQL rather
/// than pinning an id, so a re-recorded fixture moves the selection instead of reddening the
/// tier. Panics where the query answered with nothing — a leaf whose subject left the corpus is
/// not a leaf that should quietly pass.
pub fn one(db: &Path, sql: &str, params: &[(&str, Param)]) -> Row {
    all(db, sql, params)
        .into_iter()
        .next()
        .unwrap_or_else(|| panic!("the corpus holds a row for {sql}"))
}

/// Every row a query the test wrote answers with.
pub fn all(db: &Path, sql: &str, params: &[(&str, Param)]) -> Vec<Row> {
    let store = Store::open_read_only(db).expect("the store opens read only");
    store
        .fetch(sql, params)
        .unwrap_or_else(|error| panic!("the store answers {sql}: {error}"))
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
    store
        .fetch(
            &format!("SELECT {selected} FROM {table} WHERE {key} = $session_id"),
            &[("session_id", session_id.into())],
        )
        .expect("the store answers a table read")
        .into_iter()
        .map(|row| row.values().to_vec())
        .collect()
}

/// Assert two row sets are equal, naming where they differ and never what differed.
pub fn assert_rows_equal(table: &str, left: &[Vec<Value>], right: &[Vec<Value>]) {
    let columns = schema::columns(table).expect("a table this crate declares");
    assert_columns_equal(table, columns, left, right);
}

/// The same, over columns the caller names — for a table this crate's schema does not declare.
pub fn assert_columns_equal(
    table: &str,
    columns: &[&str],
    left: &[Vec<Value>],
    right: &[Vec<Value>],
) {
    assert_eq!(left.len(), right.len(), "`{table}` row count");
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
