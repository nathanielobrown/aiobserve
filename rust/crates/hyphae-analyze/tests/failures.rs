//! The two counts that turn recurring failures into evidence, over a planted corpus.
//!
//! The `error_signatures` and `command_failures` half of `tests/analyze/test_counts.py`. One
//! answers "how often did this error happen, and to which tool"; the other answers "which
//! command produced it" when the error text does not say. The leaves are about what a group
//! holds: which rows fall into one signature or one command shape, and what the trailing
//! window leaves out.
//!
//! Both need a population the recorded corpus lacks — every recorded error is a one-off
//! redacted down to a word and every tool input is redacted whole — so each plants one onto
//! real rows and says so. The compaction and reload counts, which need no plant, are
//! `reloads.rs`.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use hyphae_store::Row;
use hyphae_testsupport::{landmarks, windows};
use tempfile::TempDir;

mod common;

use common::{corpus, count, of_period};

// The first line every planted failure shares, and the tail that differs between them. A
// recurring error is one signature over many bodies — "File has not been read yet" ahead of a
// different path each time — and no recorded fixture error survived redaction with a body.
const SIGNATURE: &str = "planted failure signature";
const PLANTED_ERROR: &str = "planted failure signature\ntail for ";
/// The tool the plant marks failed, and what marking it costs: every `Read` in two sessions,
/// which is 4 calls in one thread of `FORK_ORIGIN` and 3 + 1 in two threads of `SPINE`.
const PLANTED_TOOL: &str = "Read";
const PLANTED_ERRORS: i64 = 8;
const PLANTED_SESSIONS: i64 = 2;
const PLANTED_THREADS: i64 = 3;
/// `FORK_ORIGIN` started 2026-07-21, inside either window; `SPINE` started 2026-07-06, before
/// the shorter one opens, so the window count drops its 4.
const PLANTED_IN_SHORT_WINDOW: i64 = 4;
/// The two recorded errors, each in a session of its own: an `Agent` call and a server-side
/// `advisor` call, whose results redaction left as one word apiece.
const RECORDED_SIGNATURES: &[&str] = &["[redacted]", "unavailable"];

// The error class that splits itself: a guardrail whose first line names the worktree it
// blocked. Invented text under a fake root, because fixture redaction leaves no error body at
// all, but the shape is the canonical store's — its worktree-isolation guardrail failed 36
// times in the 2026-08-13 window and split into 28 signatures, one per worktree. The call id
// stands in for the volatile segment.
const GUARDRAIL_HEAD: &str =
    "This agent is isolated in the worktree /repo/.claude/worktrees/agent-";
const GUARDRAIL_TAIL: &str = ", but this command wanted to write outside it";
/// The one group they have to collapse into: the sentence, with the path standing for itself.
const GUARDRAIL_SIGNATURE: &str =
    "This agent is isolated in the worktree <path>, but this command wanted to write outside it";
/// What the plant costs: every corpus `Bash` call — 6 over 4 sessions and 5 threads: two in
/// `SPINE`'s main, one apiece in its run, `CONFIG_ONLY`, the architect run and
/// `parallel_tools`'s auditor.
const GUARDRAIL_ERRORS: i64 = 6;
const GUARDRAIL_SESSIONS: i64 = 4;
const GUARDRAIL_THREADS: i64 = 5;

// Command lines planted onto real calls so `command_failures` has command text to shape. They
// are invented — every fixture tool input is `[redacted]` — but the shapes are the canonical
// store's: over the 2026-08-07 window, 839 of the 1,487 failed Bash commands open with a
// `cd … &&` wrapper, so the head after the wrapper is what attribution needs, and
// `gh pr checks` is one of the two benign patterns iteration 1 could not count.
const WRAPPED_GREP: &str = r#"cd /tmp/fixture-worktree && grep -rn "pattern" src/ | head -20"#;
const BARE_GREP: &str = "grep -c pattern README.md";
const GH_CHECKS: &str = "gh pr checks --watch";
/// What each of those lines has to reduce to: the command word plus the bare words after it,
/// with the wrapper, the flags, the quoted pattern and the paths gone.
const GREP_HEAD: &str = "grep";
const GH_HEAD: &str = "gh pr checks";
/// What the wrapped grep fails with. A bare code, which is the whole problem: `Exit code 1`
/// names nothing, so the command shape is the only thing left to attribute it to. The `gh`
/// calls fail with the guardrail instead, so one shape's signature is a real sentence.
const EXIT_1: &str = "Exit code 1";
/// How the plant is spread: `SPINE`'s four `Read` calls, over its two threads, become wrapped
/// grep failures; `FORK_ORIGIN`'s four become two `gh pr checks` failures and two grep calls
/// that succeeded — the denominator an error count is read against.
const WRAPPED_GREP_CALLS: i64 = 4;
const WRAPPED_GREP_THREADS: i64 = 2;
const GH_CHECKS_CALLS: i64 = 2;
const BARE_GREP_CALLS: i64 = 2;
/// Every character a shaped head is forbidden to carry: a flag, a path, or a quote.
const FORBIDDEN_IN_A_HEAD: &[char] = &['-', '/', '"', '\''];

