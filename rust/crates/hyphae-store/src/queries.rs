//! The versioned SQL library, read from the files `src/hyphae/analyze/queries/` holds.
//!
//! Compiled in with `include_str!` rather than copied: the `.sql` file is the unit a report
//! cites and a reader re-runs, so both implementations must run the same bytes. That is also
//! why the cargo workspace lives inside the repo.
//!
//! Ported from `src/hyphae/analyze/queries.py` — [`load`] is its `load`, and the widths below
//! are the ones a viewer page binds. What each query takes stays in `analyze/manifest.py`:
//! the Rust side binds by name at the call, as Python's viewer does.

// The name → text table, walked out of the query directory by `build.rs`.
include!(concat!(env!("OUT_DIR"), "/queries.rs"));

/// The SQL text of one library query, by file stem — Python's `load`.
///
/// Panics on a name the library has no file for. Every caller names a query the viewer's
/// catalog declares, so an unknown name is a typo in this crate rather than a request.
pub fn load(name: &str) -> &'static str {
    QUERIES
        .iter()
        .find(|(stem, _)| *stem == name)
        .map(|(_, sql)| *sql)
        .unwrap_or_else(|| panic!("no query named `{name}` in the library"))
}

/// The prefix that marks a query as the viewer's.
pub const VIEW_PREFIX: &str = "view_";

/// How much of a title a NavTree row carries — a turn's, a run's, an api call's. What a row
/// *can* say rather than what fits: the NavTree is draggable, so the cut has to survive a
/// reader widening it.
pub const NAV_CHARS: i64 = 110;

/// How much of a title one crumb of a crumb chain carries. A crumb is a place to click, not a
/// place to read — the node itself is open underneath.
pub const CRUMB_CHARS: i64 = 40;

/// How much of a string a node page's header carries, and how many items and how much of each
/// one a header list holds.
pub const HEADER_CHARS: i64 = 100;
pub const HEADER_ITEMS: i64 = 5;
pub const HEADER_ITEM_CHARS: i64 = 60;

/// How much of a string one row of a children log carries.
pub const LOG_CHARS: i64 = 300;

/// How many rows one children log holds.
pub const LOG_ROWS: i64 = 100;

/// How much of the one value a node page is *about* the pane shows before it offers the rest.
pub const DETAIL_CHARS: i64 = 4_000;

/// How much of a model-written description or friction line a page shows.
pub const ENRICHMENT_CHARS: i64 = 200;

/// The keyset cursor before the first row: "the last index already shown", and indexes start
/// at 0.
pub const FIRST_PAGE: i64 = -1;

/// The turn id `session_timeline` and `run_timeline` give the api calls that sit under no
/// turn. A sentinel rather than NULL so it can travel in a URL.
pub const UNATTRIBUTED: &str = "(unattributed)";
