//! A child opened in place, and the rest of a level a tail row stands for.
//!
//! Both mounts are htmx fetches rather than pages, so nothing routes to them by hand: the tests
//! read the ids out of the fixture store and ask for the fragment the page's own markup would.

mod common;

use std::path::Path;

use axum::http::StatusCode;
use hyphae_store::Store;

/// One row per kind a children log lists: the kind, and the URL its View button fetches.
fn openable(db: &Path) -> Vec<(&'static str, String)> {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let mut found = Vec::new();
    for (kind, table) in [
        ("turn", "turns"),
        ("call", "api_calls"),
        ("tool", "tool_calls"),
    ] {
        let rows = store
            .fetch(
                &format!("SELECT session_id, source, id FROM {table} ORDER BY 1, 2, 3 LIMIT 1"),
                &[],
            )
            .expect("the store answers");
        let row = rows
            .first()
            .unwrap_or_else(|| panic!("the corpus has a {kind}"));
        found.push((
            kind,
            format!(
                "/fragment/body/session/{}/thread/{}/{kind}/{}",
                row.str("session_id").expect("a session id"),
                row.str("source").expect("a thread"),
                row.str("id").expect("a node id"),
            ),
        ));
    }
    let rows = store
        .fetch(
            "SELECT session_id, id FROM agent_runs ORDER BY 1, 2 LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows.first().expect("the corpus has an agent run");
    found.push((
        "run",
        format!(
            "/fragment/body/session/{}/run/{}",
            row.str("session_id").expect("a session id"),
            row.str("id").expect("a run id"),
        ),
    ));
    found
}

#[tokio::test]
async fn every_kind_a_log_lists_opens_a_body_that_opens_nothing_further() {
    let served = common::served(|_| {});
    for (kind, url) in openable(&served.db()) {
        let (status, fragment) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "GET {url}");
        // The body arrives as a row of the log it was opened in, marked with its own kind...
        assert!(
            fragment.contains(&format!("data-expansion=\"{kind}\"")),
            "{url} mounts a {kind} body"
        );
        // ...carrying the way to the node's own page, which is where a reader goes for more...
        assert!(fragment.contains("class=\"children\""), "{url} links out");
        // ...and no View button anywhere inside it: an expansion opens no expansion.
        assert!(!fragment.contains("data-view="), "{url} opens another body");
        // What it ran is cited on the fragment itself, the way a page's footer cites.
        assert!(
            fragment.contains("class=\"citations\""),
            "{url} cites its queries"
        );
    }
}

#[tokio::test]
async fn a_body_is_asked_for_by_a_kind_and_an_id_the_store_holds() {
    let served = common::served(|_| {});
    let (session_id, _) = common::busiest_session(&served.db());
    // A kind no children log lists — the session's own body is the page, not an expansion.
    for kind in ["session", "compaction", "nonesuch"] {
        let url = format!("/fragment/body/session/{session_id}/thread/main/{kind}/whatever");
        assert_eq!(
            served.page(&url).await.0,
            StatusCode::NOT_FOUND,
            "GET {url}"
        );
    }
    // A kind that is listed, with an id the store does not hold.
    let url = format!("/fragment/body/session/{session_id}/thread/main/turn/not-a-turn");
    assert_eq!(
        served.page(&url).await.0,
        StatusCode::NOT_FOUND,
        "GET {url}"
    );
    let url = format!("/fragment/body/session/{session_id}/run/not-a-run");
    assert_eq!(
        served.page(&url).await.0,
        StatusCode::NOT_FOUND,
        "GET {url}"
    );
}

#[tokio::test]
async fn a_tail_row_fetches_the_rest_of_its_level_and_no_row_twice() {
    let served = common::served(|_| {});
    let (session_id, turns) = common::busiest_session(&served.db());
    assert!(turns > 2, "the widest fixture thread has a level to cut");
    // The session's first level, drawn at a cap of one: the NavTree shows one turn and a tail row
    // saying how many it left out, and that row's own fetch is what this asks for.
    let (status, page) = served.page(&format!("/session/{session_id}?kin=1")).await;
    assert_eq!(status, StatusCode::OK);
    // How many the window left out, read off the row itself: the level is the thread's turns and
    // whatever bucket the transcript needed, so the tail row is the only thing that knows.
    let cut = counted(&page);
    assert!(
        cut as i64 >= turns - 1,
        "the level's tail row counts what it left out"
    );
    let (status, rest) = served
        .page(&format!(
            "/fragment/kin/session/{session_id}/session/{session_id}?kin=1&thread=main&depth=1"
        ))
        .await;
    assert_eq!(status, StatusCode::OK);
    // Exactly what the tail row said. The rows an agent run adds under each come with them, one
    // level deeper, so the level itself is the rows standing where the tail row stood.
    let rows = rest.matches("data-depth=\"1\"").count();
    assert_eq!(rows, cut, "the spill is the level less the window");
    // And no row the window already drew: the two halves of one split do not overlap.
    for drawn in keys(&page) {
        assert!(!rest.contains(&drawn), "{drawn} is drawn twice");
    }
}

/// How many children a NavTree's one tail row says its level left out.
fn counted(page: &str) -> usize {
    let at = page
        .find("data-field=\"cut\">")
        .expect("the level was cut, so a tail row stands under it");
    let rest = &page[at + "data-field=\"cut\">".len()..];
    rest[..rest.find('<').expect("the count closes")]
        .parse()
        .expect("a tail row counts in digits")
}

/// Every node key the NavTree drew a row for.
fn keys(page: &str) -> Vec<String> {
    page.match_indices("data-nav-tree=\"")
        .map(|(at, mark)| {
            let rest = &page[at + mark.len()..];
            rest[..rest.find('"').expect("the attribute closes")].to_owned()
        })
        .collect()
}

#[tokio::test]
async fn a_level_is_only_spilled_where_a_nav_tree_row_could_stand() {
    let served = common::served(|_| {});
    let (session_id, _) = common::busiest_session(&served.db());
    let at = format!("/fragment/kin/session/{session_id}/session/{session_id}");
    // A depth outside the NavTree's is the reader's mistake, answered before anything is read.
    for depth in ["0", "-1", "99"] {
        let url = format!("{at}?thread=main&depth={depth}");
        assert_eq!(
            served.page(&url).await.0,
            StatusCode::BAD_REQUEST,
            "GET {url}"
        );
    }
    // Neither the thread nor the depth has a default: these rows are going somewhere in a NavTree
    // that already exists, and only the row that asked knows where.
    for asked in ["", "?thread=main", "?depth=1"] {
        let url = format!("{at}{asked}");
        assert_eq!(
            served.page(&url).await.0,
            StatusCode::BAD_REQUEST,
            "GET {url}"
        );
    }
    // A kind no node is, on either mount.
    let url =
        format!("/fragment/kin/session/{session_id}/nonesuch/{session_id}?thread=main&depth=1");
    assert_eq!(
        served.page(&url).await.0,
        StatusCode::NOT_FOUND,
        "GET {url}"
    );
    let url =
        format!("/fragment/kin/session/{session_id}/thread/main/nonesuch/x?thread=main&depth=1");
    assert_eq!(
        served.page(&url).await.0,
        StatusCode::NOT_FOUND,
        "GET {url}"
    );
}
