//! `hp extract` at the process level: the refresh loop, run over a planted projects root.
//!
//! The port of `tests/test_pipeline.py`. Python drives `refresh()` directly and reads the
//! `extracted` and `skipped` lists it returns; the Rust loop is `hp`'s own `extract`, so every
//! leaf here spawns the binary and reads the line it printed instead. Two of Python's leaves
//! have no twin here: there is no second entry point to compare the CLI against, and a Rust
//! `const` cannot be monkeypatched, so nothing can bump the extractor version under a store.
//!
//! What `hp view` refuses to start on is `cli.rs`.

use std::path::{Path, PathBuf};
use std::process::Command;

use hyphae_extract::Extractor;
use hyphae_extract::sessions::{AGENT_PREFIX, META_SUFFIX, SUBAGENTS_DIR, encode_project_path};
use hyphae_store::Store;
use hyphae_testsupport::corpus::repo;
use hyphae_testsupport::landmarks::{
    CONFIG_ONLY, DUP_UUID, OFFLOAD_FILE, SECRET, SPINE, SPINE_LEAF, TEAMMATE, TEAMMATE_RUN,
};

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

/// A Claude Code projects root on disk, the project that addresses it, and the store an
/// extract writes to. The twin of `tests/test_pipeline.py:Corpus`.
///
/// Claude Code names a project's directory after the working directory a session ran in, so
/// the tree has to be named the way discovery will look for it.
struct Corpus {
    project: PathBuf,
    root: PathBuf,
    /// Where the transcripts go: the encoded project directory under the root.
    recorded: PathBuf,
    db: PathBuf,
}

impl Corpus {
    fn new(scratch: &Path) -> Self {
        let project = scratch.join("repo");
        std::fs::create_dir_all(&project).expect("the project directory is writable");
        let root = scratch.join("projects");
        let recorded = root.join(encode_project_path(&project));
        std::fs::create_dir_all(&recorded).expect("the session directory is writable");
        Self {
            project,
            root,
            recorded,
            db: scratch.join("traces.duckdb"),
        }
    }

    /// Plant one recorded session, keeping only its first `lines` records when asked.
    ///
    /// The session's own directory comes too where the fixture has one, so the corpus holds
    /// subagent transcripts and offloaded results as Claude Code wrote them. Returns the
    /// planted transcript.
    fn add(&self, fixture: &str, stem: &str, lines: Option<usize>) -> PathBuf {
        let from = repo().join("tests/fixtures").join(fixture);
        let recorded = std::fs::read_to_string(from.join(format!("{stem}.jsonl")))
            .expect("the fixture is readable");
        let text = match lines {
            None => recorded,
            Some(kept) => {
                recorded
                    .lines()
                    .take(kept)
                    .collect::<Vec<&str>>()
                    .join("\n")
                    + "\n"
            }
        };
        let transcript = self.recorded.join(format!("{stem}.jsonl"));
        std::fs::write(&transcript, text).expect("the transcript is writable");
        if from.join(stem).is_dir() {
            let into = self.recorded.join(stem);
            std::fs::create_dir_all(&into).expect("the session directory is writable");
            copy_tree(&from.join(stem), &into);
        }
        transcript
    }

    /// `hp extract` over this corpus into `db`: whether it succeeded, and what it said on
    /// stdout and on stderr.
    fn extract_into(&self, db: &Path) -> (bool, String, String) {
        let done = Command::new(HP)
            .args([
                "extract".as_ref(),
                self.project.as_os_str(),
                "--projects-root".as_ref(),
                self.root.as_os_str(),
                "--db".as_ref(),
                db.as_os_str(),
            ])
            .output()
            .expect("hp runs");
        (
            done.status.success(),
            String::from_utf8_lossy(&done.stdout).into_owned(),
            String::from_utf8_lossy(&done.stderr).into_owned(),
        )
    }

