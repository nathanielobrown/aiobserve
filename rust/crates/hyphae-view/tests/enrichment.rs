//! Markdown a pass wrote, from the row it wrote it in to the surfaces that print it.
//!
//! A description is one line written in markdown, and every surface renders it rather than
//! printing the characters it was typed in. No fixture holds one — redaction flattened every
//! string the corpus records — so these leaves plant it and read it back off a served page.

mod common;

use axum::http::StatusCode;
use hyphae_store::Store;

/// What a pass would write in one line about a turn: the two marks a row can carry, and the
/// link only the pane can.
const WRITTEN: &str = "**bold** `code` [PR #18](https://github.test/pr/18)";

/// A model asked for a sentence that answered with a document.
const DOCUMENT: &str = "# Heading\n- one\n- two\n\n```py\nx = 1\n```";

#[tokio::test]
async fn the_markdown_a_pass_wrote_renders_in_a_title_and_links_only_in_the_pane() {
    // A NavTree row and a crumb are each already a link to the node they name, and an `<a>`
    // inside an `<a>` is markup a browser takes apart into something neither element meant. So
    // those print the link's words, and the pane's heading, which nothing wraps, gets the anchor.
    let served = common::enriched(|store: &Store| {
        store
            .connection()
            .execute("UPDATE turn_enrichments SET description = ?", [WRITTEN])
            .expect("a description is plantable");
    });
    let (session_id, turn_id) = a_described_turn(&served.db());
    let (status, page) = served
        .page(&format!("/session/{session_id}/thread/main/turn/{turn_id}"))
        .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        titled(&page, "data-body=\"turn\"", "title"),
        "<strong>bold</strong> <code>code</code> \
         <a href=\"https://github.test/pr/18\">PR #18</a>",
    );
    let inside_a_link = "<strong>bold</strong> <code>code</code> PR #18";
    let row = format!("data-nav-tree=\"turn:{turn_id}\"");
    assert_eq!(titled(&page, &row, "title"), inside_a_link);
    let crumb = format!("data-crumb=\"turn:{turn_id}\"");
    assert_eq!(titled(&page, &crumb, "turn"), inside_a_link);
    // The NavTree draws this description on every turn row of the session, so one nested anchor
    // would be every row: the single href is what says none of them nested.
    assert_eq!(
        page.matches("href=\"https://github.test/pr/18\"").count(),
        1
    );
}

#[tokio::test]
async fn no_block_element_a_pass_wrote_escapes_into_a_nav_tree_row() {
    // Only the inline parser runs, so there is no rule that could open a `<p>` or a `<pre>`
    // inside a row — a row that held a block element would not be a row any more.
    let served = common::enriched(|store: &Store| {
        store
            .connection()
            .execute("UPDATE turn_enrichments SET description = ?", [DOCUMENT])
            .expect("a description is plantable");
    });
    let (session_id, turn_id) = a_described_turn(&served.db());
    let (status, page) = served
        .page(&format!("/session/{session_id}/thread/main/turn/{turn_id}"))
        .await;
    assert_eq!(status, StatusCode::OK);
    let row = titled(&page, &format!("data-nav-tree=\"turn:{turn_id}\""), "title");
    for element in ["<h", "<ul>", "<li>", "<p>", "<pre>", "<ol>", "<blockquote>"] {
        assert!(!row.contains(element), "{element} in {row}");
    }
    // The heading's own `#` and the list's dashes survive as the typing they are.
    assert!(row.contains("# Heading") && row.contains("- one"), "{row}");
}

/// What one `data-field` holds inside the first element marked `marker`.
///
/// The title's own markup nests nothing else in a `span`, so the first close is the field's.
fn titled(page: &str, marker: &str, field: &str) -> String {
    let at = page
        .find(marker)
        .unwrap_or_else(|| panic!("no element marked {marker}"));
    let opens = format!("data-field=\"{field}\">");
    let from = page[at..]
        .find(&opens)
        .unwrap_or_else(|| panic!("no {field} under {marker}"))
        + opens.len();
    let rest = &page[at + from..];
    rest[..rest.find("</span>").expect("the field closes")].to_owned()
}

/// A turn a pass described, on the main thread and with a sibling either side, so the walk
/// controls name the same description the row does.
fn a_described_turn(db: &std::path::Path) -> (String, String) {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let rows = store
        .fetch(
            "SELECT session_id, id FROM turns WHERE source = 'main' AND \"index\" = 1 \
             AND session_id IN (SELECT session_id FROM turns WHERE source = 'main' \
             GROUP BY 1 HAVING count(*) > 2) ORDER BY session_id LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows
        .first()
        .expect("the corpus has a thread of three turns");
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.str("id").expect("a turn id").to_owned(),
    )
}
