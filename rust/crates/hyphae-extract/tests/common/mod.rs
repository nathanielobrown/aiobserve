#![allow(dead_code)] // Each test binary uses a different part of this.

//! Pointing the extractor at a recorded fixture, the way `tests/conftest.py` does.
//!
//! Discovery is not what these tests are about, so a source is built straight from a
//! transcript path rather than found under a projects root — the same shortcut
//! `conftest.py:fixture_source` takes, and the reason the fingerprint here is a placeholder:
//! `extract` never reads it.

use std::path::{Path, PathBuf};

use hyphae_extract::sessions::SessionFiles;
use hyphae_extract::{Extractor, SessionSource};
use hyphae_model::SessionTrace;

/// `tests/fixtures/` in the repo, from this crate's own location.
pub fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures")
        .canonicalize()
        .expect("the fixture corpus sits at tests/fixtures/")
}

/// One fixture session as discovery would have handed it over.
pub fn source(directory: &str, stem: &str) -> SessionSource {
    let transcript = fixtures().join(directory).join(format!("{stem}.jsonl"));
    from_transcript(&transcript)
}

/// The same, for a transcript copied somewhere else — a tempdir, usually.
pub fn from_transcript(transcript: &Path) -> SessionSource {
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
        fingerprint: "fixture-fingerprint".to_owned(),
    }
}

/// Extract one fixture, panicking with the schema error when it does not parse.
pub fn trace(directory: &str, stem: &str) -> SessionTrace {
    extractor()
        .extract(&source(directory, stem))
        .expect("the fixture parses")
}

/// An extractor rooted at the fixture corpus. Only `extract` is exercised through it; the
/// root matters solely to `sessions`.
pub fn extractor() -> Extractor {
    Extractor::new(fixtures())
}
