//! One built store per key, shared by every test process that asks for it.
//!
//! pytest amortizes a store build over a session-scoped fixture; nextest runs a process per
//! test, so the amortization has to live on disk. A key is a digest over everything that
//! decides the stored bytes — the writer sources, the lockfile, and the selected fixture
//! files — so invalidation *is* the key: there is nothing to remember to bump. Over-keying
//! costs one rebuild of a couple of seconds.
//!
//! **Open a cached store read-only.** A write lock anywhere on the file refuses every
//! read-only open beside it, and the processes reading one of these are running in parallel.
//! A test that has to write takes a [`writable_copy`] first, as `tests/conftest.py:plant`
//! does.

use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use hyphae_store::Store;
use tempfile::TempDir;

use crate::{corpus, digest, metadata};

/// The digest `build.rs` took over the sources that write a store.
pub const WRITER_DIGEST: &str = env!("HYPHAE_WRITER_DIGEST");

/// How long a process waits for another one's build before it gives up. A build is seconds;
/// this is only ever reached when a builder died holding the sentinel.
const PATIENCE: Duration = Duration::from_secs(60);

/// Where the built stores live: under cargo's target directory, so nothing is committed and
/// `cargo clean` is the reset.
pub fn root() -> &'static Path {
    Path::new(env!("HYPHAE_CACHE_ROOT"))
}

/// The whole clean fixture corpus, extracted and exported once. Open it read-only.
pub fn corpus_store() -> PathBuf {
    cached("corpus", &corpus_key(), build_corpus)
}

/// The same corpus with the enrichment rows a pass would have written. Open it read-only.
///
/// No Rust code writes one yet: the enrichment schema and its views belong to the Python
/// pass, so this shells out to `tests/conftest.py:build_enriched_store` — the same function
/// `tests/gallery/serve.py` builds the browser tier's copy with. It plants a row on all but
/// the last item of each level, which is the partly-enriched shape a page has to render.
pub fn enriched_store() -> PathBuf {
    let corpus = corpus_store();
    cached("enriched", &enriched_key(), |at| {
        build_enriched(&corpus, at)
    })
}

/// The shared corpus store, open read-only — what a leaf that only reads should take.
pub fn corpus_reader() -> Store {
    Store::open_read_only(&corpus_store()).expect("the cached corpus opens read only")
}

/// A writable copy of the shared corpus, and the tempdir holding it.
///
/// The `TempDir` comes back with the store: dropping it deletes the file the store is on.
pub fn writable_corpus() -> (TempDir, Store) {
    let (scratch, path) = writable_copy(&corpus_store());
    let store = Store::create(&path).expect("the copy opens for writing");
    (scratch, store)
}

/// A writable copy of a cached store, in a tempdir the caller keeps alive.
pub fn writable_copy(cached: &Path) -> (TempDir, PathBuf) {
    let scratch = TempDir::new().expect("a tempdir for the copy");
    let copy = scratch.path().join("traces.duckdb");
    std::fs::copy(cached, &copy).expect("the cached store copies");
    (scratch, copy)
}

/// Every file whose bytes reach the corpus store — the fold the key is taken over.
///
/// A session's records live across several files (subagent transcripts, their metas,
/// workflow journals, offloaded tool results), and the extractor reads all of them, so
/// folding only the `<session>.jsonl` paths would leave the rest free to change unnoticed.
pub fn corpus_files() -> Vec<PathBuf> {
    corpus::corpus_sources()
        .into_iter()
        .flat_map(|source| source.files)
        .collect()
}

/// The store at `<root>/<name>-<key>/store.duckdb`, built by `build` if nothing has yet.
///
/// One asker wins an `O_EXCL` sentinel and builds; the rest poll for the finished file. A
/// cold `cargo nextest run` at the default `-j` otherwise starts one multi-threaded build per
/// worker. The build writes to a pid-named scratch file in the same directory and is renamed
/// into place, so a reader never opens a half-written store.
pub fn cached(name: &str, key: &str, build: impl FnOnce(&Path)) -> PathBuf {
    let directory = root().join(format!("{name}-{key}"));
    let store = directory.join("store.duckdb");
    if store.exists() {
        return store;
    }
    std::fs::create_dir_all(&directory).expect("the cache directory is writable");
    let sentinel = directory.join("building");
    match std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&sentinel)
    {
        Ok(_) => {
            let scratch = directory.join(format!("tmp-{}.duckdb", std::process::id()));
            build(&scratch);
            // Whatever built it has to have checkpointed and closed: a read-only open cannot
            // replay a write-ahead log, and renaming the store alone would strand one.
            let wal = scratch.with_extension("duckdb.wal");
            assert!(!wal.exists(), "{} was left behind", wal.display());
            std::fs::rename(&scratch, &store).expect("the built store renames into place");
            std::fs::remove_file(&sentinel).expect("the sentinel is removable");
        }
        Err(error) if error.kind() == ErrorKind::AlreadyExists => wait_for(&store, &sentinel),
        Err(error) => panic!("{}: {error}", sentinel.display()),
    }
    store
}

