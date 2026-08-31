//! Every URL the browser tier visits, answered by the Rust router.
//!
//! `tests/e2e/routes.json` is generated from `tests/view/scenarios.py`, so reading it here is
//! what keeps this tier and the browser tier naming the same pages: a page one of them forgets
//! is a page the other still asks for.

use hyphae_testsupport::corpus;
use hyphae_testsupport::served::Served;

use axum::http::StatusCode;
use hyphae_store::Store;
use serde_json::Value;

/// Markup no fixture carries: a transcript can hold anything an agent read, and the only way to
/// see what a page does with a `<script>` is to plant one.
const SENTINEL: &str = "<script>alert('planted')</script>";

/// The one escaped form hypertext, the markdown renderer and the attribute writer agree on.
const ESCAPED: &str = "&lt;script&gt;alert(";

/// The generated route file, as the browser tier reads it.
fn routes() -> Vec<Value> {
    let text = std::fs::read_to_string(corpus::repo().join("tests/e2e/routes.json"))
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
    let served = Served::enriched();
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
    let served = Served::enriched();
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

#[tokio::test]
async fn planted_markup_arrives_inert_on_every_route() {
    // The sentinel goes into every column a page prints — a title, a prompt, what a model wrote,
    // what a tool answered, a raw record, what a pass said — and then every route is read for it.
    // A component leaf cannot stand in for this: a component that wrapped a value in `Raw` would
    // bypass it, and only a served response shows that.
    let served = Served::enriched_planted(|store: &Store| {
        for statement in [
            "UPDATE sessions SET title = ?",
            "UPDATE turns SET prompt = ?, command_args = ?",
            "UPDATE agent_runs SET brief = ?",
            "UPDATE api_calls SET text = ?, thinking = ?",
            // A tool's arguments are JSON, and three of its keys are read rather than printed:
            // the command a page shows on its own, the ask a run's pane reads off the call that
            // spawned it, and the description a NavTree row leads with. So the sentinel goes
            // inside the arguments rather than over them.
            "UPDATE tool_calls SET result = ?, input = '{\"command\": ' || to_json(?) \
             || ', \"prompt\": ' || to_json(?) || ', \"description\": ' || to_json(?) || '}'",
            "UPDATE raw_records SET raw = ?",
            "UPDATE turn_enrichments SET description = ?, friction = ?",
            "UPDATE agent_run_enrichments SET description = ?, friction = ?",
            "UPDATE session_enrichments SET description = ?, friction = ?",
        ] {
            let held = statement.matches('?').count();
            let planted = vec![SENTINEL; held];
            store
                .connection()
                .execute(statement, duckdb::params_from_iter(planted))
                .unwrap_or_else(|error| panic!("{statement}: {error}"));
        }
    });
    // Every route that prints a value the plant reached has to show it escaped, and no route may
    // show it any other way.
    let printing = ["Node kinds", "Value fetches", "Enrichment fetches"];
    for entry in routes() {
        let (status, page) = served.page(field(&entry, "url")).await;
        let route = field(&entry, "route");
        assert_eq!(status, StatusCode::OK, "{route}");
        assert!(!page.contains("<script>alert"), "raw sentinel in {route}");
        if printing.contains(&field(&entry, "group")) {
            assert!(page.contains(ESCAPED), "no escaped sentinel in {route}");
        }
    }
}
