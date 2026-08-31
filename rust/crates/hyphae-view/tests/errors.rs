//! Getting to a failure: the mark on a NavTree row, the session's list, and the stepper.
//!
//! A session can fail a tool call five spawns down a run tree, and neither the NavTree — which
//! opens one path — nor the walk gets a reader there without reading everything in front of it.
//! These leaves cover the three surfaces that do: the `error` mark a NavTree row carries, the
//! session-wide list at `/session/{session_id}/errors`, and the prev/next pair a pane offers when
//! the node it is reading is itself a failure.
//!
//! The fixture corpus records two failed tool calls, one apiece in two different sessions, which
//! is enough for the mark and for the list but not for an order or a step. A session that failed
//! several is planted onto recorded rows: `is_error` is a flag the store already holds — both
//! recorded failures prove the shape — so flipping it on a real tool call is what a busier session
//! looks like, not an invented one.

use std::collections::BTreeSet;
use std::path::Path;

use axum::http::StatusCode;
use duckdb::params;

use hyphae_store::{Param, Store, queries};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{DENSE_TOOL, FORK_ORIGIN, FORK_ORIGIN_RUN, MISSING, SPINE};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::{self, Served};
use hyphae_view::knobs;

/// Every tool call of the one session whose threads both hold one, failed.
///
/// The list, its order and the stepper are all claims about a session with several failures on
/// more than one thread, and no recorded session has that: `FORK_ORIGIN` records one failure of
/// seven calls.
fn all_failed(store: &Store) {
    store
        .connection()
        .execute(
            "UPDATE tool_calls SET is_error = true WHERE session_id = ?",
            params![FORK_ORIGIN],
        )
        .expect("the failures land");
}

/// One session's failed tool calls in the order the list shows them, thread beside id.
///
/// The expectation's own spelling of `view_session_errors`'s order: the clock, then the thread,
/// its index and its id — the last two of which are unique, so the order is total and a page that
/// cut the tail of it cut the same rows twice running.
fn failed(db: &Path, session_id: &str) -> Vec<(String, String)> {
    rows::all(
        db,
        "SELECT source, id FROM live_tool_calls WHERE session_id = $session AND is_error \
         ORDER BY started_at, source, \"index\", id",
        &[("session", Param::from(session_id))],
    )
    .iter()
    .map(|row| {
        (
            row.str("source").expect("a thread").to_owned(),
            row.str("id").expect("a tool call id").to_owned(),
        )
    })
    .collect()
}

#[tokio::test]
async fn a_nav_tree_row_for_a_tool_call_that_failed_says_so() {
    // The NavTree marks the tool calls that came back an error, and marks nothing else.
    let served = Served::corpus();
    // If a session recorded a failed tool call, on a thread of its own...
    let (source, tool_id) = failed(&served.db(), FORK_ORIGIN)[0].clone();
    // ...then the NavTree beside that call carries the mark on its row...
    let (_, page) = served
        .page(&format!(
            "/session/{FORK_ORIGIN}/thread/{source}/tool/{tool_id}"
        ))
        .await;
    let markup = Markup::of(&page);
    let key = format!("tool:{tool_id}");
    assert_eq!(markup.fields("data-nav-tree", &key)["is_error"], "error");
    // ...and on no other row of the session, whatever kind of node it stands for.
    let marked: BTreeSet<String> = markup
        .rows()
        .into_iter()
        .map(|(_, row)| row)
        .filter(|row| markup.fields("data-nav-tree", row).contains_key("is_error"))
        .collect();
    assert_eq!(marked, BTreeSet::from([key]));
}

