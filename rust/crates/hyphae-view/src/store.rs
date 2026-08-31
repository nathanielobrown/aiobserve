//! Reading the trace store for one request: the connection, the queries, and a page of rows.
//!
//! Ported from `src/hyphae/view/store.py`. Every request opens its own read-only connection,
//! checks the schema version, reads, and closes — that is what lets an extract run while a
//! page is open, and what makes a store under someone else's write lock a 503 rather than a
//! crash. [`hyphae_store::Store`] owns the connection itself, the macro install and the lock
//! text; what this module adds is the catalog and the paging the viewer composes on top.
//!
//! The three enums are the viewer's whole query catalog, split by what a query is allowed to
//! select: a page or a fragment truncates every fat column in SQL, and a per-value query is
//! the declared exception. Naming a query in one of them is what puts it in reach of the
//! payload scans, so the union is also the checklist.

use std::path::{Path, PathBuf};

use hyphae_store::{Param, Row, Store, StoreError, queries};

/// The column both turn timelines are ordered by: unique and ascending within one thread, and
/// NULL on the row standing for the calls that answer no turn, which rides no page of them.
pub const TURN_CURSOR: &str = "turn_index";

/// What the composed window counts its pre-LIMIT matches into. A name of the composition and
/// not of any library query, which is what lets the query stay unlimited and citable.
pub const MATCHED_ROWS: &str = "matched_rows";

/// One query of the library, whichever of the three catalogs declares it.
///
/// A trait rather than one flat enum so the split survives the port: which catalog a query is
/// in is what says whether it may return a fat column whole.
pub trait Query: Copy {
    /// The file stem under `src/hyphae/analyze/queries/`.
    fn stem(self) -> &'static str;
}

/// The library queries the pages are built from, by the part each one fills.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Page {
    Sessions,
    /// Every project the store holds sessions for, which is the landing page: the counts a
    /// reader lands on are a corpus's, so they come from the `corpus_*` views.
    ProjectRollups,
    /// The names the list's project filter offers, which is a column of the store rather than
    /// of the page: the projects on one page of sessions are not the projects to filter by.
    Projects,
    SessionHeader,
    /// Every failed tool call of one session, across every thread — the one page the NavTree
    /// cannot lead to, because a failure is scattered rather than nested.
    SessionErrors,
    /// One node read whole, the header of its own page. One per kind that has fields of its
    /// own; a bucket has none, and a compaction reads out of `Page::Compactions`.
    RunHeader,
    TurnHeader,
    CallHeader,
    ToolHeader,
    /// The levels of the NavTree beside a node page: one thin row per child, whatever the
    /// level holds. One query per kind of child rather than per kind of parent, so a turn's
    /// calls are read the same way under a session, under a run, or under a bucket.
    NavTreeTurns,
    NavTreeCalls,
    NavTreeTools,
    /// The two turn timelines, shared with `hp query` — the same rows a report cites. One
    /// query per thread kind: `session_timeline` reads `main`, `run_timeline` a bound source.
    Timeline,
    RunTimeline,
    Runs,
    Compactions,
    /// What an enrichment pass said about the session, its turns and its runs. Absent from a
    /// store no pass has written to, which is why a page asks before it runs.
    Enrichment,
    /// The same for the list: what the pass said each session was, joined to the page of rows
    /// the list just read. Absent from an un-enriched store for the same reason.
    DescribedSessions,
    /// One page of a thread's raw transcript, previewed a record per row, and the line each of
    /// the thread's turns was read from — what turns a timeline row into a link into it.
    Records,
    TurnRecords,
    /// One chunk of a tool result written to a file beside the transcript.
    Offload,
}

impl Page {
    /// Every one of them, so a sweep over the pages is a sweep over the whole set.
    pub const ALL: [Self; 21] = [
        Self::Sessions,
        Self::ProjectRollups,
        Self::Projects,
        Self::SessionHeader,
        Self::SessionErrors,
        Self::RunHeader,
        Self::TurnHeader,
        Self::CallHeader,
        Self::ToolHeader,
        Self::NavTreeTurns,
        Self::NavTreeCalls,
        Self::NavTreeTools,
        Self::Timeline,
        Self::RunTimeline,
        Self::Runs,
        Self::Compactions,
        Self::Enrichment,
        Self::DescribedSessions,
        Self::Records,
        Self::TurnRecords,
        Self::Offload,
    ];
}

