//! `hp extract` at the process level: the refresh loop, run over a planted projects root.
//!
//! The port of `tests/test_pipeline.py`. Python drives `refresh()` directly and reads the
//! `extracted` and `skipped` lists it returns; the Rust loop is `hp`'s own `extract`, so every
//! leaf here spawns the binary and reads the line it printed instead. Two of Python's leaves
//! have no twin here: there is no second entry point to compare the CLI against, and a Rust
//! `const` cannot be monkeypatched, so nothing can bump the extractor version under a store.
//!
//! What `hp view` refuses to start on is `cli.rs`.

use std::path::Path;
use std::process::Command;

use hyphae_extract::sessions::encode_project_path;
use hyphae_store::Store;
use hyphae_testsupport::corpus::repo;
use hyphae_testsupport::landmarks::{SECRET, TEAMMATE, TEAMMATE_RUN};

mod common;

use common::{HP, run};

/// A project nothing was ever recorded for is a typo far more often than an empty corpus, so
/// it is an error naming both paths rather than a run that extracts nothing.
#[test]
fn extract_over_a_root_with_no_sessions_names_the_directory() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let root = scratch.path().join("projects");
    let project = scratch.path().join("repo");
    std::fs::create_dir_all(&root).expect("the projects root is writable");
    std::fs::create_dir_all(&project).expect("the project directory is writable");

    let (ok, said) = run(&[
        "extract".as_ref(),
        project.as_os_str(),
        "--projects-root".as_ref(),
        root.as_os_str(),
        "--db".as_ref(),
        scratch.path().join("traces.duckdb").as_os_str(),
    ]);

    assert!(!ok, "extracting nothing should fail: {said}");
    // Both halves of the answer: which project was asked for, and where hp looked for it.
    assert!(said.contains(&project.display().to_string()), "{said}");
    assert!(said.contains(&root.display().to_string()), "{said}");
}

/// A fixture directory planted as a projects root `hp extract` can be pointed at.
///
/// Returns the working directory the session claims to have run in and the root above it.
/// Claude Code names a project's directory after that working directory, so the tree has to
/// be named the way discovery will look for it. The fixture is copied whole — a session's
/// subagent transcripts sit in a directory beside its own file, and leaving them behind
/// would plant a session that delegated nothing.
fn planted_project(scratch: &Path, fixture: &str) -> (std::path::PathBuf, std::path::PathBuf) {
    let (project, root, recorded) = projects_root(scratch);
    copy_tree(&repo().join("tests/fixtures").join(fixture), &recorded);
    // The README naming the fixture's source session is documentation, and a file the walk
    // would refuse to place.
    std::fs::remove_file(recorded.join("README.md")).expect("every fixture has a README");
    (project, root)
}

/// An empty projects root: the working directory a planted session claims to have run in,
/// the root above it, and the directory the transcripts go in.
fn projects_root(scratch: &Path) -> (std::path::PathBuf, std::path::PathBuf, std::path::PathBuf) {
    let project = scratch.join("repo");
    std::fs::create_dir_all(&project).expect("the project directory is writable");
    let root = scratch.join("projects");
    let recorded = root.join(encode_project_path(&project));
    std::fs::create_dir_all(&recorded).expect("the session directory is writable");
    (project, root, recorded)
}

/// Copy a directory's whole contents into `into`, which must exist.
fn copy_tree(from: &Path, into: &Path) {
    for entry in std::fs::read_dir(from).expect("the directory is readable") {
        let source = entry.expect("the entry is readable").path();
        let target = into.join(source.file_name().expect("a path has a name"));
        if source.is_dir() {
            std::fs::create_dir_all(&target).expect("the directory is writable");
            copy_tree(&source, &target);
        } else {
            std::fs::copy(&source, &target).expect("the fixture copies");
        }
    }
}

/// The fingerprint's whole purpose, at the level a person sees it: a second run over an
/// untouched tree reads nothing again.
#[test]
fn extract_run_twice_re_extracts_nothing() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let db = scratch.path().join("traces.duckdb");
    let (project, root) = planted_project(scratch.path(), "spine");

    let extract: Vec<&std::ffi::OsStr> = vec![
        "extract".as_ref(),
        project.as_os_str(),
        "--projects-root".as_ref(),
        root.as_os_str(),
        "--db".as_ref(),
        db.as_os_str(),
    ];
    let first = Command::new(HP).args(&extract).output().expect("hp runs");
    assert!(
        first.status.success(),
        "{}",
        String::from_utf8_lossy(&first.stderr)
    );
    assert!(
        String::from_utf8_lossy(&first.stdout).starts_with("1 session(s) extracted, 0 unchanged"),
        "{}",
        String::from_utf8_lossy(&first.stdout)
    );
    let stamped = extracted_at(&db);

    let second = Command::new(HP).args(&extract).output().expect("hp runs");
    assert!(
        second.status.success(),
        "{}",
        String::from_utf8_lossy(&second.stderr)
    );
    // Nothing changed on disk, so nothing is read again...
    assert!(
        String::from_utf8_lossy(&second.stdout).starts_with("0 session(s) extracted, 1 unchanged"),
        "{}",
        String::from_utf8_lossy(&second.stdout)
    );
    // ...and the row still says when the first run wrote it, which is what "unchanged" means.
    assert_eq!(extracted_at(&db), stamped);
}

