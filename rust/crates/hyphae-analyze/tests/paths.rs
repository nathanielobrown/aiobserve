//! The counts that read a tool call's path rather than its error text.
//!
//! The twin of `tests/analyze/test_paths.py`. `path_failures` answers "which directory was
//! the failing call pointed at" — the question `error_signatures` cannot answer, because the
//! path is exactly what its signature drops. `missing_file_recovery` answers what the thread
//! did about it in the calls that followed.
//!
//! Every recorded fixture input is `[redacted]`, so a path a query can group by is something
//! no recording carries. Both plants below say so; what stays real is the rows they land on —
//! their sessions, threads, tools and the order they ran in.

use std::path::Path;

use hyphae_store::Row;
use hyphae_testsupport::{landmarks, windows};
use tempfile::TempDir;

mod common;

use common::{corpus, key, of_period};

// The scratch directory two of iteration 3's mechanisms run through, under a fake root: it is
// gitignored, so it exists in the primary checkout and in none of the worktrees cut from it.
// One reader hitting it from three checkouts is the shape the canonical store holds — 45
// window errors over 16 sessions, 40 of them inside spawned runs, spread over a root per
// worktree.
const SCRATCH: &str = "handoffs";
const PRIMARY_PATH: &str = "/repo/handoffs/plan.md";
const WORKTREE_PATH: &str = "/repo/.claude/worktrees/agent-1/handoffs/plan.md";
const OTHER_WORKTREE_PATH: &str = "/repo/.claude/worktrees/agent-2/handoffs/plan.md";
// What the plant costs, following the fixture corpus's own `Read` calls: the four in
// `FORK_ORIGIN`'s run and the one in `SPINE`'s leaf run fail from a worktree, while the three
// on `SPINE`'s main thread read the same directory in the primary checkout and succeed.
const SCRATCH_CALLS: i64 = 8;
const SCRATCH_ERRORS: i64 = 5;
const SCRATCH_SESSIONS: i64 = 2;
const SCRATCH_THREADS: i64 = 2;
/// The two worktree copies the failures come from, which is what has to collapse to one row.
const SCRATCH_WORKTREES: usize = 2;
/// The bucket a call with no directory in its path lands in — every recorded fixture input,
/// since redaction leaves `file_path` as a bare `[redacted]`.
const NO_DIRECTORY: &str = "(no directory)";

// The guessed filename and the directory it sits in: the shape iteration 3 saw four readers
// describe and no query could count — a Read of a name nobody had seen, then an `ls`.
const ADR_DIR: &str = "/repo/docs/adrs";
const ADR_PATH: &str = "/repo/docs/adrs/0042-guessed-name.md";
const PLAN_PATH: &str = "/repo/plans/locked.md";
// The two failures, one of which no listing would have prevented. Invented text under a fake
// root, redaction having left no fixture error with a body; `$missing` is bound to the phrase
// in the first, which is what a reader narrows the population with.
const MISSING_PHRASE: &str = "does not exist";
const NOT_FOUND: &str = "File does not exist.";
const DENIED: &str = "EACCES: permission denied";
// The three dispositions the query files every failure under, and how many it has to file.
const LOOKED_UP: &str = "listed the directory";
const LOOKED_ELSEWHERE: &str = "listed elsewhere";
const NEVER_LOOKED: &str = "no listing";
const PLANTED_GUESSES: i64 = 3;

/// `path_failures` over the fixture project, as the rows of the whole-corpus period.
fn path_failures(db: &Path, params: &[(&str, &str)]) -> Vec<Row> {
    of_period(
        &corpus(db, "path_failures", windows::AS_OF_WHOLE, params),
        "corpus",
    )
}

/// `missing_file_recovery` over the same, likewise.
fn recovery(db: &Path, params: &[(&str, &str)]) -> Vec<Row> {
    of_period(
        &corpus(db, "missing_file_recovery", windows::AS_OF_WHOLE, params),
        "corpus",
    )
}

fn count(row: &Row, column: &str) -> i64 {
    row.i64(column).expect("a count")
}

// ---------------------------------------------------------------------------
// One directory across the checkouts it sits in

