//! The SQL library: that the Rust catalog holds every file Python reads, and that the widths
//! a page binds are still the widths Python declares.
//!
//! Both leaves read the Python tree. `analyze/queries.py` is the authority, and the numbers
//! below are the only part of it this crate re-declares rather than compiles in.

use hyphae_store::queries;

mod common;

/// The library directory Python's `load` reads out of.
fn query_dir() -> std::path::PathBuf {
    common::repo().join("src/hyphae/analyze/queries")
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

/// The widths a viewer page binds, against the module that declares them.
#[test]
fn the_bound_widths_match_the_python_library() {
    let source = std::fs::read_to_string(common::repo().join("src/hyphae/analyze/queries.py"))
        .expect("the Python query module is readable");
    let declared = |name: &str| -> i64 {
        let assignment = format!("\n{name} = ");
        let at = source
            .find(&assignment)
            .unwrap_or_else(|| panic!("`{name}` is assigned in analyze/queries.py"));
        source[at + assignment.len()..]
            .lines()
            .next()
            .expect("the assignment is on one line")
            .trim()
            .replace('_', "")
            .parse()
            .unwrap_or_else(|_| panic!("`{name}` is assigned a number"))
    };
    assert_eq!(queries::NAV_CHARS, declared("NAV_CHARS"));
    assert_eq!(queries::CRUMB_CHARS, declared("CRUMB_CHARS"));
    assert_eq!(queries::HEADER_CHARS, declared("HEADER_CHARS"));
    assert_eq!(queries::HEADER_ITEMS, declared("HEADER_ITEMS"));
    assert_eq!(queries::HEADER_ITEM_CHARS, declared("HEADER_ITEM_CHARS"));
    assert_eq!(queries::LOG_CHARS, declared("LOG_CHARS"));
    assert_eq!(queries::LOG_ROWS, declared("LOG_ROWS"));
    assert_eq!(queries::DETAIL_CHARS, declared("DETAIL_CHARS"));
    assert_eq!(queries::ENRICHMENT_CHARS, declared("ENRICHMENT_CHARS"));
    assert_eq!(queries::FIRST_PAGE, declared("FIRST_PAGE"));
}
