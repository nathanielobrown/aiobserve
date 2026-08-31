//! The versioned SQL library, read from the files `src/hyphae/analyze/queries/` holds.
//!
//! Compiled in with `include_str!` rather than copied: the `.sql` file is the unit a report
//! cites and a reader re-runs, so both implementations must run the same bytes. That is also
//! why the cargo workspace lives inside the repo.
//!
//! Ported from `src/hyphae/analyze/queries.py` — [`load`] is its `load`, and the widths below
//! are the ones a viewer page binds. What each query takes stays in `analyze/manifest.py`:
//! the Rust side binds by name at the call, as Python's viewer does.

use crate::Param;

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
pub const NAV_CHARS: usize = 110;

/// How much of a title one crumb of a crumb chain carries. A crumb is a place to click, not a
/// place to read — the node itself is open underneath.
pub const CRUMB_CHARS: usize = 40;

/// How much of a string a node page's header carries, and how many items and how much of each
/// one a header list holds.
pub const HEADER_CHARS: usize = 100;
pub const HEADER_ITEMS: usize = 5;
pub const HEADER_ITEM_CHARS: usize = 60;

/// The same three for one row of the session list, which the viewer composes rather than the
/// query: the list's filters read the whole values. A skill name is not a PR url, which is why the
/// header's widths do not carry over — the list multiplies its row by the page.
pub const LIST_CHARS: usize = 100;
pub const LIST_ITEMS: usize = 4;
pub const LIST_ITEM_CHARS: usize = 20;

/// How many projects the list's filter box suggests. A path is offered whole or left out: a
/// suggestion cut to its head filters to nothing.
pub const LIST_PROJECTS: usize = 10;

/// How many kinds of work one list row names before it says how many it left: the categories a
/// pass described that session's turns as. Fewer than the lists above, because the taxonomy is
/// closed and small — three names say what a session spent its time on, and a fourth is noise.
pub const LIST_CATEGORIES: usize = 3;

/// How much of a string one row of a children log carries.
pub const LOG_CHARS: usize = 300;

/// How many rows one children log holds.
pub const LOG_ROWS: i64 = 100;

/// How much of the one value a node page is *about* the pane shows before it offers the rest.
pub const DETAIL_CHARS: usize = 4_000;

/// How much of a model-written description or friction line a page shows.
pub const ENRICHMENT_CHARS: usize = 200;

/// How much of one of the two closed-vocabulary tags beside it a page shows.
pub const TAG_CHARS: usize = 20;

/// How many rows one page of the records browser previews.
pub const PAGE_RECORDS: i64 = 100;

/// How much of a raw record one browser row shows. Long enough to tell a `user` record from an
/// `assistant` one; short enough that a hundred of them is a page rather than a transcript.
pub const RECORD_PREVIEW: usize = 160;

/// How many projects the landing page ranks. A corpus grows projects the way it grows sessions,
/// so the page is bound like the list — and a store holding more says how many it left out.
pub const PAGE_PROJECTS: i64 = 100;

/// How many of a session's failed tool calls its errors page lists.
pub const PAGE_ERRORS: i64 = 100;

/// The trailing window every corpus count is reported in beside the full corpus. Fixed, not
/// decayed: a window is a filter a reader can re-run and argue with.
pub const WINDOW_DAYS: i64 = 28;

/// The two trailing windows the landing page counts a project in, beside its whole history.
/// Not [`WINDOW_DAYS`], which is what a report's counts are quoted in and four weeks long so
/// a weekly rhythm cannot bias them; these are what a reader scanning for what is running
/// lately means, and the page heads its columns from them.
pub const PAGE_RECENT_DAYS: i64 = 7;
pub const PAGE_WINDOW_DAYS: i64 = 30;

/// How much of an offloaded tool result one chunk of the offload page carries.
pub const CHUNK_CHARS: i64 = 50_000;

/// How much of a model name a popover's own line carries.
pub const MODEL_CHARS: i64 = 60;

/// How much of an agent run's three display columns a chip carries, and of a compaction's
/// trigger.
pub const CHIP_CHARS: i64 = 60;

/// A binding as it is written down — NULL is a value a reader can rebind.
///
/// One spelling for both places a binding leaves the process: the citation line below, and the
/// link a page's footer makes out of it (`hyphae_view::citation::cited`).
pub fn shown(value: &Param) -> String {
    match value {
        Param::Absent => "NULL".to_owned(),
        Param::Text(text) => text.clone(),
        Param::Int(number) => number.to_string(),
        Param::Date(date) => date.to_string(),
    }
}

/// Query file and bindings as a SQL comment: what a report quotes and a reader re-runs.
///
/// Both consumers cite the same way. The viewer passes what it composed around the query — the
/// sort a page applied is as much a part of what produced it as a bound parameter.
pub fn citation(name: &str, bindings: &[(&str, Param)]) -> String {
    let bound = bindings
        .iter()
        .map(|(key, value)| format!("{key}={}", shown(value)))
        .collect::<Vec<_>>()
        .join(" ");
    format!("-- queries/{name}.sql {bound}")
        .trim_end()
        .to_owned()
}

/// The keyset cursor before the first row: "the last index already shown", and indexes start
/// at 0.
pub const FIRST_PAGE: i64 = -1;

/// The turn id `session_timeline` and `run_timeline` give the api calls that sit under no
/// turn. A sentinel rather than NULL so it can travel in a URL.
pub const UNATTRIBUTED: &str = "(unattributed)";
