//! The pages that are not a node's, end to end: the router in, the whole document out.
//!
//! Each is swept over the whole fixture corpus rather than over one hand-picked row — a session
//! whose calls all succeeded, a thread of one record, a file shorter than a chunk are the shapes
//! that break a page, and the corpus is discovered rather than listed.

use hyphae_testsupport::served::{self, Served};

use axum::http::StatusCode;
use hyphae_store::Store;

#[tokio::test]
async fn every_session_answers_for_its_failures() {
    // Both answers are a fact about the session, and they are not the same nothing: a session
    // whose calls all succeeded is not a session the store never held.
    let served = Served::corpus();
    let mut listed = 0;
    let mut clean = 0;
    for id in served::session_ids(&served.db()) {
        let (status, page) = served.page(&format!("/session/{id}/errors")).await;
        match status {
            StatusCode::OK => {
                assert!(page.contains("id=\"errors\""), "/session/{id}/errors");
                listed += 1;
            }
            StatusCode::NOT_FOUND => {
                assert!(
                    page.contains("This session's tool calls all succeeded."),
                    "/session/{id}/errors"
                );
                clean += 1;
            }
            other => panic!("GET /session/{id}/errors answered {other}"),
        }
    }
    assert!(listed > 0, "the corpus has a session that failed a call");
    assert!(clean > 0, "the corpus has a session that failed none");
    // A session id the store never held is the other nothing, worded for the reader who typed it.
    let (status, page) = served.page("/session/no-such-session/errors").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(page.contains("No session with that id is in this store."));
}

#[tokio::test]
async fn an_offloaded_result_is_served_a_chunk_at_a_time() {
    // The one page whose content is a file rather than a row: it is read a window at a time, and
    // the walk ends when the window reaches the end rather than when a row runs out.
    let served = Served::corpus();
    let Some((session_id, name, chars)) = an_offload(&served.db()) else {
        panic!("the fixture corpus holds an offloaded tool result");
    };
    let file = format!(
        "/session/{session_id}/offload/{}",
        hyphae_view::urls::quoted_path(&name)
    );
    let size = 1 + chars / 3;
    let mut asked = format!("{file}?after=0&size={size}");
    let mut walked = 0;
    loop {
        let (status, page) = served.page(&asked).await;
        assert_eq!(status, StatusCode::OK, "GET {asked}");
        assert!(page.contains("id=\"offload\""), "GET {asked}");
        walked += 1;
        assert!(walked < 100, "the walk terminates");
        let Some(after) = attribute(&page, "data-more-offload") else {
            break;
        };
        asked = format!("{file}?after={after}&size={size}");
    }
    assert!(walked > 1, "the file is longer than one chunk");
    // A name the session has no file for is a 404, and a negative offset is the reader's mistake.
    let (missing, _) = served.page(&format!("{file}x")).await;
    assert_eq!(missing, StatusCode::NOT_FOUND);
    let (before, _) = served.page(&format!("{file}?after=-1")).await;
    assert_eq!(before, StatusCode::BAD_REQUEST);
}

/// The value of one `data-*` attribute on a page, or `None` where the page wrote none.
fn attribute(page: &str, name: &str) -> Option<String> {
    let at = page.find(&format!("{name}=\""))? + name.len() + 2;
    let rest = &page[at..];
    Some(rest[..rest.find('"')?].to_owned())
}

/// The longest offloaded result the corpus holds, with how many characters it stored.
fn an_offload(db: &std::path::Path) -> Option<(String, String, i64)> {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let rows = store
        .fetch(
            "SELECT session_id, name, length(content) AS chars FROM offload_files \
             ORDER BY chars DESC, 1, 2 LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows.first()?;
    Some((
        row.str("session_id").expect("a session id").to_owned(),
        row.str("name").expect("a file name").to_owned(),
        row.i64("chars").expect("a character count"),
    ))
}
