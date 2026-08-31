//! The node page's frame: the pane a kind dispatches to, the chain down to it, and its mark.
//!
//! One node of every kind, picked out of the store by `hyphae_testsupport::selections` rather
//! than pinned, so a re-recorded corpus moves the selection instead of reddening the tier. What
//! the pane *holds* per kind is the other node leaves; what every page shares is here.

use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::MISSING;
use hyphae_testsupport::selections::{KINDS, node_url};
use hyphae_testsupport::served::Served;

use axum::http::StatusCode;

/// The mark each kind carries wherever a page names one of its nodes. Written out here rather
/// than read from the viewer's own table: these are its whole visual vocabulary, and a test that
/// imported the table would agree with any edit to it. One mark serves both buckets — each holds
/// what the transcript could not attach, and a reader meets them as one kind of hole, not two.
const MARKS: [(&str, &str); 8] = [
    ("session", "❖"),
    ("turn", "❯"),
    ("run", "◎"),
    ("call", "⇄"),
    ("tool", "⚒"),
    ("compaction", "⊟"),
    ("unattributed", "∅"),
    ("unattached", "∅"),
];

fn mark(kind: &str) -> &'static str {
    MARKS
        .iter()
        .find(|(named, _)| *named == kind)
        .unwrap_or_else(|| panic!("no mark for {kind}"))
        .1
}

#[tokio::test]
async fn every_kind_of_node_serves_a_page_that_says_what_it_is() {
    // Swept per kind rather than over one node because the pane dispatches on the kind and each
    // arm renders different facts. What is checked is the frame every page shares: the right
    // pane, a chain that ends at the selection, and a tree whose selected row is the same node.
    let served = Served::corpus();
    for (kind, _, _) in KINDS {
        let url = node_url(&served.db(), kind);
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "GET {url}");
        let markup = Markup::of(&page);
        // The pane is the one for this kind, and it carries the node's own facts.
        assert_eq!(markup.values("data-body"), [kind], "{url}");
        assert!(!markup.fields("data-body", kind).is_empty(), "{url}");
        // The crumbs run outermost first and end at the selection, the row the NavTree marks.
        let crumbs = markup.values("data-crumb");
        let selected = markup.values("data-selected");
        assert_eq!(selected.len(), 1, "one selection on {url}: {selected:?}");
        let selected = &selected[0];
        let first = crumbs.first().expect("a chain starts somewhere");
        let last = crumbs.last().expect("a chain ends somewhere");
        assert!(first.starts_with("session:"), "{url}: {first}");
        assert_eq!(last, selected, "{url}");
        // And the selection's own row links to the URL that was asked for.
        assert_eq!(
            markup.inside("data-nav-tree", selected, "href")[0],
            url,
            "{url}"
        );
        // Four places on this page name the node, and every one of them says what kind it is with
        // the same character: the pane's heading, the browser tab, the last crumb, and the row the
        // tree marks. A reader learns eight marks once and then reads a tree without reading a
        // title — which is the whole of what the mark buys, so a surface missing it is a surface
        // where the same node looks like something else.
        let glyph = mark(kind);
        assert_eq!(markup.icons("data-body", kind), [glyph], "{url}");
        assert_eq!(
            page.matches(&format!("<title>{glyph} ")).count(),
            1,
            "{url}"
        );
        assert_eq!(markup.icons("data-crumb", last), [glyph], "{url}");
        assert_eq!(markup.icons("data-nav-tree", selected), [glyph], "{url}");
        // And a crumb above the selection is marked as what *it* is, not as what the page is
        // about: the chain says the kind of every step down to here.
        assert_eq!(
            markup.icons("data-crumb", first),
            [mark("session")],
            "{url}"
        );
        // Every one of those marks is decoration and the markup says so. It stands for a word
        // already on the page — the pane's kind, the crumb's field name, the row's class — so a
        // screen reader passes over it and reads the title instead of announcing a character it
        // has no word for (`.claude/rules/viewer-ui.md`).
        for (attribute, key) in [
            ("data-body", kind),
            ("data-crumb", last.as_str()),
            ("data-nav-tree", selected.as_str()),
        ] {
            assert_eq!(
                markup.inside(attribute, key, "aria-hidden"),
                ["true"],
                "{url} at {attribute}"
            );
        }
    }
}

#[tokio::test]
async fn a_node_the_store_does_not_hold_is_a_404() {
    // Every key a node URL carries is read, so a miss on any one of them is nothing. The session
    // is swapped on every kind and the node's own id on every kind that has one: a page that
    // answered on the session alone would be a page about some other session's turn. An empty
    // bucket is a miss too — it is a node that is not there rather than an empty one.
    let served = Served::corpus();
    for (kind, _, _) in KINDS {
        let url = node_url(&served.db(), kind);
        let session_id = url.split('/').nth(2).expect("a node URL names a session");
        let elsewhere = url.replacen(session_id, MISSING, 1);
        let (status, _) = served.page(&elsewhere).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{elsewhere}");
        let tail = url.rsplit('/').next().expect("a URL ends in something");
        if tail != session_id {
            let gone = url.replace(tail, MISSING);
            let (status, _) = served.page(&gone).await;
            assert_eq!(status, StatusCode::NOT_FOUND, "{gone}");
        }
    }
}
