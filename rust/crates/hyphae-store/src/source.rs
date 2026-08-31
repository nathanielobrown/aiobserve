//! The trace store as an extractor: its rows back out as [`SessionTrace`]s.
//!
//! Pairs with an exporter that ships somewhere else — the OTLP export reads the store rather
//! than the transcripts on disk, because the store is the archive (a pruned session exists
//! only here), because a backend then mirrors exactly what the analyses and the viewer cite,
//! and because reading rows costs a fraction of re-parsing every record.
//!
//! Ported from `src/hyphae/extract/store.py`. Python rebuilds each row by handing a table's
//! columns to its dataclass positionally; here each table's reader names its columns, and
//! `Store::check_columns` is what still holds the column lists to the DDL.
//!
//! Provenance is not rebuilt — `extract_state`'s `extractor` and `extractor_version` come
//! back verbatim, naming the parser that produced the rows rather than this reader.

use std::path::Path;

use hyphae_extract::SessionSource;
use hyphae_extract::sessions::{project_predicate, resolve_project};
use hyphae_model::{
    AgentRun, ApiCall, Compaction, OffloadFile, PrLink, RawRecord, Session, SessionTrace, ToolCall,
    Turn,
};

use crate::row::Row;
use crate::{Param, Store, StoreError, schema};

/// What each table's rows are ordered by: its primary key, minus the `session_id` a single
/// session's read holds constant. List order carries no meaning — the model's lists are keyed
/// by natural ids — but a stable one keeps two exports of an unchanged session identical.
const ROW_ORDER: &[(&str, &[&str])] = &[
    ("sessions", &["id"]),
    ("turns", &["source", "id"]),
    ("api_calls", &["source", "id"]),
    ("tool_calls", &["source", "id"]),
    ("agent_runs", &["id"]),
    ("compactions", &["source", "id"]),
    ("pr_links", &["line_no"]),
    ("offload_files", &["name"]),
    ("raw_records", &["source", "line_no"]),
];

/// The tables that are the archive rather than the session's work: every line of every
/// transcript, and the tool outputs Claude Code wrote to files beside it. Nothing ships them,
/// so they say nothing about whether excluding a session loses anything.
const ARCHIVE_TABLES: &[&str] = &["raw_records", "offload_files"];

/// What reading the store back refuses.
#[derive(Debug, thiserror::Error)]
pub enum SourceError {
    /// A session that recorded no `project_dir` holds rows, so no filter can ship it.
    #[error(
        "Session {session_id} records no project_dir but holds {held}. It sits under no \
         project, so shipping it is impossible and skipping it would lose that work silently."
    )]
    UnplaceableSession { session_id: String, held: String },
    /// Asked for a session the store never extracted.
    #[error("{session_id} is not in this store")]
    UnknownSession { session_id: String },
    /// The store holds an `extract_state` row for a session but not one session row.
    #[error("{session_id} has an `extract_state` row but {rows} session rows")]
    MismatchedSession { session_id: String, rows: usize },
    /// Asked for a project no session in the store was recorded under.
    #[error(
        "No session in this store was recorded under {project}. Check the path, or run \
         `hp extract` for it first."
    )]
    UnknownProject { project: String },
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error(transparent)]
    Row(#[from] crate::RowError),
}

/// Reads one project's extracted sessions back out of a trace store.
///
/// Borrows an open store rather than a path: DuckDB admits one writer at a time, so the
/// exporter writing its delivery rows beside this reader has to be holding the same one.
pub struct StoreSource<'a> {
    store: &'a Store,
}

impl<'a> StoreSource<'a> {
    pub fn new(store: &'a Store) -> Self {
        Self { store }
    }

