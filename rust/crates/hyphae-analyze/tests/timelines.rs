//! The reading-support queries: the timelines, `view_runs`, `error_records`, `records_slice`.
//!
//! The twin of `tests/analyze/test_timelines.py`. A timeline is what a reader sees instead of
//! the transcript, so the leaves here are about agreement and containment: the timeline's cost
//! has to equal the rollup for the scope it claims, and one run's timeline has to hold that
//! run's rows and no other's. Every expected number is read back out of the store rather than
//! pinned here, so a fixture change moves both sides together.

use std::collections::BTreeSet;

use hyphae_analyze::{QueryError, Request};
use hyphae_store::{Row, queries};
use hyphae_testsupport::{cache, landmarks};

mod common;

use common::{cap, keyed, probe};

/// The marker a timeline gives the row for api calls that sit under no turn.
const UNATTRIBUTED: &str = "(unattributed)";
/// A timeline's prompt cell comes back one character past its cut — that extra character is
/// what tells whoever prints it that the prompt went on (`view/format.py:cut`).
const PROMPT_CAP: usize = queries::LOG_CHARS + 1;
/// The id the planted agent-run compaction carries, so no first-seen twin can own it.
const PLANTED_COMPACTION: &str = "planted-compaction";

/// A value past any of the caps, with a tail the assertions can look for. Invented: fixture
/// prompts and tool results are redacted down to a few words, so nothing recorded cuts.
const SENTINEL_TAIL: &str = "TAIL";

fn sentinel() -> String {
    format!("{}{SENTINEL_TAIL}", "planted text ".repeat(40))
}

fn count(row: &Row, column: &str) -> i64 {
    row.i64(column).expect("a count")
}

/// A cell's length in characters, which is what a cap is stated in.
fn width(row: &Row, column: &str) -> usize {
    row.str(column).expect("a text cell").chars().count()
}

fn totals(rows: &[Row], column: &str) -> f64 {
    rows.iter()
        .map(|row| row.f64(column).expect("a cost"))
        .sum()
}

fn counted(rows: &[Row], column: &str) -> i64 {
    rows.iter().map(|row| count(row, column)).sum()
}

fn close(mine: f64, theirs: f64) -> bool {
    (mine - theirs).abs() < 1e-4
}

// ---------------------------------------------------------------------------
// The timelines

#[test]
fn a_session_timeline_accounts_for_api_calls_that_sit_under_no_turn() {
    let db = cache::corpus_store();
    // If a resumed session's api calls all carry a NULL `turn_id` — no turn of its own owns
    // them, because the turns they answered live in the session it resumed...
    let unattributed = count(
        &probe(
            &db,
            "SELECT count(*) AS n FROM live_api_calls
             WHERE session_id = $session AND turn_id IS NULL",
            &[("session", landmarks::RESUME.into())],
        ),
        "n",
    );
    assert!(unattributed > 0);

    // ...then the timeline still lists them, in one row that names itself...
    let rows = keyed(
        &db,
        "session_timeline",
        &[("session_id", landmarks::RESUME)],
    )
    .rows;
    let orphans: Vec<&Row> = rows
        .iter()
        .filter(|row| row.str("turn_id").expect("a turn") == UNATTRIBUTED)
        .collect();
    assert_eq!(orphans.len(), 1);
    assert_eq!(count(orphans[0], "api_calls"), unattributed);

    // ...and the timeline's total is the session's rollup cost, not the $0 a plain turn join
    // would report against a front matter quoting the real number.
    let rollup = probe(
        &db,
        "SELECT cost_usd FROM session_rollups WHERE session_id = $session",
        &[("session", landmarks::RESUME.into())],
    )
    .f64("cost_usd")
    .expect("a cost");
    assert!(rollup > 0.0);
    let listed = totals(&rows, "cost_usd");
    assert!(close(listed, rollup), "{listed} is not {rollup}");
}

