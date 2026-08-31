//! The rows Rust plants against the rows Python plants, and the prompts each side renders.
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
//! Nothing here prints a stored value. The row comparison is [`rows::assert_columns_equal`],
//! which names the table, the row and the column and stops; the render comparison travels as
//! `input_hash` alone, so a prompt built from a private transcript never leaves either process.

use std::path::Path;
use std::process::Command;

use duckdb::types::Value;
use hyphae_enrich::{EnrichmentStore, Item, Level, schema};
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
    #[expect(
        clippy::disallowed_methods,
        reason = "the uv parity bridge: the oracle is Python's own answer"
    )]
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

/// Render every item of the corpus on both sides, and compare what each one hashes to.
///
/// The render is the whole input to a model call and to `input_hash`, so a divergence here is
/// two tiers describing different things and stamping the rows as if they had not. Hashes
/// rather than text: a prompt is assembled from a private transcript, and the digest is what
/// makes the comparison sayable out loud.
///
/// The enriched store, not the bare one: a session render embeds its children's descriptions,
/// which only the planted store has.
#[test]
fn every_item_renders_to_the_same_prompt_on_both_sides() {
    if std::env::var_os(SKIP).is_some() {
        return;
    }
    // Both `EnrichmentStore`s open for writing, so each side reads its own copy of the cached
    // file rather than contending for one write lock.
    let enriched = cache::enriched_store();
    let (_mine, ours) = cache::writable_copy(&enriched);
    let (_theirs, theirs) = cache::writable_copy(&enriched);
    let mine = rendered_by_rust(&ours);
    // Every level is represented, so a reader that handed out nothing cannot pass by matching
    // an empty list against an empty list.
    for level in Level::ALL {
        let prefix = format!("{}|", level.word());
        assert!(
            mine.iter().any(|(key, _)| key.starts_with(&prefix)),
            "the corpus offers no {level} to render"
        );
    }
    let theirs = rendered_by_python(&theirs);
    assert_eq!(
        mine.len(),
        theirs.len(),
        "the two sides assembled a different number of items"
    );
    // Named one at a time rather than compared whole: a mismatched pair of lists prints every
    // key in both, and the first divergence is the one a reader acts on.
    for ((key, ours), (their_key, theirs)) in mine.iter().zip(&theirs) {
        assert_eq!(key, their_key, "the two sides assembled different items");
        assert_eq!(ours, theirs, "`{key}` renders differently on the two sides");
    }
}

/// `key\tinput_hash` for every item this side assembles, in key order.
fn rendered_by_rust(at: &Path) -> Vec<(String, String)> {
    let store = EnrichmentStore::open(at).expect("the enriched store opens");
    let items: Vec<Box<dyn Item>> = store
        .turn_items(None)
        .expect("the turns read")
        .into_iter()
        .map(|item| Box::new(item) as Box<dyn Item>)
        .chain(
            store
                .run_items(None)
                .expect("the runs read")
                .into_iter()
                .map(|item| Box::new(item) as Box<dyn Item>),
        )
        .chain(
            store
                .session_items(None)
                .expect("the sessions read")
                .into_iter()
                .map(|item| Box::new(item) as Box<dyn Item>),
        )
        .collect();
    let mut hashed: Vec<(String, String)> = items
        .iter()
        .map(|item| (item.key(), hyphae_enrich::input_hash(&item.render())))
        .collect();
    hashed.sort();
    hashed
}

/// The same list, from the Python renders, over the copy at `at`.
fn rendered_by_python(at: &Path) -> Vec<(String, String)> {
    let repo = corpus::repo();
    #[expect(
        clippy::disallowed_methods,
        reason = "the uv parity bridge: the oracle is Python's own answer"
    )]
    let run = Command::new("uv")
        .args(["run", "--project", ".", "python", "-c", RENDER])
        .arg(at)
        .current_dir(&repo)
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
        "the Python renders failed ({}). Set {SKIP} to skip them:\n{}",
        run.status,
        String::from_utf8_lossy(&run.stderr),
    );
    let mut hashed: Vec<(String, String)> = String::from_utf8(run.stdout)
        .expect("the Python side prints text")
        .lines()
        .map(|line| {
            let (key, hash) = line.split_once('\t').expect("every line is key and hash");
            (key.to_owned(), hash.to_owned())
        })
        .collect();
    hashed.sort();
    hashed
}

/// Print what the Python tier renders each item to, as `key<TAB>input_hash`.
///
/// The renders themselves stay inside the process: only the digest crosses.
const RENDER: &str = r#"
import sys
from pathlib import Path
from hyphae.enrich.prompts import input_hash, render
from hyphae.enrich.store import EnrichmentStore

store = EnrichmentStore(Path(sys.argv[1]))
for reader in (store.turn_items, store.run_items, store.session_items):
    for item in reader():
        print(f"{item.key}\t{input_hash(render(item))}")
"#;
