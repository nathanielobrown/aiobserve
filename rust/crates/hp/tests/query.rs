//! The `hp query` runner: what it selects, what it binds, and what it cites.
//!
//! The twin of `tests/analyze/test_query.py`. Nothing is stubbed — every leaf drives the CLI
//! against the cached fixture store and reads the two streams apart, because which stream a
//! line lands on is itself a contract a piped analysis depends on.
//!
//! `parity.rs` beside this file compares the same command lines against Python's output.
//! This file is what says the contract is right; that one says the two agree on it.

use std::collections::BTreeSet;

use hyphae_store::Store;
use hyphae_testsupport::{cache, landmarks, metadata, windows};

mod common;

use common::Output;

/// One `hp query` run against a store, in this process.
fn query(db: &std::path::Path, name: &str, arguments: &[&str]) -> Output {
    let mut argv = vec!["query", name, "--db", db.to_str().expect("a UTF-8 path")];
    argv.extend_from_slice(arguments);
    common::hp(&argv)
}

/// The same, against the shared corpus store every read-only leaf here takes.
fn run(name: &str, arguments: &[&str]) -> Output {
    query(&cache::corpus_store(), name, arguments)
}

/// The `--csv` stdout as rows, header included.
///
/// A record ends `\r\n`, which is also what tells a record apart from a newline inside a
/// quoted field — so the whole stream is read a character at a time rather than split first.
fn csv_rows(output: &Output) -> Vec<Vec<String>> {
    let (mut rows, mut row, mut field) = (Vec::new(), Vec::new(), String::new());
    let mut quoted = false;
    let mut characters = output.stdout.chars().peekable();
    while let Some(character) = characters.next() {
        match character {
            // A `""` inside a quoted field is one `"`; a lone one ends the quoting.
            '"' if quoted && characters.peek() == Some(&'"') => {
                field.push(characters.next().expect("the peeked quote"));
            }
            '"' => quoted = !quoted,
            ',' if !quoted => row.push(std::mem::take(&mut field)),
            '\r' if !quoted && characters.peek() == Some(&'\n') => {
                characters.next();
                row.push(std::mem::take(&mut field));
                rows.push(std::mem::take(&mut row));
            }
            _ => field.push(character),
        }
    }
    assert!(
        field.is_empty() && row.is_empty(),
        "the CSV ends mid-record"
    );
    rows
}

/// One column of the `--csv` stdout, by header name.
fn column(output: &Output, name: &str) -> Vec<String> {
    let rows = csv_rows(output);
    let (header, rows) = rows.split_first().expect("a header");
    let at = header
        .iter()
        .position(|column| column == name)
        .unwrap_or_else(|| panic!("no `{name}` column"));
    rows.iter().map(|row| row[at].clone()).collect()
}

