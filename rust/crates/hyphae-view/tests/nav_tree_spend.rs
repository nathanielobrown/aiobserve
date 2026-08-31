//! What the store holds behind every priced NavTree row, read back off the row that drew it.
//!
//! The other badge leaves (`nav_tree_badges.rs`) hold the wash to its ladder and the pair to its
//! subtree; this one holds every number a row draws to the store's own. Split off along the seam
//! the oracle below already draws, so neither file runs past the length budget.

use std::collections::{BTreeMap, BTreeSet};

use duckdb::params;
use hyphae_store::Store;
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::nav_trees::{self, Levels};
use hyphae_testsupport::served::{self, Served};
use hyphae_view::nodes::Kind;

#[tokio::test]
async fn every_priced_row_carries_the_spend_the_store_holds_under_it() {
    // The cost on a row, the bar beside it and the mark above it, read against the store.
    //
    // The buckets add up for themselves; every other priced row is handed the store's own number,
    // and this is where that number is read back. Read on the page of each priced node, where its
    // own row is on the open path whichever preset the reader picked.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    // Keyed by session as well as by row, for the reason the titles are.
    let mut said: BTreeMap<(String, String), (f64, i64)> = BTreeMap::new();
    for session_id in served::session_ids(&served.db()) {
        for (key, value) in spend(&levels, &session_id) {
            said.insert((session_id.clone(), key), value);
        }
    }
    let mut read: BTreeSet<(String, String)> = BTreeSet::new();
    for kind in [Kind::Session, Kind::Turn, Kind::Call, Kind::Run] {
        for (session_id, source, node_id) in levels.candidates(kind) {
            // A page holds more than the node it opens, so the ones already read are skipped.
            if read.contains(&(session_id.clone(), format!("{kind}:{node_id}"))) {
                continue;
            }
            let (_, html) = served
                .page(&nav_trees::node_url(kind, &session_id, &source, &node_id))
                .await;
            let page = Markup::of(&html);
            for key in page.values("data-nav-tree") {
                let at = (session_id.clone(), key.clone());
                if let Some((cost, unpriced)) = said.get(&at) {
                    nav_trees::weighed(&page, &key, &levels, &session_id, *cost, *unpriced);
                    read.insert(at);
                }
            }
        }
    }
    // Every priced row of the store was reached, so no kind is priced by a sample of itself.
    assert_eq!(read, said.keys().cloned().collect());
    // Our price table prices every call the corpus recorded, so the mark that says otherwise is
    // planted on one call: it has to reach the call's own row, the turn above it, and the session
    // at the root, each of which counts what went unpriced for itself.
    let rows = levels
        .store()
        .fetch(
            "SELECT c.session_id, c.source, c.id AS call_id, t.id AS turn_id \
             FROM live_api_calls c \
             JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
              AND t.id = c.turn_id \
             WHERE c.cost_usd IS NOT NULL ORDER BY c.session_id, c.source, c.id LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let priced = rows.first().expect("the corpus priced a call under a turn");
    let session_id = priced.str("session_id").expect("a session").to_owned();
    let source = priced.str("source").expect("a thread").to_owned();
    let call_id = priced.str("call_id").expect("a call").to_owned();
    let turn_id = priced.str("turn_id").expect("its turn").to_owned();
    let blanked = call_id.clone();
    let marked = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE api_calls SET cost_usd = NULL WHERE id = ?",
                params![blanked],
            )
            .expect("the call loses its price");
    });
    let planted = Levels::of(&marked.db());
    let said = spend(&planted, &session_id);
    assert_eq!(
        said[&format!("call:{call_id}")],
        (0.0, 1),
        "the plant left the call unpriced"
    );
    let (_, page) = marked
        .page(&nav_trees::node_url(
            Kind::Call,
            &session_id,
            &source,
            &call_id,
        ))
        .await;
    let page = Markup::of(&page);
    for key in [
        format!("call:{call_id}"),
        format!("turn:{turn_id}"),
        format!("session:{session_id}"),
    ] {
        let (cost, unpriced) = said[&key];
        nav_trees::weighed(&page, &key, &planted, &session_id, cost, unpriced);
    }
}

/// What the store holds on the own thread of each priced row: its cost and its unpriced calls.
///
/// The first half of a badge, everywhere one is drawn. A turn is worth the calls that answered it
/// on its own thread; a call is worth itself, and a call our price table could not price is worth
/// nothing rather than being free. A session is worth its main thread — what it spent less every
/// run under it — because the whole of what it spent is the badge's other half.
fn spend(levels: &Levels, session_id: &str) -> BTreeMap<String, (f64, i64)> {
    let bound = [("session_id", session_id.into())];
    let store = levels.store();
    let mut said = BTreeMap::new();
    for row in store
        .fetch(
            "SELECT round(s.cost_usd - (SELECT coalesce(sum(round(ran.cost, 4)), 0) FROM \
               (SELECT sum(c.cost_usd) AS cost FROM live_api_calls c \
                  JOIN live_agent_runs a ON a.session_id = c.session_id AND a.id = c.source \
                 WHERE c.session_id = s.session_id GROUP BY c.source) ran), 4) AS spent, \
              s.unpriced_api_calls AS unpriced FROM session_rollups s \
             WHERE s.session_id = $session_id",
            &bound,
        )
        .expect("the store answers")
    {
        said.insert(
            format!("session:{session_id}"),
            (
                zeroed(row.opt_f64("spent").expect("a total or none")),
                row.i64("unpriced").expect("a count"),
            ),
        );
    }
    for row in store
        .fetch(
            "SELECT t.id, coalesce(round(sum(c.cost_usd), 4), 0) AS spent, \
              count(c.id) FILTER (c.cost_usd IS NULL) AS unpriced \
             FROM live_turns t LEFT JOIN live_api_calls c \
              ON c.session_id = t.session_id AND c.source = t.source AND c.turn_id = t.id \
             WHERE t.session_id = $session_id GROUP BY t.id",
            &bound,
        )
        .expect("the store answers")
    {
        said.insert(
            format!("turn:{}", row.str("id").expect("a turn")),
            (
                row.f64("spent").expect("a total"),
                row.i64("unpriced").expect("a count"),
            ),
        );
    }
    for row in store
        .fetch(
            "SELECT id, round(cost_usd, 4) AS spent FROM live_api_calls \
             WHERE session_id = $session_id",
            &bound,
        )
        .expect("the store answers")
    {
        let spent = row.opt_f64("spent").expect("a cost or none");
        said.insert(
            format!("call:{}", row.str("id").expect("a call")),
            (zeroed(spent), i64::from(spent.is_none())),
        );
    }
    for row in store
        .fetch(
            "SELECT id FROM live_agent_runs WHERE session_id = $session_id",
            &bound,
        )
        .expect("the store answers")
    {
        // A run is worth its own thread — the same sum an unattached bucket gathers per run.
        let run_id = row.str("id").expect("a run");
        said.insert(
            format!("run:{run_id}"),
            levels.thread_spend(session_id, run_id),
        );
    }
    said
}

/// A cost with no money in it: no price at all, or a subtraction that left a signed zero.
///
/// A session whose main thread spent nothing comes back as `-0.0`, and a negative zero printed
/// to two places is a dollar sign, a minus, and no money.
fn zeroed(spent: Option<f64>) -> f64 {
    spent.filter(|amount| *amount != 0.0).unwrap_or(0.0)
}
