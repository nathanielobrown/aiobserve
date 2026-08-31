//! What the viewer actually serves, weighed against the ceiling.
//!
//! Ported from the sweeping half of `tests/view/test_bounds.py`. A smoke check rather than the
//! proof: the fixture corpus is far smaller than a page, so what makes the bound hold is the
//! scan and the manifest pin in `bounds_queries.rs`. What these catch is the route that ships a
//! whole column anyway, and the two values a page fetches for a reader who clicked nothing.
//!
//! The worst-case arithmetic over a measured row is `tests/view/budgets.py`'s and is not ported
//! (`hyphae_testsupport::budgets`).

use axum::http::StatusCode;
use duckdb::params;
use hyphae_store::{Store, manifest};
use hyphae_testsupport::budgets::PAGE_BYTES;
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{CONFIG_ONLY, MAIN, OFFLOAD_FILE, RESUME, RESUME_LONG_RECORD};
use hyphae_testsupport::rows;
use hyphae_testsupport::selections::scenarios;
use hyphae_testsupport::served::Served;
use hyphae_view::citation::QUERY_URL;
use hyphae_view::knobs;

#[tokio::test]
async fn a_served_page_stays_under_its_ceiling() {
    // No page the viewer serves is large enough to stall a browser, at any corpus size.
    let served = Served::corpus();
    let (status, listing) = served.page("/sessions").await;
    assert_eq!(status, StatusCode::OK);
    assert!(listing.len() < PAGE_BYTES, "{}", listing.len());
    // The fixture corpus is smaller than a page, so its own weight proves nothing about a large
    // one. What does is the marginal cost of a row — the whole list less the same page holding
    // one session — which is what a growing corpus multiplies. The rows here are redacted down
    // to a few characters, so this is a smoke check: the worst case a real corpus can reach is
    // the arithmetic `budgets.py` holds.
    let db = served.db();
    let sessions = rows::one(&db, "SELECT count(*) AS held FROM sessions", &[])
        .i64("held")
        .expect("a count");
    let (status, chrome) = served.page("/sessions?size=1").await;
    assert_eq!(status, StatusCode::OK);
    let per_session = (listing.len() - chrome.len()) as f64 / (sessions - 1) as f64;
    let grown = chrome.len() as f64 + per_session * knobs::SESSIONS.ceiling as f64;
    assert!(grown < PAGE_BYTES as f64, "{grown}");
    // And every session's own node page, which is the widest of the eight the NavTree opens on:
    // the whole main thread is under the selection. A node page's three sizes are each their own
    // ceiling, so the defaults are also the largest response a URL can ask for.
    for row in rows::all(&db, "SELECT id FROM sessions", &[]) {
        let session_id = row.str("id").expect("an id");
        let (status, page) = served.page(&format!("/session/{session_id}")).await;
        assert_eq!(status, StatusCode::OK, "{session_id}");
        assert!(page.len() < PAGE_BYTES, "{session_id}: {}", page.len());
    }
}

/// The one route the sweep does not cover, and why it does not have to.
///
/// Python mounts the statics with `app.mount`, which makes them a `Mount` rather than an
/// `APIRoute`, so its own sweep drops them by type. Here a static file is answered by a handler
/// like any other, so the exclusion is named instead — a second route hiding behind it would
/// have to be added to this list, which is the whole point of naming it.
const NOT_A_PAGE: &[&str] = &["/static/{name}"];

#[test]
fn every_route_the_viewer_exposes_is_in_the_payload_sweep() {
    // The sweep covers the routes the app has, not the ones someone remembered to list. Without
    // this, a route shipped later is a page nothing weighs — and a route that selects a fat
    // column is exactly the kind of thing that arrives quietly.
    //
    // ADAPTED: axum's `Router` cannot be asked what it holds, so the app is folded out of a
    // declared list and `routes::paths` is that list — the one `build_app` is really built from.
    // The scenarios are Python's, so a wildcard segment arrives in Starlette's spelling and is
    // read back into it: `{*name}` and `{name:path}` are one parameter in two dialects.
    let mut exposed: Vec<String> = hyphae_view::routes::paths()
        .into_iter()
        .filter(|path| !NOT_A_PAGE.contains(&path.as_str()))
        .map(|path| match path.split_once("{*") {
            Some((before, rest)) => format!("{before}{{{}:path}}", rest.trim_end_matches('}')),
            None => path,
        })
        .collect();
    assert!(!exposed.is_empty(), "the app exposes no routes");
    exposed.sort();
    let mut swept: Vec<String> = scenarios().into_keys().collect();
    swept.sort();
    assert_eq!(exposed, swept);
}

#[tokio::test]
async fn no_route_serves_more_than_the_page_ceiling() {
    // Every route answers under the ceiling at the sizes its URL carries. Over the described
    // store, because six of the routes fetch what an enrichment pass wrote and a store no pass
    // has touched holds no such table — and because a described page is the dearer one either
    // way.
    let served = Served::enriched();
    for (route, url) in scenarios() {
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "{route}");
        assert!(page.len() < PAGE_BYTES, "{route}: {}", page.len());
    }
}

