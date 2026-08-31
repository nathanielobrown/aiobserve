//! What a URL may ask for, and what comes back when it asks for too much or for nothing.
//!
//! The contracts of `docs/viewer-bounds.md`: the children log's paging, the knobs a page carries
//! back into its own links, and the cut a preview makes against the fetch that undoes it.

use hyphae_testsupport::metadata;
use hyphae_testsupport::served::{self, Served};

use std::collections::{BTreeMap, BTreeSet};

use axum::http::StatusCode;
use hyphae_store::Store;
use hyphae_view::knobs;
use hyphae_view::nodes::Preset;
use serde_json::Value;

/// A value longer than the widest a page previews, planted because no fixture carries one: the
/// corpus's largest tool input is 438 characters and the ceiling is 4,000.
const LONG: usize = 5_000;

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

/// Every ceiling and size this crate declares is the number `view/bounds.py` declares.
///
/// `knobs.rs` is a hand port of two Python modules, and nothing about a wrong number here
/// looks wrong: a page served at a ceiling Python retired renders, cites, and passes every
/// leaf above it. The bridged registry is what closes that — the same numbers as data, from
/// the module that owns them (`plans/rust-prototype/full-port.md`).
///
/// A `Bound` is compared whole. Pooling its two numbers would let a default and a ceiling
/// trade places, which is a page served at a size no payload sweep priced.
#[test]
fn every_bound_the_viewer_declares_is_the_number_python_declares() {
    let ceilings: BTreeMap<&str, &knobs::Bound> = BTreeMap::from([
        ("KIN", &knobs::KIN),
        ("LOG", &knobs::LOG),
        ("DETAIL", &knobs::DETAIL),
        ("RECORDS", &knobs::RECORDS),
        ("CHUNK", &knobs::CHUNK),
        ("SESSIONS", &knobs::SESSIONS),
        ("PROJECTS", &knobs::PROJECTS),
        ("ERRORS", &knobs::ERRORS),
    ]);
    for (name, bound) in &ceilings {
        let python = metadata::bound(name);
        assert_eq!(
            (bound.default, bound.ceiling),
            (python.default, python.ceiling),
            "{name}"
        );
    }
    // The plain sizes, which no URL carries and so nothing refuses at: a wrong one is a page
    // that renders, just not the page the ceiling was priced against.
    let sizes: BTreeMap<&str, i64> = BTreeMap::from([
        ("DEPTH", knobs::DEPTH as i64),
        ("INDENT_CHARS", knobs::INDENT_CHARS as i64),
        ("HIGHLIGHT_CHARS", knobs::HIGHLIGHT_CHARS as i64),
        ("CURSORLESS_TURNS", knobs::CURSORLESS_TURNS as i64),
        ("OPENED_RECORD_CHARS", knobs::OPENED_RECORD_CHARS as i64),
    ]);
    for (name, size) in &sizes {
        assert_eq!(*size, metadata::size(name), "{name}");
    }
    // The ratchet: nothing `knobs.rs` declares is missing from the two tables above, and
    // nothing Python declares is unaccounted for on this side.
    let named: BTreeSet<&str> = ceilings.keys().chain(sizes.keys()).copied().collect();
    assert_eq!(
        declared_here(),
        named,
        "a bound this crate declares is unchecked"
    );
    let registry = metadata::bounds();
    let python: BTreeSet<&str> = registry
        .bounds
        .keys()
        .chain(registry.sizes.keys())
        .map(String::as_str)
        .collect();
    let elsewhere: BTreeSet<&str> = BOUND_ELSEWHERE.iter().copied().collect();
    assert_eq!(
        &python - &named,
        elsewhere,
        "a Python bound is neither ported nor named"
    );
}

/// The bounds `bounds.py` declares that this crate holds somewhere other than `knobs.rs`.
const BOUND_ELSEWHERE: &[&str] = &[
    // A re-export of the query width, which `hyphae-store` declares and its own leaf checks.
    "LOG_CHARS",
    // The arithmetic behind the node page's payload ceiling, which lives in the Python
    // tier's `tests/view/budgets.py`; nothing on this side weighs a page yet.
    "NAV_TREE_ROW_BYTES",
];

/// Every bound and plain size `knobs.rs` declares, read off the source it declares them in.
///
/// Rust has no reflection over its own constants, so the ratchet reads the file. Only the
/// names — what they are worth is asserted from the constants themselves.
fn declared_here() -> BTreeSet<&'static str> {
    static SOURCE: &str = include_str!("../src/knobs.rs");
    SOURCE
        .lines()
        .filter_map(|line| line.strip_prefix("pub const "))
        .filter_map(|rest| rest.split_once(": "))
        .filter(|(_, typed)| typed.starts_with("Bound") || typed.starts_with("usize"))
        .map(|(name, _)| name)
        .collect()
}

/// A URL naming no knob is served at Python's defaults, and a link back omits exactly those.
///
/// The other half of the knob contract, and the half a constant comparison cannot reach: what
/// [`knobs::knobs`] leaves out of a link is what a reader who typed nothing gets, so a default
/// that drifted from `view/knobs.py` would mint links to a page Python serves differently
/// (`docs/viewer-bounds.md`).
#[test]
fn a_link_omits_exactly_the_knobs_python_serves_by_default() {
    let defaults = &metadata::bounds().knobs;
    let numbered = |knob: &str| defaults[knob].as_i64().expect("a size default is a number");
    assert_eq!(defaults["nav"], Value::from(Preset::Full.word()));
    let (kin, log, detail) = (numbered("kin"), numbered("log"), numbered("detail"));
    assert_eq!(
        knobs::knobs(Preset::Full, kin, log, detail),
        "",
        "a page at Python's defaults mints a bare link",
    );
    // And each one named on its own the moment it is not the default, so the suffix is not
    // empty for some other reason.
    assert_eq!(
        knobs::knobs(Preset::Agents, kin, log, detail),
        "?nav=agents"
    );
    assert_eq!(
        knobs::knobs(Preset::Full, kin - 1, log, detail),
        format!("?kin={}", kin - 1)
    );
    assert_eq!(
        knobs::knobs(Preset::Full, kin, log - 1, detail),
        format!("?log={}", log - 1)
    );
    assert_eq!(
        knobs::knobs(Preset::Full, kin, log, detail - 1),
        format!("?detail={}", detail - 1)
    );
}
