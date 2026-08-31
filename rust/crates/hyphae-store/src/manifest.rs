//! What each library query takes, compiled in from the manifest Python owns.
//!
//! Ported by generation rather than by hand: `analyze/manifest.py` is the one place a query's
//! scope and its defaults are decided, and `tools/gen_query_manifest.py` writes them out as
//! the JSON below (`plans/rust-prototype/full-port.md`). A second hand-written table here
//! would be a place for the two implementations to disagree about what a bare invocation
//! binds — and a report quotes those defaults.
//!
//! The freshness of the file against the Python module is gated in the Python tier, which is
//! where both can be read; this side gates that the file covers the catalog it ships
//! (`tests/manifest.rs`).

use std::collections::BTreeMap;
use std::sync::LazyLock;

use chrono::NaiveDate;
use serde::Deserialize;

use crate::Param;

/// The generated manifest, as committed.
pub const MANIFEST_JSON: &str = include_str!("../../../metadata/query_manifest.json");

/// What a query is asking about, which decides what the runner has to give it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Scope {
    /// Counts across sessions: bound to the corpus predicate and the trailing window.
    Corpus,
    /// Anything keyed by what it is about — a node's own query, or the viewer's lists.
    Keyed,
}

/// How a `--param` string becomes the value DuckDB binds.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ParamType {
    Text,
    Integer,
    Date,
}

/// One named parameter a query file declares.
#[derive(Debug, Clone, Deserialize)]
pub struct ParamSpec {
    /// What the value binds as.
    #[serde(rename = "type")]
    pub kind: ParamType,
    /// Whether the caller has to name it. NULL is a real default — an unbound `$mentions`
    /// surveys every command — so absence cannot stand in for a choice the caller must make,
    /// which is why this flag crosses the bridge beside the default.
    pub required: bool,
    /// The production default: the value a bare invocation runs and a report quotes.
    pub default: Option<serde_json::Value>,
}

impl ParamSpec {
    /// The default as a binding, or nothing where the caller has to name one.
    ///
    /// Panics on a default the declared type cannot hold — a manifest that says `integer` and
    /// carries a string is a bridge that has stopped describing what it binds, and binding it
    /// as whatever DuckDB accepts would hide that until a report quoted the wrong number.
    pub fn binding(&self) -> Option<Param> {
        if self.required {
            return None;
        }
        let held = match &self.default {
            None | Some(serde_json::Value::Null) => return Some(Param::Absent),
            Some(held) => held,
        };
        Some(match (self.kind, held) {
            (ParamType::Text, serde_json::Value::String(text)) => Param::Text(text.clone()),
            (ParamType::Integer, serde_json::Value::Number(number)) => Param::Int(
                number
                    .as_i64()
                    .expect("an integer default is a whole number"),
            ),
            (ParamType::Date, serde_json::Value::String(written)) => Param::Date(
                NaiveDate::parse_from_str(written, "%Y-%m-%d")
                    .expect("a date default is written as the ISO day a `--param` spells"),
            ),
            _ => panic!("a {:?} parameter defaults to {held}", self.kind),
        })
    }
}

/// What the runner needs to know about one `.sql` file to bind and scope it.
#[derive(Debug, Clone, Deserialize)]
pub struct QueryMeta {
    pub scope: Scope,
    pub params: BTreeMap<String, ParamSpec>,
}

/// The manifest, parsed once per process.
pub fn manifest() -> &'static BTreeMap<String, QueryMeta> {
    static PARSED: LazyLock<BTreeMap<String, QueryMeta>> = LazyLock::new(|| {
        serde_json::from_str(MANIFEST_JSON).expect("the generated query manifest parses")
    });
    &PARSED
}

/// What one query takes, by file stem.
///
/// Panics on a name the manifest has no entry for, as [`crate::queries::load`] does for the
/// SQL: the two are one library, and a caller naming a query neither holds is a typo.
pub fn entry(name: &str) -> &'static QueryMeta {
    manifest()
        .get(name)
        .unwrap_or_else(|| panic!("no query named `{name}` in the manifest"))
}
