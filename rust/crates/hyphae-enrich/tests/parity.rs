//! The rows Rust plants against the rows Python plants, table by table.
//!
//! Slice 3 moved the enriched fixture store off `tests/conftest.py:build_enriched_store` and
//! onto [`hyphae_testsupport::planting`]. Two recipes now write what every tier that reads a
//! description queries, and each one's own tests only hold it to its own author's reading. This
//! leaf is the thing that holds them to each other: same corpus in, same rows out.
//!
//! It runs by default, because a drift nobody runs the check for is a drift nobody finds. Set
//! `HYPHAE_SKIP_PYTHON_PARITY` on a machine with no Python environment — `mise run rust-check`
//! is meant to work there, and this is the one leaf in the workspace that shells into `uv`.
//!
//! Nothing here prints a stored value: the comparison is [`rows::assert_columns_equal`], which
//! names the table, the row and the column and stops.

use std::path::Path;
use std::process::Command;

use duckdb::types::Value;
use hyphae_enrich::{Level, schema};
use hyphae_store::Store;
use hyphae_testsupport::{cache, corpus, rows};

/// The escape hatch, named so a failure can point at it.
const SKIP: &str = "HYPHAE_SKIP_PYTHON_PARITY";

/// `enriched_at` is the clock, so it differs by construction and is the one column left out.
const CLOCK: &str = "enriched_at";

/// Plant the Python side over the same corpus, into the path the argument names.
///
/// `build_enriched_store` is the seam, not a re-implementation of it: whatever the Python tier
/// plants is what the Python tier's fixtures hold, and that is what this leaf is comparing.
const PLANT: &str = r#"
import sys
from pathlib import Path
from tests.conftest import build_enriched_store

build_enriched_store(Path(sys.argv[1]), corpus=Path(sys.argv[2]))
"#;

#[test]
fn the_planted_rows_are_the_rows_python_plants() {
    if std::env::var_os(SKIP).is_some() {
        return;
    }
    let corpus = cache::corpus_store();
    let scratch = tempfile::TempDir::new().expect("a tempdir for Python's copy");
    let theirs = scratch.path().join("traces.duckdb");
    plant_with_python(&corpus, &theirs);

    let ours = Store::open_read_only(&cache::enriched_store()).expect("the enriched store opens");
    let theirs = Store::open_read_only(&theirs).expect("Python's store opens");
    for level in Level::ALL {
        let table = level.table();
        let columns = compared(level);
        let mine = planted(&ours, level, &columns);
        // An empty set equals an empty set, so a recipe that planted nothing at all would pass
        // this leaf column by column. The corpus has items at every level; assert it planted.
        assert!(!mine.is_empty(), "`{table}` has no planted row to compare");
        rows::assert_columns_equal(table, &columns, &mine, &planted(&theirs, level, &columns));
    }
}

/// Every column of one enrichment table except the clock, keys first.
fn compared(level: Level) -> Vec<&'static str> {
    level
        .keys()
        .iter()
        .chain(schema::PAYLOAD_COLUMNS)
        .copied()
        .filter(|column| *column != CLOCK)
        .collect()
}

/// One level's planted rows, in key order so two independently written recipes line up.
fn planted(store: &Store, level: Level, columns: &[&str]) -> Vec<Vec<Value>> {
    let selected = columns.join(", ");
    let ordered = level.keys().join(", ");
    store
        .fetch(
            &format!(
                "SELECT {selected} FROM {} ORDER BY {ordered}",
                level.table()
            ),
            &[],
        )
        .expect("the store answers an enrichment read")
        .into_iter()
        .map(|row| row.values().to_vec())
        .collect()
}

/// Run the Python tier's own planting over `corpus`, into `at`.
fn plant_with_python(corpus: &Path, at: &Path) {
    let repo = corpus::repo();
    let run = Command::new("uv")
        .args(["run", "--project", ".", "python", "-c", PLANT])
        .arg(at)
        .arg(corpus)
        .current_dir(&repo)
        // The Rust tier may be running under an activated venv from another checkout, which
        // `uv` warns about and ignores; unset it rather than log the warning on every run.
        .env_remove("VIRTUAL_ENV")
        .output()
        .unwrap_or_else(|error| {
            panic!(
                "`uv` runs from {}: {error}. Set {SKIP} to skip",
                repo.display()
            )
        });
    assert!(
        run.status.success(),
        "the Python tier's planting failed ({}). Set {SKIP} to skip it:\n{}",
        run.status,
        String::from_utf8_lossy(&run.stderr),
    );
}