#[tokio::test]
async fn the_errors_page_lists_every_failure_of_the_session_in_the_order_they_happened() {
    // The list spans the whole session: what a subagent failed at is what the session failed at.
    //
    // Planted over `FORK_ORIGIN`, whose seven tool calls are split across two run threads — the
    // shape the list exists for, and the one the NavTree cannot show in a single open path.
    let served = Served::planted(all_failed);
    let (status, page) = served.page(&format!("/session/{FORK_ORIGIN}/errors")).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // Every failure the session holds, in the clock order the query states...
    let order = failed(&served.db(), FORK_ORIGIN);
    assert_eq!(
        markup.values("data-error"),
        order
            .iter()
            .map(|(_, tool_id)| format!("tool:{tool_id}"))
            .collect::<Vec<_>>()
    );
    // ...more than one thread among them, which is what makes the list session-wide...
    let threads: BTreeSet<&String> = order.iter().map(|(source, _)| source).collect();
    assert!(threads.len() > 1);
    // ...each row leading to the tool call's own page, on the thread it ran on...
    for (source, tool_id) in &order {
        assert_eq!(
            markup.inside("data-error", &format!("tool:{tool_id}"), "href"),
            vec![format!(
                "/session/{FORK_ORIGIN}/thread/{source}/tool/{tool_id}"
            )]
        );
    }
    // ...and each row saying what the call was and when it ran, so two calls of one tool are told
    // apart without opening either.
    let row = markup.fields("data-error", &format!("tool:{}", order[0].1));
    assert!(!row["title"].is_empty());
    assert!(!row["started_at"].is_empty());
}