    /// One extract into this corpus's own store, which must succeed: the line it printed.
    ///
    /// That line is what Python reads off `refresh()`'s `extracted` and `skipped` lists.
    fn extract(&self) -> String {
        let (ok, said, complained) = self.extract_into(&self.db);
        assert!(ok, "{complained}");
        said
    }

    /// The same run, read for what it warned about on the way rather than what it counted.
    fn warnings(&self) -> String {
        let (ok, _, complained) = self.extract_into(&self.db);
        assert!(ok, "{complained}");
        complained
    }
}

/// One session's rows of one table, every column, in an order both stores agree on.
///
/// Debug-formatted rather than compared as values: the point is that two stores hold the same
/// rows, and a mismatch prints as a diff of the two lines.
fn table(db: &Path, name: &str, session: &str) -> Vec<String> {
    let key = if name == "sessions" {
        "id"
    } else {
        "session_id"
    };
    let store = Store::open_read_only(db).expect("the store opens");
    store
        .fetch(
            &format!("SELECT * FROM {name} WHERE {key} = $id ORDER BY 1, 2, 3"),
            &[("id", session.into())],
        )
        .expect("the table reads")
        .iter()
        .map(|row| format!("{:?}", row.values()))
        .collect()
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
    let corpus = Corpus::new(scratch.path());
    corpus.add("spine", SPINE, None);

    let first = corpus.extract();
    assert!(
        first.starts_with("1 session(s) extracted, 0 unchanged"),
        "{first}"
    );
    let stamped = extracted_at(&corpus.db);

    // Nothing changed on disk, so nothing is read again...
    let second = corpus.extract();
    assert!(
        second.starts_with("0 session(s) extracted, 1 unchanged"),
        "{second}"
    );
    // ...and the row still says when the first run wrote it, which is what "unchanged" means.
    assert_eq!(extracted_at(&corpus.db), stamped);
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
    let corpus = Corpus::new(scratch.path());
    // If a session ran a long-lived teammate, whose meta names no spawning call...
    corpus.add("teammate", TEAMMATE, None);

    // ...then the extraction succeeds, and says which run in which session had no call
    // behind it, so the gap can be looked up rather than guessed at.
    let said = corpus.warnings();
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
    let corpus = Corpus::new(scratch.path());
    // Invented, and unavoidably so: a recorded fixture cannot hold a half-written line and
    // stay a recording. The broken line carries a tripwire the warning must not repeat.
    let stem = "invented-truncated-tail";
    let planted = corpus.add("invented", stem, None);
    // The broken line cuts off mid-payload, so the tripwire is only half there. The end of
    // what it did write stands for the record's content: a warning quoting any of the line
    // would carry it.
    let text = std::fs::read_to_string(&planted).expect("the fixture is readable");
    let written = text
        .lines()
        .next_back()
        .expect("the fixture ends on the broken line")
        .to_owned();
    let quoted = &written[written.len() - 24..];

    let said = corpus.warnings();
    assert!(said.contains("dropped an incomplete final line"), "{said}");
    assert!(said.contains("(3)"), "the warning names the line: {said}");
    assert!(!said.contains(SECRET), "{said}");
    assert!(!said.contains(quoted), "{said}");
}

/// An extract walks a project's sessions and writes each one into the store.
#[test]
fn an_extract_ingests_every_session_it_finds() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let corpus = Corpus::new(scratch.path());
    // If a project has two recorded sessions...
    corpus.add("spine", SPINE, None);
    corpus.add("dup_uuid", DUP_UUID, None);

    let said = corpus.extract();

    // ...then both are extracted, and each table holds what the extractor produced for them.
    assert!(
        said.starts_with("2 session(s) extracted, 0 unchanged"),
        "{said}"
    );
    let extractor = Extractor::new(corpus.root.clone());
    for source in extractor
        .sessions(&corpus.project)
        .expect("the project has sessions")
    {
        let trace = extractor.extract(&source).expect("the session parses");
        for (name, held) in [
            ("turns", trace.turns.len()),
            ("api_calls", trace.api_calls.len()),
            ("raw_records", trace.raw_records.len()),
        ] {
            assert_eq!(table(&corpus.db, name, &source.id).len(), held, "{name}");
        }
    }
}

