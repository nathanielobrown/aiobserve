//! The popover behind a compaction's NavTree row: the window it dropped, and what asked for it.
//!
//! Ported from `tests/view/test_numbers__compaction.py`. A compaction is the one node of a session
//! made of no api calls at all, so there is no spend here and no model — what the boundary record
//! holds is the two token counts either side of the drop (`docs/schema.md`). The ⊟ row draws the
//! span between them as a bar read backwards, and this popover is that span in figures.
//!
//! The expectations come out of `live_compactions` in the test's own SQL, so the popover has nothing
//! to agree with but the store. Every other kind's popover is `numbers.rs`.

use hyphae_store::Param;
use hyphae_testsupport::html::{Markup, counted};
use hyphae_testsupport::landmarks::{COMPACTED, COMPACTED_BOUNDARY, MAIN};
use hyphae_testsupport::popovers::{popover, popped};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::nodes::{Kind, NUMBERS_URL};

/// Where the corpus's first recorded compaction is fetched from, and the key its row carries.
fn path() -> String {
    format!(
        "/session/{COMPACTED}/thread/{MAIN}/{}/{COMPACTED_BOUNDARY}",
        Kind::Compaction
    )
}

fn key() -> String {
    format!("{}:{COMPACTED_BOUNDARY}", Kind::Compaction)
}

#[tokio::test]
async fn a_compaction_says_the_window_it_gave_back_and_what_asked_for_it() {
    // Where the window stood either side of the drop, the span between, and the trigger.
    //
    // The three numbers are one subtraction, and the point of printing all three is that the bar on
    // the row draws only the span: a reader who wants to know whether a compaction was worth what it
    // cost needs the two ends it ran between. Read here off `live_compactions` rather than off
    // `view_compactions`, which is what the row itself was drawn from — the two would otherwise be
    // one derivation agreeing with itself.
    let served = Served::corpus();
    let held = rows::one(
        &served.db(),
        "SELECT pre_tokens, post_tokens, trigger FROM live_compactions \
         WHERE session_id = $session AND source = $source AND id = $compaction",
        &[
            ("session", Param::from(COMPACTED)),
            ("source", Param::from(MAIN)),
            ("compaction", Param::from(COMPACTED_BOUNDARY)),
        ],
    );
    let pre = held.i64("pre_tokens").expect("a count");
    let post = held.i64("post_tokens").expect("a count");
    // The fixture's boundary really did give window back, which is what makes the span a reading and
    // not a zero the assertions below would pass on either way.
    assert!(pre > post && post > 0, "{pre} then {post}");
    let printed = popover(&served, &path(), &key()).await;
    assert_eq!(printed["pre_tokens"], counted(pre));
    assert_eq!(printed["post_tokens"], counted(post));
    assert_eq!(printed["freed"], counted(pre - post));
    assert_eq!(printed["trigger"], held.str("trigger").expect("a trigger"));
    // And nothing a compaction has no answer for: it is made of no api calls, so a dollar or a
    // window here would be a figure attributed to a node that spent nothing.
    for absent in ["cost_usd", "window"] {
        assert!(!printed.contains_key(absent), "{absent} in {printed:?}");
    }
}

#[tokio::test]
async fn a_compactions_popover_cites_the_query_it_was_fetched_by() {
    // The fragment carries its own citation line, keys and all.
    //
    // A popover arrives on a page already served, so it cannot ride the footer the pages share.
    // Pinned here rather than in the sweeps: `query.rs` reads pages and skips every `/fragment/`
    // route, and `app.rs`'s fragment sweep covers the whole-value fetches. A numbers fragment is
    // cited nowhere else.
    let served = Served::corpus();
    let html = popped(&served, &path()).await;
    assert_eq!(
        Markup::of(&html).values("data-query"),
        [format!(
            "-- queries/view_numbers_compaction.sql session_id={COMPACTED} source={MAIN} \
             compaction_id={COMPACTED_BOUNDARY} chip_chars=60"
        )]
    );
}

#[tokio::test]
async fn a_compaction_row_fetches_the_route_the_app_actually_mounts() {
    // The ⊟ row's URL resolves to the compaction handler and not the generic one.
    //
    // A compaction's path is shaped like every other node's — `.../thread/{source}/{kind}/{id}` — so
    // the route serving turns, calls and tool calls matches it too, and that one 404s on a kind it
    // has no query for. Which of the two answers is decided by the order the router registers them,
    // and nothing but this reads that order back.
    //
    // ADAPTED: the Python asks FastAPI which mounted route matches the URL and reads its handler's
    // name. `axum::Router` exposes no such table, so which handler answered is read off what only
    // that handler produces — its own citation line. The URL is still the one the row minted rather
    // than one written out here, so a route that moved is a failure rather than a test checking a
    // string against itself.
    let served = Served::corpus();
    let (_, html) = served.page(&format!("/session/{COMPACTED}")).await;
    let fetched: Vec<(String, String)> = Markup::of(&html)
        .wired("data-nav-tree")
        .into_iter()
        .filter(|(_, at)| at["hx-get"].starts_with(NUMBERS_URL))
        .map(|(row, at)| (row, at["hx-get"].clone()))
        .collect();
    let url = &fetched
        .iter()
        .find(|(row, _)| *row == key())
        .expect("the compaction's row mints no popover URL")
        .1;
    let (status, fragment) = served.page(url).await;
    assert!(status.is_success(), "{url}: {status}");
    let cited = Markup::of(&fragment).values("data-query");
    assert_eq!(cited.len(), 1, "{cited:?}");
    assert!(
        cited[0].contains("view_numbers_compaction.sql"),
        "{url} was answered by {}",
        cited[0]
    );
}
