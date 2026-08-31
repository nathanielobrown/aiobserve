//! The filter form above the session list: what each key narrows, and what it refuses.
//!
//! Every filter the app registers is checked with a sample the store actually holds, so a filter
//! that stopped matching anything is a red rather than an empty page nobody looks at. A value
//! reaches DuckDB only as a binding, and an unknown key or an unparseable value is a 400.

use std::collections::BTreeSet;

use axum::http::StatusCode;
use regex::Regex;

use hyphae_store::{Param, queries};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::MYCELIA;
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::listing::{FILTERS, LIST_KNOBS, list_keys};
use hyphae_view::urls::quoted;

/// One value per filter, read off the fixture corpus rather than invented, chosen so each narrows
/// the 16-session list without emptying it. The leaf below keeps the set honest when a filter is
/// added; the values themselves are checked by the narrowing leaf, which fails loudly if a fixture
/// change makes one of them match everything or nothing.
const SAMPLES: [(&str, &str); 5] = [
    // 13 of the 16 fixture sessions ran in the mycelia checkout...
    ("project", MYCELIA),
    // ...the corpus starts on 2026-06-30 and ends on 2026-08-06, so a bound inside that window
    // cuts rows off each end...
    ("since", "2026-07-01"),
    ("until", "2026-08-01"),
    // ...two sessions ran the grill-me skill...
    ("skill", "grill-me"),
    // ...and two recorded a failing tool call.
    ("errors", "1"),
];

/// The sample for one filter.
fn sample(key: &str) -> &'static str {
    SAMPLES
        .iter()
        .find(|(named, _)| *named == key)
        .map(|(_, value)| *value)
        .unwrap_or_else(|| panic!("no sample for the `{key}` filter"))
}

/// What every list citation says about the display cut, which the viewer composes around the query
/// the same way it composes the paging: re-running the file alone answers whole values.
fn cut() -> String {
    format!(
        "head_chars={} item_chars={} head_items={}",
        queries::LIST_CHARS,
        queries::LIST_ITEM_CHARS,
        queries::LIST_ITEMS
    )
}

/// The session ids one list page shows, in the order it showed them.
async fn listed(served: &Served, query: &str) -> Vec<String> {
    let (status, page) = served.page(&format!("/sessions{query}")).await;
    assert_eq!(status, StatusCode::OK, "{query}");
    Markup::of(&page).values("data-session-id")
}

#[test]
fn every_filter_the_list_offers_has_a_sample_to_check_it_with() {
    // Each filter the list offers is exercised below, so a new one cannot land untested.
    let sampled: BTreeSet<&str> = SAMPLES.iter().map(|(key, _)| *key).collect();
    let offered: BTreeSet<&str> = FILTERS.iter().map(|(key, _)| *key).collect();
    assert_eq!(sampled, offered);
}

#[tokio::test]
async fn a_filter_narrows_the_list_without_emptying_it() {
    // Every filter cuts the list to some of the sessions it held, never to all or none.
    let served = Served::corpus();
    let whole = listed(&served, "").await;
    for (key, value) in SAMPLES {
        let narrowed = listed(&served, &format!("?{key}={}", quoted(value))).await;
        let held: BTreeSet<&String> = narrowed.iter().collect();
        let all: BTreeSet<&String> = whole.iter().collect();
        // A filter that matched everything would pass a subset check while filtering nothing, and
        // one that matched nothing would pass it vacuously. This is a proper, non-empty cut.
        assert!(held.is_subset(&all) && held.len() < all.len(), "{key}");
        assert!(!narrowed.is_empty(), "{key}");
        // The rows kept their order rather than being re-sorted by the filtering.
        let kept: Vec<&String> = whole.iter().filter(|row| held.contains(row)).collect();
        assert_eq!(narrowed.iter().collect::<Vec<&String>>(), kept, "{key}");
    }
}

#[tokio::test]
async fn a_filter_keeps_exactly_the_sessions_the_store_says_it_should() {
    // A skill filter shows the sessions that ran that skill — the whole set, and no other.
    let served = Served::corpus();
    let skill = sample("skill");
    let ran_it: BTreeSet<String> = rows::all(
        &served.db(),
        "SELECT DISTINCT session_id FROM live_api_calls WHERE attribution_skill = $skill",
        &[("skill", Param::from(skill))],
    )
    .iter()
    .map(|row| row.str("session_id").expect("a session id").to_owned())
    .collect();
    let query = format!("?skill={}", quoted(skill));
    let shown = listed(&served, &query).await;
    assert_eq!(shown.iter().cloned().collect::<BTreeSet<String>>(), ran_it);
    // Every row shown says the skill it was filtered by, so the page shows its own evidence.
    let (_, page) = served.page(&format!("/sessions{query}")).await;
    let markup = Markup::of(&page);
    for session_id in &shown {
        assert!(
            markup.fields("data-session-id", session_id)["skills"].contains(skill),
            "{session_id}"
        );
    }
}

