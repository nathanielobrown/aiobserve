//! The shared store cache: what decides its key, and that it builds once.
//!
//! nextest runs a process per test, so pytest's session-scoped stores have no counterpart —
//! the amortization has to live on disk instead. These leaves hold the cache to the two
//! things that makes safe: a key that moves whenever the stored bytes would, and one build
//! however many processes ask at once.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use serde_json::Value;

use hyphae_testsupport::{cache, corpus, digest, metadata};

/// The digest compiled into the crate is recomputed from the sources it claims to cover.
///
/// This is the leaf that catches the failure mode the cache is one `cargo clean` away from:
/// a build script that did not re-run leaves a stale key, the cache hits, and every store
/// test passes against bytes the old writer wrote. If `build.rs` ever stops naming a source
/// root in `cargo:rerun-if-changed`, this reddens on the next edit under it.
#[test]
fn the_writer_digest_matches_the_sources_on_disk() {
    let recomputed = digest::writer_digest(&corpus::repo());
    assert_eq!(
        recomputed,
        cache::WRITER_DIGEST,
        "the compiled-in writer digest is stale: `build.rs` did not re-run for an edit under \
         one of the roots it digests",
    );
}

/// Every file that decides the corpus store's bytes is in the fold — not just the transcripts.
///
/// A session's records live in several files, and the extractor reads all of them. Folding
/// only `<session>.jsonl` would leave a subagent transcript, its meta, or an offloaded tool
/// result free to change under a cache that never noticed.
#[test]
fn the_key_folds_every_file_the_corpus_reads() {
    let folded = cache::corpus_files();
    let named = |suffix: &str| {
        folded
            .iter()
            .any(|path| path.to_string_lossy().ends_with(suffix))
    };
    // One of each kind the layout puts beside a transcript, by name so the claim is checkable.
    assert!(
        named("/spine/4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b.jsonl"),
        "a transcript"
    );
    assert!(
        named("/subagents/agent-a3b37063695183556.meta.json"),
        "a subagent meta"
    );
    assert!(
        named("/tool-results/bosvr1kjx.txt"),
        "an offloaded tool result"
    );
    // And the selection itself: the two clean `invented/` transcripts are in, the rest out.
    let invented: Vec<&PathBuf> = folded
        .iter()
        .filter(|path| path.to_string_lossy().contains("/invented/"))
        .collect();
    assert_eq!(invented.len(), corpus::CLEAN_INVENTED.len());
}

/// The cache lands under cargo's own target directory, and a second ask rebuilds nothing.
///
/// Under `target/` because nothing there is committed and `cargo clean` is the reset; found
/// through the build script rather than spelled `rust/target/`, which `CARGO_TARGET_DIR`
/// would move out from under it.
#[test]
fn the_cache_is_built_once_under_cargos_target_directory() {
    let once = cache::corpus_store();
    assert!(
        once.starts_with(cache::root()),
        "{once:?} is under the cache root"
    );
    assert!(
        cache::root()
            .components()
            .any(|part| part.as_os_str() == "target"),
        "the cache root {:?} sits under a cargo target directory",
        cache::root(),
    );
    let built_at = modified(&once);
    let twice = cache::corpus_store();
    assert_eq!(once, twice, "the same key answers with the same file");
    assert_eq!(built_at, modified(&twice), "the second ask rebuilt nothing");
}

/// The cached file is closed cleanly and holds the corpus every test expects to read.
///
/// No `.wal` beside it: a store still carrying a write-ahead log is one a reader would have
/// to replay, and a read-only open cannot. The builder checkpoints before it renames.
#[test]
fn a_cached_store_is_closed_cleanly_and_holds_the_whole_corpus() {
    let path = cache::corpus_store();
    let wal = path.with_extension("duckdb.wal");
    assert!(!wal.exists(), "no write-ahead log survives the build");
    let store = hyphae_store::Store::open_read_only(&path).expect("the cache opens read only");
    let rows = store
        .fetch("SELECT count(*) AS n FROM sessions", &[])
        .expect("the store counts its sessions");
    assert_eq!(
        rows[0].i64("n").expect("a count"),
        corpus::corpus_transcripts().len() as i64,
        "one session per selected transcript",
    );
}

/// However many askers arrive at once, one of them builds.
///
/// The sentinel is an `O_EXCL` create, so the losers here are the same losers a cold
/// `cargo nextest run` produces — the file system, not the process boundary, is what
/// serializes them.
#[test]
fn concurrent_askers_build_the_store_once_between_them() {
    let key = std::process::id().to_string();
    let key = key.as_str();
    let builds = Arc::new(AtomicUsize::new(0));
    let answers: Vec<PathBuf> = std::thread::scope(|scope| {
        let handles: Vec<_> = (0..4)
            .map(|_| {
                let builds = Arc::clone(&builds);
                scope.spawn(move || {
                    cache::cached("probe", key, |path| {
                        builds.fetch_add(1, Ordering::SeqCst);
                        // Long enough that a racer reaches the sentinel before the rename.
                        std::thread::sleep(std::time::Duration::from_millis(200));
                        std::fs::write(path, b"built once").expect("the probe store is writable");
                    })
                })
            })
            .collect();
        handles
            .into_iter()
            .map(|handle| handle.join().unwrap())
            .collect()
    });
    assert_eq!(
        builds.load(Ordering::SeqCst),
        1,
        "one build between four askers"
    );
    assert!(
        answers.windows(2).all(|pair| pair[0] == pair[1]),
        "one answer"
    );
    assert_eq!(std::fs::read(&answers[0]).unwrap(), b"built once");
    std::fs::remove_dir_all(answers[0].parent().unwrap())
        .expect("the probe cleans up after itself");
}