/// Every session's `extracted_at`, by session id.
fn extracted_at(db: &Path) -> Vec<(String, chrono::DateTime<chrono::Utc>)> {
    let store = Store::open_read_only(db).expect("the store opens");
    store
        .fetch(
            "SELECT session_id, extracted_at FROM extract_state ORDER BY session_id",
            &[],
        )
        .expect("extract_state is readable")
        .iter()
        .map(|row| {
            (
                row.str("session_id")
                    .expect("a row names its session")
                    .to_owned(),
                row.timestamp("extracted_at").expect("a row is stamped"),
            )
        })
        .collect()
}

/// A run with no spawning tool call is announced rather than dropped.
///
/// The rows it produces are asserted in `hyphae-extract`'s
/// `a_teammate_run_is_an_orphan_and_says_so`; the announcement goes to stderr, which only a
/// spawned process can read. Dropping such a run silently would hide a whole delegated
/// workload — the prior importer reported 100% direct tool calls that way.
#[test]
fn an_orphan_agent_run_is_announced_on_stderr() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let db = scratch.path().join("traces.duckdb");
    // If a session ran a long-lived teammate, whose meta names no spawning call...
    let (project, root) = planted_project(scratch.path(), "teammate");

    let done = Command::new(HP)
        .args([
            "extract".as_ref(),
            project.as_os_str(),
            "--projects-root".as_ref(),
            root.as_os_str(),
            "--db".as_ref(),
            db.as_os_str(),
        ])
        .output()
        .expect("hp runs");

    // ...then the extraction succeeds, and says which run in which session had no call
    // behind it, so the gap can be looked up rather than guessed at.
    let said = String::from_utf8_lossy(&done.stderr);
    assert!(done.status.success(), "{said}");
    assert!(said.contains(TEAMMATE_RUN), "{said}");
    assert!(said.contains(TEAMMATE), "{said}");
}

/// A file Claude Code is still appending to costs its last line and nothing else.
///
/// The rows that survive are asserted in `hyphae-extract`'s
/// `a_transcript_still_being_written_drops_only_its_last_line`. What only a spawned process
/// can read is the warning, which has to name the line without quoting what the line held —
/// a transcript can hold anything the agent read.
#[test]
fn a_truncated_tail_is_a_warning_that_quotes_nothing() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let db = scratch.path().join("traces.duckdb");
    // Invented, and unavoidably so: a recorded fixture cannot hold a half-written line and
    // stay a recording. The broken line carries a tripwire the warning must not repeat.
    let (project, root, recorded) = projects_root(scratch.path());
    let stem = "invented-truncated-tail";
    let from = repo()
        .join("tests/fixtures/invented")
        .join(format!("{stem}.jsonl"));
    std::fs::copy(&from, recorded.join(format!("{stem}.jsonl"))).expect("the fixture copies");
    // The broken line cuts off mid-payload, so the tripwire is only half there. The end of
    // what it did write stands for the record's content: a warning quoting any of the line
    // would carry it.
    let text = std::fs::read_to_string(&from).expect("the fixture is readable");
    let written = text
        .lines()
        .next_back()
        .expect("the fixture ends on the broken line")
        .to_owned();
    let quoted = &written[written.len() - 24..];

    let done = Command::new(HP)
        .args([
            "extract".as_ref(),
            project.as_os_str(),
            "--projects-root".as_ref(),
            root.as_os_str(),
            "--db".as_ref(),
            db.as_os_str(),
        ])
        .output()
        .expect("hp runs");

    let said = String::from_utf8_lossy(&done.stderr);
    assert!(done.status.success(), "{said}");
    assert!(said.contains("dropped an incomplete final line"), "{said}");
    assert!(said.contains("(3)"), "the warning names the line: {said}");
    assert!(!said.contains(SECRET), "{said}");
    assert!(!said.contains(quoted), "{said}");
}
