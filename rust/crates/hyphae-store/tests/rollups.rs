//! The rollup views: one row per session, and which of two questions it answers.
//!
//! The port of the view half of `tests/export/test_duckdb.py`. Claude Code copies a whole
//! transcript forward on a fork or a resume, so the same api call is recorded under two
//! sessions. `session_rollups` counts what a session's own files hold; `corpus_rollups`
//! credits each copy to the session that ran it first. Every number here was measured by
//! running this file, not carried over from Python.
//!
//! What goes into the base tables is `exported.rs`.

use hyphae_store::{Param, Store};
use hyphae_testsupport::corpus;
use hyphae_testsupport::landmarks::{ANCESTOR, DUP_UUID, FORK_ORIGIN, MYCELIA, RESUME, SPINE};
use tempfile::TempDir;

/// A store of its own, and the tempdir that outlives it.
fn store() -> (TempDir, Store) {
    let scratch = TempDir::new().expect("a tempdir");
    let store = Store::create(&scratch.path().join("traces.duckdb")).expect("a fresh store");
    (scratch, store)
}

/// Two costs agree to the cent and beyond. DuckDB sums floats, so this is not equality.
fn same_cost(held: f64, wanted: f64) {
    assert!((held - wanted).abs() < 1e-6, "cost {held} is not {wanted}");
}

/// A session's totals count a fork's copied history under whoever ran it, and once.
///
/// Three readings of this fixture give three different totals, so the number is the whole
/// argument: 7,196 output tokens if copies are counted wherever they appear, 4,904 if both
/// copies are dropped, and 6,050 — the auditor's 1,146 plus the fork's own 4,904 — when each
/// record counts under the transcript that ran it first.
#[test]
fn a_rollup_counts_replayed_work_once() {
    let (_scratch, store) = store();

    // If a session ran an auditor and a fork that replayed it...
    store
        .export(&corpus::trace("fork_origin", FORK_ORIGIN), "fingerprint-1")
        .expect("the fork's origin exports");

    // ...then the rollup counts the copied message once and the fork's own work beside it...
    let rollup = store
        .fetch(
            "SELECT api_calls, output_tokens FROM session_rollups WHERE session_id = $session",
            &[("session", FORK_ORIGIN.into())],
        )
        .expect("the rollup reads");
    assert_eq!(rollup[0].i64("api_calls").expect("a count"), 3);
    assert_eq!(rollup[0].i64("output_tokens").expect("a sum"), 6050);

    // ...while the base table still holds the copy, flagged, so the archive keeps what the
    // fork's file recorded.
    let replayed = store
        .fetch(
            "SELECT count(*) AS n, sum(output_tokens) AS tokens FROM api_calls WHERE replayed",
            &[],
        )
        .expect("the base table reads");
    assert_eq!(replayed[0].i64("n").expect("a count"), 1);
    assert_eq!(replayed[0].i64("tokens").expect("a sum"), 1146);
}

/// One rollup row's counts. The cost rides beside it rather than in it: DuckDB sums floats,
/// so it is compared to a tolerance while the counts are compared exactly.
#[derive(Debug, PartialEq, Eq)]
struct Totals {
    session_id: String,
    project_dir: String,
    turns: i64,
    api_calls: i64,
    tool_calls: i64,
    compactions: i64,
    unpriced_api_calls: i64,
}

/// What a rollup view says about every session it holds, oldest first, with each row's cost.
fn rollup(store: &Store, view: &str) -> Vec<(Totals, f64)> {
    store
        .fetch(
            &format!(
                "SELECT session_id, project_dir, turns, api_calls, tool_calls, compactions, \
                 cost_usd, unpriced_api_calls FROM {view} ORDER BY started_at"
            ),
            &[],
        )
        .expect("the rollup reads")
        .iter()
        .map(|row| {
            (
                Totals {
                    session_id: row.str("session_id").expect("an id").to_owned(),
                    project_dir: row.str("project_dir").expect("a path").to_owned(),
                    turns: row.i64("turns").expect("a count"),
                    api_calls: row.i64("api_calls").expect("a count"),
                    tool_calls: row.i64("tool_calls").expect("a count"),
                    compactions: row.i64("compactions").expect("a count"),
                    unpriced_api_calls: row.i64("unpriced_api_calls").expect("a count"),
                },
                row.f64("cost_usd").expect("a total"),
            )
        })
        .collect()
}