impl Query for Page {
    fn stem(self) -> &'static str {
        match self {
            Self::Sessions => "view_sessions",
            Self::ProjectRollups => "view_project_rollups",
            Self::Projects => "view_projects",
            Self::SessionHeader => "view_session_header",
            Self::SessionErrors => "view_session_errors",
            Self::RunHeader => "view_run_header",
            Self::TurnHeader => "view_turn_header",
            Self::CallHeader => "view_call_header",
            Self::ToolHeader => "view_tool_header",
            Self::NavTreeTurns => "view_nav_tree_turns",
            Self::NavTreeCalls => "view_nav_tree_calls",
            Self::NavTreeTools => "view_nav_tree_tools",
            Self::Timeline => "session_timeline",
            Self::RunTimeline => "run_timeline",
            Self::Runs => "view_runs",
            Self::Compactions => "view_compactions",
            Self::Enrichment => "view_enrichment",
            Self::DescribedSessions => "view_described_sessions",
            Self::Records => "view_records",
            Self::TurnRecords => "view_turn_records",
            Self::Offload => "view_offload",
        }
    }
}

/// The library queries htmx fetches a page of at a time, on expanding something.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Fragment {
    /// One page of the api calls under a turn, and one page of the tool calls under a call.
    TurnCalls,
    CallTools,
    /// The numbers behind one NavTree row, fetched when a reader points at it: what the row
    /// draws as a bar and a badge, written out. One query for every kind made of api calls,
    /// and one apiece for the two kinds made of none — the tool call and the compaction.
    Numbers,
    ToolNumbers,
    CompactionNumbers,
}

impl Fragment {
    /// Every one of them, so a sweep over the fragments is a sweep over the whole set.
    pub const ALL: [Self; 5] = [
        Self::TurnCalls,
        Self::CallTools,
        Self::Numbers,
        Self::ToolNumbers,
        Self::CompactionNumbers,
    ];
}

impl Query for Fragment {
    fn stem(self) -> &'static str {
        match self {
            Self::TurnCalls => "view_turn_calls",
            Self::CallTools => "view_call_tools",
            Self::Numbers => "view_numbers",
            Self::ToolNumbers => "view_numbers_tool",
            Self::CompactionNumbers => "view_numbers_compaction",
        }
    }
}

/// The library queries that fetch one whole value: the exception to the page bound.
///
/// Every other query truncates in SQL. These return a fat column untruncated because the unit
/// *is* one value — the bound is the largest single value in the store, not a page's worth of
/// them, and it is only reached when a reader opens that one value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Whole {
    CallText,
    CallThinking,
    /// What one tool call was asked and what it returned, one value each rather than the row
    /// whole: a pane previews the two apart, so each has its own way to the rest of it.
    ToolInput,
    ToolResult,
    /// And what a `Bash` call ran, which the input holds escaped onto one line: a value of its
    /// own because a shell command is read as shell, not as a string inside JSON.
    ToolCommand,
    Record,
    /// What a turn was asked, what followed the command a slash turn ran, and what an agent
    /// run was briefed with. Each is a value a pane previews, cut in the node's header query
    /// and fetched whole here.
    TurnPrompt,
    TurnCommandArgs,
    RunBrief,
    /// And the two a run's page reads off the call that spawned it: what that call asked for,
    /// and what it returned to the agent that made it.
    RunPrompt,
    RunResult,
    /// And the two lines an enrichment pass wrote about an item, one query per level: what the
    /// model said the item did, and the friction it saw in it.
    TurnSaid,
    RunSaid,
    SessionSaid,
}

impl Whole {
    /// Every one of them: the declared exceptions to the page bound, in one list.
    pub const ALL: [Self; 14] = [
        Self::CallText,
        Self::CallThinking,
        Self::ToolInput,
        Self::ToolResult,
        Self::ToolCommand,
        Self::Record,
        Self::TurnPrompt,
        Self::TurnCommandArgs,
        Self::RunBrief,
        Self::RunPrompt,
        Self::RunResult,
        Self::TurnSaid,
        Self::RunSaid,
        Self::SessionSaid,
    ];
}

