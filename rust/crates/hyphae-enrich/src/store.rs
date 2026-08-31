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

use crate::items::{
    AgentRunItem, ApiCallRow, Item, RunSection, SessionChild, SessionItem, ToolCallRow, TurnItem,
};
use crate::schema::{self, Level};

/// The `source` every main transcript's rows carry (`hyphae_model::MAIN_SOURCE`).
const MAIN: &str = "main";

/// The tag Claude Code wraps a slash command's own output in, and the pattern that reads a
/// body out of it. `(?s)` is load-bearing: without it a multi-line body matches nothing and
/// extracts as the empty string, which is a state of its own.
const STDOUT_TAG: &str = "local-command-stdout";

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

/// One accepted model answer about one item.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Enrichment {
    /// One or two sentences saying what the item did.
    pub description: String,
    /// A member of the closed vocabulary in `enrich/taxonomy.py`, which the generation bridge
    /// carries across as data rather than as a second Rust enum.
    pub category: String,
    pub outcome: String,
    /// One line naming visible struggle. None when the records show none, the common case.
    pub friction: Option<String>,
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
fn source_clause(alias: &str, main: bool) -> String {
    let operator = if main { "=" } else { "<>" };
    format!("{alias}.source {operator} '{MAIN}'")
}

/// Narrows a query already joined to `sessions s` to one analyzed repository.
fn project_clause(project: Option<&str>) -> String {
    match project {
        Some(_) => format!(" AND {}", project_predicate("s.project_dir", "$project")),
        None => String::new(),
    }
}