#[tokio::test]
async fn a_session_with_no_failure_to_jump_to_has_no_errors_page() {
    // A session that never failed a call and one the store never held are different misses. Swept
    // over the corpus rather than over two named sessions: both answers are a fact about the
    // session, and which sessions hold which is what a re-recorded fixture moves.
    let served = Served::corpus();
    let mut listed = 0;
    let mut clean = 0;
    for id in served::session_ids(&served.db()) {
        let (status, page) = served.page(&format!("/session/{id}/errors")).await;
        match status {
            StatusCode::OK => {
                assert!(page.contains(r#"id="errors""#), "/session/{id}/errors");
                listed += 1;
            }
            StatusCode::NOT_FOUND => {
                assert_eq!(
                    Markup::of(&page).fields("id", "error")["message"],
                    "This session's tool calls all succeeded.",
                    "/session/{id}/errors"
                );
                clean += 1;
            }
            other => panic!("GET /session/{id}/errors answered {other}"),
        }
    }
    assert!(listed > 0, "the corpus has a session that failed a call");
    assert!(clean > 0, "the corpus has a session that failed none");
    // A session id the store never held is the other nothing, worded for the reader who typed it:
    // one is a store that does not hold the session, the other a session that holds no failure.
    let (status, page) = served.page(&format!("/session/{MISSING}/errors")).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(
        Markup::of(&page).fields("id", "error")["message"],
        "No session with that id is in this store."
    );
}

#[tokio::test]
async fn the_stepper_steps_between_failures_and_only_where_the_pane_stands_on_one() {
    // A pane reading a failed tool call offers the failure before it and the one after.
    //
    // The step is between failures rather than between nodes, so it crosses threads the way the
    // list does — and it costs a query, which is why a pane reading anything else does not offer
    // one.
    let served = Served::planted(all_failed);
    let order = failed(&served.db(), FORK_ORIGIN);
    let mut walked = Vec::new();
    for (source, tool_id) in &order {
        let (_, page) = served
            .page(&format!(
                "/session/{FORK_ORIGIN}/thread/{source}/tool/{tool_id}"
            ))
            .await;
        walked.push(page);
    }
    for (place, page) in walked.iter().enumerate() {
        let markup = Markup::of(page);
        // Every failure offers the way to the whole list...
        let offered: BTreeSet<String> = markup.values("data-step").into_iter().collect();
        // ...and a step in each direction there is a failure in: the first has nothing before it,
        // the last nothing after.
        let mut expected = BTreeSet::from(["all".to_owned()]);
        if place > 0 {
            expected.insert("previous".to_owned());
            assert_eq!(
                markup.inside("data-step", "previous", "data-node"),
                vec![format!("tool:{}", order[place - 1].1)],
                "{place}"
            );
        }
        if place + 1 < order.len() {
            expected.insert("next".to_owned());
            assert_eq!(
                markup.inside("data-step", "next", "data-node"),
                vec![format!("tool:{}", order[place + 1].1)],
                "{place}"
            );
        }
        assert_eq!(offered, expected, "{place}");
    }
    // A step lands on the neighbour's own page, thread and all — the list is not one thread's.
    let second = Markup::of(&walked[0]).inside("data-step", "next", "href")[0].clone();
    assert!(
        second.starts_with(&format!(
            "/session/{FORK_ORIGIN}/thread/{}/tool/{}",
            order[1].0, order[1].1
        )),
        "{second}"
    );
    // And a pane reading a tool call that succeeded offers only the way to the list, because there
    // is no step between failures to take from a node that is not one.
    let corpus = Served::corpus();
    let (_, succeeded) = corpus
        .page(&format!(
            "/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
        ))
        .await;
    assert_eq!(Markup::of(&succeeded).values("data-step"), vec!["all"]);
}

#[tokio::test]
async fn every_node_page_of_a_failing_session_offers_the_way_to_its_failures() {
    // The count beside the link is the whole session's, whatever node the pane is reading.
    let served = Served::corpus();
    let failures = rows::one(
        &served.db(),
        "SELECT count(*) AS failed FROM live_tool_calls \
         WHERE session_id = $session AND is_error",
        &[("session", Param::from(FORK_ORIGIN))],
    )
    .i64("failed")
    .expect("a count");
    let (source, tool_id) = failed(&served.db(), FORK_ORIGIN)[0].clone();
    // Wherever the reader is standing in a session that failed a call — its own node, a run of it,
    // the failure itself — the link says how many the session failed...
    for url in [
        format!("/session/{FORK_ORIGIN}"),
        format!("/session/{FORK_ORIGIN}/run/{FORK_ORIGIN_RUN}"),
        format!("/session/{FORK_ORIGIN}/thread/{source}/tool/{tool_id}"),
    ] {
        let (_, page) = served.page(&url).await;
        let markup = Markup::of(&page);
        assert_eq!(
            markup.fields("data-step", "all")["tool_errors"],
            failures.to_string(),
            "{url}"
        );
        assert_eq!(
            markup.inside("data-step", "all", "href"),
            vec![format!("/session/{FORK_ORIGIN}/errors")],
            "{url}"
        );
    }
    // ...and a session that failed none offers nothing at all, rather than a link to a 404.
    let (_, clean) = served.page(&format!("/session/{SPINE}")).await;
    assert_eq!(Markup::of(&clean).values("data-step"), Vec::<String>::new());
}

#[tokio::test]
async fn the_errors_page_and_the_stepper_cite_the_one_query_behind_them() {
    // Both surfaces cite the same line, and a page that reads neither cites nothing.
    //
    // The stepper is a conditional read — only a pane standing on a failure runs it — so the
    // citation is what says a page paid for it, and its absence is what says a page did not.
    let served = Served::corpus();
    let line = format!(
        "-- queries/view_session_errors.sql session_id={FORK_ORIGIN} nav_chars={} errors={}",
        queries::NAV_CHARS,
        knobs::ERRORS.default
    );
    // The list itself is that one query and nothing else...
    let (_, list) = served.page(&format!("/session/{FORK_ORIGIN}/errors")).await;
    let cited = Markup::of(&list).fields("id", "citation");
    assert_eq!(cited.len(), 1);
    assert_eq!(cited["view_session_errors"], line);
    // ...a node page standing on a failure cites it beside the reads every node page makes...
    let (source, tool_id) = failed(&served.db(), FORK_ORIGIN)[0].clone();
    let (_, standing) = served
        .page(&format!(
            "/session/{FORK_ORIGIN}/thread/{source}/tool/{tool_id}"
        ))
        .await;
    assert_eq!(
        Markup::of(&standing).fields("id", "citation")["view_session_errors"],
        line
    );
    // ...and a node page of the same session standing on a call that succeeded does not, which is
    // the whole reason the read is conditional.
    let (_, beside) = served
        .page(&format!(
            "/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
        ))
        .await;
    assert!(
        !Markup::of(&beside)
            .fields("id", "citation")
            .contains_key("view_session_errors")
    );
}