    /// Every extracted session recorded at or under `project`, resolved as typed.
    ///
    /// No files: the store is the source, so there is nothing on disk to stat, and the
    /// fingerprint is the one `extract_state` recorded when the rows were written.
    ///
    /// Sessions with no `project_dir` sit under no project and are excluded by the filter
    /// itself. That is only safe while they are empty, so one holding rows crashes here
    /// rather than disappearing. A project nothing was recorded under is refused too: the
    /// store is a finite corpus, so an empty answer here is a mistyped argument, and the
    /// export it feeds would otherwise report a clean delivery of nothing.
    pub fn sessions(&self, project: &Path) -> Result<Vec<SessionSource>, SourceError> {
        self.refuse_unplaceable_content()?;
        let placed = resolve_project(project).display().to_string();
        let rows = self.store.fetch(
            &format!(
                "SELECT e.session_id, e.fingerprint FROM extract_state e \
                 JOIN sessions s ON s.id = e.session_id \
                 WHERE {} ORDER BY e.session_id",
                project_predicate("s.project_dir", "$project")
            ),
            &[("project", Param::Text(placed.clone()))],
        )?;
        if rows.is_empty() {
            return Err(SourceError::UnknownProject { project: placed });
        }
        rows.iter()
            .map(|row| {
                Ok(SessionSource {
                    id: row.str("session_id")?.to_owned(),
                    files: Vec::new(),
                    fingerprint: row.str("fingerprint")?.to_owned(),
                })
            })
            .collect()
    }

    /// Rebuild one session's whole trace from its rows.
    pub fn extract(&self, source: &SessionSource) -> Result<SessionTrace, SourceError> {
        let id = source.id.as_str();
        let state = self.store.fetch(
            "SELECT extractor, extractor_version FROM extract_state WHERE session_id = $id",
            &[("id", Param::Text(id.to_owned()))],
        )?;
        let Some(state) = state.first() else {
            return Err(SourceError::UnknownSession {
                session_id: id.to_owned(),
            });
        };
        let sessions = self.read("sessions", id)?;
        if sessions.len() != 1 {
            return Err(SourceError::MismatchedSession {
                session_id: id.to_owned(),
                rows: sessions.len(),
            });
        }
        Ok(SessionTrace {
            extractor: state.str("extractor")?.to_owned(),
            extractor_version: state.str("extractor_version")?.to_owned(),
            session: session(&sessions[0])?,
            turns: self.rebuild("turns", id, turn)?,
            api_calls: self.rebuild("api_calls", id, api_call)?,
            tool_calls: self.rebuild("tool_calls", id, tool_call)?,
            agent_runs: self.rebuild("agent_runs", id, agent_run)?,
            compactions: self.rebuild("compactions", id, compaction)?,
            pr_links: self.rebuild("pr_links", id, pr_link)?,
            offload_files: self.rebuild("offload_files", id, offload_file)?,
            raw_records: self.rebuild("raw_records", id, raw_record)?,
        })
    }

    /// One table's rows for one session, each read into its model.
    fn rebuild<T>(
        &self,
        table: &str,
        session_id: &str,
        read: fn(&Row) -> Result<T, crate::RowError>,
    ) -> Result<Vec<T>, SourceError> {
        self.read(table, session_id)?
            .iter()
            .map(|row| read(row).map_err(SourceError::Row))
            .collect()
    }

    /// One table's rows for one session, in the order [`ROW_ORDER`] pins.
    fn read(&self, table: &str, session_id: &str) -> Result<Vec<Row>, SourceError> {
        let columns = schema::columns(table)
            .expect("every table read here is one the schema declares")
            .iter()
            .map(|column| format!("\"{column}\""))
            .collect::<Vec<_>>()
            .join(", ");
        let order = ROW_ORDER
            .iter()
            .find(|(name, _)| *name == table)
            .expect("every table read here has a row order")
            .1
            .iter()
            .map(|column| format!("\"{column}\""))
            .collect::<Vec<_>>()
            .join(", ");
        Ok(self.store.fetch(
            &format!(
                "SELECT {columns} FROM {table} WHERE {} = $id ORDER BY {order}",
                schema::session_key(table)
            ),
            &[("id", Param::Text(session_id.to_owned()))],
        )?)
    }

