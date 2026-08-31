//! Two facts the crate cannot reach for itself: cargo's target directory, and a digest over
//! the sources that decide what a cached store holds.
//!
//! The digest belongs here because a build script is the one place cargo will re-run when a
//! file under another crate changes. `tests/cache.rs` re-derives it at run time and compares,
//! so a `rerun-if-changed` that stops covering a root reddens rather than going quiet.

use std::path::{Path, PathBuf};

include!("src/digest.rs");

fn main() {
    let repo = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("the crate sits three levels under the repository root");
    // Every root the digest covers is a trigger: a file added, removed or edited under one of
    // them has to re-run this script or the key it emits goes stale behind the cache.
    for crate_dir in WRITER_CRATES {
        println!(
            "cargo:rerun-if-changed={}",
            repo.join("rust/crates").join(crate_dir).display()
        );
    }
    println!(
        "cargo:rerun-if-changed={}",
        repo.join(WRITER_LOCK).display()
    );
    println!(
        "cargo:rustc-env=HYPHAE_WRITER_DIGEST={}",
        writer_digest(&repo)
    );
    println!(
        "cargo:rustc-env=HYPHAE_CACHE_ROOT={}",
        cache_root().display()
    );
}

/// Where cargo would put `CARGO_TARGET_TMPDIR`, found from `OUT_DIR`.
///
/// `env!("CARGO_TARGET_TMPDIR")` is what a test binary would use, but cargo only defines it
/// for integration test and bench targets — a library cannot read it, and this crate is one.
/// `OUT_DIR` is `<target>/<profile>/build/<crate>-<hash>/out`, so the directory holding
/// `build` is the profile's, and its parent is the target directory `CARGO_TARGET_DIR` moves.
fn cache_root() -> PathBuf {
    let out = PathBuf::from(std::env::var("OUT_DIR").expect("cargo sets OUT_DIR"));
    let build = out
        .ancestors()
        .find(|at| at.file_name().is_some_and(|name| name == "build"))
        .unwrap_or_else(|| panic!("{} sits under a `build` directory", out.display()));
    let target = build
        .parent()
        .and_then(Path::parent)
        .unwrap_or_else(|| panic!("{} sits under a target directory", build.display()));
    target.join("tmp/hyphae-stores")
}