#[test]
fn a_session_timeline_totals_only_the_thread_it_lists() {
    let db = cache::corpus_store();
    // If a session spends part of its cost inside an agent run — here on a call under no turn
    // at all, the shape most likely to be swept into the wrong scope...
    let costs = probe(
        &db,
        "SELECT
             coalesce(sum(cost_usd) FILTER (source = $main), 0) AS scoped,
             coalesce(sum(cost_usd) FILTER (source = $run AND turn_id IS NULL), 0) AS elsewhere
         FROM live_api_calls WHERE session_id = $session",
        &[
            ("main", landmarks::MAIN.into()),
            ("run", landmarks::SERVER_TOOLS_RUN.into()),
            ("session", landmarks::SERVER_TOOLS.into()),
        ],
    );
    let (scoped, elsewhere) = (
        costs.f64("scoped").expect("a cost"),
        costs.f64("elsewhere").expect("a cost"),
    );
    assert!(elsewhere > 0.0);

    // ...then the main-thread timeline totals the main thread and stops there: a timeline that
    // lists one scope and advertises another's total is a number no reader can reconcile.
    let rows = keyed(
        &db,
        "session_timeline",
        &[("session_id", landmarks::SERVER_TOOLS)],
    )
    .rows;
    let listed = totals(&rows, "cost_usd");
    assert!(close(listed, scoped), "{listed} is not {scoped}");
}

#[test]
fn a_run_timeline_holds_one_run_and_no_other() {
    let db = cache::corpus_store();
    // If a run spawned a leaf run of its own...
    let rows = keyed(
        &db,
        "run_timeline",
        &[
            ("session_id", landmarks::SPINE),
            ("source", landmarks::SPINE_RUN),
        ],
    )
    .rows;
    let held = |table: &str, source: &str| -> i64 {
        count(
            &probe(
                &db,
                &format!(
                    "SELECT count(*) AS n FROM {table}
                     WHERE session_id = $session AND source = $source"
                ),
                &[
                    ("session", landmarks::SPINE.into()),
                    ("source", source.into()),
                ],
            ),
            "n",
        )
    };
    // ...then its timeline lists exactly its own turns...
    assert_eq!(
        rows.len(),
        usize::try_from(held("live_turns", landmarks::SPINE_RUN)).expect("a count fits")
    );
    // ...and its own api and tool calls, counted once each: a join that fans out over the
    // tree inflates every number a reader copies, and the totals still look plausible.
    for (column, table) in [
        ("api_calls", "live_api_calls"),
        ("tool_calls", "live_tool_calls"),
    ] {
        assert_eq!(
            counted(&rows, column),
            held(table, landmarks::SPINE_RUN),
            "{column}"
        );
    }
    // ...and the leaf's own rows are absent, since they answer to the leaf's timeline.
    let leaf = keyed(
        &db,
        "run_timeline",
        &[
            ("session_id", landmarks::SPINE),
            ("source", landmarks::SPINE_LEAF),
        ],
    )
    .rows;
    let turns = |rows: &[Row]| -> BTreeSet<String> {
        rows.iter()
            .map(|row| row.str("turn_id").expect("a turn").to_owned())
            .collect()
    };
    assert!(turns(&rows).is_disjoint(&turns(&leaf)));
}

#[test]
fn a_timeline_truncates_a_long_prompt() {
    // If a turn's prompt runs past the cap (planted: the longest recorded prompt is 145 chars
    // after redaction, so no fixture can carry this)...
    let (_scratch, db) = common::planted(|store| {
        store
            .connection()
            .execute(
                r#"UPDATE turns SET prompt = ?
                   WHERE session_id = ? AND source = ? AND "index" = 0"#,
                duckdb::params![sentinel(), landmarks::SPINE, landmarks::MAIN],
            )
            .expect("the copy takes the planted prompt");
    });
    let rows = keyed(&db, "session_timeline", &[("session_id", landmarks::SPINE)]).rows;
    let long: Vec<usize> = rows
        .iter()
        .map(|row| width(row, "prompt"))
        .filter(|length| *length >= PROMPT_CAP)
        .collect();
    // ...then the timeline cuts it at the cap and the tail never reaches the reader.
    assert_eq!(long, vec![PROMPT_CAP]);
    for row in &rows {
        assert!(!row.str("prompt").expect("a prompt").contains(SENTINEL_TAIL));
    }
}

// ---------------------------------------------------------------------------
// Errors, and the records behind them

