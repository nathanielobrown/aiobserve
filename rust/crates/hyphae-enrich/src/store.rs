//! Reads enrichable items out of a trace store and writes enrichments back to it.
//!
//! Ported from `src/hyphae/enrich/store.py`. What is deliberately left in Python, as in
//! [`hyphae_store::store`]: `migrate` and `check_shape`. This build writes and reads a store
//! already at `SCHEMA_VERSION`, and [`hyphae_store::Store::open_for_write`] refuses anything
//! else rather than carrying it forward.

use std::collections::HashMap;
use std::path::Path;

use chrono::Utc;
use duckdb::Connection;
use hyphae_extract::sessions::project_predicate;
use hyphae_store::{Param, Row, Store, StoreError};

use crate::items::Item;
use crate::schema::{self, Level};
use crate::validation::Enrichment;

/// The `source` every main transcript's rows carry (`hyphae_model::MAIN_SOURCE`).
pub(crate) const MAIN: &str = "main";

/// The tag Claude Code wraps a slash command's own output in, and the pattern that reads a
/// body out of it. `(?s)` is load-bearing: without it a multi-line body matches nothing and
/// extracts as the empty string, which is a state of its own.
pub(crate) const STDOUT_TAG: &str = "local-command-stdout";

/// What enrichment refuses, and why.
#[derive(Debug, thiserror::Error)]
pub enum EnrichError {
    #[error(
        "session {session_id} source {thread} line {line_no} archives a command result in a \
         shape this build cannot read: no <{tag}> in either carrier field. Claude Code changed \
         the record shape — record it and teach the reader before enriching again."
    )]
    UnreadableCommandResult {
        session_id: String,
        /// The `source` column's value. Named for the thread it identifies, because
        /// `thiserror` reads a field called `source` as the error this one wraps.
        thread: String,
        line_no: i64,
        tag: &'static str,
    },
    #[error(
        "agent run {session_id}/{run_id} names parent run {parent}, which the store does not \
         hold — re-extract the session before enriching it"
    )]
    OrphanedRun {
        session_id: String,
        run_id: String,
        parent: String,
    },
    #[error("agent run {session_id}/{run_id} holds no turn and no api call")]
    EmptyRun { session_id: String, run_id: String },
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error(transparent)]
    DuckDb(#[from] duckdb::Error),
    #[error(transparent)]
    Row(#[from] hyphae_store::RowError),
}

/// What a row was written under. A row is current when its stamp equals today's.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Stamp {
    /// sha256 of the rendered prompt content — not of the instructions, which
    /// `prompt_version` covers.
    pub input_hash: String,
    pub prompt_version: i64,
    pub taxonomy_version: i64,
    pub model: String,
}

/// One agent run against whatever spawned it, as the records name it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunLink {
    pub session_id: String,
    pub run_id: String,
    /// The run whose transcript holds the spawning call, named either way the records name
    /// it.
    pub parent_run: Option<String>,
    /// The main turn holding the spawning call, when no run does. None alongside `parent_run`
    /// means nothing in the session embeds this run, and the session carries it directly.
    pub parent_turn: Option<String>,
}

/// The rows of the main transcript, or of every agent run — the two families a `source` has.
pub(crate) fn source_clause(alias: &str, main: bool) -> String {
    let operator = if main { "=" } else { "<>" };
    format!("{alias}.source {operator} '{MAIN}'")
}

/// Narrows a query already joined to `sessions s` to one analyzed repository.
pub(crate) fn project_clause(project: Option<&str>) -> String {
    match project {
        Some(_) => format!(" AND {}", project_predicate("s.project_dir", "$project")),
        None => String::new(),
    }
}

/// What [`project_clause`] binds. Named, so the path appears once however often the
/// predicate spells it.
pub(crate) fn project_params(project: Option<&str>) -> Vec<(&'static str, Param)> {
    project
        .map(|path| vec![("project", Param::from(path))])
        .unwrap_or_default()
}

/// Reads enrichable items out of a trace store and writes enrichments back to it.
#[derive(Debug)]
pub struct EnrichmentStore {
    store: Store,
}

impl EnrichmentStore {
    /// Open a trace store for enrichment, creating the enrichment tables it lacks.
    ///
    /// Enrichment reads the pipeline's views by name and column, so a store another schema
    /// wrote is not one this code can enrich, and it opens on the same terms as every other
    /// reader: nothing is created at a path that holds no store.
    pub fn open(path: &Path) -> Result<Self, EnrichError> {
        let store = Store::open_for_write(path)?;
        store.connection().execute_batch(&schema::ddl())?;
        Ok(Self { store })
    }

