//! Serving, and the two things that go wrong under a running viewer.
//!
//! The store is a file another process writes. An extract can replace its schema between two
//! page loads, so the version is checked per request rather than once at startup — and the
//! answer is a page that says what to do. The other is the viewer's own: a builder that fails
//! halfway down a page.
//!
//! Ported from `tests/view/test_lifecycle.py`, which is split by what a leaf needs a process
//! for. Its four startup and lock leaves are `hp/tests/cli.rs`: a lock is per process, and a
//! taken port and a refused launch are exit codes, so all four are already there driving the
//! real binary rather than a router. What is left is what a router can be asked.

use std::path::Path;

use axum::http::StatusCode;
use hyphae_store::{Store, schema};
use hyphae_testsupport::corpus;
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::SPINE;
use hyphae_testsupport::served::Served;

#[tokio::test]
async fn a_store_replaced_under_the_viewer_is_caught_per_request() {
    // A re-extract between two page loads is refused rather than half-read.
    //
    // The viewer opened this store and served off it, so the version it checked at startup was
    // the right one. What arrives next is a schema bump plus a fresh extract, seen from inside a
    // running viewer — and the only thing that catches it is the check the next request makes.
    let served = Served::planted(|_| {});
    assert_eq!(served.page("/").await.0, StatusCode::OK);
    {
        let store = Store::open_for_write(&served.db()).expect("the store opens for writing");
        store
            .connection()
            .execute(
                "UPDATE meta SET schema_version = ?",
                duckdb::params![schema::SCHEMA_VERSION + 1],
            )
            .expect("the version row is writable");
    }
    let (status, page) = served.page("/").await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    // Naming the version this build reads, because the way out is to restart the viewer.
    assert!(
        Markup::of(&page).fields("id", "error")["message"]
            .contains(&schema::SCHEMA_VERSION.to_string()),
        "{page}"
    );
}

#[tokio::test]
async fn a_page_that_fails_mid_build_answers_500_and_sends_none_of_it() {
    // A page is built whole before a response exists, so a failure is never half-sent.
    //
    // Python has to prove this at runtime: htpy renders lazily, so a component that yields
    // markup and then raises leaves a 200 already sent and a reader looking at a page that
    // looks finished. Rust cannot reach that state — a builder returns `Result<Rendered<String>>`
    // and the handler turns the string into a body only after it has one — so the leaf pins the
    // two halves of that claim instead: what a failed page answers, and that nothing streams.
    //
    // The plant drops a view the session page reads after its header, which is the shape of a
    // real bug: the store answered, the page began, and then a query it makes further down did
    // not.
    let url = format!("/session/{SPINE}");
    let served = Served::planted(|store| {
        store
            .connection()
            .execute("DROP VIEW live_compactions CASCADE", [])
            .expect("the view is droppable");
    });
    let (status, page) = served.page(&url).await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    // Loud rather than blank: the cause is the one thing that turns a 500 into a bug report.
    assert!(
        Markup::of(&page).fields("id", "error")["message"].contains("live_compactions"),
        "{page}"
    );
    // And not one byte of the page it was building. The markers are the ones the session page
    // always carries, read off the same URL served over an unplanted store, so this cannot pass
    // by naming markup the page never had.
    let (whole_status, whole) = Served::corpus().page(&url).await;
    assert_eq!(whole_status, StatusCode::OK);
    for marker in ["data-nav-tree", "data-body", "data-crumb"] {
        assert!(whole.contains(marker), "the session page dropped {marker}");
        assert!(!page.contains(marker), "the failed page leaked {marker}");
    }
    // The other half: no route hands axum a body it has not finished writing. A streaming
    // response is the one way a 500 could arrive after a 200 already had.
    let streamed: Vec<String> = sources(&corpus::repo().join("rust/crates/hyphae-view/src"))
        .into_iter()
        .filter(|(_, text)| text.contains("Body::from_stream") || text.contains("StreamBody"))
        .map(|(path, _)| path)
        .collect();
    assert_eq!(streamed, Vec::<String>::new());
}

/// Every Rust source under `at`, by path relative to it, with its text.
fn sources(at: &Path) -> Vec<(String, String)> {
    let mut found = Vec::new();
    let mut pending = vec![at.to_owned()];
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(&directory).expect("the source tree is readable") {
            let path = entry.expect("a directory entry").path();
            if path.is_dir() {
                pending.push(path);
            } else if path.extension().is_some_and(|suffix| suffix == "rs") {
                let text = std::fs::read_to_string(&path).expect("a source file reads");
                let named = path
                    .strip_prefix(at)
                    .expect("every file is under the root")
                    .display()
                    .to_string();
                found.push((named, text));
            }
        }
    }
    assert!(!found.is_empty(), "no sources under {}", at.display());
    found
}
