//! The session's node page, end to end: the router in, the whole document out.
//!
//! Everything here goes through `oneshot` rather than through a component call, deliberately.
//! A component test cannot see the escaping contract — a component that wrapped a value in
//! `Raw` would bypass every assertion below, and only a served response shows it.

mod common;

use axum::http::StatusCode;
use hyphae_store::Store;
use hyphae_view::app::CSP;

/// The sentinel: markup no recorded fixture carries, so planting it is the only way to see what
/// a page does with text nobody here wrote.
const SENTINEL: &str = "<script>alert('planted')</script>";

/// What the sentinel must look like by the time it reaches a reader — the markupsafe spelling
/// `view/render.py` serves, which is what `escape` in `render.rs` writes.
const ESCAPED: &str = "&lt;script&gt;alert(&#39;planted&#39;)&lt;/script&gt;";

#[tokio::test]
async fn every_session_in_the_corpus_gets_a_page() {
    // The whole fixture corpus, not one hand-picked session: a kind of session the walk handles
    // and the page does not is exactly the failure this sweep is for.
    let served = common::served(|_| {});
    let ids = common::session_ids(&served.db());
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
    let served = common::served(|_| {});
    for path in [
        "/session/no-such-session",
        "/static/style.css",
        "/nothing/here",
    ] {
        let response = served.get(&path).await;
        assert_eq!(
            response.headers()["content-security-policy"],
            CSP,
            "GET {path}"
        );
    }
}

#[tokio::test]
async fn planted_markup_arrives_inert() {
    // Both surfaces this page prints a recorded string on: the session's title, which heads the
    // pane and names the crumb, and a turn's prompt, which is what its NavTree row says. Each
    // lands on a real row, so this checks the whole chain rather than a hand-built page.
    let served = common::served(|store: &Store| {
        store
            .connection()
            .execute("UPDATE sessions SET title = ?", [SENTINEL])
            .expect("the title is plantable");
        store
            .connection()
            .execute(
                "UPDATE turns SET prompt = ?, command_args = ?",
                [SENTINEL, SENTINEL],
            )
            .expect("the prompt is plantable");
    });
    let mut checked = 0;
    for id in common::session_ids(&served.db()) {
        let (status, page) = served.page(&format!("/session/{id}")).await;
        assert_eq!(status, StatusCode::OK);
        assert!(!page.contains(SENTINEL), "raw sentinel in /session/{id}");
        if page.contains(ESCAPED) {
            checked += 1;
        }
    }
    // A page that escaped nothing because it printed nothing would pass the assertion above, so
    // the sweep has to have seen the sentinel arrive somewhere.
    assert!(checked > 0, "the sentinel reached at least one page");
}

#[tokio::test]
async fn a_page_number_outside_the_level_is_answered_rather_than_served() {
    // The children log's window binds `?log=` and `?page=` through the keyset composition in
    // `store::window`. Stage 3a does not draw the log's table, so what is observable here is the
    // guard rather than the rows: a page past the level's end, a page below the first, and a
    // knob outside its bounds. 3b's log rows are what make the pages differ.
    let served = common::served(|_| {});
    let (id, turns) = common::busiest_session(&served.db());
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