/// What [`project_clause`] binds. Named, so the path appears once however often the
/// predicate spells it.
fn project_params(project: Option<&str>) -> Vec<(&'static str, Param)> {
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

    /// Every enrichable item of one level. The enricher's one door into the store.
    pub fn items(
        &self,
        level: Level,
        project: Option<&str>,
    ) -> Result<Vec<Box<dyn Item>>, EnrichError> {
        Ok(match level {
            Level::Turn => self
                .turn_items(project)?
                .into_iter()
                .map(|item| Box::new(item) as Box<dyn Item>)
                .collect(),
            Level::AgentRun => self
                .run_items(project)?
                .into_iter()
                .map(|item| Box::new(item) as Box<dyn Item>)
                .collect(),
            Level::Session => self
                .session_items(project)?
                .into_iter()
                .map(|item| Box::new(item) as Box<dyn Item>)
                .collect(),
        })
    }

    /// Every enrichable main turn, each carrying the api and tool calls it drove.
    ///
    /// `project` filters by the analyzed repository's resolved path, taking its worktrees
    /// with it (`sessions::project_predicate`); None takes every session in the store.
    pub fn turn_items(&self, project: Option<&str>) -> Result<Vec<TurnItem>, EnrichError> {
        let turns = self.store.fetch(
            &format!(
                r#"SELECT t.session_id, t.source, t.id, t."index", t.prompt,
                          t.command_name, t.command_args
                   FROM live_turns t JOIN sessions s ON s.id = t.session_id
                   WHERE {}{}
                   ORDER BY t.session_id, t."index" "#,
                source_clause("t", true),
                project_clause(project),
            ),
            &project_params(project),
        )?;
        let calls = self.api_calls(true, project)?;
        let results = self.command_results(project)?;
        // Keyed by the turn each call answers; a call belonging to no turn is dropped here.
        let mut by_turn: HashMap<(String, String, String), Vec<ApiCallRow>> = HashMap::new();
        for ((session_id, source), sequence) in &calls {
            for (turn_id, row) in sequence {
                if let Some(turn_id) = turn_id {
                    by_turn
                        .entry((session_id.clone(), source.clone(), turn_id.clone()))
                        .or_default()
                        .push(row.clone());
                }
            }
        }
        turns
            .iter()
            .map(|row| {
                let key = (
                    row.str("session_id")?.to_owned(),
                    row.str("source")?.to_owned(),
                    row.str("id")?.to_owned(),
                );
                Ok(TurnItem {
                    session_id: key.0.clone(),
                    source: key.1.clone(),
                    turn_id: key.2.clone(),
                    index: i32::try_from(row.i64("index")?).unwrap_or(i32::MAX),
                    prompt: row.str("prompt")?.to_owned(),
                    command_name: row.opt_str("command_name")?.map(str::to_owned),
                    command_args: row.opt_str("command_args")?.map(str::to_owned),
                    command_result: results.get(&key).cloned(),
                    api_calls: by_turn.get(&key).cloned().unwrap_or_default(),
                })
            })
            .collect()
    }

    /// What the CLI printed for each command turn, keyed by session, source and turn.
    ///
    /// A turn absent from the mapping had no such record archived, which is a different state
    /// from one whose record printed nothing. A record this build cannot classify raises
    /// rather than reading as either.
    fn command_results(
        &self,
        project: Option<&str>,
    ) -> Result<HashMap<(String, String, String), String>, EnrichError> {
        let body = format!("(?s)<{STDOUT_TAG}>(.*)</{STDOUT_TAG}>");
        let mut params = project_params(project);
        params.push(("body", Param::from(body.as_str())));
        let rows = self.store.fetch(
            &format!(
                r#"WITH carriers AS (
                       SELECT r.session_id, r.source, t.id AS turn_id, r.line_no,
                              -- The two recorded carriers: a `user` record holds the output
                              -- in its message, a `system`/`local_command` one at the top
                              -- level. Both are plain strings in every recorded case. A
                              -- list-shaped `message.content` would extract as the serialised
                              -- array, so a tag quoted inside it would match and pass the
                              -- guard below.
                              coalesce(json_extract_string(r.raw, '$.message.content'),
                                       json_extract_string(r.raw, '$.content')) AS carrier
                       FROM raw_records r
                       JOIN live_turns t
                         ON t.session_id = r.session_id AND t.source = r.source
                        AND t.id = json_extract_string(r.raw, '$.parentUuid')
                       JOIN sessions s ON s.id = r.session_id
                       WHERE r.raw LIKE '%<{STDOUT_TAG}>%'
                         AND t.command_name IS NOT NULL
                         AND {}{}
                   )
                   SELECT session_id, source, turn_id, line_no,
                          regexp_extract(carrier, $body, 1) AS body,
                          -- Tells "no match" from "matched nothing": without it an unreadable
                          -- record extracts as '', which is the printed-nothing state.
                          coalesce(regexp_matches(carrier, $body), false) AS readable
                   FROM carriers
                   ORDER BY session_id, source, turn_id, line_no"#,
                source_clause("t", true),
                project_clause(project),
            ),
            &params,
        )?;
        let mut results: HashMap<(String, String, String), String> = HashMap::new();
        for row in &rows {
            if !row.bool("readable")? {
                return Err(EnrichError::UnreadableCommandResult {
                    session_id: row.str("session_id")?.to_owned(),
                    thread: row.str("source")?.to_owned(),
                    line_no: row.i64("line_no")?,
                    tag: STDOUT_TAG,
                });
            }
            let key = (
                row.str("session_id")?.to_owned(),
                row.str("source")?.to_owned(),
                row.str("turn_id")?.to_owned(),
            );
            let body = row.str("body")?;
            // Ordered by line, so a turn answered over several records reads in sequence.
            results
                .entry(key)
                .and_modify(|held| {
                    held.push('\n');
                    held.push_str(body);
                })
                .or_insert_with(|| body.to_owned());
        }
        Ok(results)
    }

    /// Every agent run, each as the sequence of instructions and work its transcript holds.
    ///
    /// A run's api calls that belong to no turn of its own come first, as one continuation
    /// section: they are a fork's work on a conversation another transcript opened, and the
    /// turn its records replay is that other transcript's, not this run's.
    pub fn run_items(&self, project: Option<&str>) -> Result<Vec<AgentRunItem>, EnrichError> {
        let runs = self.store.fetch(
            &format!(
                r#"SELECT r.session_id, r.id, r.agent_type
                   FROM live_agent_runs r JOIN sessions s ON s.id = r.session_id
                   WHERE true{} ORDER BY r.session_id, r.id"#,
                project_clause(project),
            ),
            &project_params(project),
        )?;
        let mut turns: HashMap<(String, String), Vec<(String, String)>> = HashMap::new();
        for row in self.store.fetch(
            &format!(
                r#"SELECT t.session_id, t.source, t.id, t.prompt
                   FROM live_turns t JOIN sessions s ON s.id = t.session_id
                   WHERE {}{}
                   ORDER BY t.session_id, t.source, t."index" "#,
                source_clause("t", false),
                project_clause(project),
            ),
            &project_params(project),
        )? {
            turns
                .entry((
                    row.str("session_id")?.to_owned(),
                    row.str("source")?.to_owned(),
                ))
                .or_default()
                .push((row.str("id")?.to_owned(), row.str("prompt")?.to_owned()));
        }
        let calls = self.api_calls(false, project)?;
        let mut items = Vec::with_capacity(runs.len());
        for row in &runs {
            let session_id = row.str("session_id")?.to_owned();
            let run_id = row.str("id")?.to_owned();
            let local = turns
                .get(&(session_id.clone(), run_id.clone()))
                .cloned()
                .unwrap_or_default();
            let local_ids: Vec<&str> = local.iter().map(|(id, _)| id.as_str()).collect();
            let sequence = calls
                .get(&(session_id.clone(), run_id.clone()))
                .cloned()
                .unwrap_or_default();
            let mut sections: Vec<RunSection> = Vec::new();
            let continuation: Vec<ApiCallRow> = sequence
                .iter()
                .filter(|(turn_id, _)| turn_id.as_deref().is_none_or(|id| !local_ids.contains(&id)))
                .map(|(_, call)| call.clone())
                .collect();
            if !continuation.is_empty() {
                sections.push(RunSection {
                    prompt: None,
                    api_calls: continuation,
                });
            }
            for (turn_id, prompt) in &local {
                sections.push(RunSection {
                    prompt: Some(prompt.clone()),
                    api_calls: sequence
                        .iter()
                        .filter(|(held, _)| held.as_deref() == Some(turn_id.as_str()))
                        .map(|(_, call)| call.clone())
                        .collect(),
                });
            }
            if sections.is_empty() {
                // No turn and no api call: nothing to describe, and no recorded run is in
                // this state (2,459 scanned). Crash rather than buy a description of nothing.
                return Err(EnrichError::EmptyRun { session_id, run_id });
            }
            items.push(AgentRunItem {
                session_id,
                agent_run_id: run_id,
                agent_type: row.str("agent_type")?.to_owned(),
                sections,
            });
        }
        Ok(items)
    }

    /// Every api call of the selected sources, in order, with its tool calls attached.
    ///
    /// Keyed by session and source, each call paired with the turn it belongs to — which is
    /// None for a call no turn opened. Read in two queries and joined here rather than in
    /// SQL: a row per tool call would repeat every call's text once per tool.
    #[expect(
        clippy::type_complexity,
        reason = "the shape `turn_items` joins against"
    )]
    fn api_calls(
        &self,
        main: bool,
        project: Option<&str>,
    ) -> Result<HashMap<(String, String), Vec<(Option<String>, ApiCallRow)>>, EnrichError> {
        let spawned = self.spawned_descriptions()?;
        let mut tools: HashMap<(String, String, String), Vec<ToolCallRow>> = HashMap::new();
        for row in self.store.fetch(
            &format!(
                r#"SELECT c.session_id, c.source, c.api_call_id, c.id, c.name, c.input, c.result,
                          c.is_error, c.incomplete
                   FROM live_tool_calls c
                   JOIN live_api_calls a
                     ON a.session_id = c.session_id AND a.source = c.source
                    AND a.id = c.api_call_id
                   JOIN sessions s ON s.id = c.session_id
                   WHERE {}{}
                   ORDER BY c.session_id, c.source, c."index" "#,
                source_clause("c", main),
                project_clause(project),
            ),
            &project_params(project),
        )? {
            let call = (
                row.str("session_id")?.to_owned(),
                row.str("source")?.to_owned(),
            );
            let tool_call_id = row.str("id")?.to_owned();
            tools
                .entry((
                    call.0.clone(),
                    call.1.clone(),
                    row.str("api_call_id")?.to_owned(),
                ))
                .or_default()
                .push(ToolCallRow {
                    name: row.str("name")?.to_owned(),
                    input: row.str("input")?.to_owned(),
                    result: row.opt_str("result")?.map(str::to_owned),
                    is_error: row.bool("is_error")?,
                    incomplete: row.bool("incomplete")?,
                    spawned: spawned.get(&(call.0, call.1, tool_call_id)).cloned(),
                });
        }
        let mut calls: HashMap<(String, String), Vec<(Option<String>, ApiCallRow)>> =
            HashMap::new();
        for row in self.store.fetch(
            &format!(
                r#"SELECT a.session_id, a.source, a.turn_id, a.id, a.text, a.stop_reason
                   FROM live_api_calls a JOIN sessions s ON s.id = a.session_id
                   WHERE {}{}
                   ORDER BY a.session_id, a.source, a."index" "#,
                source_clause("a", main),
                project_clause(project),
            ),
            &project_params(project),
        )? {
            let key = (
                row.str("session_id")?.to_owned(),
                row.str("source")?.to_owned(),
            );
            let held = (key.0.clone(), key.1.clone(), row.str("id")?.to_owned());
            calls.entry(key).or_default().push((
                row.opt_str("turn_id")?.map(str::to_owned),
                ApiCallRow {
                    text: row.str("text")?.to_owned(),
                    stop_reason: row.opt_str("stop_reason")?.map(str::to_owned),
                    tool_calls: tools.get(&held).cloned().unwrap_or_default(),
                },
            ));
        }
        Ok(calls)
    }

    /// What each spawning tool call's run was described as, for the calls that have one.
    ///
    /// Keyed by the *call*, so a tool line can carry its child's description. A call recorded
    /// inside the very run it spawned is left out: forking replays the spawning call into the
    /// fork's own transcript, and a run embedding itself is a cycle.
    fn spawned_descriptions(
        &self,
    ) -> Result<HashMap<(String, String, String), String>, EnrichError> {
        let rows = self.store.fetch(
            "SELECT c.session_id, c.source, c.id, e.description
             FROM live_tool_calls c
             JOIN live_agent_runs r
               ON r.session_id = c.session_id AND r.tool_use_id = c.id
             JOIN agent_run_enrichments e
               ON e.session_id = r.session_id AND e.agent_run_id = r.id
             WHERE c.source <> r.id",
            &[],
        )?;
        rows.iter()
            .map(|row| {
                Ok((
                    (
                        row.str("session_id")?.to_owned(),
                        row.str("source")?.to_owned(),
                        row.str("id")?.to_owned(),
                    ),
                    row.str("description")?.to_owned(),
                ))
            })
            .collect()
    }

    /// Every session worth describing, with what it cost and what its children did.
    ///
    /// `describable_sessions` decides which those are: 102 of 575 recorded sessions hold no
    /// main turn and no agent run, and 45 more drove no api call under the turns they hold.
    pub fn session_items(&self, project: Option<&str>) -> Result<Vec<SessionItem>, EnrichError> {
        let mut children = self.session_children(project)?;
        self.store
            .fetch(
                &format!(
                    r#"SELECT r.session_id, s.title, s.git_branch, r.wall_ms, r.active_ms,
                              r.input_tokens, r.output_tokens, r.cache_read_tokens,
                              r.cache_creation_tokens, r.cost_usd
                       FROM describable_sessions r JOIN sessions s ON s.id = r.session_id
                       WHERE true{}
                       ORDER BY r.session_id"#,
                    project_clause(project),
                ),
                &project_params(project),
            )?
            .iter()
            .map(|row| {
                let session_id = row.str("session_id")?.to_owned();
                Ok(SessionItem {
                    children: children.remove(&session_id).unwrap_or_default(),
                    session_id,
                    title: row.opt_str("title")?.map(str::to_owned),
                    git_branch: row.opt_str("git_branch")?.map(str::to_owned),
                    wall_ms: row.opt_i64("wall_ms")?,
                    active_ms: row.opt_i64("active_ms")?,
                    input_tokens: row.i64("input_tokens")?,
                    output_tokens: row.i64("output_tokens")?,
                    cache_read_tokens: row.i64("cache_read_tokens")?,
                    cache_creation_tokens: row.i64("cache_creation_tokens")?,
                    cost_usd: row.f64("cost_usd")?,
                })
            })
            .collect()
    }

    /// What each session did directly, in the order it started doing it.
    ///
    /// Its main turns, plus the runs nothing in the session embeds — everything else reaches
    /// the session through the turn or the run whose prompt carries its description.
    fn session_children(
        &self,
        project: Option<&str>,
    ) -> Result<HashMap<String, Vec<SessionChild>>, EnrichError> {
        let direct: Vec<(String, String)> = self
            .run_links(project)?
            .into_iter()
            .filter(|link| link.parent_run.is_none() && link.parent_turn.is_none())
            .map(|link| (link.session_id, link.run_id))
            .collect();
        // Sorted on `started_at` with the nulls last, which is what Python's
        // `(started_at is None, started_at)` key does.
        let mut rows: Vec<(String, Option<i64>, SessionChild)> = Vec::new();
        for row in self.store.fetch(
            &format!(
                r#"SELECT t.session_id, epoch_ns(t.started_at) AS started, e.description,
                          e.category, e.outcome
                   FROM live_turns t JOIN sessions s ON s.id = t.session_id
                   LEFT JOIN turn_enrichments e
                     ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id
                   WHERE {}{}"#,
                source_clause("t", true),
                project_clause(project),
            ),
            &project_params(project),
        )? {
            rows.push((
                row.str("session_id")?.to_owned(),
                row.opt_i64("started")?,
                SessionChild {
                    level: Level::Turn,
                    agent_type: None,
                    description: row.opt_str("description")?.map(str::to_owned),
                    category: row.opt_str("category")?.map(str::to_owned),
                    outcome: row.opt_str("outcome")?.map(str::to_owned),
                },
            ));
        }
        for row in self.store.fetch(
            &format!(
                r#"SELECT r.session_id, r.id, r.agent_type, epoch_ns(r.started_at) AS started,
                          e.description, e.category, e.outcome
                   FROM live_agent_runs r JOIN sessions s ON s.id = r.session_id
                   LEFT JOIN agent_run_enrichments e
                     ON e.session_id = r.session_id AND e.agent_run_id = r.id
                   WHERE true{}"#,
                project_clause(project),
            ),
            &project_params(project),
        )? {
            let session_id = row.str("session_id")?.to_owned();
            let run_id = row.str("id")?.to_owned();
            if !direct.contains(&(session_id.clone(), run_id)) {
                continue;
            }
            rows.push((
                session_id,
                row.opt_i64("started")?,
                SessionChild {
                    level: Level::AgentRun,
                    agent_type: row.opt_str("agent_type")?.map(str::to_owned),
                    description: row.opt_str("description")?.map(str::to_owned),
                    category: row.opt_str("category")?.map(str::to_owned),
                    outcome: row.opt_str("outcome")?.map(str::to_owned),
                },
            ));
        }
        rows.sort_by_key(|(_, started, _)| (started.is_none(), *started));
        let mut children: HashMap<String, Vec<SessionChild>> = HashMap::new();
        for (session_id, _, child) in rows {
            children.entry(session_id).or_default().push(child);
        }
        Ok(children)
    }

    /// Each agent run against whatever spawned it, by both rules the records offer.
    ///
    /// `parent_agent_id` where the records name one, and otherwise the transcript holding the
    /// spawning tool call. Both are needed: 112 of 2,459 recorded runs name no parent agent
    /// yet were spawned from inside another run, and either rule alone strands them.
    ///
    /// Ordering cannot be right for a tree with a gap in it, so a run naming a parent run the
    /// store does not hold crashes here rather than being treated as a root.
    pub fn run_links(&self, project: Option<&str>) -> Result<Vec<RunLink>, EnrichError> {
        let rows = self.store.fetch(
            &format!(
                r#"SELECT r.session_id, r.id, r.parent_agent_id, c.source, a.turn_id
                   FROM live_agent_runs r
                   JOIN sessions s ON s.id = r.session_id
                   -- The spawning call, excluding the copy of itself a fork's own transcript
                   -- holds: a run is not its own parent.
                   LEFT JOIN live_tool_calls c
                     ON c.session_id = r.session_id AND c.id = r.tool_use_id AND c.source <> r.id
                   LEFT JOIN live_api_calls a
                     ON a.session_id = c.session_id AND a.source = c.source
                    AND a.id = c.api_call_id
                   WHERE true{}"#,
                project_clause(project),
            ),
            &project_params(project),
        )?;
        let held: Vec<(String, String)> = rows
            .iter()
            .map(|row| Ok((row.str("session_id")?.to_owned(), row.str("id")?.to_owned())))
            .collect::<Result<_, hyphae_store::RowError>>()?;
        let mut links = Vec::with_capacity(rows.len());
        for row in &rows {
            let session_id = row.str("session_id")?.to_owned();
            let run_id = row.str("id")?.to_owned();
            let source = row.opt_str("source")?;
            let run = match row.opt_str("parent_agent_id")? {
                Some(named) => Some(named.to_owned()),
                None => source.filter(|held| *held != MAIN).map(str::to_owned),
            };
            if let Some(parent) = &run
                && !held.contains(&(session_id.clone(), parent.clone()))
            {
                return Err(EnrichError::OrphanedRun {
                    session_id,
                    run_id,
                    parent: parent.clone(),
                });
            }
            let parent_turn = match run {
                Some(_) => None,
                None => row.opt_str("turn_id")?.map(str::to_owned),
            };
            links.push(RunLink {
                session_id,
                run_id,
                parent_run: run,
                parent_turn,
            });
        }
        Ok(links)
    }

    /// Each item's key against the key of the item whose prompt embeds its description.
    ///
    /// A run's parent is the agent that spawned it, or the main turn that did, or — when
    /// nothing in the session embeds it — the session itself. A main turn's parent is always
    /// its session. Sessions are not here: nothing embeds a session, so they are the roots
    /// every chain ends at.
    ///
    /// A run's `tool_use_id` alone would not do: 9 recorded runs were spawned by a
    /// main-transcript call belonging to no turn, and reading those as embedded by nothing
    /// *and* claimed by nothing would drop them out of every render there is.
    pub fn item_parents(
        &self,
        project: Option<&str>,
    ) -> Result<HashMap<String, String>, EnrichError> {
        let mut parents = HashMap::new();
        for link in self.run_links(project)? {
            let session_id = &link.session_id;
            let parent = match (&link.parent_run, &link.parent_turn) {
                (Some(run), _) => format!("{}|{session_id}|{run}", Level::AgentRun),
                (None, Some(turn)) => format!("{}|{session_id}|{MAIN}|{turn}", Level::Turn),
                (None, None) => format!("{}|{session_id}", Level::Session),
            };
            parents.insert(
                format!("{}|{session_id}|{}", Level::AgentRun, link.run_id),
                parent,
            );
        }
        for row in self.store.fetch(
            &format!(
                r#"SELECT t.session_id, t.id FROM live_turns t
                   JOIN sessions s ON s.id = t.session_id
                   WHERE {}{}"#,
                source_clause("t", true),
                project_clause(project),
            ),
            &project_params(project),
        )? {
            let session_id = row.str("session_id")?;
            parents.insert(
                format!("{}|{session_id}|{MAIN}|{}", Level::Turn, row.str("id")?),
                format!("{}|{session_id}", Level::Session),
            );
        }
        Ok(parents)
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
