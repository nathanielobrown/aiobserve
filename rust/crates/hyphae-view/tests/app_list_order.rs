//! How the session list is ordered, paged and refused.
//!
//! The order is re-derived in the test's own SQL against the library query the list ranks, the
//! page size comes from `view::knobs`, and the pages are turned by following the hrefs the list
//! itself minted — so a pager pointing at the wrong page is a red rather than a tiling that
//! happens to work.

use std::collections::BTreeSet;

use axum::http::StatusCode;
use duckdb::params;
use html_escape::decode_html_entities;
use regex::Regex;

use hyphae_store::{Param, Store, manifest, queries};
use hyphae_testsupport::html::{Markup, counted, cut, plain};
use hyphae_testsupport::landmarks::SPINE;
use hyphae_testsupport::rows;
use hyphae_testsupport::served::{Served, listed_sessions};
use hyphae_view::knobs;
use hyphae_view::listing::{ARIA_SORT, DEFAULT_DIRECTION, DEFAULT_SORT, DIRECTIONS, SORTS};

/// The list query as the runner runs it, at the defaults the manifest declares.
///
/// What a sort key names is a column, and no binding the file takes changes which columns come
/// back — so the defaults are read off the manifest rather than listed here.
fn listing_query() -> (String, Vec<(&'static str, Param)>) {
    let sql = queries::load("view_sessions").trim().trim_end_matches(';');
    let defaults = manifest::entry("view_sessions")
        .params
        .iter()
        .map(|(name, spec)| {
            (
                name.as_str(),
                spec.binding()
                    .expect("the list query names no required parameter"),
            )
        })
        .collect();
    (sql.to_owned(), defaults)
}

/// The session ids one list page shows, in the order it showed them.
async fn listed(served: &Served, query: &str) -> Vec<String> {
    let (status, page) = served.page(&format!("/sessions{query}")).await;
    assert_eq!(status, StatusCode::OK, "{query}");
    Markup::of(&page).values("data-session-id")
}

#[test]
fn every_sort_key_names_a_column_the_query_returns() {
    // No sort key can reach past the library query into SQL of its own.
    let db = hyphae_testsupport::cache::corpus_store();
    let (sql, defaults) = listing_query();
    let returned: BTreeSet<String> = rows::all(&db, &format!("DESCRIBE ({sql})"), &defaults)
        .iter()
        .map(|row| row.str("column_name").expect("a column name").to_owned())
        .collect();
    for (key, _) in SORTS {
        assert!(returned.contains(key), "the query returns no `{key}`");
    }
}

#[tokio::test]
async fn a_sort_and_its_reverse_are_exact_opposites() {
    // Every sort key totally orders the rows that carry a value, so flipping the direction
    // reverses them — and the rows carrying none sit at the end of both.
    //
    // Two claims rather than one, because the empty rows are pinned. A session the store knows
    // nothing about is not the newest, the cheapest or the busiest, so it sorts last whichever way
    // the reader asked; the rows that do carry a value are the ones reversal is about.
    let served = Served::corpus();
    let db = served.db();
    let (sql, defaults) = listing_query();
    let mut pinned = 0;
    for (sort, _) in SORTS {
        // Which sessions carry no value in this column, asked of the query the list ranks rather
        // than of a table beside it: two of the keys are the query's own arithmetic.
        let empty: BTreeSet<String> = rows::all(
            &db,
            &format!("SELECT session_id FROM ({sql}) WHERE {sort} IS NULL"),
            &defaults,
        )
        .iter()
        .map(|row| row.str("session_id").expect("a session id").to_owned())
        .collect();
        let mut order = Vec::new();
        for (direction, _) in DIRECTIONS {
            let shown = listed(&served, &format!("?sort={sort}&direction={direction}")).await;
            assert!(shown.len() > 1, "{sort} {direction}");
            let valued: Vec<String> = shown
                .iter()
                .filter(|row| !empty.contains(*row))
                .cloned()
                .collect();
            // And the empties trail the list rather than riding to the top of it.
            let trailing: BTreeSet<&String> = shown[valued.len()..].iter().collect();
            assert_eq!(
                trailing,
                empty.iter().filter(|row| shown.contains(row)).collect(),
                "{sort} {direction}"
            );
            order.push(valued);
        }
        pinned += usize::from(!empty.is_empty());
        let mut reversed = order[1].clone();
        reversed.reverse();
        assert_eq!(order[0], reversed, "{sort}");
    }
    // The empties are pinned somewhere: a corpus in which every session carried every value would
    // pass the trailing check above without ever running it.
    assert!(pinned > 0, "no sort key has a row the store left empty");
}

#[tokio::test]
async fn the_sorted_heading_says_which_way_in_arias_own_words() {
    // The column in force is the only one marked `aria-sort`, in the words ARIA defines.
    //
    // The query string's `asc` and `desc` are ours; `ascending` and `descending` are the tokens a
    // screen reader reads. An invalid token is not read as "unsorted" — it is read as nothing at
    // all, which is the one thing the mark exists to prevent.
    let served = Served::corpus();
    let marked = Regex::new(r#"<th[^>]*\bdata-column="([^"]*)"[^>]*\baria-sort="([^"]*)""#)
        .expect("a pattern");
    for (direction, _) in DIRECTIONS {
        let (_, page) = served
            .page(&format!("/sessions?sort=cost_usd&direction={direction}"))
            .await;
        let spelling = ARIA_SORT
            .iter()
            .find(|(key, _)| *key == direction)
            .map(|(_, word)| *word)
            .expect("the direction is one of two");
        let found: Vec<(String, String)> = marked
            .captures_iter(&page)
            .map(|took| (took[1].to_owned(), took[2].to_owned()))
            .collect();
        assert_eq!(
            found,
            vec![("cost_usd".to_owned(), spelling.to_owned())],
            "{direction}"
        );
        // And the vocabulary is ARIA's, not a rewording of ours that happens to be longer.
        assert!(
            matches!(spelling, "ascending" | "descending"),
            "{direction}"
        );
    }
}

#[tokio::test]
async fn an_unknown_sort_or_direction_is_refused() {
    // Sort and direction come from closed dictionaries; anything else is a 400.
    let served = Served::corpus();
    for (key, value) in [
        // A key that is not in the closed dict, however plausible...
        ("sort", "session_id"),
        // ...the two the recompose demoted to secondary lines, which the query still returns and
        // the list no longer offers — a stale bookmark, not an injection...
        ("sort", "output_tokens"),
        ("sort", "active_ms"),
        // ...a direction that is not one of the two...
        ("direction", "sideways"),
        // ...and the shape of an attempt to reach the SQL through either.
        ("sort", "cost_usd; DROP TABLE sessions"),
        ("direction", "asc, 1"),
    ] {
        let (status, body) = served
            .page(&format!(
                "/sessions?{key}={}",
                hyphae_view::urls::quoted(value)
            ))
            .await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{key}={value}");
        // The refusal says what is allowed, and does not echo what was asked for.
        assert!(!body.contains(value), "{key}={value}");
        assert!(body.contains(DEFAULT_SORT), "{key}={value}");
    }
}

#[tokio::test]
async fn the_list_footer_cites_its_query_and_what_was_composed_around_it() {
    // The page carries the query behind it, at the sort and the page this request ran.
    let served = Served::corpus();
    let (_, page) = served
        .page("/sessions?sort=cost_usd&direction=asc&size=5")
        .await;
    assert_eq!(
        Markup::of(&page).fields("id", "citation")["view_sessions"],
        format!(
            "-- queries/view_sessions.sql sort=cost_usd direction=asc limit=5 offset=0 {}",
            cut()
        )
    );
    // A bare request cites the defaults, so a copied line reproduces what was seen.
    let (_, bare) = served.page("/sessions").await;
    assert_eq!(
        Markup::of(&bare).fields("id", "citation")["view_sessions"],
        format!(
            "-- queries/view_sessions.sql sort={DEFAULT_SORT} direction={DEFAULT_DIRECTION} \
             limit={} offset=0 {}",
            knobs::SESSIONS.default,
            cut()
        )
    );
}

#[tokio::test]
async fn the_list_is_served_a_page_at_a_time() {
    // The pages of the list tile the store in order: no row twice, none missing.
    //
    // Turned the way a reader turns them: the "older page" href the list itself minted is the
    // string fetched, unescaped as a browser would unescape it. A test that built its own `?page=`
    // would tile the store just as well against a pager pointing at the wrong page, so following
    // the link is what puts the link under test.
    let served = Served::corpus();
    let size = 5;
    let mut seen: Vec<String> = Vec::new();
    let mut url = format!("/sessions?size={size}");
    let mut ran_out = false;
    // A size the fixture corpus needs four pages of, followed to the end...
    for _ in 0..9 {
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "GET {url}");
        let markup = Markup::of(&page);
        let rows = markup.values("data-session-id");
        assert!(rows.len() <= size);
        seen.extend(rows);
        // `inside` panics on a scope that is not there, and the last page is exactly that: the
        // pager mints no "older page" link, which is how the walk knows it has reached the end.
        if !markup.holds("data-page", "next") {
            ran_out = true;
            break;
        }
        let onward: BTreeSet<String> = markup
            .inside("data-page", "next", "href")
            .iter()
            .map(|href| decode_html_entities(href).into_owned())
            .collect();
        // The pager above the table and the one below it offer the same next page.
        assert_eq!(onward.len(), 1);
        url = onward.into_iter().next().expect("the one next page");
    }
    assert!(ran_out, "the pager never ran out of pages");
    // ...holds every session once, in the order one long list would have had.
    assert_eq!(seen, listed_sessions(&served.db()));
    // Past the end is an empty page rather than an error: a stale link is not a fault...
    let (status, beyond) = served.page("/sessions?page=99").await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&beyond);
    assert!(markup.values("data-session-id").is_empty());
    // ...and it says so, rather than counting a range that ends before it starts.
    assert_eq!(markup.fields("data-pager", "top")["range"], "No sessions");
}