/// Resuming a session and extracting again gives the same rows as extracting it fresh.
///
/// A session grows by appending, and the naive fix — insert only the new lines — leaves the
/// session's own metadata frozen at its first extract. Comparing against a store built from
/// scratch over the grown file catches that, where a row count would not.
#[test]
fn a_grown_session_is_replaced_rather_than_appended() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let corpus = Corpus::new(scratch.path());
    // If a session was extracted while it was still short...
    corpus.add("spine", SPINE, Some(22));
    corpus.extract();
    // Three turns of its own, plus the two its subagent's transcript holds.
    assert_eq!(table(&corpus.db, "turns", SPINE).len(), 5);

    // ...and then it resumed, growing by thirteen more records...
    corpus.add("spine", SPINE, None);
    corpus.extract();

    // ...then the store matches one built from scratch over the grown file, table for table.
    let fresh = scratch.path().join("fresh.duckdb");
    let (ok, _, complained) = corpus.extract_into(&fresh);
    assert!(ok, "{complained}");
    for name in ["sessions", "turns", "api_calls", "raw_records"] {
        assert_eq!(
            table(&corpus.db, name, SPINE),
            table(&fresh, name, SPINE),
            "{name}"
        );
    }
    assert_eq!(table(&corpus.db, "turns", SPINE).len(), 6);
}

/// Extracting a live session keeps the complete records and picks up the rest later.
///
/// Claude Code appends to the transcript of a session that is still running, so an extract on
/// a timer will sooner or later read a line that stops mid-JSON. Refusing the file would
/// leave the session unextracted for as long as it stays open.
#[test]
fn a_session_caught_mid_write_heals_on_the_next_extract() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let corpus = Corpus::new(scratch.path());
    // If an extract catches a transcript with a record only half written...
    let transcript = corpus.add("spine", SPINE, Some(22));
    let whole = std::fs::read_to_string(
        repo()
            .join("tests/fixtures/spine")
            .join(format!("{SPINE}.jsonl")),
    )
    .expect("the fixture is readable");
    let half: String = whole
        .lines()
        .nth(22)
        .expect("the fixture has a 23rd record")
        .chars()
        .take(60)
        .collect();
    let kept = std::fs::read_to_string(&transcript).expect("the planted transcript is readable");
    std::fs::write(&transcript, kept + &half).expect("the transcript is writable");
    corpus.extract();

    // ...then the records before it are stored and the half one is not...
    assert_eq!(archived(&corpus.db), 22);

    // ...and once Claude Code has finished the line, the next extract takes it whole.
    corpus.add("spine", SPINE, None);
    let said = corpus.extract();
    assert!(said.starts_with("1 session(s) extracted"), "{said}");
    assert_eq!(archived(&corpus.db), 41);
}

/// How many of the spine session's own records reached the archive.
fn archived(db: &Path) -> i64 {
    let store = Store::open_read_only(db).expect("the store opens");
    store
        .fetch(
            "SELECT count(*) AS lines FROM raw_records WHERE session_id = $id AND source = 'main'",
            &[("id", SPINE.into())],
        )
        .expect("the archive reads")[0]
        .i64("lines")
        .expect("a count is a number")
}

