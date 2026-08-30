//! Every URL the browser tier visits, answered by the Rust router.
//!
//! `tests/e2e/routes.json` is generated from `tests/view/scenarios.py`, so reading it here is
//! what keeps this tier and the browser tier naming the same pages: a page one of them forgets
//! is a page the other still asks for.

mod common;

use axum::http::StatusCode;
use serde_json::Value;

/// The generated route file, as the browser tier reads it.
fn routes() -> Vec<Value> {
    let text = std::fs::read_to_string(common::repo().join("tests/e2e/routes.json"))
        .expect("the generated route file is committed");
    serde_json::from_str::<Vec<Value>>(&text).expect("the route file is a list of entries")
}

fn field<'a>(entry: &'a Value, name: &str) -> &'a str {
    entry[name]
        .as_str()
        .unwrap_or_else(|| panic!("every route entry carries {name}"))
}

#[tokio::test]
async fn every_route_the_browser_tier_visits_answers() {
    // The enriched store, because the description and friction fragments are 404 until a pass
    // has written to the store: an un-enriched sweep would pass while saying nothing.
    let served = common::enriched(|_| ());
    let mut failed = Vec::new();
    for entry in routes() {
        let (status, _) = served.page(field(&entry, "url")).await;
        if status != StatusCode::OK {
            // The template rather than the url: it names the mount that broke.
            failed.push(format!("{} -> {status}", field(&entry, "route")));
        }
    }
    assert!(failed.is_empty(), "routes that did not answer: {failed:#?}");
}

#[tokio::test]
async fn every_route_carries_the_content_security_policy() {
    // The header is what makes the browser tier's empty-console assertion mean anything: without
    // it a page could load an inline script and nothing would notice.
    let served = common::enriched(|_| ());
    for entry in routes() {
        let url = field(&entry, "url");
        let response = served.get(url).await;
        assert_eq!(
            response
                .headers()
                .get("content-security-policy")
                .map(|value| value.to_str().expect("an ASCII header")),
            Some("default-src 'self'"),
            "{url}",
        );
    }
}
