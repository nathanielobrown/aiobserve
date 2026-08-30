#![allow(dead_code)] // Each test binary uses a different part of this.

//! A viewer over a store built the way the pipeline builds one: the recorded fixture corpus,
//! extracted through the Rust path into a tempdir.
//!
//! No leaf here reads the canonical store at `data/traces.duckdb`. Session data is private
//! (`CLAUDE.md`), and the redacted excerpts under `tests/fixtures/` carry the shapes a node page
//! draws — so the corpus these tests serve is one nobody has to be careful with.
//!
//! The app is driven with `oneshot`: `Router` is a `tower::Service`, so a test hands it a request
//! and gets a response with no socket in between, the way `TestClient` drives the Python app.

use std::path::{Path, PathBuf};

use axum::Router;
use axum::body::Body;
use axum::http::{Request, Response, StatusCode};
use hyphae_extract::sessions::SessionFiles;
use hyphae_extract::{Extractor, SessionSource};
use hyphae_store::Store;
use hyphae_view::app::build_app;
use tempfile::TempDir;
use tower::ServiceExt as _;

/// The two `invented/` transcripts that export cleanly. The other six carry unknown record
/// shapes and crash by design, which is what `hyphae-extract`'s walk tier proves.
const CLEAN_INVENTED: &[&str] = &["invented-no-cache-creation", "invented-truncated-tail"];

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

/// Every fixture transcript that exports cleanly, discovered rather than listed — the twin of
/// `tests/conftest.py:corpus_transcripts`.
fn corpus_transcripts() -> Vec<PathBuf> {
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

/// A store in a tempdir holding the whole clean fixture corpus, plus whatever `plant` writes
/// over it afterwards.
///
/// The `TempDir` stays alive in the returned [`Served`]: dropping it deletes the file the viewer
/// reads. `plant` is the hook for a test that needs text no recorded fixture carries — a
/// `<script>` never reached one of these transcripts, so it has to be written in.
pub fn served(plant: impl Fn(&Store)) -> Served {
    let scratch = TempDir::new().expect("a tempdir for the store");
    let path = scratch.path().join("traces.duckdb");
    corpus(&path, plant);
    let app = build_app(&path).expect("the viewer opens the store it just wrote");
    Served { scratch, path, app }
}

/// The same corpus with the enrichment rows a pass would have written.
///
/// No Rust code writes one: the enrichment schema and its views belong to the Python pass, so
/// the store this serves is built by calling `tests/conftest.py:build_enriched_store` over the
/// store the Rust extractor just wrote. It plants a row on all but the last item of each level,
/// which is the partly-enriched shape a page has to render. `plant` then writes over that store,
/// for a test that needs a row to say something the planted ones do not.
pub fn enriched(plant: impl Fn(&Store)) -> Served {
    let scratch = TempDir::new().expect("a tempdir for the store");
    let path = scratch.path().join("traces.duckdb");
    corpus(&path, |_| ());
    let enriched = scratch.path().join("enriched.duckdb");
    let script = format!(
        "import sys; sys.path.insert(0, {repo:?}); \
         from tests.conftest import build_enriched_store; \
         build_enriched_store(__import__('pathlib').Path({enriched:?}), \
         corpus=__import__('pathlib').Path({corpus:?}))",
        repo = repo().to_string_lossy(),
        enriched = enriched.to_string_lossy(),
        corpus = path.to_string_lossy(),
    );
    let done = std::process::Command::new("uv")
        .args(["run", "--project"])
        .arg(repo())
        .args(["python", "-c", &script])
        .current_dir(repo())
        .output()
        .expect("uv runs the enrichment pass that owns the schema");
    assert!(
        done.status.success(),
        "the enrichment pass failed: {}",
        String::from_utf8_lossy(&done.stderr)
    );
    {
        let store = Store::create(&enriched).expect("the enriched store opens for writing");
        plant(&store);
    }
    let app = build_app(&enriched).expect("the viewer opens the enriched store");
    Served {
        scratch,
        path: enriched,
        app,
    }
}

/// The whole clean fixture corpus, extracted into a store at `path`.
fn corpus(path: &Path, plant: impl Fn(&Store)) {
    let store = Store::create(path).expect("a fresh store");
    let extractor = Extractor::new(fixtures());
    for transcript in corpus_transcripts() {
        let source = source(&transcript);
        let trace = extractor
            .extract(&source)
            .unwrap_or_else(|error| panic!("{} extracts: {error}", source.id));
        store
            .export(&trace, &source.fingerprint)
            .unwrap_or_else(|error| panic!("{} exports: {error}", source.id));
    }
    plant(&store);
}

/// One transcript as discovery would have handed it over.
fn source(transcript: &Path) -> SessionSource {
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

/// A viewer and the tempdir its store lives in.
pub struct Served {
    scratch: TempDir,
    path: PathBuf,
    app: Router,
}

impl Served {
    /// One GET, answered. The router is cloned per call because `oneshot` consumes the service.
    pub async fn get(&self, path: &str) -> Response<Body> {
        self.app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(path)
                    .body(Body::empty())
                    .expect("a GET with no body"),
            )
            .await
            .expect("the router answers")
    }

    /// One GET's status and body text, which is what most leaves assert over.
    pub async fn page(&self, path: &str) -> (StatusCode, String) {
        let response = self.get(path).await;
        let status = response.status();
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("the whole body");
        (
            status,
            String::from_utf8(bytes.to_vec()).expect("a page is UTF-8"),
        )
    }

    /// The served store's own path, for a test that reads it back.
    pub fn db(&self) -> PathBuf {
        self.path.clone()
    }
}

/// The session with the most turns on its main thread, which is the one whose children log has
/// pages to walk. Read from the store rather than guessed: the fixture corpus is discovered, so
/// a session added to it must not quietly turn a paging test into a one-page one.
pub fn busiest_session(db: &Path) -> (String, i64) {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let rows = store
        .fetch(
            "SELECT session_id, count(*) AS turns FROM turns WHERE source = 'main' \
             GROUP BY session_id ORDER BY turns DESC, session_id LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows.first().expect("the corpus has a session with turns");
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.i64("turns").expect("a turn count"),
    )
}

/// Every session id the fixture corpus put in the store, sorted.
pub fn session_ids(db: &Path) -> Vec<String> {
    let store = Store::open_read_only(db).expect("the store opens read only");
    store
        .fetch("SELECT id FROM sessions ORDER BY id", &[])
        .expect("the store answers")
        .iter()
        .map(|row| row.str("id").expect("a session id").to_owned())
        .collect()
}