#[test]
fn error_records_finds_a_runs_errors_without_being_told_the_thread() {
    let db = cache::corpus_store();
    // If a session's only error happened inside an agent run rather than on the main thread —
    // the shape a reader cannot search for, because finding it means knowing the run first...
    let failure = probe(
        &db,
        "SELECT source, id FROM live_tool_calls WHERE session_id = $session AND is_error",
        &[("session", landmarks::FORK_ORIGIN.into())],
    );
    let source = failure.str("source").expect("a thread");
    let tool_call_id = failure.str("id").expect("a call");
    assert_ne!(source, landmarks::MAIN);

    // ...then a query keyed on the session alone lists it, naming the thread it belongs to...
    let rows = keyed(
        &db,
        "error_records",
        &[("session_id", landmarks::FORK_ORIGIN)],
    )
    .rows;
    let listed: Vec<(&str, &str)> = rows
        .iter()
        .map(|row| {
            (
                row.str("source").expect("a thread"),
                row.str("tool_call_id").expect("a call"),
            )
        })
        .collect();
    assert_eq!(listed, vec![(source, tool_call_id)]);

    // ...and the line it gives is the record a reader can go read: `records_slice` at that
    // line comes back holding the call. Locating errors by scanning raw records at a thousand
    // lines a session is what this query exists to replace.
    let line = count(&rows[0], "line_no").to_string();
    let sliced = keyed(
        &db,
        "records_slice",
        &[
            ("session_id", landmarks::FORK_ORIGIN),
            ("source", source),
            ("first_line", &line),
            ("last_line", &line),
        ],
    )
    .rows;
    assert!(
        sliced[0]
            .str("raw")
            .expect("a record")
            .contains(tool_call_id)
    );

    // ...while binding the source narrows to that one thread, so the main thread holds none.
    let main_only = keyed(
        &db,
        "error_records",
        &[
            ("session_id", landmarks::FORK_ORIGIN),
            ("source", landmarks::MAIN),
        ],
    )
    .rows;
    assert!(main_only.is_empty());
}

#[test]
fn error_records_lists_the_failures_and_nothing_else() {
    let db = cache::corpus_store();
    // If a session made both failing and succeeding tool calls, one of them server-side —
    // whose result rides an assistant record rather than a user one...
    let calls = probe(
        &db,
        "SELECT count(*) FILTER (is_error) AS failed, count(*) FILTER (NOT is_error) AS succeeded
         FROM live_tool_calls WHERE session_id = $session",
        &[("session", landmarks::SERVER_TOOLS.into())],
    );
    assert_eq!(count(&calls, "failed"), 1);
    assert!(count(&calls, "succeeded") > 0);

    // ...then the errors come back one row apiece, with what the tool was and how long its
    // result ran, whether or not a raw record could be found to cite.
    let rows = keyed(
        &db,
        "error_records",
        &[("session_id", landmarks::SERVER_TOOLS)],
    )
    .rows;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].str("tool").expect("a tool"), "advisor");
    assert!(count(&rows[0], "error_chars") > 0);
}

#[test]
fn error_records_bounds_the_error_text_it_returns() {
    // If a tool returned an error longer than the cap (planted: fixture results are redacted
    // down to a word, so nothing recorded exercises the cut)...
    let sentinel = sentinel();
    let (_scratch, db) = common::planted(|store| {
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET result = ? WHERE session_id = ? AND is_error",
                duckdb::params![sentinel, landmarks::SERVER_TOOLS],
            )
            .expect("the copy takes the planted error");
    });
    let rows = keyed(
        &db,
        "error_records",
        &[("session_id", landmarks::SERVER_TOOLS)],
    )
    .rows;
    // ...then the cell stops at the cap and reports the length it cut from, and the tail of a
    // private result never reaches the reader's context.
    assert_eq!(rows.len(), 1);
    assert_eq!(width(&rows[0], "error"), cap("error_records", "max_chars"));
    assert_eq!(
        count(&rows[0], "error_chars"),
        i64::try_from(sentinel.chars().count()).expect("a length fits")
    );
    assert!(
        !rows[0]
            .str("error")
            .expect("an error")
            .contains(SENTINEL_TAIL)
    );
}

#[test]
fn records_slice_refuses_to_run_without_a_line_range() {
    // If a reader asks for raw records without saying which lines...
    let refusal = common::attempt(
        &cache::corpus_store(),
        "records_slice",
        Request {
            project: None,
            since: None,
            as_of: hyphae_testsupport::windows::date(hyphae_testsupport::windows::AS_OF_WHOLE),
            params: [
                ("session_id".to_owned(), landmarks::RESUME.to_owned()),
                ("source".to_owned(), landmarks::MAIN.to_owned()),
            ]
            .into_iter()
            .collect(),
        },
    )
    .expect_err("a slice with no range cannot run");
    // ...it refuses naming the parameter: a defaulted range would hand back a window of
    // private transcript, and the reader would see no error to tell them so.
    assert!(matches!(refusal, QueryError::Unbound { .. }), "{refusal}");
    assert!(refusal.to_string().contains("first_line"), "{refusal}");
}