/// The corpus with one gitignored directory read from three checkouts of one repository.
///
/// Invented paths, and they have to be: fixture redaction replaces every `file_path` with
/// `[redacted]`, so the recorded corpus cannot tell one directory from another. The shape is
/// the canonical store's (see [`SCRATCH`]).
fn planted_paths() -> (TempDir, std::path::PathBuf) {
    common::planted(|store| {
        for (session, source, target, fails) in [
            // The spawned runs are pointed at a copy of the directory their worktree lacks...
            (
                landmarks::FORK_ORIGIN,
                landmarks::FORK_ORIGIN_RUN,
                WORKTREE_PATH,
                true,
            ),
            (
                landmarks::SPINE,
                landmarks::SPINE_LEAF,
                OTHER_WORKTREE_PATH,
                true,
            ),
            // ...while the thread that spawned one reads the primary checkout and finds it.
            (landmarks::SPINE, landmarks::MAIN, PRIMARY_PATH, false),
        ] {
            let input = serde_json::json!({ "file_path": target }).to_string();
            store
                .connection()
                .execute(
                    "UPDATE tool_calls SET input = ?, is_error = ?
                     WHERE name = 'Read' AND session_id = ? AND source = ?",
                    duckdb::params![input, fails, session, source],
                )
                .expect("the copy takes the planted paths");
        }
    })
}

#[test]
fn path_failures_counts_one_directory_across_the_checkouts_it_sits_in() {
    let (_scratch, db) = planted_paths();
    // If five reads of one gitignored directory failed from two worktrees, and three reads of
    // that same directory in the primary checkout succeeded...
    let rows = key(&path_failures(&db, &[]), "directory");
    let scratch = &rows[SCRATCH];
    // ...then the failures are one row rather than one per worktree, which is what makes the
    // count comparable at all: a per-root split reads as a handful of one-off accidents...
    assert_eq!(count(scratch, "errors"), SCRATCH_ERRORS);
    assert_eq!(count(scratch, "sessions"), SCRATCH_SESSIONS);
    assert_eq!(count(scratch, "threads"), SCRATCH_THREADS);
    // ...with the reads that worked beside them, since a directory nobody touches and one
    // nobody can reach are the same number of failures and different findings...
    assert_eq!(count(scratch, "calls"), SCRATCH_CALLS);
    // ...and the share a spawned run hit says where the mechanism sits: the thread that
    // spawned the run could see the directory, and the run could not.
    assert_eq!(count(scratch, "run_errors"), SCRATCH_ERRORS);
}

#[test]
fn path_failures_can_be_asked_to_tell_the_checkouts_apart() {
    let (_scratch, db) = planted_paths();
    // If the same eight calls are grouped on two path segments instead of one...
    let rows = path_failures(&db, &[("min_occurrences", "1"), ("tail_segments", "2")]);
    let split: Vec<&Row> = rows
        .iter()
        .filter(|row| {
            row.str("directory")
                .expect("a directory")
                .ends_with(&format!("/{SCRATCH}"))
        })
        .collect();
    // ...then the aggregation comes apart into a row per worktree, each naming the copy it
    // failed in — the reading a follow-up wants, and the reason the default is 1...
    assert_eq!(split.len(), SCRATCH_WORKTREES);
    let errors: i64 = split.iter().map(|row| count(row, "errors")).sum();
    assert_eq!(errors, SCRATCH_ERRORS);
    // ...and no failure is lost or double-counted on the way: the split is a regrouping of the
    // same calls, so both readings total the corpus's failures the same.
    let whole = path_failures(&db, &[("min_occurrences", "1")]);
    let total = |rows: &[Row]| -> i64 { rows.iter().map(|row| count(row, "errors")).sum() };
    assert_eq!(total(&rows), total(&whole));
}

#[test]
fn path_failures_names_the_calls_that_carried_no_directory() {
    let (_scratch, db) = planted_paths();
    // If some calls named a bare file — which every recorded fixture call does, redaction
    // having cut its path to one word...
    let rows = key(
        &path_failures(&db, &[("min_occurrences", "0")]),
        "directory",
    );
    // ...then they are counted under a bucket that says so, instead of an empty string a
    // reader would take for a query bug.
    assert!(count(&rows[NO_DIRECTORY], "calls") > 0);
    assert!(!rows.contains_key(""));
}

// ---------------------------------------------------------------------------
// What the thread did in the calls after