impl Query for Whole {
    fn stem(self) -> &'static str {
        match self {
            Self::CallText => "view_call_text",
            Self::CallThinking => "view_call_thinking",
            Self::ToolInput => "view_tool_input",
            Self::ToolResult => "view_tool_result",
            Self::ToolCommand => "view_tool_command",
            Self::Record => "view_record",
            Self::TurnPrompt => "view_turn_prompt",
            Self::TurnCommandArgs => "view_turn_command_args",
            Self::RunBrief => "view_run_brief",
            Self::RunPrompt => "view_run_prompt",
            Self::RunResult => "view_run_result",
            Self::TurnSaid => "view_turn_said",
            Self::RunSaid => "view_run_said",
            Self::SessionSaid => "view_session_said",
        }
    }
}

/// What a request cannot be answered with, and what the reader is told instead.
#[derive(Debug, thiserror::Error)]
pub enum ViewError {
    #[error(transparent)]
    Store(#[from] StoreError),
    /// A column a query was expected to ship, read off a row that does not carry it.
    #[error(transparent)]
    Row(#[from] hyphae_store::RowError),
    /// A query gave more rows outside every page than the page that renders them budgeted.
    #[error("{query} gave more than {limit} row(s) with no {cursor}")]
    Cursorless {
        query: &'static str,
        cursor: &'static str,
        limit: usize,
    },
    /// A URL naming something the store does not hold.
    #[error("{0}")]
    NoSuchNode(String),
    /// The store gave a shape the viewer has no page for, such as a chain past [`crate::knobs::DEPTH`].
    #[error("{0}")]
    Shape(String),
}

/// The store one viewer serves, opened afresh for every request.
///
/// Holds a path and no connection: DuckDB admits one writer, and a viewer that kept a reader
/// open would pin the file for as long as it ran.
#[derive(Debug, Clone)]
pub struct Reader {
    path: PathBuf,
}

impl Reader {
    /// The reader over the store at `path`, which must exist and hold this schema.
    ///
    /// Opens once here so a typo in `--db` refuses at startup rather than opening a browser
    /// onto an error page.
    pub fn open(path: &Path) -> Result<Self, ViewError> {
        // Before resolving, because a path with nothing behind it does not resolve and the
        // I/O error that comes back names no file — the one thing a typo needs said.
        let resolved = path
            .canonicalize()
            .map_err(|_| StoreError::NoStore(path.to_owned()))?;
        let reader = Self { path: resolved };
        reader.connect()?;
        Ok(reader)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// A read-only connection for one request, checked and dropped with the response.
    ///
    /// The version is checked per request rather than at startup because an extract can land
    /// between two page loads.
    pub fn connect(&self) -> Result<Store, ViewError> {
        let store = Store::open_read_only(&self.path)?;
        store.check_version()?;
        Ok(store)
    }
}

/// The rows of one library query, bound as given — Python's `page_rows`.
pub fn page_rows(
    store: &Store,
    query: impl Query,
    bindings: &[(&str, Param)],
) -> Result<Vec<Row>, ViewError> {
    Ok(store.fetch(queries::load(query.stem()), bindings)?)
}

/// One keyset page: the rows, what is behind them, and where to resume.
///
/// The records browser's way of paging, and the only one left: a citation names a line, so the
/// page for it is the one that *starts* at that line rather than the nth page of the thread.
#[derive(Debug)]
pub struct Paged {
    pub rows: Vec<Row>,
    /// How many rows the cap cut, for the "+N more" the page shows instead of losing them.
    pub more: i64,
    /// The `$after` cursor the next fetch binds, or `None` when this page is the last.
    pub after: Option<i64>,
}

/// One numbered page of a level: the rows, and how many the level holds in all.
///
/// `total` is the count before the LIMIT bit, which is what lets a page say which of how many
/// it is — and what lets a heading count the level rather than the rows in front of the reader.
#[derive(Debug)]
pub struct Listed {
    pub rows: Vec<Row>,
    pub total: i64,
}

/// One numbered page of a library query that limits nothing itself.
///
/// A query whose whole result a report quotes cannot carry a viewer's LIMIT, so the viewer
/// wraps it. Rows come back ordered by `cursor`, which is a column name this package supplies
/// — never request text — while `skipped` and `size` bind. A row the query gives no cursor
/// value is outside every page and outside the count ([`cursorless_rows`]).
pub fn window(
    store: &Store,
    query: impl Query,
    cursor: &'static str,
    skipped: i64,
    size: i64,
    bindings: &[(&str, Param)],
) -> Result<Listed, ViewError> {
    let sql = format!(
        "SELECT *, count(*) OVER () AS {MATCHED_ROWS} FROM ({core}) \
         WHERE {cursor} IS NOT NULL ORDER BY {cursor} LIMIT $size OFFSET $skipped",
        core = core(query),
    );
    let mut bound = bindings.to_vec();
    bound.push(("skipped", Param::Int(skipped)));
    bound.push(("size", Param::Int(size)));
    let rows = store.fetch(&sql, &bound)?;
    Ok(listed(rows, MATCHED_ROWS)?)
}

/// A whole thread in outline — a timeline's rows, id and cursor and clock only.
///
/// Two questions need the thread and not the page: which runs the session could place, and
/// which page each compaction falls on. Both are cheap here because the projection is three
/// scalars; neither can be answered from a window without changing what the answer means.
pub fn thread_outline(
    store: &Store,
    query: impl Query,
    cursor: &'static str,
    bindings: &[(&str, Param)],
) -> Result<Vec<Row>, ViewError> {
    let sql = format!(
        "SELECT turn_id, {cursor}, started_at FROM ({core}) ORDER BY {cursor} NULLS LAST",
        core = core(query),
    );
    Ok(store.fetch(&sql, bindings)?)
}

/// The rows a paged query gives no cursor value, which no window can reach.
///
/// The timelines' unattributed row is the case: it stands for the calls that answer no turn,
/// so it has no turn index and rides the last page instead. `limit` is what the page that
/// renders them budgeted; a query answering with more is an error, because these rows arrive
/// outside the size the reader asked for and a page that serves them anyway is a page whose
/// ceiling was computed against something else.
pub fn cursorless_rows(
    store: &Store,
    query: impl Query,
    cursor: &'static str,
    limit: usize,
    bindings: &[(&str, Param)],
) -> Result<Vec<Row>, ViewError> {
    let sql = format!(
        "SELECT * FROM ({core}) WHERE {cursor} IS NULL LIMIT $cursorless",
        core = core(query),
    );
    let mut bound = bindings.to_vec();
    bound.push(("cursorless", Param::Int(limit as i64 + 1)));
    let rows = store.fetch(&sql, &bound)?;
    if rows.len() > limit {
        return Err(ViewError::Cursorless {
            query: query.stem(),
            cursor,
            limit,
        });
    }
    Ok(rows)
}

/// A page of rows and the size of the level it came from, out of the query's own count.
///
/// `matched` names the column carrying how many rows matched before the LIMIT, which the
/// paging queries compute with a window function — so a page knows the whole level without a
/// second query, and a level whose page is empty is one whose pages ran out.
pub fn listed(rows: Vec<Row>, matched: &str) -> Result<Listed, StoreError> {
    let total = match rows.first() {
        None => 0,
        Some(row) => row.i64(matched)?,
    };
    Ok(Listed { rows, total })
}

/// A page of rows and its continuation, from a query's own pre-LIMIT match count.
///
/// `matched` names the column carrying how many rows the cursor had ahead of it, which the
/// paging queries compute with a window function — so a page knows what it cut without a
/// second query, and cannot report "+0 more" for rows it silently dropped.
pub fn paged(rows: Vec<Row>, matched: &str, cursor: &str) -> Result<Paged, StoreError> {
    let Some(first) = rows.first() else {
        return Ok(Paged {
            rows,
            more: 0,
            after: None,
        });
    };
    let more = first.i64(matched)? - rows.len() as i64;
    let after = match rows.last() {
        Some(last) if more > 0 => Some(last.i64(cursor)?),
        _ => None,
    };
    Ok(Paged { rows, more, after })
}

/// One library query as a subquery: its own text, unchanged, ready to be wrapped.
fn core(query: impl Query) -> &'static str {
    queries::load(query.stem()).trim().trim_end_matches(';')
}