/// The counts one session is expected to report, under the project every fixture was
/// recorded in and with nothing left unpriced.
fn totals(
    session_id: &str,
    turns: i64,
    api_calls: i64,
    tool_calls: i64,
    compactions: i64,
) -> Totals {
    Totals {
        session_id: session_id.to_owned(),
        project_dir: MYCELIA.to_owned(),
        turns,
        api_calls,
        tool_calls,
        compactions,
        unpriced_api_calls: 0,
    }
}

/// Work a resume copied from the session it continued counts under the original only.
///
/// `/resume` writes the whole prior transcript into the new session's file, so the base
/// tables hold both copies and the two rollups answer different questions: what this
/// session's files say, and what this session added to the corpus.
#[test]
fn a_corpus_rollup_counts_a_resumed_session_once() {
    let (_scratch, store) = store();

    // If a session and the resume that continued it are both exported...
    store
        .export(&corpus::trace("resume_pair", ANCESTOR), "fingerprint-1")
        .expect("the ancestor exports");
    store
        .export(&corpus::trace("resume_pair", RESUME), "fingerprint-2")
        .expect("the resume exports");

    // ...then each session's own rollup reports what its file holds, copies included — four
    // calls under the original, and five under the resume that copied them...
    let held = rollup(&store, "session_rollups");
    assert_eq!(
        held.iter().map(|row| &row.0).collect::<Vec<_>>(),
        [&totals(ANCESTOR, 1, 4, 5, 1), &totals(RESUME, 0, 5, 5, 1)]
    );
    same_cost(held[0].1, 1.47611);
    same_cost(held[1].1, 2.386974);

    // ...while the corpus rollup credits every copied call, tool call and compaction to the
    // session that ran it first, leaving the resume its own single new call.
    let corpus_wide = rollup(&store, "corpus_rollups");
    assert_eq!(
        corpus_wide.iter().map(|row| &row.0).collect::<Vec<_>>(),
        [&totals(ANCESTOR, 1, 4, 5, 1), &totals(RESUME, 0, 1, 0, 0)]
    );
    same_cost(corpus_wide[0].1, 1.47611);
    same_cost(corpus_wide[1].1, 1.150518);
}

/// One store holds every project, and a rollup filters down to the one you asked about.
#[test]
fn a_rollup_can_be_scoped_to_one_project() {
    let (_scratch, store) = store();
    let here = corpus::trace("spine", SPINE);
    // The same session under another checkout — invented, because the fixtures are all
    // mycelia sessions and the column, not the path, is what the test is about.
    let mut elsewhere = corpus::trace("dup_uuid", DUP_UUID);
    elsewhere.session.project_dir = Some("/repos/other".to_owned());

    // If two projects' sessions share the store...
    store.export(&here, "fingerprint-1").expect("spine exports");
    store
        .export(&elsewhere, "fingerprint-2")
        .expect("the re-homed session exports");

    // ...then a rollup filtered by project reports that project's sessions and no others.
    let scoped = |project: &str| -> Vec<String> {
        store
            .fetch(
                "SELECT session_id FROM corpus_rollups WHERE project_dir = $project",
                &[("project", Param::Text(project.to_owned()))],
            )
            .expect("the rollup reads")
            .iter()
            .map(|row| row.str("session_id").expect("an id").to_owned())
            .collect()
    };
    assert_eq!(scoped("/repos/other"), [DUP_UUID]);
    assert_eq!(scoped(MYCELIA), [SPINE]);
}

/// A cost total says how many calls it left out, so it is never read as complete.
///
/// Our price table is ours, not Claude Code's: a model it lacks prices as NULL rather than
/// as free, and the rollup carries the gap beside the sum.
#[test]
fn a_call_we_cannot_price_is_counted_out_of_the_total() {
    let (_scratch, store) = store();
    let mut trace = corpus::trace("spine", SPINE);

    // If a session holds a call whose model our table does not price — invented by nulling a
    // real call's cost, since every model the corpus used is priced...
    let priced = trace.api_calls[0]
        .cost_usd
        .expect("the first call is priced");
    trace.api_calls.truncate(2);
    trace.api_calls[1].cost_usd = None;
    store
        .export(&trace, "fingerprint-1")
        .expect("the session exports");

    // ...then the total sums the calls we could price, and says one was left out.
    let rollup = store
        .fetch(
            "SELECT cost_usd, unpriced_api_calls FROM session_rollups WHERE session_id = $session",
            &[("session", SPINE.into())],
        )
        .expect("the rollup reads");
    same_cost(rollup[0].f64("cost_usd").expect("a total"), priced);
    assert_eq!(
        rollup[0].i64("unpriced_api_calls").expect("a count"),
        1,
        "the gap is reported beside the sum"
    );
}
