//! The recorded fixture corpus, and the extractor pointed at it.
//!
//! No leaf anywhere reads the canonical store at `data/traces.duckdb`. Session data is
//! private (`CLAUDE.md`), and the redacted excerpts under `tests/fixtures/` carry every shape
//! the tiers below need — so the corpus these tests query is one nobody has to be careful
//! with.

use std::path::{Path, PathBuf};

use chrono::{DateTime, NaiveDateTime, Utc};
use hyphae_extract::sessions::SessionFiles;
use hyphae_extract::{Extractor, SessionSource};
use hyphae_model::SessionTrace;

/// The two `invented/` transcripts that export cleanly. The other six carry unknown record
/// shapes and crash by design, which is what `hyphae-extract`'s walk tier proves.
pub const CLEAN_INVENTED: &[&str] = &["invented-no-cache-creation", "invented-truncated-tail"];

/// A transcript timestamp, as the extractor parses it: naive ISO 8601, read as UTC.
///
/// The twin of `tests/extract/test_claude_code.py:at`. A fixture writes
/// `2026-08-06T10:44:33.136Z`; a leaf naming that instant drops the zone, as Python's does.
///
/// # Panics
/// When `moment` is not a naive ISO 8601 instant with optional fractional seconds.
pub fn at(moment: &str) -> DateTime<Utc> {
    NaiveDateTime::parse_from_str(moment, "%Y-%m-%dT%H:%M:%S%.f")
        .expect("a naive ISO 8601 instant")
        .and_utc()
}

/// The repository root, from this crate's own location.
pub fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("the crate sits three levels under the repository root")
}

/// `tests/fixtures/` in the repo.
pub fn fixtures() -> PathBuf {
    repo().join("tests/fixtures")
}

/// An extractor rooted at the fixture corpus. Only `extract` is exercised through it; the
/// root matters solely to `sessions`.
pub fn extractor() -> Extractor {
    Extractor::new(fixtures())
}

/// Every fixture transcript that exports cleanly, discovered rather than listed — the twin of
/// `tests/conftest.py:corpus_transcripts`.
pub fn corpus_transcripts() -> Vec<PathBuf> {
    let mut directories: Vec<PathBuf> = std::fs::read_dir(fixtures())
        .expect("the fixture corpus is readable")
        .map(|entry| entry.expect("the entry is readable").path())
        .filter(|path| path.is_dir() && path.file_name().is_some_and(|name| name != "invented"))
        .collect();
    directories.sort();
    let mut transcripts: Vec<PathBuf> = directories.iter().flat_map(|dir| jsonl(dir)).collect();
    transcripts.extend(
        CLEAN_INVENTED
            .iter()
            .map(|stem| fixtures().join("invented").join(format!("{stem}.jsonl"))),
    );
    transcripts
}

/// The whole clean corpus as discovery would have handed it over.
pub fn corpus_sources() -> Vec<SessionSource> {
    corpus_transcripts().iter().map(|at| source(at)).collect()
}

fn jsonl(directory: &Path) -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = std::fs::read_dir(directory)
        .expect("a fixture directory is readable")
        .map(|entry| entry.expect("the entry is readable").path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "jsonl")
        })
        .collect();
    found.sort();
    found
}

/// One transcript as discovery would have handed it over.
///
/// The fingerprint is a placeholder: it belongs to discovery, and `extract` never reads it.
/// Discovery is not what any of these leaves is about, so a source is built straight from a
/// transcript path rather than found under a projects root — the shortcut
/// `tests/conftest.py:fixture_source` takes.
pub fn source(transcript: &Path) -> SessionSource {
    let stem = transcript
        .file_stem()
        .expect("a transcript path ends in a file name")
        .to_string_lossy()
        .into_owned();
    let session = SessionFiles {
        id: stem.clone(),
        transcript: transcript.to_owned(),
    };
    SessionSource {
        id: stem,
        files: session.files().expect("the fixture's files are readable"),
        fingerprint: "fixture".to_owned(),
    }
}

/// The same, named by the fixture directory and session it sits in.
pub fn fixture_source(directory: &str, stem: &str) -> SessionSource {
    source(&fixtures().join(directory).join(format!("{stem}.jsonl")))
}

/// A fixture session copied into a tempdir, with extra files planted in its directory.
///
/// The twin of `tests/conftest.py:planted_source`. The transcript is the recorded one; only
/// the planted file *names* are invented, which is the point — they stand for layouts Claude
/// Code writes, or might write next. Hold the returned value for as long as the source is
/// read: dropping it takes the tempdir with it.
pub struct Planted {
    /// The tempdir the transcript was copied into; the session's own directory is under it.
    pub root: tempfile::TempDir,
    pub source: SessionSource,
}

/// Copy `directory/stem.jsonl` into a tempdir and plant `files` under the session directory.
///
/// # Panics
/// When the fixture is unreadable or the tempdir cannot be written.
pub fn planted(directory: &str, stem: &str, files: &[(&str, &[u8])]) -> Planted {
    let root = tempfile::tempdir().expect("a tempdir");
    let transcript = root.path().join(format!("{stem}.jsonl"));
    std::fs::copy(
        fixtures().join(directory).join(format!("{stem}.jsonl")),
        &transcript,
    )
    .expect("the fixture transcript is readable");
    for (relative, content) in files {
        let path = root.path().join(stem).join(relative);
        std::fs::create_dir_all(path.parent().expect("a planted file has a parent"))
            .expect("the planted directory is writable");
        std::fs::write(&path, content).expect("the planted file is writable");
    }
    let session = SessionFiles {
        id: stem.to_owned(),
        transcript,
    };
    let source = SessionSource {
        id: stem.to_owned(),
        files: session.files().expect("the planted session's files"),
        fingerprint: "planted".to_owned(),
    };
    Planted { root, source }
}

/// Extract one fixture, panicking with the schema error when it does not parse.
pub fn trace(directory: &str, stem: &str) -> SessionTrace {
    extractor()
        .extract(&fixture_source(directory, stem))
        .expect("the fixture parses")
}
