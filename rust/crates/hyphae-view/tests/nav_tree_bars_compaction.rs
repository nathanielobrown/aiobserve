//! The ⊟ row's context bar: the window a boundary gave back, drawn between the two fills it kept.
//!
//! The one bar drawn from a row's own columns rather than from the calls under it, and the one
//! mark the bar mints. What every other row draws is in `nav_tree_bars.rs`.

use duckdb::params;
use hyphae_store::Store;
use hyphae_testsupport::html::{Bar, Markup, bands};
use hyphae_testsupport::landmarks::{COMPACTED, COMPACTED_RUN, SPINE, SPINE_RUN};
use hyphae_testsupport::nav_trees::{self, Levels};
use hyphae_testsupport::served::Served;
use hyphae_view::nodes::Kind;

/// One compaction, with the model whose window its bar is drawn against.
struct Boundary {
    compaction_id: String,
    source: String,
    /// The fill it was compacted at, and the fill it was left on.
    pre: i64,
    post: i64,
    model: String,
}

/// Every compaction of one session, in the order they happened.
///
/// The window comes off the thread and not the session: the nearest call of the same source at or
/// before the boundary, else the first after it. Restated in the test's own SQL, so the query the
/// page reads has something to disagree with.
fn compactions(levels: &Levels, session_id: &str) -> Vec<Boundary> {
    levels
        .store()
        .fetch(
            "SELECT k.id, k.source, k.pre_tokens, k.post_tokens, \
               (SELECT coalesce( \
                   max_by(c.model, c.started_at) FILTER (c.started_at <= k.timestamp), \
                   min_by(c.model, c.started_at) FILTER (c.started_at > k.timestamp)) \
                FROM live_api_calls c WHERE c.session_id = k.session_id AND c.source = k.source \
                  AND NOT c.synthetic) AS model \
             FROM live_compactions k WHERE k.session_id = $session_id ORDER BY k.timestamp",
            &[("session_id", session_id.into())],
        )
        .expect("the store answers")
        .iter()
        .map(|row| Boundary {
            compaction_id: row.str("id").expect("a boundary").to_owned(),
            source: row.str("source").expect("a thread").to_owned(),
            pre: row.i64("pre_tokens").expect("a token count"),
            post: row.i64("post_tokens").expect("a token count"),
            model: row
                .opt_str("model")
                .expect("a model or none")
                .unwrap_or_default()
                .to_owned(),
        })
        .collect()
}

#[tokio::test]
async fn a_compaction_bars_what_it_freed_between_the_two_fills_it_records() {
    // The ⊟ row's bar: dim up to where the thread was left, and green up to where it stood.
    //
    // The one bar drawn from a row's own columns rather than from the calls under it — a
    // compaction records the fill either side of itself and no model, so the window it is drawn
    // against is the thread's nearest answered call. Both of the session's main-thread boundaries
    // and the one its run hit, each read on the page that opens its level.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let recorded = compactions(&levels, COMPACTED);
    assert_eq!(recorded.len(), 3);
    let (_, session_page) = served.page(&format!("/session/{COMPACTED}")).await;
    for boundary in &recorded {
        // A boundary inside a turn is that turn's child, so it is read on the turn's page; the
        // main thread of this session recorded no prompt at all, and its two sit at the level.
        let holding = levels
            .store()
            .fetch(
                "SELECT t.id FROM live_turns t, live_compactions k WHERE k.id = $compaction_id \
                   AND t.session_id = k.session_id AND t.source = k.source \
                   AND k.timestamp >= t.started_at AND k.timestamp < t.ended_at",
                &[("compaction_id", boundary.compaction_id.as_str().into())],
            )
            .expect("the store answers");
        let opened = match holding.first() {
            None => session_page.clone(),
            Some(row) => {
                let turn_id = row.str("id").expect("a turn");
                served
                    .page(&nav_trees::node_url(
                        Kind::Turn,
                        COMPACTED,
                        &boundary.source,
                        turn_id,
                    ))
                    .await
                    .1
            }
        };
        let page = Markup::of(&opened);
        let key = format!("compaction:{}", boundary.compaction_id);
        // The fill it was compacted at, and the fill it was left on: what stands between them is
        // the context the boundary gave back, which is the band the row draws green.
        assert_eq!(
            page.bar(&key),
            bands(boundary.pre, boundary.post, None, &boundary.model),
            "{key}"
        );
        assert!(
            !page.field("data-nav-tree", &key, "title").is_empty(),
            "{key}"
        );
    }
    // The run's boundary is the one drawn against a window its session's main thread does not
    // name: it answered on a model of its own (`tests/fixtures/compaction/README.md`).
    let mut models: Vec<&str> = recorded.iter().map(|found| found.model.as_str()).collect();
    models.sort_unstable();
    models.dedup();
    assert_eq!(models, ["claude-fable-5", "claude-opus-4-8"]);
}

