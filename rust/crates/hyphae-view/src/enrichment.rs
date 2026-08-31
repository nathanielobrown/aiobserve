//! What the model said about the items on one page, or nothing at all.
//!
//! Ported from `src/hyphae/view/enrichment.py`. Enrichment rows are written by a pass that may
//! never have run (`docs/enrichment.md`), and the tables themselves are created by that pass
//! rather than by the exporter — so a store the viewer opens read-only may not hold them.
//! [`described`] asks the catalog first and hands back an empty answer when they are absent,
//! which is what makes a page over an un-enriched store render the same as a page over an item
//! the pass has not reached yet: nothing beside the item.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use hyphae_store::{Param, Row, Store, queries};

use crate::format::when;
use crate::store::{Page, ViewError, page_rows};

/// The three things that get an enrichment row, re-exported from the crate that writes
/// them: `view/enrichment.py` imports `Level` from `hyphae.enrich` for the same reason, and a
/// second copy here would drift with nothing to catch it.
pub use hyphae_enrich::Level;

/// The closed vocabularies a pass writes against (`enrich/taxonomy.py:TAXONOMY_VERSION`).
pub const TAXONOMY_VERSION: i64 = 2;

/// What marks a string a model wrote rather than a session, written once so that every surface
/// showing it reads the character from here.
pub const GLYPH: &str = "✨";
/// The class that styles it, and the one thing a test can read a bare glyph by: a NavTree row
/// carries the mark alone, because the provenance behind it is a pane's to spell out.
pub const GLYPH_CLASS: &str = "glyph";

/// One item's enrichment, as a page shows it.
#[derive(Debug, Clone)]
pub struct Enrichment {
    /// Which level's pass wrote it, which is what its versions are current against.
    pub level: Level,
    /// The turn, run or session the description is about — what keys the block on the page.
    pub item_id: String,
    /// The head the pane prints, one character past the width — the cut-and-mark protocol every
    /// other fat value rides ([`crate::format::cut`]) — beside how long the whole line runs,
    /// which is what the fetch behind the mark offers.
    pub description: String,
    pub description_chars: i64,
    pub category: String,
    pub outcome: String,
    /// One line of visible struggle, or `None` when the model saw none.
    pub friction: Option<String>,
    pub friction_chars: Option<i64>,
    /// Which model wrote the line and when, and the two versions it was written under.
    pub model: String,
    pub enriched_at: Option<DateTime<Utc>>,
    pub prompt_version: i64,
    pub taxonomy_version: i64,
}

impl Enrichment {
    /// Written under a prompt or taxonomy version this build no longer writes.
    ///
    /// Two of the four staleness axes are invisible from a read — whether the rendered content
    /// moved needs a re-render, and which model a pass would use today is the pass's own
    /// configuration — so a row this leaves untagged is current on the versions and unjudged on
    /// the rest.
    pub fn stale(&self) -> bool {
        self.prompt_version != self.level.prompt_version()
            || self.taxonomy_version != TAXONOMY_VERSION
    }

    /// What the glyph beside the line says: who wrote it, when, and under what.
    ///
    /// Everything a reader needs to decide whether re-running a pass would say more, in the one
    /// place the page has room for it — the pane. A NavTree row carries the mark alone.
    pub fn provenance(&self) -> String {
        let freshness = if self.stale() { "stale" } else { "fresh" };
        format!(
            "{} · {} · prompt v{} · taxonomy v{} · {freshness}",
            self.model,
            when(self.enriched_at),
            self.prompt_version,
            self.taxonomy_version,
        )
    }
}

/// What one page's items were described as, by level, keyed by item id.
#[derive(Debug, Default)]
pub struct Descriptions {
    /// Whether the store held the tables to ask at all. What a page cites is what it ran, and a
    /// store with the tables and no rows in them ran the query — an empty answer is one.
    pub queried: bool,
    pub session: Option<Enrichment>,
    pub turns: HashMap<String, Enrichment>,
    pub runs: HashMap<String, Enrichment>,
}

impl Descriptions {
    /// What the pass called one turn, or nothing where it said nothing about it.
    pub fn turn(&self, turn_id: &str) -> Option<&str> {
        self.turns
            .get(turn_id)
            .map(|said| said.description.as_str())
    }

    /// What the pass called one run, or nothing where it said nothing about it.
    pub fn run(&self, run_id: &str) -> Option<&str> {
        self.runs.get(run_id).map(|said| said.description.as_str())
    }
}

/// Whether this store holds the enrichment tables at all — a pass creates them, not the
/// exporter, so a store nothing has enriched holds none of them.
pub fn enriched(store: &Store) -> Result<bool, ViewError> {
    let rows = store.fetch(
        "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'",
        &[],
    )?;
    let held: Vec<&str> = rows
        .iter()
        .map(|row| row.str("table_name"))
        .collect::<Result<_, _>>()?;
    Ok(Level::ALL
        .into_iter()
        .all(|level| held.contains(&level.table())))
}

/// What the store says about one session, one thread's turns, and the session's runs.
///
/// `source` is the thread the page renders — `main` on a session page, the run's id on a run
/// page. An item with no row is absent from the mapping rather than present and empty, so a
/// component asks for a description and gets one or nothing.
pub fn described(store: &Store, session_id: &str, source: &str) -> Result<Descriptions, ViewError> {
    if !enriched(store)? {
        return Ok(Descriptions::default());
    }
    let rows = page_rows(
        store,
        Page::Enrichment,
        &[
            ("session_id", session_id.into()),
            ("source", source.into()),
            (
                "description_chars",
                Param::Int(queries::ENRICHMENT_CHARS as i64),
            ),
            ("tag_chars", Param::Int(queries::TAG_CHARS as i64)),
            ("head_chars", Param::Int(queries::HEADER_CHARS as i64)),
        ],
    )?;
    let mut described = Descriptions {
        queried: true,
        ..Descriptions::default()
    };
    for row in &rows {
        let word = row.str("level")?;
        let level =
            Level::of(word).unwrap_or_else(|| panic!("no enrichment level is called `{word}`"));
        let said = read(level, row)?;
        match level {
            Level::Turn => {
                described.turns.insert(said.item_id.clone(), said);
            }
            Level::AgentRun => {
                described.runs.insert(said.item_id.clone(), said);
            }
            Level::Session if said.item_id == session_id => described.session = Some(said),
            Level::Session => {}
        }
    }
    Ok(described)
}

/// One enrichment row as the page reads it.
fn read(level: Level, row: &Row) -> Result<Enrichment, ViewError> {
    Ok(Enrichment {
        level,
        item_id: row.str("item_id")?.to_owned(),
        description: row.str("description")?.to_owned(),
        description_chars: row.i64("description_chars")?,
        category: row.str("category")?.to_owned(),
        outcome: row.str("outcome")?.to_owned(),
        friction: row.opt_str("friction")?.map(str::to_owned),
        friction_chars: row.opt_i64("friction_chars")?,
        model: row.str("model")?.to_owned(),
        enriched_at: row.opt_timestamp("enriched_at")?,
        prompt_version: row.i64("prompt_version")?,
        taxonomy_version: row.i64("taxonomy_version")?,
    })
}