    /// Crash when a session with no `project_dir` holds work this filter would drop.
    ///
    /// The bar for a finding is that an absence is bounded: excluding a session because it
    /// names no project is honest only while excluding it loses nothing.
    ///
    /// Only the work tables can make it dishonest. Every transcript has lines, so a session
    /// that recorded nothing but its own opening bookkeeping still owns `raw_records` — the
    /// shape all four unplaceable sessions of the canonical store have. Those rows and the
    /// offloaded outputs beside them are the archive, which stays local whatever ships, so
    /// they are reported in the message and are never the reason for it.
    fn refuse_unplaceable_content(&self) -> Result<(), SourceError> {
        let owned: Vec<&str> = schema::TABLES
            .iter()
            .map(|(table, _)| *table)
            .filter(|table| *table != "sessions")
            .collect();
        let counts = owned
            .iter()
            .map(|table| {
                format!("(SELECT count(*) FROM {table} t WHERE t.session_id = s.id) AS {table}")
            })
            .collect::<Vec<_>>()
            .join(", ");
        let rows = self.store.fetch(
            &format!("SELECT s.id, {counts} FROM sessions s WHERE s.project_dir IS NULL"),
            &[],
        )?;
        for row in &rows {
            let found: Vec<(&str, i64)> = owned
                .iter()
                .map(|table| Ok((*table, row.i64(table)?)))
                .collect::<Result<_, crate::RowError>>()?;
            let works = found
                .iter()
                .any(|(table, count)| *count > 0 && !ARCHIVE_TABLES.contains(table));
            if !works {
                continue;
            }
            return Err(SourceError::UnplaceableSession {
                session_id: row.str("id")?.to_owned(),
                held: found
                    .iter()
                    .filter(|(_, count)| *count > 0)
                    .map(|(table, count)| format!("{table} {count}"))
                    .collect::<Vec<_>>()
                    .join(", "),
            });
        }
        Ok(())
    }
}

fn session(row: &Row) -> Result<Session, crate::RowError> {
    Ok(Session {
        id: row.str("id")?.to_owned(),
        project_dir: row.opt_str("project_dir")?.map(str::to_owned),
        git_branch: row.opt_str("git_branch")?.map(str::to_owned),
        version: row.opt_str("version")?.map(str::to_owned),
        entrypoint: row.opt_str("entrypoint")?.map(str::to_owned),
        started_at: row.opt_timestamp("started_at")?,
        ended_at: row.opt_timestamp("ended_at")?,
        active_ms: row.i64("active_ms")?,
        transcript_path: row.str("transcript_path")?.to_owned(),
        title: row.opt_str("title")?.map(str::to_owned),
        agent_name: row.opt_str("agent_name")?.map(str::to_owned),
    })
}

fn turn(row: &Row) -> Result<Turn, crate::RowError> {
    Ok(Turn {
        id: row.str("id")?.to_owned(),
        session_id: row.str("session_id")?.to_owned(),
        source: row.str("source")?.to_owned(),
        index: row.i64("index")? as i32,
        prompt: row.str("prompt")?.to_owned(),
        command_name: row.opt_str("command_name")?.map(str::to_owned),
        command_args: row.opt_str("command_args")?.map(str::to_owned),
        started_at: row.timestamp("started_at")?,
        ended_at: row.timestamp("ended_at")?,
        replayed: row.bool("replayed")?,
    })
}

fn api_call(row: &Row) -> Result<ApiCall, crate::RowError> {
    Ok(ApiCall {
        id: row.str("id")?.to_owned(),
        session_id: row.str("session_id")?.to_owned(),
        source: row.str("source")?.to_owned(),
        turn_id: row.opt_str("turn_id")?.map(str::to_owned),
        index: row.i64("index")? as i32,
        model: row.str("model")?.to_owned(),
        fallback_from: row.opt_str("fallback_from")?.map(str::to_owned),
        effort: row.opt_str("effort")?.map(str::to_owned),
        stop_reason: row.opt_str("stop_reason")?.map(str::to_owned),
        attribution_skill: row.opt_str("attribution_skill")?.map(str::to_owned),
        request_id: row.opt_str("request_id")?.map(str::to_owned),
        started_at: row.timestamp("started_at")?,
        ended_at: row.timestamp("ended_at")?,
        input_tokens: row.i64("input_tokens")?,
        output_tokens: row.i64("output_tokens")?,
        cache_read_tokens: row.i64("cache_read_tokens")?,
        cache_creation_tokens: row.i64("cache_creation_tokens")?,
        cache_5m_tokens: row.opt_i64("cache_5m_tokens")?,
        cache_1h_tokens: row.opt_i64("cache_1h_tokens")?,
        text: row.str("text")?.to_owned(),
        thinking: row.str("thinking")?.to_owned(),
        cost_usd: row.opt_f64("cost_usd")?,
        synthetic: row.bool("synthetic")?,
        replayed: row.bool("replayed")?,
    })
}