#[tokio::test]
async fn a_compaction_whose_thread_names_no_window_draws_no_bar() {
    // A boundary on a thread that answered nothing is a row with its facts and no bar.
    //
    // `compactions` records two fills and no model, so the scale comes from the thread's calls —
    // and a thread that made none, or made them on a model our table holds no window for, gives
    // the bar no denominator. Drawn at nothing it would read as a window that emptied, so it is
    // not drawn. Planted, because every recorded thread that compacted also answered: the
    // session's calls are dropped and its boundaries kept.
    let unpriced = Served::planted(|store: &Store| {
        store
            .connection()
            .execute(
                "DELETE FROM api_calls WHERE session_id = ?",
                params![COMPACTED],
            )
            .expect("the session's calls are dropped");
    });
    let (_, html) = unpriced.page(&format!("/session/{COMPACTED}")).await;
    let page = Markup::of(&html);
    let keys: Vec<String> = page
        .values("data-nav-tree")
        .into_iter()
        .filter(|key| key.starts_with("compaction:"))
        .collect();
    assert!(!keys.is_empty(), "the boundaries still stand on the page");
    for key in keys {
        assert_eq!(
            page.bar(&key),
            Bar {
                fill: None,
                prior: None,
                base: None
            },
            "{key}"
        );
        // The row is still a row: what a compaction is, and what triggered it, are its own.
        assert!(
            !page.field("data-nav-tree", &key, "title").is_empty(),
            "{key}"
        );
    }
}

#[tokio::test]
async fn a_run_whose_own_thread_compacted_is_drawn_full_in_the_alarm() {
    // The one warning the NavTree draws: a subagent that ran its own window out.
    //
    // A run's thread compacting is a fact about the run and not about the call that spawned it, so
    // the row says so at full width whatever its last call left behind — the reader looking for
    // why a run's answer thinned out has one place to see it.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let (_, html) = served
        .page(&format!("/session/{COMPACTED}/run/{COMPACTED_RUN}"))
        .await;
    assert!(Markup::of(&html).marked(&format!("run:{COMPACTED_RUN}"), "maxed"));
    // The store agrees: the mark is drawn off the run's own thread having a boundary on it.
    assert_eq!(boundaries(&levels, COMPACTED, COMPACTED_RUN), 1);
    // And a run whose thread held out carries no mark. Read on its own page, the way the run above
    // is: the two rows are the same kind, drawn by the same builder.
    let (_, html) = served
        .page(&format!("/session/{SPINE}/run/{SPINE_RUN}"))
        .await;
    assert!(!Markup::of(&html).marked(&format!("run:{SPINE_RUN}"), "maxed"));
    assert_eq!(boundaries(&levels, SPINE, SPINE_RUN), 0);
}

/// How many boundaries one thread of one session recorded.
fn boundaries(levels: &Levels, session_id: &str, source: &str) -> i64 {
    levels
        .store()
        .fetch(
            "SELECT count(*) AS at FROM live_compactions \
             WHERE session_id = $session_id AND source = $source",
            &[("session_id", session_id.into()), ("source", source.into())],
        )
        .expect("the store answers")
        .first()
        .expect("a count comes back")
        .i64("at")
        .expect("a count")
}
