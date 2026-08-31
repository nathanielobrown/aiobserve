//! What a URL may ask for, and what comes back when it asks for too much or for nothing.
//!
//! The contracts of `docs/viewer-bounds.md`: the children log's paging, the knobs a page carries
//! back into its own links, and the cut a preview makes against the fetch that undoes it.

use hyphae_testsupport::corpus;
use hyphae_testsupport::served::{self, Served};

use std::collections::BTreeSet;

use axum::http::StatusCode;
use hyphae_store::Store;
use serde_json::Value;

/// An id no fixture holds, so every key a node URL carries can be missed one at a time.
const MISSING: &str = "no-such-id";

/// A value longer than the widest a page previews, planted because no fixture carries one: the
/// corpus's largest tool input is 438 characters and the ceiling is 4,000.
const LONG: usize = 5_000;

/// The eight node-page URLs the generated route file names, one per kind.
fn node_urls() -> Vec<(String, String)> {
    let text = std::fs::read_to_string(corpus::repo().join("tests/e2e/routes.json"))
        .expect("the generated route file is committed");
    serde_json::from_str::<Vec<Value>>(&text)
        .expect("the route file is a list of entries")
        .into_iter()
        .filter(|entry| entry["group"] == "Node kinds")
        .map(|entry| {
            (
                entry["route"].as_str().expect("a route").to_owned(),
                entry["url"].as_str().expect("a url").to_owned(),
            )
        })
        .collect()
}

/// Every child a children log listed on one page, by the key its row carries.
fn children(page: &str) -> Vec<String> {
    page.match_indices("data-child=\"")
        .map(|(at, marker)| {
            let rest = &page[at + marker.len()..];
            rest[..rest.find('"').expect("an attribute closes")].to_owned()
        })
        .collect()
}

#[tokio::test]
async fn a_node_the_store_does_not_hold_is_a_404() {
    // Every key a node URL carries is read, so a miss on any one of them is nothing. The session
    // is swapped on every kind and the node's own id on every kind that has one: a page that
    // answered on the session alone would be a page about some other session's turn.
    let served = Served::corpus();
    for (route, url) in node_urls() {
        let session_id = url.split('/').nth(2).expect("a node url names a session");
        let (gone, _) = served.page(&url.replacen(session_id, MISSING, 1)).await;
        assert_eq!(gone, StatusCode::NOT_FOUND, "{route} without its session");
        let tail = url.rsplit('/').next().expect("a url has a last segment");
        if tail != session_id {
            let (lost, _) = served.page(&url.replace(tail, MISSING)).await;
            assert_eq!(lost, StatusCode::NOT_FOUND, "{route} without its node");
        }
    }
}

#[tokio::test]
async fn every_page_of_a_level_lists_each_row_once_and_stops() {
    // The children log's window is `store::window`: an offset page over the rows that have a
    // cursor value. A cursor bug there loses rows silently rather than erroring, so the walk
    // reads every page a row at a time and holds the union against the whole level.
    let served = Served::corpus();
    let (id, turns) = served::busiest_session(&served.db());
    assert!(turns > 1, "the corpus has a level worth paging");
    let (status, whole) = served.page(&format!("/session/{id}")).await;
    assert_eq!(status, StatusCode::OK);
    let level: BTreeSet<String> = children(&whole).into_iter().collect();
    assert_eq!(level.len() as i64, turns, "the unpaged log lists the level");
    let mut walked = Vec::new();
    for page in 1.. {
        let (status, markup) = served
            .page(&format!("/session/{id}?log=1&page={page}"))
            .await;
        if status == StatusCode::NOT_FOUND {
            break;
        }
        assert_eq!(status, StatusCode::OK, "page {page}");
        assert!(page < 500, "the walk terminates");
        walked.extend(children(&markup));
    }
    assert_eq!(walked.len() as i64, turns, "each row on exactly one page");
    assert_eq!(walked.iter().cloned().collect::<BTreeSet<_>>(), level);
}

#[tokio::test]
async fn a_row_with_no_cursor_is_on_the_page_and_outside_the_count() {
    // A bucket stands for rows the transcript attached to nothing, so the paging query gives it
    // no cursor value and `store::cursorless_rows` is what finds it. It has to reach the NavTree
    // without joining the count the children log pages against.
    let served = Served::corpus();
    let (id, source) = bucketed(&served.db());
    let (status, page) = served.page(&format!("/session/{id}")).await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        page.contains(&format!("/session/{id}/thread/{source}/unattributed")),
        "the bucket stands in the NavTree of /session/{id}",
    );
    // The log counts the turns it pages over; the bucket is not one of them.
    let counted = children(&page).len();
    let store = Store::open_read_only(&served.db()).expect("the store opens read only");
    let rows = store
        .fetch(
            "SELECT count(*) AS turns FROM turns WHERE session_id = $session_id \
             AND source = 'main'",
            &[("session_id", id.as_str().into())],
        )
        .expect("the store answers");
    let turns = rows[0].i64("turns").expect("a turn count");
    assert_eq!(counted as i64, turns, "the bucket is outside the count");
}

