//! Runs one library query against the trace store and hands back its rows and its citation.
//!
//! Ported from `src/hyphae/analyze/runner.py`, which stays the authority. The store is opened
//! read-only, and every value the caller supplies reaches DuckDB as a bound parameter —
//! nothing is interpolated into SQL. A corpus query gets one thing from the runner that its
//! file does not define: `project_sessions`, the temp table holding the sessions `--project`
//! selected and whether each falls in the trailing window.

use std::path::{Path, PathBuf};

use chrono::NaiveDate;
use hyphae_extract::sessions::{project_predicate, resolve_project};
use hyphae_store::manifest::{self, ParamType, Scope};
use hyphae_store::{Param, Row, Store, StoreError, macros, queries};
use indexmap::IndexMap;

/// The two windows every count is reported in, as rows a count can group by. Written here
/// rather than in each query file so that a query that filtered its own window would not be a
/// second implementation of the recency rule, free to drift from the total it restricts.
const SESSION_PERIODS: &str = "\
CREATE OR REPLACE TEMP VIEW session_period AS
SELECT session_id, 'corpus' AS period FROM project_sessions
UNION ALL
SELECT session_id, 'trailing_window' AS period FROM project_sessions WHERE in_window";

/// What the runner puts in scope for a corpus query. A query that reads neither is not scoped
/// to `--project` at all, whatever its manifest says.
pub const CORPUS_RELATIONS: [&str; 2] = ["project_sessions", "session_period"];

/// Sessions no project predicate can place. They are excluded from every corpus count, so the
/// runner reports how many there were rather than leaving the gap silent.
const UNPLACEABLE: &str = "SELECT count(*) FROM sessions WHERE project_dir IS NULL";

/// The sessions `--project` selects, and the window flag every corpus query reads.
///
/// Written here rather than in each query file so that a query cannot scope itself
/// differently from the corpus it reports against.
fn project_sessions() -> String {
    format!(
        "\
CREATE OR REPLACE TEMP TABLE project_sessions AS
SELECT
    id AS session_id,
    started_at,
    coalesce(
        started_at >= $as_of::DATE - to_days($window_days::INTEGER)
            AND started_at < $as_of::DATE + INTERVAL 1 DAY,
        false
    ) AS in_window
FROM sessions
WHERE {}
  AND ($since::DATE IS NULL OR started_at >= $since::DATE)",
        project_predicate("project_dir", "$project")
    )
}