/// The `k=v` pairs of a citation line, which under `--csv` sits on stderr.
fn bindings(output: &Output) -> Vec<(String, String)> {
    let citation = output
        .stderr
        .lines()
        .find(|line| line.starts_with("-- queries/"))
        .expect("a citation");
    citation
        .split_whitespace()
        .skip(2)
        .map(|pair| {
            let (key, value) = pair.split_once('=').expect("a k=v pair");
            (key.to_owned(), value.to_owned())
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Which sessions a project selects

#[test]
fn a_project_selects_its_own_sessions_and_no_others() {
    // If the store holds three sessions recorded outside mycelia — two other projects and
    // one with no `project_dir` at all...
    let ids = column(
        &run("sessions", &["--project", landmarks::MYCELIA, "--csv"]),
        "session_id",
    );
    // ...then the corpus is the mycelia sessions, and none of the three.
    assert_eq!(ids.len(), windows::MYCELIA_SESSIONS);
    let ids: BTreeSet<&str> = ids.iter().map(String::as_str).collect();
    for outside in landmarks::NON_CORPUS {
        assert!(!ids.contains(outside), "`{outside}` is not this project's");
    }
    assert!(ids.contains(landmarks::SPINE));
}

#[test]
fn a_worktree_session_is_in_the_corpus_and_a_prefix_sibling_is_not() {
    // If one session sits under `<project>/.claude/worktrees/` and another under a checkout
    // that merely shares the prefix — the two values planted over real traces, since no
    // recorded fixture sits under either...
    let (_scratch, db) = cache::writable_copy(&cache::corpus_store());
    {
        let store = Store::create(&db).expect("the copy opens for writing");
        for (session_id, project_dir) in [
            (
                landmarks::WORKTREE_SESSION,
                format!("{}/.claude/worktrees/planted", landmarks::MYCELIA),
            ),
            (
                landmarks::SIBLING_SESSION,
                format!("{}-old", landmarks::MYCELIA),
            ),
        ] {
            store
                .connection()
                .execute(
                    "UPDATE sessions SET project_dir = ? WHERE id = ?",
                    [project_dir.as_str(), session_id],
                )
                .expect("the project is planted");
        }
    }
    let ids = column(
        &query(&db, "sessions", &["--project", landmarks::MYCELIA, "--csv"]),
        "session_id",
    );
    let ids: BTreeSet<&str> = ids.iter().map(String::as_str).collect();
    // ...then the worktree child is in the corpus...
    assert!(ids.contains(landmarks::WORKTREE_SESSION));
    // ...and the sibling is not: `starts_with` without the `/` would annex every neighbour,
    // and the failure would be a wrong number rather than an error.
    assert!(!ids.contains(landmarks::SIBLING_SESSION));
}

#[test]
fn every_spelling_of_one_project_names_one_corpus() {
    // A recorded `project_dir` is an absolute path, so the root is the one working directory
    // a relative spelling of one can be typed from. The change is process-wide; nextest gives
    // this leaf a process of its own, and nothing else in this file reads a relative path.
    std::env::set_current_dir("/").expect("the root is reachable");
    let canonical = run("sessions", &["--project", landmarks::MYCELIA, "--csv"]);
    for spelling in [
        format!("{}/", landmarks::MYCELIA),
        landmarks::MYCELIA.trim_start_matches('/').to_owned(),
    ] {
        let said = run("sessions", &["--project", &spelling, "--csv"]);
        assert_eq!(said.stdout, canonical.stdout, "`{spelling}`");
    }
    // Two identical empty corpora would prove nothing.
    assert_eq!(csv_rows(&canonical).len(), windows::MYCELIA_SESSIONS + 1);
}

// ---------------------------------------------------------------------------
// Which stream each line lands on

#[test]
fn the_excluded_count_goes_to_stderr_and_csv_stdout_stays_clean() {
    let said = run("sessions", &["--project", landmarks::MYCELIA, "--csv"]);
    // If one recorded session carries no `project_dir`, so no predicate can place it...
    assert!(
        said.stderr.contains("1 session(s)") && said.stderr.contains("excluded"),
        "{}",
        said.stderr
    );
    // ...then that count is on stderr, and stdout is the header plus one row per session and
    // nothing else — prose on stdout would break every piped analysis silently.
    let rows = csv_rows(&said);
    assert_eq!(rows.len(), windows::MYCELIA_SESSIONS + 1);
    assert!(rows.iter().all(|row| row.len() == rows[0].len()));
    assert!(!said.stdout.contains(landmarks::NO_PROJECT_SESSION));
}

#[test]
fn the_citation_names_the_query_file_and_every_resolved_binding() {
    // If a corpus query runs with an explicit `--as-of`...
    let table = run(
        "sessions",
        &[
            "--project",
            landmarks::MYCELIA,
            "--as-of",
            windows::AS_OF_PARTIAL,
        ],
    );
    // ...then the citation heads the table, naming the file and every resolved binding —
    // `$as_of` as its date, and the `--since` the caller never passed as NULL rather than
    // dropped, because a citation a reader cannot paste back is not a citation.
    let citation = table.stdout.lines().next().expect("a first line");
    assert_eq!(
        citation,
        format!(
            "-- queries/sessions.sql project={} since=NULL as_of={} window_days={}",
            landmarks::MYCELIA,
            windows::AS_OF_PARTIAL,
            hyphae_store::queries::WINDOW_DAYS,
        )
    );
    // ...and under `--csv` the same line moves to stderr, leaving stdout machine-readable.
    let piped = run(
        "sessions",
        &[
            "--project",
            landmarks::MYCELIA,
            "--as-of",
            windows::AS_OF_PARTIAL,
            "--csv",
        ],
    );
    assert!(piped.stderr.contains(citation), "{}", piped.stderr);
    assert!(!piped.stdout.contains("queries/sessions.sql"));
}

// ---------------------------------------------------------------------------
// What the window and the parameters resolve to

#[test]
fn as_of_defaults_to_today() {
    // A bare run cites the date its window was measured back from, off the shared clock —
    // frozen here, so the leaf names the date it expects instead of recomputing it the way
    // the code does. The freeze is process-wide and irreversible; no other leaf in this file
    // lets `--as-of` default.
    hyphae_model::clock::freeze(
        windows::date(windows::FAR_FUTURE)
            .and_hms_opt(0, 0, 0)
            .expect("midnight")
            .and_utc(),
    );
    let said = run("sessions", &["--project", landmarks::MYCELIA]);
    let citation = said.stdout.lines().next().expect("a first line");
    assert!(
        citation.contains(&format!("as_of={}", windows::FAR_FUTURE)),
        "{citation}"
    );

    // And that default is what a windowed query answers about, so a leaf or a report that
    // leaves `--as-of` unbound is reading the 28 days ending *now*: green while the
    // recordings are recent, red the morning they fall out of it. Years past the corpus the
    // window reaches none of them, so the grouping writes no window row at all — the loud
    // shape, rather than a quietly smaller number.
    let counts = run(
        "session_counts",
        &["--project", landmarks::MYCELIA, "--csv"],
    );
    assert!(!column(&counts, "period").contains(&"trailing_window".to_owned()));
    // ...while the corpus row, which no window touches, still holds every recording: the
    // store is what it always was, and only the date it is read at moved.
    assert_eq!(
        column(&counts, "sessions"),
        vec![windows::MYCELIA_SESSIONS.to_string()]
    );
}

#[test]
fn since_filters_and_omitting_it_means_the_whole_corpus() {
    // If some of the mycelia sessions started on or after a date inside the corpus...
    let since = run(
        "sessions",
        &[
            "--project",
            landmarks::MYCELIA,
            "--since",
            windows::SINCE,
            "--csv",
        ],
    );
    assert_eq!(column(&since, "session_id").len(), windows::SESSIONS_SINCE);
    // ...then the same query with no `--since` still returns the whole corpus.
    let whole = run("sessions", &["--project", landmarks::MYCELIA, "--csv"]);
    assert_eq!(
        column(&whole, "session_id").len(),
        windows::MYCELIA_SESSIONS
    );
}

#[test]
fn the_production_defaults_run_unless_a_param_overrides_one() {
    let keys = [
        "--param",
        &format!("session_id={}", landmarks::RESUME),
        "--param",
        &format!("source={}", landmarks::MAIN),
        "--param",
        "first_line=1",
        "--param",
        "last_line=1",
    ]
    .map(str::to_owned);
    let keys: Vec<&str> = keys.iter().map(String::as_str).collect();
    // If a query declares a parameter with a production default and the caller binds none...
    let mut bare = keys.clone();
    bare.push("--csv");
    let bare = run("records_slice", &bare);
    // ...the citation reports the manifest's value, which is what a committed report quotes...
    let cap = metadata::width("RAW_CHARS").to_string();
    assert!(bindings(&bare).contains(&("max_chars".to_owned(), cap)));
    // ...and an explicit override moves that one binding and no other...
    let mut over = keys.clone();
    over.extend(["--param", "max_chars=50", "--csv"]);
    let over = run("records_slice", &over);
    let moved: Vec<(String, String)> = bindings(&bare)
        .into_iter()
        .map(|(key, value)| {
            let value = if key == "max_chars" {
                "50".to_owned()
            } else {
                value
            };
            (key, value)
        })
        .collect();
    assert_eq!(bindings(&over), moved);
    // ...and the result obeys the value the citation reports, which is the point of citing it.
    let rows = csv_rows(&over);
    assert_eq!(rows[1].last().expect("a last column").len(), 50);
}

// ---------------------------------------------------------------------------
// What it refuses

#[test]
fn an_unknown_query_or_parameter_names_what_it_did_not_recognize() {
    // If the query does not exist...
    let unknown = run("no_such_query", &["--project", landmarks::MYCELIA]);
    assert!(!unknown.ok);
    assert!(
        unknown.stderr.contains("no_such_query"),
        "{}",
        unknown.stderr
    );
    // ...or a `--param` names something the query never declared, the message says which —
    // a silently ignored parameter produces a plausible wrong number and no signal.
    let undeclared = run(
        "sessions",
        &["--project", landmarks::MYCELIA, "--param", "nonsense=1"],
    );
    assert!(!undeclared.ok);
    assert!(
        undeclared.stderr.contains("nonsense"),
        "{}",
        undeclared.stderr
    );
}

#[test]
fn a_corpus_query_needs_a_project_and_a_keyed_one_refuses_the_window() {
    // A corpus query counts across sessions, so running it over whatever the store holds
    // would answer about a corpus nobody named...
    let unscoped = run("sessions", &[]);
    assert!(!unscoped.ok);
    assert!(unscoped.stderr.contains("--project"), "{}", unscoped.stderr);
    // ...and a keyed query is one session's, so a project or a window would say nothing about
    // what it returns. Silently ignoring either is what leaves a reader citing a filter that
    // never applied.
    for extra in [
        vec!["--project", landmarks::MYCELIA],
        vec!["--since", windows::SINCE],
    ] {
        let mut argv = vec!["--param", "session_id=x", "--param", "source=main"];
        argv.extend(extra.clone());
        let said = run("session_timeline", &argv);
        assert!(!said.ok, "{extra:?} was accepted");
        assert!(said.stderr.contains("session_timeline"), "{}", said.stderr);
    }
}

#[test]
fn a_store_from_another_schema_is_refused_and_sends_the_reader_to_the_guide() {
    // If the store holds a schema version this build does not read — stamped onto a copy,
    // since every fixture store is written by the current schema...
    let (_scratch, db) = cache::writable_copy(&cache::corpus_store());
    {
        let store = Store::create(&db).expect("the copy opens for writing");
        store
            .connection()
            .execute(
                "UPDATE meta SET schema_version = ?",
                [hyphae_store::schema::SCHEMA_VERSION - 1],
            )
            .expect("the version row is writable");
    }
    // ...then the query refuses rather than reading tables it may not understand, and points
    // at the store guide — a reader told to delete the store instead can destroy the only
    // copy of a session Claude Code has since pruned from disk.
    let said = query(&db, "sessions", &["--project", landmarks::MYCELIA]);
    assert!(!said.ok);
    assert!(said.stderr.contains("docs/store.md"), "{}", said.stderr);
}