// ---------------------------------------------------------------------------
// error_signatures

/// Errors that differ only after their first line are counted as one recurring error.
#[test]
fn error_signatures_counts_one_signature_over_many_bodies() {
    let (_scratch, db) = planted_failures();

    // If eight tool calls failed with the same opening line and a different body each — the
    // shape of a recurring error, planted because the recorded ones are one-offs — spread
    // over two sessions and three threads...
    let rows: Vec<Row> = signatures(&db, &[("min_occurrences", "2")], windows::AS_OF_WHOLE)
        .into_iter()
        .filter(|row| row.str("tool").expect("a tool") == PLANTED_TOOL)
        .collect();

    // ...then they come back as one row. The signature is the first line, so the bodies do
    // not split the count, and the spread says how much of the corpus it is evidence about.
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].str("signature").expect("a signature"), SIGNATURE);
    assert_eq!(count(&rows[0], "errors"), PLANTED_ERRORS);
    assert_eq!(count(&rows[0], "sessions"), PLANTED_SESSIONS);
    assert_eq!(count(&rows[0], "threads"), PLANTED_THREADS);
}

/// Each signature is counted twice: over the trailing window, and over the whole corpus.
#[test]
fn error_signatures_counts_the_window_beside_the_corpus() {
    let (_scratch, db) = planted_failures();

    // If the as-of moves forward until one of the two erring sessions falls out of the
    // trailing window...
    let bindings = [("min_occurrences", "2")];
    let answer = corpus(&db, "error_signatures", windows::AS_OF_PARTIAL, &bindings);
    let window = of_period(&answer, "trailing_window");
    let whole = of_period(&answer, "corpus");

    // ...then the window count drops that session's four errors, so a report quoting it is
    // quoting a number its citation's `as_of` can be re-run for...
    assert_eq!(count(&window[0], "errors"), PLANTED_IN_SHORT_WINDOW);
    assert_eq!(count(&window[0], "sessions"), 1);
    // ...and the corpus count still holds all eight, which is the baseline that says whether
    // a window number is a spike or the way this tool always behaves.
    assert_eq!(count(&whole[0], "errors"), PLANTED_ERRORS);
}

/// A reader can count one phrase's occurrences, and one-off errors stay out of the way.
#[test]
fn error_signatures_narrows_to_a_bound_phrase_and_a_floor() {
    let (_scratch, db) = planted_failures();

    // If the corpus holds the two planted signatures and the two recorded one-off errors...
    let every = whole_corpus(&db, &[("min_occurrences", "1")]);
    let mut found = spelled(&every);
    found.sort_unstable();
    let mut expected = vec![SIGNATURE, GUARDRAIL_SIGNATURE];
    expected.extend_from_slice(RECORDED_SIGNATURES);
    expected.sort_unstable();
    assert_eq!(found, expected);

    // ...then the floor keeps the singletons out, which is what bounds a listing on a corpus
    // where most error text is unique...
    let kept = whole_corpus(&db, &[("min_occurrences", "2")]);
    assert_eq!(spelled(&kept), vec![SIGNATURE, GUARDRAIL_SIGNATURE]);

    // ...and binding a phrase counts just the error holding it, matched anywhere in the text
    // rather than only in the line the signature is cut from — a tail is where the path sits.
    let bound = signatures(
        &db,
        &[("min_occurrences", "1"), ("signature", "tail for ")],
        windows::AS_OF_WHOLE,
    );
    assert_eq!(spelled(&bound), vec![SIGNATURE]);
    assert_eq!(count(&bound[0], "errors"), PLANTED_ERRORS);
}