/// The corpus with three failed reads and what each thread did in the calls after.
///
/// Invented inputs and results, and they have to be: redaction leaves no fixture call with a
/// path, a command or an error body. What the plant keeps is the recorded order — it rewrites
/// calls in place, so the distance between a failure and the listing after it is the distance
/// the transcript recorded.
fn planted_guesses() -> (TempDir, std::path::PathBuf) {
    common::planted(|store| {
        let listing = |command: &str| serde_json::json!({ "command": command }).to_string();
        let read = |path: &str| serde_json::json!({ "file_path": path }).to_string();
        for (session, source, index, name, input, result) in [
            // The run guesses an ADR filename and lists that directory in the very next call...
            (
                landmarks::FORK_ORIGIN,
                landmarks::FORK_ORIGIN_RUN,
                0,
                "Read",
                read(ADR_PATH),
                Some(NOT_FOUND),
            ),
            (
                landmarks::FORK_ORIGIN,
                landmarks::FORK_ORIGIN_RUN,
                1,
                "Bash",
                listing(&format!("ls -la {ADR_DIR}")),
                None,
            ),
            // ...then hits a failure listing could not have helped with, and globs elsewhere...
            (
                landmarks::FORK_ORIGIN,
                landmarks::FORK_ORIGIN_RUN,
                2,
                "Read",
                read(PLAN_PATH),
                Some(DENIED),
            ),
            (
                landmarks::FORK_ORIGIN,
                landmarks::FORK_ORIGIN_RUN,
                3,
                "Glob",
                serde_json::json!({ "path": "/repo/src" }).to_string(),
                None,
            ),
            // ...while the main thread guesses, does two other things, and only then looks.
            (
                landmarks::SPINE,
                landmarks::MAIN,
                1,
                "Read",
                read(ADR_PATH),
                Some(NOT_FOUND),
            ),
            (
                landmarks::SPINE,
                landmarks::MAIN,
                2,
                "Bash",
                listing("git status --short"),
                None,
            ),
            (
                landmarks::SPINE,
                landmarks::MAIN,
                4,
                "Bash",
                listing(&format!("ls {ADR_DIR}")),
                None,
            ),
        ] {
            store
                .connection()
                .execute(
                    r#"UPDATE tool_calls SET name = ?, input = ?, is_error = ?, result = ?
                       WHERE session_id = ? AND source = ? AND "index" = ?"#,
                    duckdb::params![
                        name,
                        input,
                        result.is_some(),
                        result,
                        session,
                        source,
                        index
                    ],
                )
                .expect("the copy takes the planted guesses");
        }
    })
}

#[test]
fn missing_file_recovery_counts_the_guess_the_thread_looked_up() {
    let (_scratch, db) = planted_guesses();
    // If three reads failed on a path they named, and each thread did something different in
    // the call after — listed that directory, listed another one, listed nothing...
    let rows = key(&recovery(&db, &[]), "recovery");
    // ...then the recovery iteration 3 could only describe is a number, with the spread that
    // says how much of the corpus it is evidence about...
    assert_eq!(count(&rows[LOOKED_UP], "failures"), 1);
    assert_eq!(count(&rows[LOOKED_UP], "sessions"), 1);
    assert_eq!(count(&rows[LOOKED_UP], "threads"), 1);
    // ...the listing of some other directory is kept apart from it, because a broader search
    // is not the thread finding the name it guessed at...
    assert_eq!(count(&rows[LOOKED_ELSEWHERE], "failures"), 1);
    // ...and the dispositions close over the population: every failed call that named a path
    // is in exactly one of them, so the recovery rate has a denominator.
    let failures: i64 = rows.values().map(|row| count(row, "failures")).sum();
    assert_eq!(failures, PLANTED_GUESSES);
    let named: Vec<&str> = rows.keys().map(String::as_str).collect();
    assert_eq!(
        named
            .iter()
            .copied()
            .collect::<std::collections::BTreeSet<_>>(),
        [LOOKED_UP, LOOKED_ELSEWHERE, NEVER_LOOKED]
            .into_iter()
            .collect()
    );
}

#[test]
fn missing_file_recovery_moves_with_the_window_it_is_asked_for() {
    let (_scratch, db) = planted_guesses();
    // If one thread listed the directory it guessed in, but three calls later rather than in
    // the next one...
    let default = key(&recovery(&db, &[]), "recovery");
    assert_eq!(count(&default[NEVER_LOOKED], "failures"), 1);
    // ...then it reads as no recovery at the production window, which is the strict claim —
    // the call after the failure is the one that answers it...
    let widened = key(&recovery(&db, &[("within_calls", "3")]), "recovery");
    // ...and widening the window moves it, so a report that widens has to say so: the number
    // is a function of a binding its citation carries...
    assert_eq!(count(&widened[LOOKED_UP], "failures"), 2);
    // ...while the disposition it left keeps its row at zero, because a disposition nothing
    // fell into is a finding and a missing row reads as a broken query.
    assert_eq!(count(&widened[NEVER_LOOKED], "failures"), 0);
}

#[test]
fn missing_file_recovery_narrows_to_the_failures_a_listing_could_have_prevented() {
    let (_scratch, db) = planted_guesses();
    // If one of the three failures was a permission error rather than a missing file — a
    // failure no amount of listing would have prevented...
    let rows = key(&recovery(&db, &[("missing", MISSING_PHRASE)]), "recovery");
    // ...then binding the phrase drops it, and the rate is quoted over the population it is
    // actually about — here the two reads of a name that was never there.
    let failures: i64 = rows.values().map(|row| count(row, "failures")).sum();
    assert_eq!(failures, PLANTED_GUESSES - 1);
    assert_eq!(count(&rows[LOOKED_ELSEWHERE], "failures"), 0);
}
