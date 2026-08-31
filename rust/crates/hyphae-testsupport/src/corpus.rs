//! The recorded fixture corpus, and the extractor pointed at it.
//!
//! No leaf anywhere reads the canonical store at `data/traces.duckdb`. Session data is
//! private (`CLAUDE.md`), and the redacted excerpts under `tests/fixtures/` carry every shape
//! the tiers below need — so the corpus these tests query is one nobody has to be careful
//! with.

use std::path::{Path, PathBuf};

use hyphae_extract::sessions::SessionFiles;
use hyphae_extract::{Extractor, SessionSource};
use hyphae_model::SessionTrace;

/// The two `invented/` transcripts that export cleanly. The other six carry unknown record
/// shapes and crash by design, which is what `hyphae-extract`'s walk tier proves.
pub const CLEAN_INVENTED: &[&str] = &["invented-no-cache-creation", "invented-truncated-tail"];

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

/// Extract one fixture, panicking with the schema error when it does not parse.
pub fn trace(directory: &str, stem: &str) -> SessionTrace {
    extractor()
        .extract(&fixture_source(directory, stem))
        .expect("the fixture parses")
}