#[tokio::test]
async fn a_preview_is_cut_at_the_ceiling_and_the_fetch_behind_it_is_not() {
    // The planted value is longer than the ceiling on every tool call, so whichever the route
    // file names is one whose page has to cut.
    let served = Served::planted(|store: &Store| {
        store
            .connection()
            .execute("UPDATE tool_calls SET input = ?", ["x".repeat(LONG)])
            .expect("the input is plantable");
    });
    let (session_id, source, id) = a_tool(&served.db());
    let node = format!("/session/{session_id}/thread/{source}/tool/{id}");
    let fetch = format!("/fragment/input/session/{session_id}/thread/{source}/tool/{id}");
    let ceiling = hyphae_store::queries::DETAIL_CHARS;
    // The default: the preview stops at the ceiling and marks where it stopped.
    let (status, page) = served.page(&node).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cut_at(&page, "input"), Some(ceiling), "the preview is cut");
    assert!(
        page.contains(&fetch),
        "the cut mark links to the whole value"
    );
    // A knob only goes down, and the cut moves with it.
    let (status, narrow) = served.page(&format!("{node}?detail=100")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        cut_at(&narrow, "input"),
        Some(100),
        "the knob moves the cut"
    );
    // The fetch behind the mark is the whole value, which is what makes the cut safe.
    let (status, whole) = served.page(&fetch).await;
    assert_eq!(status, StatusCode::OK);
    // The fetch names the value rather than the column it came from: it is one value, alone.
    assert_eq!(cut_at(&whole, "value"), None, "the fetch is uncut");
    assert_eq!(shown(&whole, "value").chars().count(), LONG);
}

#[tokio::test]
async fn a_knob_a_page_was_asked_for_comes_back_in_the_links_it_mints() {
    // A click has to serve the URL it displays, so a page under a non-default knob carries it
    // into its own links rather than dropping the reader back to the default.
    let served = Served::corpus();
    let (id, _) = served::busiest_session(&served.db());
    // A turn's own link, which every one of the four knobs has to reach: the preset control mints
    // a link per preset whatever the page was asked for, so reading those would prove nothing.
    let under = format!("/session/{id}/thread/main/turn/");
    for knob in ["nav=agents", "log=1", "kin=5", "detail=100"] {
        let (status, page) = served.page(&format!("/session/{id}?{knob}")).await;
        assert_eq!(status, StatusCode::OK, "?{knob}");
        let links = linked(&page, &under);
        assert!(!links.is_empty(), "?{knob} draws the turns of the session");
        for link in links {
            assert!(link.ends_with(&format!("?{knob}")), "{link} under ?{knob}");
        }
    }
    // And a default is never spelled out, so a link stays as short as the page it came from.
    let (status, page) = served.page(&format!("/session/{id}")).await;
    assert_eq!(status, StatusCode::OK);
    for link in linked(&page, &under) {
        assert!(!link.contains('?'), "{link} on a page under no knob");
    }
}

/// Every href a page wrote that starts with `under`, deduplicated by nothing: a link written
/// twice is written twice.
fn linked(page: &str, under: &str) -> Vec<String> {
    let marker = format!("href=\"{under}");
    page.match_indices(&marker)
        .map(|(at, found)| {
            let rest = &page[at + found.len() - under.len()..];
            rest[..rest.find('"').expect("an attribute closes")].to_owned()
        })
        .collect()
}

/// What one `data-field` printed, once the markup around it is off.
fn shown(page: &str, field: &str) -> String {
    let marker = format!("data-field=\"{field}\">");
    let at = page.find(&marker).unwrap_or_else(|| panic!("no {field}")) + marker.len();
    let rest = &page[at..];
    rest[..rest.find("</").expect("the element closes")].to_owned()
}

/// Where a value was cut, or nothing where it arrived whole: the ellipsis is the mark, so a
/// value that carries none is one nothing was left out of.
fn cut_at(page: &str, field: &str) -> Option<usize> {
    let text = shown(page, field);
    text.strip_suffix(hyphae_view::format::ELLIPSIS)
        .map(|kept| kept.chars().count())
}

/// The session with a thread whose calls answer no turn, and that thread.
fn bucketed(db: &std::path::Path) -> (String, String) {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let rows = store
        .fetch(
            "SELECT session_id, source FROM api_calls WHERE turn_id IS NULL \
             AND source = 'main' ORDER BY 1, 2 LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows.first().expect("the corpus holds an unattributed call");
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.str("source").expect("a thread").to_owned(),
    )
}

/// One tool call, whichever the store lists first.
fn a_tool(db: &std::path::Path) -> (String, String, String) {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let rows = store
        .fetch(
            "SELECT session_id, source, id FROM tool_calls ORDER BY 1, 2, 3 LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows.first().expect("the corpus holds a tool call");
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.str("source").expect("a thread").to_owned(),
        row.str("id").expect("a tool call id").to_owned(),
    )
}