/// The caller asked for something the library cannot run, and it says which part.
///
/// One variant per refusal rather than a string, so the messages stay in one place beside
/// Python's — `tests/analyze/test_query.py` reads several of them word for word.
#[derive(Debug, thiserror::Error)]
pub enum QueryError {
    #[error("no query named '{name}'. Known queries: {known}")]
    UnknownQuery { name: String, known: String },
    #[error("{name} counts across sessions: it needs --project")]
    NeedsProject { name: String },
    #[error("{name} is keyed to one session: --project and --since mean nothing to it")]
    NotAcrossSessions { name: String },
    #[error("{name} declares no parameter named {unknown}")]
    UndeclaredParameter { name: String, unknown: String },
    #[error("{name} has no default for {missing}: bind each with --param {first}=<value>")]
    Unbound {
        name: String,
        missing: String,
        first: String,
    },
    #[error("--param {parameter}={text} is not a {kind}: {why}")]
    Unparseable {
        parameter: String,
        text: String,
        kind: &'static str,
        why: String,
    },
    #[error(transparent)]
    Store(#[from] StoreError),
}

/// What the caller is asking of the query, beside its name.
///
/// `params` are raw `k=v` values in the order the command line gave them; each is parsed to
/// the type its manifest entry declares. No defaults: every field is a decision `hp query`
/// makes from a flag, `as_of` included — its default is today, which is the clock's business
/// rather than this crate's.
#[derive(Debug, Clone)]
pub struct Request {
    pub project: Option<PathBuf>,
    pub since: Option<NaiveDate>,
    pub as_of: NaiveDate,
    pub params: IndexMap<String, String>,
}

/// One query's rows, and the line a report copies to show what produced them.
#[derive(Debug)]
pub struct QueryResult {
    pub name: String,
    /// Resolved bindings in citation order — every one at the value DuckDB actually saw.
    pub bindings: IndexMap<String, Param>,
    pub columns: Vec<String>,
    pub rows: Vec<Row>,
    /// Sessions with no `project_dir`; `None` for a keyed query, which asks about one session.
    pub unplaceable_sessions: Option<i64>,
}

impl QueryResult {
    /// Query file and resolved bindings, as a SQL comment: the claim's query.
    pub fn citation(&self) -> String {
        let bound: Vec<(&str, Param)> = self
            .bindings
            .iter()
            .map(|(name, value)| (name.as_str(), value.clone()))
            .collect();
        queries::citation(&self.name, &bound)
    }
}

/// Bind one library query and run it read-only against the store at `db`.
///
/// Refuses anything the manifest cannot account for — an unknown query, an undeclared
/// parameter, a required one left unbound, `--project` where it is needed or where it means
/// nothing.
pub fn run(db: &Path, name: &str, request: &Request) -> Result<QueryResult, QueryError> {
    let query = manifest::manifest()
        .get(name)
        .ok_or_else(|| QueryError::UnknownQuery {
            name: name.to_owned(),
            known: manifest::manifest()
                .keys()
                .map(String::as_str)
                .collect::<Vec<_>>()
                .join(", "),
        })?;
    let bindings = resolve(name, &query.params, &request.params)?;
    let corpus = query.scope == Scope::Corpus;
    match (corpus, request.project.as_deref(), request.since) {
        (true, None, _) => Err(QueryError::NeedsProject {
            name: name.to_owned(),
        }),
        (false, Some(_), _) | (false, _, Some(_)) => Err(QueryError::NotAcrossSessions {
            name: name.to_owned(),
        }),
        (true, Some(project), _) => finish(db, name, bindings, Some((project, request))),
        (false, None, None) => finish(db, name, bindings, None),
    }
}

/// Open the store, build the corpus if there is one, and run the query.
fn finish(
    db: &Path,
    name: &str,
    bindings: IndexMap<String, Param>,
    corpus: Option<(&Path, &Request)>,
) -> Result<QueryResult, QueryError> {
    let store = Store::open_read_only(db)?;
    macros::install(store.connection()).map_err(StoreError::from)?;
    let mut cited: IndexMap<String, Param> = IndexMap::new();
    let mut unplaceable = None;
    if let Some((project, request)) = corpus {
        cited = build_project_sessions(&store, project, request)?;
        unplaceable = Some(
            store
                .fetch(UNPLACEABLE, &[])?
                .first()
                .expect("count(*) answers one row")
                .i64("count_star()")
                .map_err(StoreError::from)?,
        );
    }
    let bound: Vec<(&str, Param)> = bindings
        .iter()
        .map(|(key, value)| (key.as_str(), value.clone()))
        .collect();
    let (columns, rows) = store.fetch_shape(queries::load(name), &bound)?;
    // Python's `cited | bindings`: the corpus bindings lead, and a query declaring one of
    // their names binds its own value in their place.
    cited.extend(bindings);
    Ok(QueryResult {
        name: name.to_owned(),
        bindings: cited,
        columns,
        rows,
        unplaceable_sessions: unplaceable,
    })
}

/// Materialize the corpus for `project`, and hand back the bindings that defined it.
fn build_project_sessions(
    store: &Store,
    project: &Path,
    request: &Request,
) -> Result<IndexMap<String, Param>, QueryError> {
    let bindings: IndexMap<String, Param> = [
        (
            "project".to_owned(),
            Param::Text(resolve_project(project).display().to_string()),
        ),
        ("since".to_owned(), Param::from(request.since)),
        ("as_of".to_owned(), Param::Date(request.as_of)),
        ("window_days".to_owned(), Param::Int(queries::WINDOW_DAYS)),
    ]
    .into_iter()
    .collect();
    let bound: Vec<(&str, Param)> = bindings
        .iter()
        .map(|(key, value)| (key.as_str(), value.clone()))
        .collect();
    store.fetch(&project_sessions(), &bound)?;
    store.fetch(SESSION_PERIODS, &[])?;
    Ok(bindings)
}

/// Parse what the caller passed, fill in the production defaults, refuse the rest.
fn resolve(
    name: &str,
    declared: &IndexMap<String, manifest::ParamSpec>,
    given: &IndexMap<String, String>,
) -> Result<IndexMap<String, Param>, QueryError> {
    let mut unknown: Vec<&str> = given
        .keys()
        .filter(|key| !declared.contains_key(*key))
        .map(String::as_str)
        .collect();
    if !unknown.is_empty() {
        unknown.sort_unstable();
        return Err(QueryError::UndeclaredParameter {
            name: name.to_owned(),
            unknown: unknown.join(", "),
        });
    }
    let mut resolved = IndexMap::new();
    let mut missing: Vec<&str> = Vec::new();
    for (parameter, spec) in declared {
        match (given.get(parameter), spec.binding()) {
            (Some(text), _) => {
                resolved.insert(parameter.clone(), parse(parameter, spec.kind, text)?);
            }
            (None, Some(default)) => {
                resolved.insert(parameter.clone(), default);
            }
            (None, None) => missing.push(parameter),
        }
    }
    if let Some(first) = missing.first() {
        return Err(QueryError::Unbound {
            name: name.to_owned(),
            missing: missing.join(", "),
            first: (*first).to_owned(),
        });
    }
    Ok(resolved)
}

/// One `--param` value as the type its query declared.
fn parse(parameter: &str, kind: ParamType, text: &str) -> Result<Param, QueryError> {
    let refuse = |why: String| QueryError::Unparseable {
        parameter: parameter.to_owned(),
        text: text.to_owned(),
        kind: match kind {
            ParamType::Text => "text",
            ParamType::Integer => "integer",
            ParamType::Date => "date",
        },
        why,
    };
    match kind {
        ParamType::Text => Ok(Param::Text(text.to_owned())),
        ParamType::Integer => text
            .parse::<i64>()
            .map(Param::Int)
            .map_err(|error| refuse(error.to_string())),
        ParamType::Date => NaiveDate::parse_from_str(text, "%Y-%m-%d")
            .map(Param::Date)
            .map_err(|error| refuse(error.to_string())),
    }
}