fn tool_call(row: &Row) -> Result<ToolCall, crate::RowError> {
    Ok(ToolCall {
        id: row.str("id")?.to_owned(),
        session_id: row.str("session_id")?.to_owned(),
        source: row.str("source")?.to_owned(),
        api_call_id: row.str("api_call_id")?.to_owned(),
        index: row.i64("index")? as i32,
        name: row.str("name")?.to_owned(),
        server_side: row.bool("server_side")?,
        input: row.str("input")?.to_owned(),
        result: row.opt_str("result")?.map(str::to_owned),
        offload_file: row.opt_str("offload_file")?.map(str::to_owned),
        is_error: row.bool("is_error")?,
        incomplete: row.bool("incomplete")?,
        started_at: row.timestamp("started_at")?,
        ended_at: row.opt_timestamp("ended_at")?,
        duration_synthetic: row.bool("duration_synthetic")?,
        replayed: row.bool("replayed")?,
    })
}

fn agent_run(row: &Row) -> Result<AgentRun, crate::RowError> {
    Ok(AgentRun {
        id: row.str("id")?.to_owned(),
        session_id: row.str("session_id")?.to_owned(),
        parent_agent_id: row.opt_str("parent_agent_id")?.map(str::to_owned),
        tool_use_id: row.opt_str("tool_use_id")?.map(str::to_owned),
        agent_type: row.str("agent_type")?.to_owned(),
        brief: row.opt_str("brief")?.map(str::to_owned),
        model: row.opt_str("model")?.map(str::to_owned),
        workflow_id: row.opt_str("workflow_id")?.map(str::to_owned),
        spawn_depth: row.opt_i64("spawn_depth")?.map(|depth| depth as i32),
        is_fork: row.bool("is_fork")?,
        fork_context_uuid: row.opt_str("fork_context_uuid")?.map(str::to_owned),
        started_at: row.opt_timestamp("started_at")?,
        ended_at: row.opt_timestamp("ended_at")?,
    })
}

fn compaction(row: &Row) -> Result<Compaction, crate::RowError> {
    Ok(Compaction {
        id: row.str("id")?.to_owned(),
        session_id: row.str("session_id")?.to_owned(),
        source: row.str("source")?.to_owned(),
        timestamp: row.timestamp("timestamp")?,
        trigger: row.str("trigger")?.to_owned(),
        pre_tokens: row.i64("pre_tokens")?,
        post_tokens: row.i64("post_tokens")?,
        duration_ms: row.i64("duration_ms")?,
    })
}

fn pr_link(row: &Row) -> Result<PrLink, crate::RowError> {
    Ok(PrLink {
        session_id: row.str("session_id")?.to_owned(),
        line_no: row.i64("line_no")? as i32,
        pr_number: row.i64("pr_number")? as i32,
        pr_url: row.str("pr_url")?.to_owned(),
        pr_repository: row.str("pr_repository")?.to_owned(),
        timestamp: row.timestamp("timestamp")?,
    })
}

fn offload_file(row: &Row) -> Result<OffloadFile, crate::RowError> {
    Ok(OffloadFile {
        session_id: row.str("session_id")?.to_owned(),
        name: row.str("name")?.to_owned(),
        content: row.str("content")?.to_owned(),
        lossy_decode: row.bool("lossy_decode")?,
        size_bytes: row.i64("size_bytes")?,
    })
}

fn raw_record(row: &Row) -> Result<RawRecord, crate::RowError> {
    Ok(RawRecord {
        session_id: row.str("session_id")?.to_owned(),
        source: row.str("source")?.to_owned(),
        line_no: row.i64("line_no")? as i32,
        uuid: row.opt_str("uuid")?.map(str::to_owned),
        timestamp: row.opt_timestamp("timestamp")?,
        r#type: row.str("type")?.to_owned(),
        raw: row.str("raw")?.to_owned(),
    })
}