#[tokio::test]
async fn the_pager_counts_a_store_deeper_than_a_page_with_separators() {
    // The range the pager prints goes through the formatter every count on a page does.
    //
    // Planted, because the fixture corpus holds sixteen sessions and the store this list is read
    // against holds thousands: under a thousand a formatted range and a bare one are the same
    // string. The clones are of a recorded session, so each one is a row the list really builds.
    let over: i64 = 1_200;
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "INSERT INTO sessions (SELECT s.* REPLACE (s.id || '-planted-' || i AS id) \
                 FROM sessions s, range(1, ?) t(i) WHERE s.id = ?)",
                params![over + 1, SPINE],
            )
            .expect("the clones land");
    });
    // The first page whose numbers run past a thousand, derived rather than typed: the page size
    // moves with what a row costs, and a page number that did not move with it would stop reaching
    // the boundary this test is about.
    let size = knobs::SESSIONS.default;
    let page_number = 1_000 / size + 2;
    let (_, page) = served
        .page(&format!("/sessions?size={size}&page={page_number}"))
        .await;
    let markup = Markup::of(&page);
    let first = (page_number - 1) * size + 1;
    let last = first + markup.values("data-session-id").len() as i64 - 1;
    // A page deep into the list says which rows of it these are, both ends grouped in threes.
    assert!(
        first > 1_000,
        "the plant no longer reaches past a thousand rows"
    );
    let range = format!("Sessions {}–{}", counted(first), counted(last));
    assert_eq!(markup.fields("data-pager", "top")["range"], range);
    // And it reads as three phrases. The `data-*` above cannot see that: the three controls are
    // inline and no rule in the stylesheet holds them apart, so the spaces between them are
    // children the page writes on purpose, and a page that dropped them would say
    // `← newer pageSessions 1,021–1,040older page →` and still pass every assertion above.
    let nav = Regex::new(r#"(?s)<nav class="pager" data-pager="top".*?</nav>"#).expect("a pattern");
    let found: Vec<&str> = nav.find_iter(&page).map(|at| at.as_str()).collect();
    assert_eq!(found.len(), 1);
    assert_eq!(
        plain(found[0]),
        format!("← newer page {range} older page →")
    );
}

#[tokio::test]
async fn a_page_outside_the_bounds_is_refused() {
    // The page size is bounded on both ends: a page cannot be asked to hold the store.
    let served = Served::corpus();
    for query in ["page=0", "page=-1", "size=0", "size=100000"] {
        let (status, body) = served.page(&format!("/sessions?{query}")).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{query}");
        assert!(
            body.contains(&knobs::SESSIONS.ceiling.to_string()),
            "{query}"
        );
    }
}
