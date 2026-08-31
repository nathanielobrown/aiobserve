//! What the viewer serves when no pass has said anything.
//!
//! Ported from `tests/view/test_enrichment.py`. Enrichment is written by a pass that may never have
//! run against the store a reader points the viewer at (`docs/enrichment.md`), so absence is the
//! ordinary case rather than the edge: a store with no enrichment tables, a store whose tables are
//! empty, and a store described but for the items the pass has not reached yet all have to render.

use std::collections::BTreeSet;

use axum::http::StatusCode;
use hyphae_store::Store;
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::selections::{pages, scenarios};
use hyphae_testsupport::served::Served;
use hyphae_testsupport::{cache, rows};
use hyphae_view::enrichment::Level;

/// The fetches behind what a pass wrote, read off the route sweep rather than listed, so a fourth
/// level's pair lands in the absence check with the rest.
fn enrichment_urls() -> Vec<String> {
    scenarios()
        .into_iter()
        .filter(|(route, _)| {
            route.starts_with("/fragment/description/") || route.starts_with("/fragment/friction/")
        })
        .map(|(_, url)| url)
        .collect()
}

/// The viewer over a store holding no enrichment table at all serves every page.
///
/// The ordinary case, not the edge: the tables are created by a pass that writes, and the viewer
/// only ever reads. Nothing on the page stands in for the missing rows either — an empty tag is
/// noise a reader has to learn to ignore.
#[tokio::test]
async fn a_store_no_enrichment_pass_has_touched_renders_every_page() {
    let served = Served::corpus();
    let db = served.db();
    for url in pages(&db) {
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "{url}");
        assert!(
            Markup::of(&page).values("data-enrichment").is_empty(),
            "{url}"
        );
    }
    // And the fetches behind those words answer nothing rather than crashing. No page here links
    // to one — the section that carries the link is not rendered at all — but the URLs are ones a
    // reader can paste from a described store's page, and the table they read does not exist:
    // unguarded, the query raises a catalog error and the route serves a 500.
    let fetches = enrichment_urls();
    assert!(
        !fetches.is_empty(),
        "the route sweep no longer names the fetches behind a pass's words"
    );
    for url in &fetches {
        assert_eq!(served.page(url).await.0, StatusCode::NOT_FOUND, "{url}");
    }
    // And the store really is the bare one, so the sweep above proves what it claims.
    let tables: BTreeSet<String> = rows::all(&db, "SELECT table_name FROM duckdb_tables()", &[])
        .iter()
        .map(|row| row.str("table_name").expect("a table name").to_owned())
        .collect();
    for level in Level::ALL {
        assert!(!tables.contains(level.table()), "{level}");
    }
}

/// A store a pass created but described nothing in renders like a store with no pass at all.
///
/// A pass that quits before its first round leaves exactly this: the three tables, and no row in
/// any of them.
#[tokio::test]
async fn a_store_whose_enrichment_tables_are_empty_renders_every_page() {
    let emptied = Served::enriched_planted(|store: &Store| {
        for level in Level::ALL {
            store
                .connection()
                .execute(&format!("DELETE FROM {}", level.table()), [])
                .unwrap_or_else(|error| panic!("{} is emptied: {error}", level.table()));
        }
    });
    for url in pages(&cache::enriched_store()) {
        let (status, page) = emptied.page(&url).await;
        assert_eq!(status, StatusCode::OK, "{url}");
        assert!(
            Markup::of(&page).values("data-enrichment").is_empty(),
            "{url}"
        );
    }
}

/// An item no pass has described yet is simply absent from its page, not a blank tag.
///
/// The enriched store leaves the last item of every level undescribed, which is the state a pass
/// stopped part way — or one run under `--limit` — leaves behind, and the state any store is in
/// while a pass is still going.
#[tokio::test]
async fn a_partly_described_store_shows_the_items_it_reached_and_nothing_for_the_rest() {
    let served = Served::enriched();
    let db = served.db();
    let mut answered = Vec::new();
    for url in pages(&db) {
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "{url}");
        answered.push((url, page));
    }
    // Some of them carry descriptions and some carry none, which is what makes this store the
    // partial case rather than either of the two above...
    let described = answered
        .iter()
        .filter(|(_, page)| !Markup::of(page).values("data-enrichment").is_empty())
        .count();
    assert!(0 < described && described < answered.len());
    // ...and the turn the pass never reached is on its page, with nothing beside it.
    let bare = rows::one(
        &db,
        "SELECT t.session_id, t.id FROM live_turns t \
         LEFT JOIN turn_enrichments e \
           ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id \
         WHERE t.source = 'main' AND e.turn_id IS NULL",
        &[],
    );
    let (session_id, turn_id) = (
        bare.str("session_id").expect("a session id"),
        bare.str("id").expect("a turn id"),
    );
    let url = format!("/session/{session_id}/thread/main/turn/{turn_id}");
    let shown = answered
        .iter()
        .find(|(served, _)| *served == url)
        .map(|(_, page)| Markup::of(page))
        .unwrap_or_else(|| panic!("the sweep serves {url}"));
    assert_eq!(shown.values("data-selected"), [format!("turn:{turn_id}")]);
    assert!(shown.values("data-enrichment").is_empty());
}