/// Poll until the builder renames its store into place.
///
/// A builder that panics leaves its sentinel behind and every later run waits the full
/// [`PATIENCE`] on it, so the message names the file to delete.
fn wait_for(store: &Path, sentinel: &Path) {
    let until = Instant::now() + PATIENCE;
    while Instant::now() < until {
        if store.exists() {
            return;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    panic!(
        "waited {PATIENCE:?} for another process to build {}. If nothing is building, a run \
         died holding {} — delete it.",
        store.display(),
        sentinel.display(),
    );
}

/// The writer sources and the selected fixture files, in one digest.
///
/// `hyphae_extract::fingerprint` is the fold over `(path, size, mtime_ns)` the extractor
/// already uses to decide re-extraction, reused rather than restated; its `version` argument
/// is the slot the writer digest goes in. Fixture mtimes mean a fresh clone or worktree
/// misses cold — correct, just noisy.
pub fn corpus_key() -> String {
    hyphae_extract::fingerprint(&corpus_files(), &corpus::repo(), WRITER_DIGEST)
        .expect("the fixture corpus is readable")
}

/// The corpus, the enrichment stamps Python emits, and the Python that plants the rows.
///
/// The stamps come across the generation bridge ([`crate::metadata`]) because a prompt or
/// taxonomy bump changes what a row says with no corpus byte moved. They are folded *beside*
/// the content digest rather than instead of it: Python still writes these rows, and a change
/// to the planting recipe in `tests/conftest.py` need not bump a version. The digest half
/// retires when `hyphae-enrich` writes them and joins [`digest::WRITER_CRATES`].
pub fn enriched_key() -> String {
    fold_enriched(
        &corpus_key(),
        metadata::ENRICHMENT_JSON,
        &digest::python_digest(&corpus::repo()),
    )
}

/// The three parts of the enriched key, folded — a function so the claim above is probeable.
pub fn fold_enriched(corpus: &str, enrichment: &str, python: &str) -> String {
    use sha2::Digest as _;
    let mut digest = sha2::Sha256::new();
    for part in [corpus.as_bytes(), enrichment.as_bytes(), python.as_bytes()] {
        digest.update((part.len() as u64).to_le_bytes());
        digest.update(part);
    }
    format!("{:x}", digest.finalize())
}

/// The whole clean fixture corpus, extracted into a store at `at`.
///
/// Checkpointed before it closes: a store still carrying a write-ahead log is one a reader
/// would have to replay, and a read-only open cannot.
fn build_corpus(at: &Path) {
    let store = Store::create(at).expect("a fresh store");
    let extractor = corpus::extractor();
    for source in corpus::corpus_sources() {
        let trace = extractor
            .extract(&source)
            .unwrap_or_else(|error| panic!("{} extracts: {error}", source.id));
        store
            .export(&trace, &source.fingerprint)
            .unwrap_or_else(|error| panic!("{} exports: {error}", source.id));
    }
    store
        .connection()
        .execute_batch("FORCE CHECKPOINT")
        .expect("the store checkpoints before it closes");
}

/// The enrichment rows, written by the Python pass that owns their schema.
fn build_enriched(corpus: &Path, at: &Path) {
    let repo = corpus::repo();
    let script = format!(
        "import sys; sys.path.insert(0, {repo:?}); \
         from tests.conftest import build_enriched_store; \
         build_enriched_store(__import__('pathlib').Path({at:?}), \
         corpus=__import__('pathlib').Path({corpus:?}))",
        repo = repo.to_string_lossy(),
        at = at.to_string_lossy(),
        corpus = corpus.to_string_lossy(),
    );
    let done = std::process::Command::new("uv")
        .args(["run", "--project"])
        .arg(&repo)
        .args(["python", "-c", &script])
        .current_dir(&repo)
        .output()
        .expect("uv runs the enrichment pass that owns the schema");
    assert!(
        done.status.success(),
        "the enrichment pass failed: {}",
        String::from_utf8_lossy(&done.stderr)
    );
}
