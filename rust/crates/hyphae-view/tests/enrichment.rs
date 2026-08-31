//! What this crate believes about enrichment, held against what the pass that writes it believes.
//!
//! A page reads rows a Python pass wrote and judges them fresh or stale by versions declared on
//! both sides. Nothing on a rendered page shows the two drifting apart, so the bridged metadata is
//! the only thing that can say. What the pages do with the rows is `enrichment_pages.rs`,
//! `enrichment_words.rs` and `enrichment_absence.rs`; how a description is rendered once read is
//! `node_markdown.rs`.

use std::collections::BTreeSet;

use hyphae_store::Store;
use hyphae_testsupport::{cache, metadata};
use hyphae_view::enrichment::{Level, TAXONOMY_VERSION};

/// The stamps this crate judges a row's freshness by are the ones the pass writes them under.
///
/// [`Enrichment::stale`] compares a row's two versions against constants declared here, so a
/// version Python bumped and this side did not would mark every fresh row stale — and one
/// bumped only here would mark every stale row fresh. Neither is visible on a rendered page:
/// the provenance line says "fresh" either way. The bridged metadata is what settles it
/// (`plans/rust-prototype/full-port.md`).
#[test]
fn the_versions_a_page_judges_freshness_by_are_the_ones_the_pass_writes() {
    let bridged = metadata::enrichment();
    assert_eq!(TAXONOMY_VERSION, bridged.taxonomy_version);
    for level in Level::ALL {
        let named = bridged
            .levels
            .get(level.word())
            .unwrap_or_else(|| panic!("no enrichment level called `{}`", level.word()));
        assert_eq!(level.table(), named.table, "{level}");
        assert_eq!(level.prompt_version(), named.prompt_version, "{level}");
    }
    // Both ways: a fourth level Python describes would otherwise be one this crate never asks
    // the store about, and a page over it would render as if the pass had said nothing.
    let here: BTreeSet<&str> = Level::ALL.iter().map(|level| level.word()).collect();
    let bridged: BTreeSet<&str> = bridged.levels.keys().map(String::as_str).collect();
    assert_eq!(here, bridged);
}

/// Every category and outcome the store holds is a member of the bridged vocabularies.
///
/// A vocabulary is closed (`docs/enrichment.md`), which is what lets a page group by it. This
/// reads what a pass actually wrote rather than what the module declares — the check the
/// Python-side freshness leaf cannot make, since the rows are the thing being described.
#[test]
fn every_word_the_stored_rows_are_written_in_is_in_the_bridged_vocabulary() {
    let bridged = metadata::enrichment();
    let store = Store::open_read_only(&cache::enriched_store()).expect("the enriched store opens");
    let mut seen = 0;
    for level in Level::ALL {
        let rows = store
            .fetch(
                &format!("SELECT category, outcome FROM {}", level.table()),
                &[],
            )
            .expect("the enrichment table is readable");
        for row in &rows {
            let (category, outcome) = (row.str("category").unwrap(), row.str("outcome").unwrap());
            assert!(
                bridged.categories.iter().any(|held| held == category),
                "{category}"
            );
            assert!(
                bridged.outcomes.iter().any(|held| held == outcome),
                "{outcome}"
            );
            seen += 1;
        }
    }
    // The absence is bounded: a store with no rows would pass the walk above saying nothing.
    assert!(seen > 0, "the enriched fixture store holds no rows to read");
}
