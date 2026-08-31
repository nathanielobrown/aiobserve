//! What every Rust test in the workspace shares: the fixture corpus, the stores built from
//! it, and the viewer served over one.
//!
//! Dev-only — nothing outside a `[dev-dependencies]` entry depends on it. It exists because
//! nextest runs a process per test: the session-scoped fixtures `tests/conftest.py` builds
//! once per pytest run have no in-process counterpart here, so [`cache`] moves that
//! amortization onto disk and this crate is the one place that knows how.
//!
//! It replaces the three copies of `tests/common/mod.rs` that drifted apart between the
//! extract, store and view crates.

pub mod cache;
pub mod corpus;
/// The digest over the sources that decide a stored row — shared with `build.rs`, which
/// `include!`s this module rather than depending on the crate it is building.
pub mod digest;
/// The bounds and enrichment numbers Python owns, compiled in from the generated JSON.
pub mod metadata;
pub mod rows;
pub mod served;
