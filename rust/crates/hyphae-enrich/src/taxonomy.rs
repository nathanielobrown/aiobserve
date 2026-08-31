//! The one vocabulary every enrichment level is written in, and the prose a prompt is built
//! from — read from the generated bridge rather than declared twice.
//!
//! `src/hyphae/enrich/taxonomy.py` and `prompts.py` stay the one owner; `tools/gen_enrichment.py`
//! writes what crosses into `rust/metadata/enrichment.json` and the Python tier gates the file
//! against the modules (`plans/rust-prototype/full-port.md`). What crosses is material, never a
//! rendered prompt: this side composes it, so this side's leaves still hold that composition to
//! its order.

use std::collections::BTreeMap;
use std::sync::LazyLock;

use serde::Deserialize;

use crate::schema::Level;

/// The stamps, the two closed vocabularies and the prompt material, as generated.
pub const ENRICHMENT_JSON: &str = include_str!("../../../metadata/enrichment.json");

/// What a failing lookup tells the reader to run.
const GEN_ENRICHMENT: &str = "uv run python -m tools.gen_enrichment";

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

/// The four blocks `instructions` composes, in the words the classifier reads them in.
#[derive(Debug, Deserialize)]
pub struct PromptText {
    /// What each level is looking at — the one part that reads differently per level.
    pub subject: BTreeMap<String, String>,
    /// The output contract in prose, beside the schema that enforces it.
    pub answer: String,
    /// How to break the ties a QC pass found the model getting wrong.
    pub choosing: String,
    /// Appended for a session alone: its lines are other readers' descriptions to relay.
    pub relaying: String,
}

/// What a reader of enrichment rows needs and cannot derive from the rows.
#[derive(Debug, Deserialize)]
pub struct Metadata {
    /// One entry per level, by the word its `level` column carries.
    pub levels: BTreeMap<String, LevelMeta>,
    /// The version of the two vocabularies below, bumped when either changes.
    pub taxonomy_version: i64,
    /// What kind of work it was, in declaration order.
    pub categories: Vec<String>,
    /// How it ended, in declaration order.
    pub outcomes: Vec<String>,
    /// What each category means, in one line. The prompt is written from these.
    pub category_definitions: BTreeMap<String, String>,
    /// What each outcome means, in one line.
    pub outcome_definitions: BTreeMap<String, String>,
    /// The prose blocks a level's instructions are composed of.
    pub prompt_text: PromptText,
}

impl Metadata {
    /// What one level is told it is reading.
    pub fn subject(&self, level: Level) -> &str {
        self.prompt_text
            .subject
            .get(level.word())
            .unwrap_or_else(|| missing("subject", level.word()))
            .as_str()
    }
}

/// The one-line definition of one member of either vocabulary.
///
/// Panics on a member with no definition, as [`Metadata::subject`] does: a member the
/// classifier is never told about is a member it will not use, and a prompt short one line is
/// worse than a crash before the run spends anything.
pub fn definition<'held>(vocabulary: &'held BTreeMap<String, String>, member: &str) -> &'held str {
    vocabulary
        .get(member)
        .unwrap_or_else(|| missing("definition", member))
        .as_str()
}

fn missing(kind: &str, name: &str) -> ! {
    panic!("no {kind} for `{name}` — regenerate the bridge with `{GEN_ENRICHMENT}`")
}

/// The enrichment metadata, parsed once per process.
pub fn enrichment() -> &'static Metadata {
    static PARSED: LazyLock<Metadata> = LazyLock::new(|| {
        serde_json::from_str(ENRICHMENT_JSON).expect("the enrichment metadata parses")
    });
    &PARSED
}

/// Whether one string is a member of a closed vocabulary.
pub fn is_member(vocabulary: &[String], value: &str) -> bool {
    vocabulary.iter().any(|member| member == value)
}
