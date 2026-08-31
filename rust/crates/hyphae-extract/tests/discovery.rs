//! Discovery: which sessions a project has, and where each one's files are.
//!
//! The port of `tests/test_sessions.py`, over `hyphae_extract::sessions`. Nothing here reads a
//! record — the directory layout is the subject, and it rots on a different schedule from the
//! record shapes. The last leaf is the exception: the path `hp extract` really takes over a
//! recorded fixture, files and fingerprint together.

use std::path::{Path, PathBuf};

use hyphae_testsupport::corpus;
use hyphae_testsupport::landmarks::MYCELIA;

use hyphae_extract::ExtractError;
use hyphae_extract::sessions::{SessionFiles, encode_project_path, find_sessions, resolve_project};
use hyphae_extract::{SessionSource, sessions};

/// The on-disk shape Claude Code writes, as observed under `~/.claude/projects`: a transcript
/// per session under a directory named for the project. Content does not matter here.
fn projects_root(scratch: &Path, session_ids: &[&str]) -> PathBuf {
    let root = scratch.join("projects");
    let project_dir = root.join(encode_project_path(Path::new(MYCELIA)));
    std::fs::create_dir_all(&project_dir).expect("the scratch tree is writable");
    for id in session_ids {
        std::fs::write(
            project_dir.join(format!("{id}{}", sessions::TRANSCRIPT_SUFFIX)),
            "",
        )
        .expect("a transcript is writable");
    }
    root
}

/// A project's directory name is its absolute path with each separator turned into a dash.
#[test]
fn a_projects_directory_is_its_path_with_the_separators_dashed() {
    // Spelled out rather than derived from the landmark: deriving it would restate the
    // implementation and pass however the encoding changed.
    assert_eq!(
        encode_project_path(Path::new(MYCELIA)),
        "-Users-nob-repos-mycelia"
    );
}

/// A relative path is resolved first — the directory is named after the absolute one.
#[test]
fn a_relative_project_is_encoded_from_its_absolute_path() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    // If the caller passes a path relative to the working directory...
    std::fs::create_dir(scratch.path().join("myproject")).expect("the project directory");
    std::env::set_current_dir(scratch.path()).expect("the scratch tree is a working directory");

    // ...then the encoding is of the absolute path, so it matches what is on disk.
    let encoded = encode_project_path(Path::new("myproject"));
    assert_eq!(
        encoded,
        encode_project_path(&scratch.path().join("myproject"))
    );
    assert!(encoded.starts_with('-'), "{encoded}");
}

/// A quoted `~/repos/x` selects the same repository the unquoted spelling does.
#[test]
fn a_home_relative_project_names_what_the_shell_would_have_expanded() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let project = scratch.path().join("repos").join("mycelia");
    std::fs::create_dir_all(&project).expect("the project directory");
    // If `~` reaches us unexpanded — a quoted argument, or one read out of a config file...
    // Safe here because nextest gives each test its own process.
    unsafe { std::env::set_var("HOME", scratch.path()) };

    // ...then it names the home directory, not a directory called `~` under the working one.
    assert_eq!(
        resolve_project(Path::new("~/repos/mycelia")),
        std::fs::canonicalize(&project).expect("the project is on disk")
    );
}

/// A path typed in the wrong case still names the directory Claude Code recorded.
#[test]
fn a_project_typed_in_the_wrong_case_resolves_to_the_recorded_spelling() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    // If a project sits on disk under `repos/MyProject`...
    let project = scratch.path().join("repos").join("MyProject");
    std::fs::create_dir_all(&project).expect("the project directory");

    // ...then typing it as `REPOS/myproject` still resolves to the real spelling, since a
    // string comparison against the recorded `cwd` would otherwise match nothing.
    assert_eq!(
        resolve_project(&scratch.path().join("REPOS").join("myproject")),
        std::fs::canonicalize(&project).expect("the project is on disk")
    );
}

/// Every session transcript in a project's directory is discovered, sorted by id.
#[test]
fn every_transcript_in_a_projects_directory_is_a_session() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    // If a project has three recorded sessions...
    let root = projects_root(scratch.path(), &["c-third", "a-first", "b-second"]);

    // ...then all three come back, in a stable order...
    let found = find_sessions(Path::new(MYCELIA), &root).expect("the project has a directory");
    assert_eq!(
        found.iter().map(|s| s.id.as_str()).collect::<Vec<&str>>(),
        ["a-first", "b-second", "c-third"]
    );
    // ...each carrying the path to its own transcript.
    assert_eq!(found[0].id, "a-first");
    assert_eq!(
        found[0].transcript,
        root.join("-Users-nob-repos-mycelia").join("a-first.jsonl")
    );
}