#[tokio::test]
async fn every_query_the_library_ships_serves_under_the_ceiling() {
    // A query page weighs its file marked up, and no library file is near the ceiling. The one
    // page whose size is a file's rather than a bound's: the SQL is served whole, because a
    // statement a reader cannot run is not a citation. Marking it up multiplies it about
    // fourfold, so what this pins is that no query in the library is long enough for that to
    // matter — and that a query added later is measured rather than assumed.
    let served = Served::corpus();
    for name in manifest::manifest().keys() {
        let (status, page) = served.page(&format!("{QUERY_URL}/{name}")).await;
        assert_eq!(status, StatusCode::OK, "{name}");
        assert!(page.len() < PAGE_BYTES, "{name}: {}", page.len());
    }
}

#[tokio::test]
async fn an_offload_of_nothing_but_escapes_still_serves_under_the_ceiling() {
    // The largest chunk anyone can ask for stays under the ceiling however the file escapes.
    // Every other bound here rests on a measured cost per row. An offload can't: it holds a file
    // a tool wrote, and a chunk of pure `&` weighs five times what the same chunk of prose does.
    // The content is invented for exactly that reason — no recorded offload is adversarial, and
    // the point of the leaf is the character no corpus happens to contain.
    let ceiling = knobs::CHUNK.ceiling;
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE offload_files SET content = ? WHERE session_id = ?",
                params!["&".repeat(ceiling as usize), CONFIG_ONLY],
            )
            .expect("the offload widens");
    });
    let (status, page) = served
        .page(&format!(
            "/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}?size={ceiling}"
        ))
        .await;
    assert_eq!(status, StatusCode::OK);
    // Served whole — the chunk is not silently cut — and still under the ceiling. Counted inside
    // the block rather than over the page, which also carries escaped `&` in its links.
    assert_eq!(
        Markup::of(&page).block("content").matches("&amp;").count(),
        ceiling as usize
    );
    assert!(page.len() < PAGE_BYTES, "{}", page.len());
}

/// Valid JSON of exactly `chars` characters, in the shape a record costs most to mark up.
///
/// A list of one-character strings: every element is its own token, so the highlighter writes a
/// span around three characters, and the character inside escapes to five bytes. Indented, each
/// element also lands on a line of its own. Invented for the same reason the offload's content
/// is — no recorded record is adversarial, and a record that parses is the only one the page
/// marks up at all.
fn escaping_json(chars: usize) -> String {
    let listed = format!("[{}]", vec![r#""&""#; (chars - 2) / 4].join(","));
    // The slack goes inside the last string, which keeps it valid JSON and one more token.
    format!(
        "{}{}{}",
        &listed[..listed.len() - 2],
        "&".repeat(chars - listed.len()),
        &listed[listed.len() - 2..]
    )
}

#[tokio::test]
async fn the_record_a_page_opens_unasked_serves_under_the_ceiling() {
    // The widest record a page fetches without a click stays under a page's ceiling. Every other
    // per-value fetch is exempt from the page bound: its unit is one value, and a reader who
    // clicks for a value has asked for whatever the store holds. This one is not, because nobody
    // clicked — the row the browser opens on arrival is a fetch the page starts — so
    // `knobs::OPENED_RECORD_CHARS` is what keeps it a page's worth.
    let raw = escaping_json(knobs::OPENED_RECORD_CHARS);
    assert_eq!(raw.len(), knobs::OPENED_RECORD_CHARS);
    let planted = raw.clone();
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE raw_records SET raw = ? \
                 WHERE session_id = ? AND source = ? AND line_no = ?",
                params![planted, RESUME, MAIN, RESUME_LONG_RECORD],
            )
            .expect("the record widens");
    });
    let (status, page) = served
        .page(&format!(
            "/session/{RESUME}/thread/{MAIN}/records?after={}",
            RESUME_LONG_RECORD - 1
        ))
        .await;
    assert_eq!(status, StatusCode::OK);
    // The page opens this one on arrival, so what it weighs is what the page's load costs...
    assert_eq!(
        Markup::of(&page).inside(
            "data-open-record",
            &RESUME_LONG_RECORD.to_string(),
            "hx-trigger"
        ),
        ["load"]
    );
    let (status, fetched) = served
        .page(&format!(
            "/fragment/record/session/{RESUME}/thread/{MAIN}/line/{RESUME_LONG_RECORD}"
        ))
        .await;
    // ...and it is the marked-up path being weighed, not a record served plain because it did
    // not parse — which is the whole reason a character is priced at a span and not an escape.
    assert_eq!(status, StatusCode::OK);
    assert!(Markup::of(&fetched).block("raw").contains("<span"));
    assert!(fetched.len() < PAGE_BYTES, "{}", fetched.len());
}
