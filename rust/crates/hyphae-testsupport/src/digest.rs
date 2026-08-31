// One digest over the sources that decide what a cached store holds.
//
// `build.rs` `include!`s this file rather than importing it: a build script cannot depend on
// the crate it is building, and the two sides have to digest the same way or the freshness
// leaf comparing them is comparing two functions instead of one.
//
// Plain `//` comments and no `use` of anything outside `std` and `sha2`, for the same reason:
// this text is compiled twice, once at the top of a build script.

/// The source trees whose bytes reach a stored row, under `rust/crates/`.
pub const WRITER_CRATES: &[&str] = &[
    // The trace an exporter walks.
    "hyphae-model/src",
    // What a transcript parses into.
    "hyphae-extract/src",
    // The DDL, the column lists, and every value mapping between them.
    "hyphae-store/src",
    // The enrichment tables and the upsert that fills them.
    "hyphae-enrich/src",
    // The corpus selection and the build recipe itself.
    "hyphae-testsupport/src",
];

/// The crates whose sources cannot change a stored row, so the digest leaves them out.
///
/// The other half of [`WRITER_CRATES`]: between them they name every crate in the workspace,
/// which is what makes a new one impossible to leave unclassified (`tests/cache.rs`).
pub const NON_WRITERS: &[&str] = &[
    // The CLI shell over the crates above; it writes nothing a store holds by itself.
    "hp",
    // Read-only too: the runner opens read-only, and its corpus relations are TEMP views.
    "hyphae-analyze",
    // Read-only by construction: every store it opens, it opens read-only.
    "hyphae-view",
    // The OTLP mapper writes to a collector, not a store. Its delivery ledger is the one
    // exception, and it lands in whatever store the caller names — never in a cached one,
    // which every delivery leaf copies before it writes.
    "hyphae-export",
];

/// The lockfile counts too: `duckdb`'s version decides the file format and `serde_json`'s
/// `preserve_order` decides a stored JSON value's key order, and neither is a source edit.
pub const WRITER_LOCK: &str = "rust/Cargo.lock";

/// Everything under `rust/` that decides a stored row, digested by content.
///
/// Content and not mtime, so two clones of one commit land on the same key. The *fixtures*
/// are keyed by mtime instead — that is `hyphae_extract::fingerprint`'s fold, reused rather
/// than restated — which is why a fresh clone misses the cache cold.
pub fn writer_digest(repo: &std::path::Path) -> String {
    let mut paths = Vec::new();
    for crate_dir in WRITER_CRATES {
        paths.extend(sources(&repo.join("rust/crates").join(crate_dir), "rs"));
    }
    paths.push(repo.join(WRITER_LOCK));
    over_contents(&paths, repo)
}

/// Every file under `root` with `extension`, sorted, so a walk order never moves a digest.
pub fn sources(root: &std::path::Path, extension: &str) -> Vec<std::path::PathBuf> {
    let mut found = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let entries = std::fs::read_dir(&directory)
            .unwrap_or_else(|error| panic!("{} is readable: {error}", directory.display()));
        for entry in entries {
            let path = entry.expect("the entry is readable").path();
            if path.is_dir() {
                pending.push(path);
            } else if path.extension().is_some_and(|held| held == extension) {
                found.push(path);
            }
        }
    }
    found.sort();
    found
}

/// A digest over what `paths` hold, named by their place under `relative_to`.
///
/// The name is in the digest as well as the bytes, so moving a file moves the key.
pub fn over_contents(paths: &[std::path::PathBuf], relative_to: &std::path::Path) -> String {
    use sha2::Digest as _;
    let mut digest = sha2::Sha256::new();
    for path in paths {
        let relative = path
            .strip_prefix(relative_to)
            .unwrap_or_else(|_| panic!("{} sits under {}", path.display(), relative_to.display()));
        let content = std::fs::read(path)
            .unwrap_or_else(|error| panic!("{} is readable: {error}", path.display()));
        digest.update(format!("{}\0{}\0", relative.display(), content.len()).as_bytes());
        digest.update(&content);
    }
    format!("{:x}", digest.finalize())
}