#[test]
fn records_slice_caps_the_raw_text_it_returns() {
    let db = cache::corpus_store();
    // If the store holds a record longer than the cap...
    let line = landmarks::RESUME_LONG_RECORD.to_string();
    let length = count(
        &probe(
            &db,
            "SELECT length(raw) AS n FROM raw_records
             WHERE session_id = $session AND source = $source AND line_no = $line",
            &[
                ("session", landmarks::RESUME.into()),
                ("source", landmarks::MAIN.into()),
                (
                    "line",
                    hyphae_store::Param::Int(landmarks::RESUME_LONG_RECORD),
                ),
            ],
        ),
        "n",
    );
    let capped = cap("records_slice", "max_chars");
    assert!(length > i64::try_from(capped).expect("a cap fits"));

    // ...then the slice that names it returns the record cut to the cap.
    let rows = keyed(
        &db,
        "records_slice",
        &[
            ("session_id", landmarks::RESUME),
            ("source", landmarks::MAIN),
            ("first_line", &line),
            ("last_line", &line),
        ],
    )
    .rows;
    assert_eq!(rows.len(), 1);
    assert_eq!(width(&rows[0], "raw"), capped);
}

// ---------------------------------------------------------------------------
// Ranking a session's runs

#[test]
fn view_runs_carries_what_ranking_a_sessions_runs_takes() {
    // If a session's two runs differ on every measure a reader ranks by — one spent four
    // times the other, one failed a tool call, one ran out of context (that compaction is
    // planted onto a real run: the one recorded run compaction is in a single-run session).
    // A copy under a new id rather than a move, so the recorded compaction stays where it was
    // recorded and the `corpus_*` first-seen rule has no twin to prefer.
    let (_scratch, db) = common::planted(|store| {
        store
            .connection()
            .execute(
                "INSERT INTO compactions
                 SELECT ?, ?, ?, timestamp, trigger, pre_tokens, post_tokens, duration_ms
                 FROM compactions WHERE session_id = ? LIMIT 1",
                duckdb::params![
                    PLANTED_COMPACTION,
                    landmarks::FORK_ORIGIN,
                    landmarks::FORK_ORIGIN_RUN,
                    landmarks::ANCESTOR
                ],
            )
            .expect("the copy takes the planted compaction");
    });
    let rows = common::key(
        &keyed(&db, "view_runs", &[("session_id", landmarks::FORK_ORIGIN)]).rows,
        "run_id",
    );

    // ...then one query ranks them: each row carries its own numbers, read back from the
    // store rather than pinned here...
    let named: BTreeSet<&str> = rows.keys().map(String::as_str).collect();
    assert_eq!(
        named,
        [landmarks::FORK_ORIGIN_RUN, landmarks::FORK_RUN]
            .into_iter()
            .collect()
    );
    for (run_id, row) in &rows {
        let held = probe(
            &db,
            "SELECT
               (SELECT coalesce(round(sum(c.cost_usd), 4), 0) FROM live_api_calls c
                  WHERE c.session_id = $session AND c.source = $run) AS cost,
               (SELECT count(*) FILTER (c.cost_usd IS NULL) FROM live_api_calls c
                  WHERE c.session_id = $session AND c.source = $run) AS unpriced,
               (SELECT count(*) FILTER (t.is_error) FROM live_tool_calls t
                  WHERE t.session_id = $session AND t.source = $run) AS errors,
               (SELECT count(*) FROM live_compactions k
                  WHERE k.session_id = $session AND k.source = $run) AS compactions",
            &[
                ("session", landmarks::FORK_ORIGIN.into()),
                ("run", run_id.into()),
            ],
        );
        assert_eq!(
            row.f64("cost_usd").expect("a cost"),
            held.f64("cost").expect("a cost")
        );
        assert_eq!(count(row, "unpriced_api_calls"), count(&held, "unpriced"));
        assert_eq!(count(row, "tool_errors"), count(&held, "errors"));
        assert_eq!(count(row, "compactions"), count(&held, "compactions"));
    }

    // ...and the numbers are the run's own: the failure sits on the run that made it and the
    // compaction on the other, where a session-wide total would put both on both.
    let (fork, origin) = (
        &rows[landmarks::FORK_RUN],
        &rows[landmarks::FORK_ORIGIN_RUN],
    );
    assert_eq!(
        (count(fork, "tool_errors"), count(fork, "compactions")),
        (1, 0)
    );
    assert_eq!(
        (count(origin, "tool_errors"), count(origin, "compactions")),
        (0, 1)
    );
    assert!(row_cost(fork) > row_cost(origin));
}

fn row_cost(row: &Row) -> f64 {
    row.f64("cost_usd").expect("a cost")
}
