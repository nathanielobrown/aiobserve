//! The file-wide schema: the tables a Rust store creates, against the DDL Python declares.
//!
//! Two implementations write one DuckDB file, so their table declarations have to agree —
//! and nothing at runtime ties them together. `store.rs` pins the two `SCHEMA_VERSION`
//! constants to each other; this file pins what the DDL behind that version builds.
//!
//! Python's own guard is a digest over its DDL text (`tests/export/test_schema.py`), which
//! makes any edit a deliberate, versioned decision on that side. A second digest here would
//! pin Rust to itself and go green through a one-sided Python edit, so the comparison is
//! against Python's shape instead: whichever side moves, this reds.
//!
//! Shape is name *and* type. Names alone leave a column one side widened — `INTEGER` here
//! against `BIGINT` there — green, and that divergence is silent until the value that needs
//! the wider column reaches the narrower side.

use std::collections::BTreeMap;

use hyphae_store::Store;
use hyphae_testsupport::corpus;
use tempfile::TempDir;

/// What Python's trace DDL declares, derived by running it against a scratch database.
///
/// Shelling out rather than keeping a copy here: `declared_columns` already answers this, and
/// a copy is the drift the leaf exists to catch. Each line is `table: name type, name type`.
fn python_shape() -> BTreeMap<String, Vec<String>> {
    let script = "import sys; sys.path.insert(0, sys.argv[1]); \
                  from hyphae.export.duckdb import _SCHEMA; \
                  from hyphae.export.schema import declared_columns; \
                  print('\\n'.join(f'{t}: {\", \".join(f\"{n} {d}\" for n, d in sorted(c.items()))}' \
                        for t, c in sorted(declared_columns(_SCHEMA).items())))";
    let repo = corpus::repo();
    #[expect(
        clippy::disallowed_methods,
        reason = "the uv/python bridge: this leaf's oracle is Python's own DDL, which only a Python process can report"
    )]
    let run = std::process::Command::new(python())
        .args(["-c", script, &repo.join("src").display().to_string()])
        .current_dir(&repo)
        .output()
        .expect("a Python interpreter is available");
    assert!(
        run.status.success(),
        "python failed: {}",
        String::from_utf8_lossy(&run.stderr)
    );
    String::from_utf8(run.stdout)
        .expect("python printed UTF-8")
        .lines()
        .map(|line| {
            let (table, columns) = line.split_once(": ").expect("a table and its columns");
            (
                table.to_owned(),
                columns.split(", ").map(str::to_owned).collect(),
            )
        })
        .collect()
}

/// The interpreter the repo's virtualenv owns, or the system one.
fn python() -> std::path::PathBuf {
    let venv = corpus::repo().join(".venv/bin/python");
    if venv.exists() {
        venv
    } else {
        "python3".into()
    }
}

/// The tables a Rust store holds, and each column of each with its type, as DuckDB reports.
fn rust_shape(store: &Store) -> BTreeMap<String, Vec<String>> {
    let mut shape: BTreeMap<String, Vec<String>> = BTreeMap::new();
    // `duckdb_tables()` lists tables only, which is what leaves the views out below.
    for row in store
        .fetch(
            "SELECT table_name, column_name, data_type FROM information_schema.columns \
             WHERE table_name IN (SELECT table_name FROM duckdb_tables()) \
             ORDER BY table_name, column_name",
            &[],
        )
        .expect("the store reports its own columns")
    {
        let column = row.str("column_name").expect("a name");
        let data_type = row.str("data_type").expect("a type");
        shape
            .entry(row.str("table_name").expect("a name").to_owned())
            .or_default()
            .push(format!("{column} {data_type}"));
    }
    shape
}

/// The tables the Rust store creates are the tables Python's DDL declares, column for column.
///
/// The failure this pins is silent both ways: `CREATE TABLE IF NOT EXISTS` leaves an existing
/// table alone, so a column one side added and the other did not is found at the first insert
/// into a store the other side wrote — or, where the views are `SELECT *`, at a binder error
/// naming a column with no version and no remedy behind it.
#[test]
fn the_tables_a_store_creates_are_the_ones_python_declares() {
    let scratch = TempDir::new().expect("a tempdir");
    let store = Store::create(&scratch.path().join("traces.duckdb")).expect("a fresh store");

    let held = rust_shape(&store);
    let declared = python_shape();

    // Table by table rather than map against map: a whole-schema diff buries the one line
    // that moved, and the column names are the entire message.
    assert_eq!(
        held.keys().collect::<Vec<_>>(),
        declared.keys().collect::<Vec<_>>(),
        "the two DDLs declare different tables"
    );
    for (table, columns) in &declared {
        assert_eq!(
            &held[table], columns,
            "`{table}` differs between the two DDLs"
        );
    }
}

/// The shape is the tables, and none of the views the same DDL creates.
///
/// A view is `CREATE OR REPLACE`, so every open rebuilds it and no store can hold a stale
/// one. Counting one as part of the shape would make the leaf above red on a view edit that
/// needs no version bump at all.
#[test]
fn a_view_is_no_part_of_the_declared_shape() {
    let scratch = TempDir::new().expect("a tempdir");
    let store = Store::create(&scratch.path().join("traces.duckdb")).expect("a fresh store");
    let shape = rust_shape(&store);

    // If a store is created, then the tables are in the shape...
    assert!(shape.contains_key("agent_runs"), "a table is in the shape");
    assert!(
        shape["agent_runs"]
            .iter()
            .any(|held| held.starts_with("brief "))
    );
    // ...and every view the same DDL built is out of it, though the store does hold them.
    for view in ["first_seen", "live_api_calls", "session_rollups"] {
        assert!(!shape.contains_key(view), "{view} is a view, not a table");
        store
            .fetch(&format!("SELECT count(*) FROM {view}"), &[])
            .unwrap_or_else(|error| panic!("{view} is in the store: {error}"));
    }
}