/// A test that writes takes a copy; the shared file it copied is left as it was.
///
/// Python's `plant` fixture copies for the same reason, and here it is load-bearing twice
/// over: a write lock anywhere on the cached file refuses every read-only open beside it.
#[test]
fn a_writable_copy_leaves_the_cached_store_untouched() {
    let cached = cache::corpus_store();
    let before = (modified(&cached), std::fs::metadata(&cached).unwrap().len());
    let (_scratch, copy) = cache::writable_copy(&cached);
    assert_ne!(copy, cached);
    {
        let store = hyphae_store::Store::create(&copy).expect("the copy opens for writing");
        store
            .connection()
            .execute("DELETE FROM sessions", [])
            .expect("the copy is writable");
    }
    let after = (modified(&cached), std::fs::metadata(&cached).unwrap().len());
    assert_eq!(before, after, "the shared store is untouched");
}

fn modified(path: &Path) -> std::time::SystemTime {
    std::fs::metadata(path)
        .expect("the cached store is there")
        .modified()
        .expect("the platform reports a modification time")
}

/// The enriched store's key folds the stamps Python emits, and moves when either bumps.
///
/// This is the leaf the bridge exists for. A pass writes `prompt_version` and
/// `taxonomy_version` onto every row, and a bump changes what a store holds without touching
/// a byte of the corpus — so a key blind to them serves yesterday's enrichment to every test
/// that opens the cache, green. The fold is a pure function so the claim can be probed rather
/// than asserted: bump a stamp in a copy of the compiled-in JSON and the key has to move.
#[test]
fn the_enriched_key_moves_when_a_bridged_enrichment_stamp_bumps() {
    let live: Value = serde_json::from_str(metadata::ENRICHMENT_JSON).expect("the metadata parses");
    let folded = |written: &Value| cache::fold_enriched("corpus", &written.to_string(), "python");
    let base = folded(&live);
    let mut taxonomy = live.clone();
    taxonomy["taxonomy_version"] = (metadata::enrichment().taxonomy_version + 1).into();
    assert_ne!(
        folded(&taxonomy),
        base,
        "a taxonomy bump leaves the key where it was"
    );
    // And per level, since each carries its own prompt and only one may move.
    for (word, level) in &metadata::enrichment().levels {
        let mut prompt = live.clone();
        prompt["levels"][word]["prompt_version"] = (level.prompt_version + 1).into();
        assert_ne!(
            folded(&prompt),
            base,
            "a `{word}` prompt bump leaves the key where it was"
        );
    }
}

/// The other two things the enriched store's bytes depend on still move the key.
///
/// The stamps are what the bridge adds, not what it replaces: the corpus underneath and the
/// Python that plants the rows both decide the same file, and a planting recipe can change
/// with no version to bump. Dropping either half would trade one blind spot for another.
#[test]
fn the_enriched_key_still_folds_the_corpus_and_the_python_that_plants_the_rows() {
    let key = |corpus: &str, python: &str| {
        cache::fold_enriched(corpus, metadata::ENRICHMENT_JSON, python)
    };
    let base = key("corpus", "python");
    assert_ne!(key("moved", "python"), base, "the corpus key");
    assert_ne!(key("corpus", "moved"), base, "the Python writer digest");
    // And the wiring: the key the cache directory is named with is that fold over the live
    // three. A `enriched_key` that stopped reading the bridge would pass every leaf above.
    assert_eq!(
        cache::enriched_key(),
        cache::fold_enriched(
            &cache::corpus_key(),
            metadata::ENRICHMENT_JSON,
            &digest::python_digest(&corpus::repo()),
        ),
    );
}

/// Every crate in the workspace either writes a stored row or is named as one that does not.
///
/// The ratchet fact (3) of the slice-1 handoff asks for: `hyphae-enrich` has to join
/// [`digest::WRITER_CRATES`] the day it exists, and nothing else would notice if it did not —
/// a new writer outside the digest leaves the cache hitting on bytes an older writer wrote.
/// Silence is what this takes away: a crate is a writer, or it is excused by name here.
#[test]
fn every_crate_writes_a_stored_row_or_is_named_as_one_that_does_not() {
    let crates = corpus::repo().join("rust/crates");
    let present: BTreeSet<String> = std::fs::read_dir(&crates)
        .expect("the workspace crates are readable")
        .map(|entry| entry.expect("the entry is readable").path())
        .filter(|path| path.is_dir())
        .map(|path| path.file_name().unwrap().to_string_lossy().into_owned())
        .collect();
    let writers: BTreeSet<&str> = digest::WRITER_CRATES
        .iter()
        .map(|source| source.split('/').next().expect("a crate directory"))
        .collect();
    let excused: BTreeSet<&str> = digest::NON_WRITERS.iter().copied().collect();
    for name in &present {
        assert!(
            writers.contains(name.as_str()) || excused.contains(name.as_str()),
            "`{name}` is neither in WRITER_CRATES nor named in NON_WRITERS: say which it is, \
             because a writer outside the digest is a stale cache nothing reds on",
        );
    }
    // And the other direction: a deleted crate takes its entry with it, and no crate is both.
    for named in writers.union(&excused) {
        assert!(
            present.contains(*named),
            "`{named}` is named but no such crate exists"
        );
    }
    assert!(
        writers.is_disjoint(&excused),
        "a crate is a writer or it is not"
    );
}