/// One guardrail message is one error, however many worktrees it names.
#[test]
fn error_signatures_groups_past_a_path_inside_the_line() {
    let (_scratch, db) = planted_failures();

    // If six calls failed with one guardrail message whose *first line* names the worktree it
    // blocked — a different path each time, so the cut that keeps a trailing path out of the
    // signature cannot help...
    let rows: Vec<Row> = signatures(&db, &[("min_occurrences", "2")], windows::AS_OF_WHOLE)
        .into_iter()
        .filter(|row| row.str("signature").expect("a signature") == GUARDRAIL_SIGNATURE)
        .collect();

    // ...then they are one recurring error rather than one group per worktree, which is what
    // iteration 3's isolation guardrail had been split into...
    assert_eq!(rows.len(), 1);
    assert_eq!(count(&rows[0], "errors"), GUARDRAIL_ERRORS);
    assert_eq!(count(&rows[0], "sessions"), GUARDRAIL_SESSIONS);
    assert_eq!(count(&rows[0], "threads"), GUARDRAIL_THREADS);

    // ...and the path is gone from the output rather than shortened, so no signature a report
    // quotes carries a run of somebody's filesystem.
    let every = whole_corpus(&db, &[("min_occurrences", "1")]);
    assert!(!spelled(&every).iter().any(|line| line.contains('/')));
}

// ---------------------------------------------------------------------------
// command_failures

/// Failures of one command are counted together however the command line was wrapped.
#[test]
fn command_failures_groups_by_the_shape_of_the_command_line() {
    let (_scratch, db) = planted_commands();
    let rows = of_period(
        &corpus(
            &db,
            "command_failures",
            windows::AS_OF_WHOLE,
            &[("min_occurrences", "1")],
        ),
        "corpus",
    );
    let shapes: BTreeMap<(String, Option<String>), &Row> = rows
        .iter()
        .map(|row| {
            (
                (
                    row.str("command_head").expect("a head").to_owned(),
                    row.opt_str("signature")
                        .expect("a column")
                        .map(str::to_owned),
                ),
                row,
            )
        })
        .collect();
    let shape = |head: &str, signature: Option<&str>| -> &Row {
        shapes[&(head.to_owned(), signature.map(str::to_owned))]
    };

    // If four calls failed with a bare `Exit code 1` behind a `cd … &&` wrapper...
    let wrapped = shape(GREP_HEAD, Some(EXIT_1));
    // ...then the wrapper, the flags, the quoted pattern and the paths are all gone, and what
    // is left is the command word — which is the attribution the error text cannot give...
    assert_eq!(count(wrapped, "calls"), WRAPPED_GREP_CALLS);
    assert_eq!(count(wrapped, "threads"), WRAPPED_GREP_THREADS);
    // ...with the head marked as standing for a chain, so nobody reads it as the whole command.
    assert_eq!(count(wrapped, "chained"), WRAPPED_GREP_CALLS);

    // ...and two calls of the same command that succeeded come back as their own row, under a
    // NULL signature: the denominator that says whether the failures are the norm for it...
    let clean = shape(GREP_HEAD, None);
    assert_eq!(count(clean, "calls"), BARE_GREP_CALLS);
    assert_eq!(count(clean, "chained"), 0);
    // ...while the head's error total rides on both rows, so ranking shapes by failures takes
    // no arithmetic.
    assert_eq!(count(wrapped, "head_errors"), WRAPPED_GREP_CALLS);
    assert_eq!(count(clean, "head_errors"), WRAPPED_GREP_CALLS);

    // ...and a command whose subcommands name what it did keeps them, because `gh` alone
    // would put `gh pr checks` and `gh pr create` in one group. Its two calls failed with a
    // guardrail naming a different worktree each, and land in one group all the same: the
    // signature is normalized here the way `error_signatures` normalizes it.
    let checks = shape(GH_HEAD, Some(GUARDRAIL_SIGNATURE));
    assert_eq!(count(checks, "calls"), GH_CHECKS_CALLS);

    // Nothing else reaches the output: no head carries a flag, a path, or a quoted argument,
    // and no signature carries a path either...
    for (head, signature) in shapes.keys() {
        assert!(!head.contains(FORBIDDEN_IN_A_HEAD), "{head}");
        let signature = signature.as_deref().unwrap_or_default();
        assert!(!signature.contains('/'), "{signature}");
    }

    // ...and `$head_chars` cuts whatever is left, which is the backstop under that rule — a
    // command line is private text, and a shape nobody anticipated must not carry a run of it.
    let capped = of_period(
        &corpus(
            &db,
            "command_failures",
            windows::AS_OF_WHOLE,
            &[("min_occurrences", "1"), ("head_chars", "4")],
        ),
        "corpus",
    );
    let longest = capped
        .iter()
        .map(|row| row.str("command_head").expect("a head").chars().count())
        .max()
        .expect("the capped corpus has rows");
    assert_eq!(longest, 4);
}

// ---------------------------------------------------------------------------
// The planted stores these read, and the shapes they read them in