/// A session whose subagent wrote a transcript is stale, though its own file never changed.
///
/// The fingerprint covers every file under the session directory for exactly this case: a
/// subagent, a workflow journal, or an offloaded tool result can all change while the main
/// transcript's size and mtime stand still.
#[test]
fn a_new_subagent_file_re_extracts_its_session() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let corpus = Corpus::new(scratch.path());
    let transcript = corpus.add("spine", SPINE, None);
    corpus.extract();
    let before = fingerprints(&corpus.db);
    let stat = transcript.metadata().expect("the transcript is on disk");
    let unchanged = (stat.len(), stat.modified().expect("an mtime"));

    // If another subagent transcript appears beside an untouched main transcript — with the
    // `meta.json` Claude Code always writes with it, here a recorded one under the new name...
    let subagents = corpus.recorded.join(SPINE).join(SUBAGENTS_DIR);
    let planted = "a1d0bc50fe316ed8e";
    std::fs::copy(
        repo()
            .join("tests/fixtures/dup_uuid")
            .join(format!("{DUP_UUID}.jsonl")),
        subagents.join(format!("{AGENT_PREFIX}{planted}.jsonl")),
    )
    .expect("the transcript copies");
    std::fs::copy(
        subagents.join(format!("{AGENT_PREFIX}{SPINE_LEAF}{META_SUFFIX}")),
        subagents.join(format!("{AGENT_PREFIX}{planted}{META_SUFFIX}")),
    )
    .expect("the meta copies");
    let said = corpus.extract();

    // ...then the session is re-extracted under a new fingerprint.
    let stat = transcript.metadata().expect("the transcript is on disk");
    assert_eq!((stat.len(), stat.modified().expect("an mtime")), unchanged);
    assert!(
        said.starts_with("1 session(s) extracted, 0 unchanged"),
        "{said}"
    );
    assert_ne!(fingerprints(&corpus.db), before);
}

/// Rewriting an offloaded tool result re-extracts the session and re-archives the file.
#[test]
fn a_changed_offload_file_re_extracts_its_session() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let corpus = Corpus::new(scratch.path());
    corpus.add("offload", CONFIG_ONLY, None);
    corpus.extract();
    let offloaded = corpus
        .recorded
        .join(CONFIG_ONLY)
        .join("tool-results")
        .join(OFFLOAD_FILE);

    // If the file holding a tool's output changes while the transcript stands still...
    let rewritten = "[redacted] — a shorter output than before";
    std::fs::write(&offloaded, rewritten).expect("the offloaded file is writable");
    let said = corpus.extract();

    // ...then the session is parsed again, and the store holds the file as it now reads.
    assert!(
        said.starts_with("1 session(s) extracted, 0 unchanged"),
        "{said}"
    );
    let store = Store::open_read_only(&corpus.db).expect("the store opens");
    let rows = store
        .fetch("SELECT content, size_bytes FROM offload_files", &[])
        .expect("the archive reads");
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].str("content").expect("content reads"), rewritten);
    assert_eq!(
        rows[0].i64("size_bytes").expect("a size reads"),
        rewritten.len() as i64
    );
}

/// Claude Code deletes transcripts after a few weeks; the store is the archive.
///
/// An extract only ever adds and replaces. A session whose file is gone stops being
/// discovered, and its rows stay exactly as its last extract left them.
#[test]
fn a_pruned_session_keeps_its_rows() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let corpus = Corpus::new(scratch.path());
    corpus.add("spine", SPINE, None);
    let transcript = corpus.add("dup_uuid", DUP_UUID, None);
    corpus.extract();
    let before = table(&corpus.db, "raw_records", DUP_UUID);
    assert!(!before.is_empty(), "the pruned session was extracted first");

    // If one session's transcript is pruned from disk...
    std::fs::remove_file(&transcript).expect("the transcript is removable");
    let said = corpus.extract();

    // ...then discovery no longer sees it, and its rows survive untouched.
    assert!(
        said.starts_with("0 session(s) extracted, 1 unchanged"),
        "{said}"
    );
    assert_eq!(table(&corpus.db, "raw_records", DUP_UUID), before);
}

/// Every session's fingerprint, as the store holds it.
fn fingerprints(db: &Path) -> std::collections::HashMap<String, String> {
    let store = Store::open_read_only(db).expect("the store opens");
    store.fingerprints().expect("the fingerprints read")
}