    pub fn store(&self) -> &Store {
        &self.store
    }

    pub fn connection(&self) -> &Connection {
        self.store.connection()
    }

    /// The planned items whose stored stamp is not the one the enricher would write now.
    ///
    /// Called again after every round: a child's new description changes its parents'
    /// rendered input, and only a fresh comparison sees that. `planned` is a sequence rather
    /// than a map because the answer keeps its order, which is the order a pass sends in.
    pub fn stale_keys(
        &self,
        level: Level,
        planned: &[(String, Stamp)],
    ) -> Result<Vec<String>, EnrichError> {
        let stored = self.stamps(level)?;
        Ok(planned
            .iter()
            .filter(|(key, stamp)| stored.get(key) != Some(stamp))
            .map(|(key, _)| key.clone())
            .collect())
    }

    /// Every stored stamp of one level, by item key.
    pub fn stamps(&self, level: Level) -> Result<HashMap<String, Stamp>, EnrichError> {
        let keys = level.keys().join(", ");
        let rows = self.store.fetch(
            &format!(
                "SELECT {keys}, input_hash, prompt_version, taxonomy_version, model
                 FROM {}",
                level.table()
            ),
            &[],
        )?;
        rows.iter()
            .map(|row| {
                let mut parts = vec![level.word().to_owned()];
                for column in level.keys() {
                    parts.push(row.str(column)?.to_owned());
                }
                Ok((parts.join("|"), read_stamp(row)?))
            })
            .collect()
    }

    /// Write one item's enrichment, replacing whatever the key held before.
    pub fn upsert(
        &self,
        item: &dyn Item,
        enrichment: &Enrichment,
        stamp: &Stamp,
    ) -> Result<(), EnrichError> {
        let level = item.level();
        let columns = level
            .keys()
            .iter()
            .copied()
            .chain(schema::PAYLOAD_COLUMNS.iter().copied())
            .collect::<Vec<_>>()
            .join(", ");
        let placeholders = vec!["?"; level.keys().len() + schema::PAYLOAD_COLUMNS.len()].join(", ");
        let mut bound: Vec<Box<dyn duckdb::ToSql>> = item
            .key_values()
            .into_iter()
            .map(|value| Box::new(value) as Box<dyn duckdb::ToSql>)
            .collect();
        bound.push(Box::new(enrichment.description.clone()));
        bound.push(Box::new(enrichment.category.clone()));
        bound.push(Box::new(enrichment.outcome.clone()));
        bound.push(Box::new(enrichment.friction.clone()));
        bound.push(Box::new(stamp.input_hash.clone()));
        bound.push(Box::new(stamp.prompt_version));
        bound.push(Box::new(stamp.taxonomy_version));
        bound.push(Box::new(stamp.model.clone()));
        bound.push(Box::new(Utc::now()));
        let references: Vec<&dyn duckdb::ToSql> =
            bound.iter().map(std::convert::AsRef::as_ref).collect();
        self.connection().execute(
            &format!(
                "INSERT OR REPLACE INTO {} ({columns}) VALUES ({placeholders})",
                level.table()
            ),
            references.as_slice(),
        )?;
        Ok(())
    }

    /// Delete enrichments whose base row is gone, and say how many there were.
    ///
    /// An extractor bump can redraw turn boundaries or drop a run, and the LEFT-joined views
    /// hide the leftovers completely — nothing else in the system would report them.
    pub fn sweep_zombies(&self) -> Result<usize, EnrichError> {
        let mut swept = 0;
        for level in Level::ALL {
            let matched = level
                .base_keys()
                .iter()
                .zip(level.keys())
                .map(|(base, key)| format!("b.{base} = e.{key}"))
                .collect::<Vec<_>>()
                .join(" AND ");
            swept += self.connection().execute(
                &format!(
                    "DELETE FROM {} e WHERE NOT EXISTS (SELECT 1 FROM {} b WHERE {matched})",
                    level.table(),
                    level.base(),
                ),
                [],
            )?;
        }
        Ok(swept)
    }
}

/// The four staleness fields of one stored row.
fn read_stamp(row: &Row) -> Result<Stamp, hyphae_store::RowError> {
    Ok(Stamp {
        input_hash: row.str("input_hash")?.to_owned(),
        prompt_version: row.i64("prompt_version")?,
        taxonomy_version: row.i64("taxonomy_version")?,
        model: row.str("model")?.to_owned(),
    })
}