/// The corpus with two recurring errors planted: one splitting after its first line, one
/// inside it.
///
/// Invented data, and deliberately so: the recorded errors are one-offs whose text redaction
/// cut to a word, and a recurring error is precisely what this query counts.
fn planted_failures() -> (TempDir, PathBuf) {
    common::planted(|store| {
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET is_error = true, result = ? || id
                 WHERE name = ? AND session_id IN (?, ?)",
                duckdb::params![
                    PLANTED_ERROR,
                    PLANTED_TOOL,
                    landmarks::SPINE,
                    landmarks::FORK_ORIGIN
                ],
            )
            .expect("the copy takes the planted failures");
        // Every `Bash` call gets the guardrail, whose volatile segment is the call id: one
        // message class over four worktrees, which the corpus has and no fixture records.
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET is_error = true, result = ? || id || ? WHERE name = 'Bash'",
                duckdb::params![GUARDRAIL_HEAD, GUARDRAIL_TAIL],
            )
            .expect("the copy takes the planted guardrail");
    })
}

/// The corpus with eight real calls rewritten as `Bash` calls carrying a command line.
///
/// Invented text, and it has to be: fixture redaction replaces every tool input, so the
/// recorded corpus holds eight `[redacted]` command lines and no failed one at all. What is
/// real here is the rows — their sessions, threads and periods — and the shapes the lines were
/// drawn from, which are the canonical store's (see the constants above).
fn planted_commands() -> (TempDir, PathBuf) {
    common::planted(|store| {
        // `SPINE`'s reads become the wrapped failures, spread over the two threads it has...
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET name = 'Bash', input = ?, is_error = true, result = ?
                 WHERE name = ? AND session_id = ?",
                duckdb::params![
                    serde_json::json!({ "command": WRAPPED_GREP }).to_string(),
                    EXIT_1,
                    PLANTED_TOOL,
                    landmarks::SPINE
                ],
            )
            .expect("the copy takes the wrapped failures");

        // ...and `FORK_ORIGIN`'s split into the failing `gh` calls and the succeeding ones.
        // Its fork replays every call under a second source, so only the live rows are
        // rewritten.
        let ids: Vec<String> = {
            let mut statement = store
                .connection()
                .prepare(
                    "SELECT id FROM tool_calls
                     WHERE name = ? AND session_id = ? AND NOT replayed ORDER BY id",
                )
                .expect("the copy answers");
            statement
                .query_map(
                    duckdb::params![PLANTED_TOOL, landmarks::FORK_ORIGIN],
                    |row| row.get::<_, String>(0),
                )
                .expect("the ids read")
                .collect::<Result<Vec<String>, _>>()
                .expect("the ids read")
        };
        assert_eq!(ids.len() as i64, GH_CHECKS_CALLS + BARE_GREP_CALLS);
        let plan: Vec<(&str, bool)> =
            std::iter::repeat_n((GH_CHECKS, true), GH_CHECKS_CALLS as usize)
                .chain(std::iter::repeat_n(
                    (BARE_GREP, false),
                    BARE_GREP_CALLS as usize,
                ))
                .collect();
        for (id, (command, fails)) in ids.iter().zip(plan) {
            // The failing ones carry the guardrail, whose volatile segment is the call id, so
            // this query's signature has the same path to normalize away that its own does.
            let result = fails.then(|| format!("{GUARDRAIL_HEAD}{id}{GUARDRAIL_TAIL}"));
            store
                .connection()
                .execute(
                    "UPDATE tool_calls SET name = 'Bash', input = ?, is_error = ?, result = ?
                     WHERE id = ? AND session_id = ? AND NOT replayed",
                    duckdb::params![
                        serde_json::json!({ "command": command }).to_string(),
                        fails,
                        result,
                        id,
                        landmarks::FORK_ORIGIN
                    ],
                )
                .expect("the copy takes the planted command");
        }
    })
}

/// `error_signatures` over the fixture project, as the rows of its trailing window.
fn signatures(db: &Path, bindings: &[(&str, &str)], as_of: &str) -> Vec<Row> {
    of_period(
        &corpus(db, "error_signatures", as_of, bindings),
        "trailing_window",
    )
}

/// The same over the whole corpus, at the `as_of` every unwindowed leaf here reads.
fn whole_corpus(db: &Path, bindings: &[(&str, &str)]) -> Vec<Row> {
    of_period(
        &corpus(db, "error_signatures", windows::AS_OF_WHOLE, bindings),
        "corpus",
    )
}

/// How each row of a signature listing spells its group, in the order the query ranked them.
fn spelled(rows: &[Row]) -> Vec<&str> {
    rows.iter()
        .map(|row| row.str("signature").expect("a signature"))
        .collect()
}
