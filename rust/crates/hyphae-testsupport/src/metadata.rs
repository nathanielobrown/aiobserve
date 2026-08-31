//! The bounds and enrichment metadata Python owns, compiled in from the generated JSON.
//!
//! `view/bounds.py`, `view/knobs.py`, `analyze/queries.py` and `hyphae/enrich/` stay the one
//! owner of every number here; `tools/gen_bounds.py registry` and `tools/gen_enrichment.py`
//! write them out as data, and the Python tier gates the files against the modules
//! (`plans/rust-prototype/full-port.md`).
//!
//! Dev-only for now, which is the honest statement of where the port stands: `hyphae-view`
//! and `hyphae-store` still declare their own constants, and what this module buys is the
//! leaf that reds when one of those hand copies stops matching Python. A page that *binds*
//! from here rather than from a `const` is slice 4's to make.

use std::collections::BTreeMap;
use std::sync::LazyLock;

use serde::Deserialize;

/// The viewer's ceilings, knob defaults and query widths, as generated.
pub const BOUNDS_JSON: &str = include_str!("../../../metadata/bounds.json");

/// The enrichment stamps and the two closed vocabularies, as generated.
pub const ENRICHMENT_JSON: &str = include_str!("../../../metadata/enrichment.json");

/// What a failing lookup tells the reader to run.
const GEN_BOUNDS: &str = "uv run python -m tools.gen_bounds registry";

/// One size a reader may name: what a link omits, and what a URL may not exceed.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Bound {
    pub default: i64,
    pub ceiling: i64,
}

/// Everything that bounds a page, in the four shapes Python declares them in.
#[derive(Debug, Deserialize)]
pub struct Bounds {
    /// What a URL naming no knob is served at; `nav` is a preset's word, the rest are numbers.
    pub knobs: BTreeMap<String, serde_json::Value>,
    /// The sizes a URL may name, each with the ceiling it is refused past.
    pub bounds: BTreeMap<String, Bound>,
    /// The plain numbers `bounds.py` declares — bounds no URL carries.
    pub sizes: BTreeMap<String, i64>,
    /// The widths `analyze/queries.py` declares, which the SQL binds and a page cuts at.
    pub widths: BTreeMap<String, i64>,
}

/// One enrichment level: where its rows live and what stamps them.
#[derive(Debug, Deserialize)]
pub struct LevelMeta {
    /// The instructions and output schema its rows were written under.
    pub prompt_version: i64,
    /// The table a pass writes them to.
    pub table: String,
    /// The columns that key a row in that table.
    pub keys: Vec<String>,
    /// The view holding the items this level describes.
    pub base: String,
    /// The columns that key one of those items.
    pub base_keys: Vec<String>,
}

/// What a reader of enrichment rows needs and cannot derive from the rows.
#[derive(Debug, Deserialize)]
pub struct Enrichment {
    /// One entry per level, by the word its `level` column carries.
    pub levels: BTreeMap<String, LevelMeta>,
    /// The version of the two vocabularies below, bumped when either changes.
    pub taxonomy_version: i64,
    /// What kind of work it was.
    pub categories: Vec<String>,
    /// How it ended.
    pub outcomes: Vec<String>,
}

/// The bounds registry, parsed once per process.
pub fn bounds() -> &'static Bounds {
    static PARSED: LazyLock<Bounds> =
        LazyLock::new(|| serde_json::from_str(BOUNDS_JSON).expect("the bounds registry parses"));
    &PARSED
}

/// The enrichment metadata, parsed once per process.
pub fn enrichment() -> &'static Enrichment {
    static PARSED: LazyLock<Enrichment> = LazyLock::new(|| {
        serde_json::from_str(ENRICHMENT_JSON).expect("the enrichment metadata parses")
    });
    &PARSED
}

/// One `Bound` by the name `view/bounds.py` declares it under.
///
/// Panics on a name the registry does not carry, as the two below do: a drift leaf asking
/// about a constant Python renamed wants the crash, not a pass against nothing.
pub fn bound(name: &str) -> &'static Bound {
    bounds()
        .bounds
        .get(name)
        .unwrap_or_else(|| missing("bound", name))
}

/// One plain size by the name `view/bounds.py` declares it under.
pub fn size(name: &str) -> i64 {
    *bounds()
        .sizes
        .get(name)
        .unwrap_or_else(|| missing("size", name))
}

/// One query width by the name `analyze/queries.py` declares it under.
pub fn width(name: &str) -> i64 {
    *bounds()
        .widths
        .get(name)
        .unwrap_or_else(|| missing("width", name))
}

fn missing(kind: &str, name: &str) -> ! {
    panic!("no {kind} called `{name}` in the registry — regenerate it with `{GEN_BOUNDS}`")
}
