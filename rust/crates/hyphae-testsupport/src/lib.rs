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
/// Reading a served page back the way a browser and htmx would.
pub mod html;
/// The recorded rows every tier names, and why each one is worth naming.
pub mod landmarks;
/// The character every surface prints beside a node of each kind.
pub mod marks;
/// The bounds and enrichment numbers Python owns, compiled in from the generated JSON.
pub mod metadata;
/// Building the level the store says a NavTree should hold, and reading the one it drew.
pub mod nav_trees;
/// The enrichment rows a partial pass would have left behind.
pub mod planting;
pub mod rows;
/// Which page to fetch: one node of every kind, and one level of every shape a log has.
pub mod selections;
pub mod served;
/// The tools the fixture corpus records under a name the viewer names its calls by.
pub mod tools;
