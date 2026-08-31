//! The session's node page, end to end: the router in, the whole document out.
//!
//! Everything here goes through `oneshot` rather than through a component call: the escaping
//! sweep these leaves stand beside is in `tests/routes.rs`.

use hyphae_testsupport::served::{self, Served};

use axum::http::StatusCode;
use hyphae_view::app::CSP;

#[tokio::test]
async fn every_session_in_the_corpus_gets_a_page() {
    // The whole fixture corpus, not one hand-picked session: a kind of session the walk handles
    // and the page does not is exactly the failure this sweep is for.
    let served = Served::corpus();
    let ids = served::session_ids(&served.db());
    assert!(
        !ids.is_empty(),
        "the fixture corpus put sessions in a store"
    );
    for id in &ids {
        let (status, page) = served.page(&format!("/session/{id}")).await;
        assert_eq!(status, StatusCode::OK, "GET /session/{id}");
        // The two halves of a node page arrive in one response, which is what a click re-fetches.
        assert!(
            page.contains("id=\"nav-tree-rows\""),
            "NavTree in /session/{id}"
        );
        assert!(
            page.contains("id=\"reading-pane\""),
            "pane in /session/{id}"
        );
        // The session's own row is the one the NavTree opens on, and the pane is reading it.
        assert!(
            page.contains("aria-current=\"true\""),
            "selection in /session/{id}"
        );
    }
}

#[tokio::test]
async fn a_response_that_is_not_a_page_still_carries_the_content_security_policy() {
    // The route sweep asserts the header over every page; these are the responses no route file
    // names — a refusal, a static file, a miss — where dropping it would go unseen.
    let served = Served::corpus();
    for path in [
        "/session/no-such-session",
        "/static/style.css",
        "/nothing/here",
    ] {
        let response = served.get(path).await;
        assert_eq!(
            response.headers()["content-security-policy"],
            CSP,
            "GET {path}"
        );
    }
}

#[tokio::test]
async fn a_page_number_outside_the_level_is_answered_rather_than_served() {
    // The children log's window binds `?log=` and `?page=` through the keyset composition in
    // `store::window`. Stage 3a does not draw the log's table, so what is observable here is the
    // guard rather than the rows: a page past the level's end, a page below the first, and a
    // knob outside its bounds. 3b's log rows are what make the pages differ.
    let served = Served::corpus();
    let (id, turns) = served::busiest_session(&served.db());
    assert!(turns > 1, "the corpus has a level worth paging");
    let (first, _) = served.page(&format!("/session/{id}?log=1")).await;
    let (second, _) = served.page(&format!("/session/{id}?log=1&page=2")).await;
    assert_eq!((first, second), (StatusCode::OK, StatusCode::OK));
    // Past the last page: a level that has no such page is a 404, and a page below the first is
    // a 400 — what is wrong there is the number, not the node the URL names.
    let (past, _) = served.page(&format!("/session/{id}?log=1&page=9999")).await;
    assert_eq!(past, StatusCode::NOT_FOUND);
    let (below, _) = served.page(&format!("/session/{id}?page=0")).await;
    assert_eq!(below, StatusCode::BAD_REQUEST);
    // And a knob outside its bounds, which is the other thing a reader can type.
    let (huge, _) = served.page(&format!("/session/{id}?kin=100000")).await;
    assert_eq!(huge, StatusCode::BAD_REQUEST);
    let (unknown, _) = served.page(&format!("/session/{id}?nav=bogus")).await;
    assert_eq!(unknown, StatusCode::BAD_REQUEST);
}
