//! What a URL may ask for, and what comes back when it asks for too much or for nothing.
//!
//! The contracts of `docs/viewer-bounds.md`: the knobs a page carries back into its own links, and
//! the numbers this crate declares held against the ones Python declares. The children log's own
//! paging is `node_logs.rs`; what a page weighs is not yet ported.

use hyphae_testsupport::served::{self, Served};
use hyphae_testsupport::{budgets, metadata};

use std::collections::{BTreeMap, BTreeSet};

use axum::http::StatusCode;
use hyphae_view::knobs;
use hyphae_view::nodes::Preset;
use serde_json::Value;

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
        ("NAV_TREE_ROW_BYTES", knobs::NAV_TREE_ROW_BYTES as i64),
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
];

/// Every bound and plain size `knobs.rs` declares, read off the source it declares them in.
///
/// Rust has no reflection over its own constants, so the ratchet reads the file. Only the
/// names — what they are worth is asserted from the constants themselves.
fn declared_here() -> BTreeSet<&'static str> {
    &sized_here("Bound") | &sized_here("usize")
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

/// Every ceiling is projected at the largest page a URL can ask for, because a size is something
/// a reader types.
///
/// ADAPTED: Python carries this arithmetic in the second half of the leaf that pins the
/// manifest's production page sizes (`test_bounds.py`). Here the pins live in
/// `bounds_queries.rs`, which is at its file budget, and the arithmetic is a claim about the
/// bounds this file already owns — so it stands on its own rather than riding a name about the
/// manifest.
#[test]
fn every_ceiling_the_viewer_declares_is_priced_under_the_page_it_serves() {
    assert!(budgets::worst_records_page_bytes() < budgets::PAGE_BYTES);
    assert!(budgets::worst_opened_record_bytes() < budgets::PAGE_BYTES);
    assert!(knobs::CHUNK.ceiling as usize * budgets::ESCAPED_CHAR_BYTES < budgets::PAGE_BYTES);
    // The list is the page a corpus grows, so its ceiling is the widest page a URL can ask for
    // plus the chrome that rides every page — both bound by construction, not by how long the
    // titles this corpus happens to hold are.
    assert!(budgets::worst_session_list_bytes() < budgets::PAGE_BYTES);
    // And it is the most rows that fit, not merely some number that does: the ceiling is derived
    // from the row's cost, so a row that grew has to move it rather than eat the slack silently.
    // The two together are what make `SESSIONS` a measurement — an upper bound alone is satisfied
    // by any smaller page, including one a stale derivation left behind. It is the only ceiling
    // held from below.
    assert!(
        budgets::MEASURED_LIST_CHROME
            + (knobs::SESSIONS.ceiling as usize + 1) * budgets::worst_session_row_bytes()
            >= budgets::PAGE_BYTES
    );
    // The landing page grows the same way — a project per repository the corpus records — and its
    // ceiling is not a size a URL carries: a reader picks a project rather than paging. A
    // session's errors list grows the way both of those do and is not a size a URL carries
    // either: nothing about a session caps how often its tools fail.
    assert!(budgets::worst_projects_page_bytes() < budgets::PAGE_BYTES);
    assert!(budgets::worst_errors_page_bytes() < budgets::PAGE_BYTES);
    // And the node page, the one page every node URL serves: the NavTree a reader walks down the
    // left, and the pane beside it. Its three sizes are each their own ceiling, so this is the
    // widest response any node URL can be asked for.
    assert!(
        budgets::worst_node_bytes() < budgets::NODE_BYTES,
        "{}",
        budgets::worst_node_bytes()
    );
    // And the expansion a row of a log opens in place, which is a click and so has a ceiling of
    // its own: a body, and one page of the level under it at the size the reader is reading logs
    // under. Nothing derives this from `PAGE_BYTES` — it is over it — so the number is declared
    // and the arithmetic checked against it here.
    assert!(budgets::worst_expansion_bytes() < budgets::EXPANSION_BYTES);
    // And no default asks for more than its own ceiling allows, which nothing else checks: a
    // default above the ceiling serves a 400 to a reader who typed no size at all.
    let priced: BTreeMap<&str, &knobs::Bound> = BTreeMap::from([
        ("KIN", &knobs::KIN),
        ("LOG", &knobs::LOG),
        ("DETAIL", &knobs::DETAIL),
        ("RECORDS", &knobs::RECORDS),
        ("CHUNK", &knobs::CHUNK),
        ("SESSIONS", &knobs::SESSIONS),
        ("PROJECTS", &knobs::PROJECTS),
        ("ERRORS", &knobs::ERRORS),
    ]);
    for (name, bound) in &priced {
        assert!(bound.default <= bound.ceiling, "{name}");
    }
    // ...and those are the sizes this leaf priced above: a new one reds here until its ceiling is
    // spent in the arithmetic too, rather than riding a page nobody weighed.
    assert_eq!(
        priced.keys().copied().collect::<BTreeSet<_>>(),
        sized_here("Bound"),
        "a size this crate declares is unpriced"
    );
}

/// The mode that turns re-pinning into something a run reds on rather than a step to remember.
///
/// Every measured pin in `budgets.rs` is read as a ceiling, which is what lets an everyday change
/// make a page smaller without re-pinning the page — and is also how a constant quietly stops
/// describing what it measured. A change that moves bytes on purpose runs the suite under
/// `HYPHAE_PIN_EXACT=1`, where the same pin has to be the measurement, and re-pins whatever the
/// run names.
///
/// ADAPTED: Python drives the variable with `monkeypatch`, which restores it after the test. A
/// nextest leaf owns its process, so the variable is set and cleared here — and read through
/// `budgets::exact_pins` either way, so what is under test is still the reader rather than a
/// second copy of it.
#[test]
fn a_pin_a_page_no_longer_reaches_passes_the_everyday_run_and_reds_the_exact_one() {
    // A page that came in 100 B under its pin. The everyday run has nothing to say about it...
    // SAFETY: nextest runs one test per process, so nothing else reads the environment.
    unsafe { std::env::remove_var(budgets::EXACT_PIN) };
    assert!(!budgets::exact_pins());
    assert!(budgets::fits(8_896, 8_996));
    // ...and the exact run reds on it, naming both numbers through the leaf that called it.
    unsafe { std::env::set_var(budgets::EXACT_PIN, "1") };
    assert!(budgets::exact_pins());
    assert!(!budgets::fits(8_896, 8_996));
    // The pin that run would write instead is the measurement, and it passes both ways.
    assert!(budgets::fits(8_896, 8_896));
    unsafe { std::env::remove_var(budgets::EXACT_PIN) };
    assert!(budgets::fits(8_896, 8_896));
}

/// Every constant `knobs.rs` declares of one type, read off the source it declares them in.
fn sized_here(kind: &str) -> BTreeSet<&'static str> {
    static SOURCE: &str = include_str!("../src/knobs.rs");
    SOURCE
        .lines()
        .filter_map(|line| line.strip_prefix("pub const "))
        .filter_map(|rest| rest.split_once(": "))
        .filter(|(_, typed)| typed.starts_with(kind))
        .map(|(name, _)| name)
        .collect()
}