#[tokio::test]
async fn a_filter_value_reaches_duckdb_only_as_a_binding() {
    // A filter value that is SQL rather than a name matches nothing and runs nothing.
    //
    // The two filters whose predicates a value could break out of, one per shape: `skill` binds
    // its parameter once, `project` binds the same one twice and concatenates it, which is the
    // place a value spliced as text would have two chances to become SQL.
    let served = Served::corpus();
    let counted = |db: &std::path::Path| {
        rows::one(db, "SELECT count(*) AS held FROM sessions", &[])
            .i64("held")
            .expect("a count")
    };
    let db = served.db();
    for key in ["skill", "project"] {
        let before = counted(&db);
        let query = format!("?{key}={}", quoted("'; DROP TABLE sessions; --"));
        // A value that reached SQL as text would either error or execute; bound, it is a name no
        // session carries...
        assert!(listed(&served, &query).await.is_empty(), "{key}");
        // ...and the table it named is still there, with every row it had.
        assert_eq!(counted(&db), before, "{key}");
    }
}

#[tokio::test]
async fn an_unknown_filter_key_or_unparseable_value_is_refused() {
    // The list reads a closed set of query keys, each at one type; anything else is a 400.
    let served = Served::corpus();
    for (key, value, says) in [
        // A key the list does not offer, however plausible, is told the keys it does...
        ("filter", "grill-me", "skill"),
        ("Skill", "grill-me", "skill"),
        // ...and a known key whose value is not the type its predicate binds is told which.
        ("since", "last tuesday", "since takes date values"),
        ("errors", "many", "errors takes integer values"),
    ] {
        let (status, body) = served
            .page(&format!("/sessions?{key}={}", quoted(value)))
            .await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{key}={value}");
        // The refusal says what would have worked, and never echoes what was asked for — a page
        // that reflected the value back would be the one place unescaped request text could land.
        assert!(body.contains(says), "{key}={value}");
        assert!(!body.contains(value), "{key}={value}");
    }
}

#[tokio::test]
async fn a_form_submitted_with_every_key_filled_in_is_still_a_narrowing() {
    // Every key the list reads, sent at once, is a legal request rather than a 400.
    //
    // The filter form posts all five filters and rides the sort, the page and the size, so a
    // reader who types into every box sends the whole of the list's keys — the boundary the
    // membership test sits on. The samples are the same recorded values the filter leaves use, so
    // the request that comes back is a real cut of the corpus and not an empty page.
    let knobs = [
        ("sort", "cost_usd"),
        ("direction", "asc"),
        ("page", "1"),
        ("size", "5"),
    ];
    let filled: Vec<(&str, &str)> = SAMPLES.iter().copied().chain(knobs).collect();
    let sent: BTreeSet<&str> = filled.iter().map(|(key, _)| *key).collect();
    assert_eq!(
        sent,
        list_keys().into_iter().collect::<BTreeSet<&str>>(),
        "the list reads a key this leaf does not fill in"
    );
    assert_eq!(knobs.len(), LIST_KNOBS.len());
    let query: Vec<String> = filled
        .iter()
        .map(|(key, value)| format!("{key}={}", quoted(value)))
        .collect();
    let served = Served::corpus();
    let shown = listed(&served, &format!("?{}", query.join("&"))).await;
    // It narrowed rather than merely surviving: the corpus is wider than what came back.
    assert!(!shown.is_empty());
    let whole = listed(&served, "").await;
    let held: BTreeSet<&String> = shown.iter().collect();
    let all: BTreeSet<&String> = whole.iter().collect();
    assert!(held.is_subset(&all) && held.len() < all.len());
}

#[tokio::test]
async fn a_filter_rides_the_links_and_the_citation() {
    // A filter survives re-sorting and paging, and the footer says the list was filtered.
    let served = Served::corpus();
    let skill = sample("skill");
    let (_, page) = served
        .page(&format!(
            "/sessions?skill={}&sort=cost_usd&size=1",
            quoted(skill)
        ))
        .await;
    // Every heading link and every pager link carries the filter, so changing the order or turning
    // the page does not quietly widen the list back to the corpus...
    let minted = Regex::new(r#"href="(/sessions\?[^"]*)""#).expect("a pattern");
    let links: Vec<&str> = minted
        .captures_iter(&page)
        .map(|found| found.get(1).expect("the group").as_str())
        .collect();
    assert!(!links.is_empty());
    for link in &links {
        assert!(link.contains("skill=grill-me"), "{link}");
    }
    // The list lives at `/sessions` whole — its form, its clear link and every link it mints go
    // there. A `/?sort=` survivor would land on the projects page, which answers a different
    // question and would drop the filter on the way.
    assert!(
        !Regex::new(r#"href="/\?[^"]*""#)
            .expect("a pattern")
            .is_match(&page)
    );
    assert!(page.contains(r#"<form id="filters" method="get" action="/sessions">"#));
    assert!(page.contains(r#"<a href="/sessions">clear</a>"#));
    // ...and the citation carries it too, after the paging, so the line reproduces the rows.
    assert_eq!(
        Markup::of(&page).fields("id", "citation")["view_sessions"],
        format!(
            "-- queries/view_sessions.sql sort=cost_usd direction=desc limit=1 offset=0 {} \
             skill=grill-me",
            cut()
        )
    );
}
