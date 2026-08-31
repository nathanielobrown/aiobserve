//! The SQL library: that the Rust catalog holds every file Python reads, and that the widths
//! a page binds are still the widths Python declares.
//!
//! Both leaves read the Python tree. `analyze/queries.py` is the authority, and the numbers
//! below are the only part of it this crate re-declares rather than compiles in.

use std::collections::{BTreeMap, BTreeSet};

use hyphae_store::queries;

use hyphae_testsupport::{corpus, metadata};

/// The library directory Python's `load` reads out of.
fn query_dir() -> std::path::PathBuf {
    corpus::repo().join("src/hyphae/analyze/queries")
}

/// Every `.sql` file is in the catalog, and nothing else is.
///
/// `build.rs` walks the directory rather than listing it, so this fails only if the walk
/// itself broke — a stale `OUT_DIR` table, or a filter that stopped matching. Worth a leaf:
/// a catalog silently missing a file is a query the viewer cannot load at run time.
#[test]
fn the_catalog_holds_every_query_file() {
    let mut on_disk: Vec<String> = std::fs::read_dir(query_dir())
        .expect("the SQL library is readable")
        .map(|entry| entry.expect("the entry is readable").path())
        .filter(|path| path.extension().is_some_and(|extension| extension == "sql"))
        .map(|path| path.file_stem().unwrap().to_string_lossy().into_owned())
        .collect();
    on_disk.sort();
    let catalog: Vec<String> = queries::QUERIES
        .iter()
        .map(|(stem, _)| (*stem).to_owned())
        .collect();
    assert_eq!(catalog, on_disk);
    // And the text is the file's, byte for byte — the whole reason it is compiled in from the
    // Python tree rather than copied.
    for stem in &on_disk {
        let file = std::fs::read_to_string(query_dir().join(format!("{stem}.sql")))
            .expect("a library query is readable");
        assert_eq!(queries::load(stem), file, "`{stem}.sql`");
    }
}

/// A name the library has no file for is a crash, not an empty statement.
#[test]
fn an_unknown_query_name_is_refused() {
    let asked = std::panic::catch_unwind(|| queries::load("view_no_such_query"));
    assert!(asked.is_err());
}

/// The widths this crate declares, against the registry Python writes them out to.
///
/// `analyze/queries.py` is the authority, and these numbers are the only part of it the crate
/// re-declares rather than compiles in — a width bound one character short here cuts a title
/// the SQL sized for a different page. The registry crossing the generation bridge is what
/// they are read against, rather than the Python source text: a number parsed out of an
/// assignment is a second parser to keep honest (`plans/rust-prototype/full-port.md`).
///
/// Both ratchets are below, because a table of names is exactly the thing that rots: a
/// constant added here and left out of it would go unchecked, and a Python width with no
/// counterpart is either a port still to come or a name that moved.
#[test]
fn the_bound_widths_match_the_python_library() {
    // A width is a count of characters and is spent as one (`format::cut`), so it is `usize`;
    // a row count and the cursor's first page are signed, since the latter is negative. Both
    // families are compared as the one signed number the registry writes.
    let checked: BTreeMap<&str, i64> = BTreeMap::from([
        ("NAV_CHARS", queries::NAV_CHARS as i64),
        ("CRUMB_CHARS", queries::CRUMB_CHARS as i64),
        ("HEADER_CHARS", queries::HEADER_CHARS as i64),
        ("HEADER_ITEMS", queries::HEADER_ITEMS as i64),
        ("HEADER_ITEM_CHARS", queries::HEADER_ITEM_CHARS as i64),
        ("LIST_CHARS", queries::LIST_CHARS as i64),
        ("LIST_ITEMS", queries::LIST_ITEMS as i64),
        ("LIST_ITEM_CHARS", queries::LIST_ITEM_CHARS as i64),
        ("LIST_PROJECTS", queries::LIST_PROJECTS as i64),
        ("LIST_CATEGORIES", queries::LIST_CATEGORIES as i64),
        ("LOG_CHARS", queries::LOG_CHARS as i64),
        ("LOG_ROWS", queries::LOG_ROWS),
        ("DETAIL_CHARS", queries::DETAIL_CHARS as i64),
        ("ENRICHMENT_CHARS", queries::ENRICHMENT_CHARS as i64),
        ("TAG_CHARS", queries::TAG_CHARS as i64),
        ("PAGE_RECORDS", queries::PAGE_RECORDS),
        ("RECORD_PREVIEW", queries::RECORD_PREVIEW as i64),
        ("PAGE_PROJECTS", queries::PAGE_PROJECTS),
        ("PAGE_ERRORS", queries::PAGE_ERRORS),
        ("PAGE_RECENT_DAYS", queries::PAGE_RECENT_DAYS),
        ("PAGE_WINDOW_DAYS", queries::PAGE_WINDOW_DAYS),
        ("CHUNK_CHARS", queries::CHUNK_CHARS),
        ("MODEL_CHARS", queries::MODEL_CHARS),
        ("CHIP_CHARS", queries::CHIP_CHARS),
        ("FIRST_PAGE", queries::FIRST_PAGE),
        ("WINDOW_DAYS", queries::WINDOW_DAYS),
    ]);
    for (name, bound) in &checked {
        assert_eq!(*bound, metadata::width(name), "{name}");
    }
    // Nothing this crate declares is left out of the table above.
    let here: BTreeSet<&str> = declared_here();
    let named: BTreeSet<&str> = checked.keys().copied().collect();
    assert_eq!(here, named, "a width this crate declares is unchecked");
    // And nothing Python declares is quietly unaccounted for: it is bound here, or it is one
    // of the queries no page on this side reads yet.
    let python: BTreeSet<&str> = metadata::bounds()
        .widths
        .keys()
        .map(String::as_str)
        .collect();
    let unported: BTreeSet<&str> = UNPORTED.iter().copied().collect();
    assert_eq!(
        &python - &named,
        unported,
        "a Python width is neither bound nor named unported"
    );
    assert!(
        named.is_disjoint(&unported),
        "a width is bound or it is unported"
    );
}

/// The widths only the analysis queries bind, which no crate here has a page for yet.
///
/// They leave this list as the queries that read them are ported; the leaf above reds either
/// way, so neither direction can be forgotten.
const UNPORTED: &[&str] = &[
    "COMMAND_HEAD_CHARS",
    "ERROR_CHARS",
    "RAW_CHARS",
    "SIGNATURE_CHARS",
];

/// Every numeric width `queries.rs` declares, read off the source it declares them in.
///
/// Rust has no reflection over its own constants, so the ratchet has to read the file. Only
/// the names: what they are worth is the assertion above, taken from the constants themselves.
fn declared_here() -> BTreeSet<&'static str> {
    static SOURCE: &str = include_str!("../src/queries.rs");
    SOURCE
        .lines()
        .filter_map(|line| line.trim().strip_prefix("pub const "))
        .filter_map(|rest| rest.split_once(": "))
        .filter(|(_, typed)| typed.starts_with("usize") || typed.starts_with("i64"))
        .map(|(name, _)| name)
        .collect()
}