/// Only `.jsonl` files count as sessions — the tree also holds metadata and scratch.
#[test]
fn only_a_jsonl_file_is_a_session() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let root = projects_root(scratch.path(), &["real-session"]);
    let project_dir = root.join("-Users-nob-repos-mycelia");
    std::fs::write(project_dir.join("notes.md"), "").expect("scratch beside the transcripts");
    std::fs::write(project_dir.join("agent-abc.meta.json"), "").expect("a stray meta");

    let found = find_sessions(Path::new(MYCELIA), &root).expect("the project has a directory");
    assert_eq!(
        found.iter().map(|s| s.id.as_str()).collect::<Vec<&str>>(),
        ["real-session"]
    );
}

/// A subagent run belongs to its session, so it is never returned as a session of its own.
///
/// Both nestings at once: a fan-out puts its agents a level deeper, under a `wf_` directory,
/// and the walk that collects a session's files is recursive, so one leaf covers both.
#[test]
fn a_subagent_transcript_hangs_off_its_session_rather_than_being_one() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    // If a session spawned two subagents directly and one inside a workflow...
    let root = projects_root(scratch.path(), &["parent-session"]);
    let subagents = root
        .join("-Users-nob-repos-mycelia")
        .join("parent-session")
        .join(sessions::SUBAGENTS_DIR);
    let workflow = subagents.join(sessions::WORKFLOWS_DIR).join("wf_1");
    std::fs::create_dir_all(&workflow).expect("the subagent tree is writable");
    for path in [
        subagents.join("agent-aaa.jsonl"),
        subagents.join("agent-bbb.jsonl"),
        workflow.join("agent-ccc.jsonl"),
    ] {
        std::fs::write(&path, "").expect("a subagent transcript");
    }

    // ...then only the parent is a session...
    let found = find_sessions(Path::new(MYCELIA), &root).expect("the project has a directory");
    assert_eq!(
        found.iter().map(|s| s.id.as_str()).collect::<Vec<&str>>(),
        ["parent-session"]
    );
    // ...and all three transcripts hang off it, however deep, after its own.
    assert_eq!(
        found[0].files().expect("the session's files are readable"),
        [
            found[0].transcript.clone(),
            subagents.join("agent-aaa.jsonl"),
            subagents.join("agent-bbb.jsonl"),
            workflow.join("agent-ccc.jsonl"),
        ]
    );
}

/// A session that spawned no subagents reports its own transcript, not an error.
#[test]
fn a_session_that_spawned_nothing_has_only_its_transcript() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let root = projects_root(scratch.path(), &["solo-session"]);

    let found = find_sessions(Path::new(MYCELIA), &root).expect("the project has a directory");
    assert_eq!(
        found[0].files().expect("the session's files are readable"),
        [found[0].transcript.clone()]
    );
}

/// An unknown project is a mistake to surface, not an empty result to quietly return.
#[test]
fn a_project_with_nothing_recorded_is_refused() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    // If the project has never been opened in Claude Code, no directory exists for it...
    let root = scratch.path().join("projects");
    std::fs::create_dir(&root).expect("the projects root is writable");

    // ...so the caller hears about it, with both the path they asked for and the one we
    // looked in.
    let error = find_sessions(Path::new("/Users/nob/repos/nonexistent"), &root)
        .expect_err("a project with no directory is refused");
    let ExtractError::Schema(message) = &error else {
        panic!("expected a schema error, got {error:?}");
    };
    assert!(
        message.contains("-Users-nob-repos-nonexistent"),
        "{message}"
    );
    assert!(message.contains(&root.display().to_string()), "{message}");
}

/// Discovery over a recording: the files `hp extract` reads, and the fingerprint over them.
#[test]
fn discovery_finds_a_projects_sessions_with_fingerprints() {
    // The fixture directories are not encoded project paths, so this points the extractor at
    // one directly: what is under test is `files()` and the digest, not the path encoding.
    let transcript = corpus::fixtures()
        .join("spine")
        .join("4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b.jsonl");
    let session = SessionFiles {
        id: "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b".to_owned(),
        transcript: transcript.clone(),
    };
    let files = session.files().expect("the session's files are readable");
    // The transcript first, then everything under its sibling directory.
    assert_eq!(files[0], transcript);
    assert!(
        files.len() > 1,
        "spine records subagent transcripts beside it"
    );
    let source: SessionSource = corpus::source(&transcript);
    assert_eq!(source.files, files);
}
