//! The NavTree beside a node page: the path down to the selection, and only that path opened.
//!
//! Ported from `src/hyphae/view/nav_tree.py`. Every node of a session has a URL of its own, and
//! the NavTree is how a reader walks between them. What renders is one open path — the
//! selection's ancestors, the selection, and the selection's children — so a session's whole
//! shape is never on the page at once. The rows come back flat, in document order, because a
//! click swaps the list out of band and a nested list would swap only the part of itself the
//! click happened to land in.
//!
//! The path is resolved bottom-up in refs, which are ids and nothing else, and then expanded
//! top-down: a node renders out of its parent's level, so every visible node has a visible
//! parent by construction rather than by a check afterwards.
//!
//! Reads the store one level at a time and says which query it ran, so the page can cite it.
//! Everything else here is arithmetic over the rows those queries returned.

use chrono::{DateTime, Utc};
use hyphae_store::{Param, Row, Store, queries};

use crate::builders::{
    call_node, compaction_node, run_node, tool_node, turn_node, unattached_node, unattributed_node,
};
use crate::components::nav_tree::NavTreeRow;
use crate::enrichment::Descriptions;
use crate::knobs::{CURSORLESS_TURNS, DEPTH};
use crate::nodes::{Kind, Ledger, Node, Preset, Ref};
use crate::store::{Page, Query, TURN_CURSOR, ViewError, cursorless_rows, page_rows};

/// The main thread, the one every session's NavTree opens on.
pub const MAIN_SOURCE: &str = "main";

/// What one query was bound with, in the order the manifest declares its parameters.
pub type Bound = Vec<(&'static str, Param)>;

/// What one level's queries were bound with, in the order the level ran them.
///
/// The query is its file stem rather than a catalog member, because a level may run a
/// [`Page`] or a [`crate::store::Fragment`] and a citation names the file either way.
pub type Ran = Vec<(&'static str, Bound)>;

/// What every level of one session's NavTree is built against, read once for the request.
///
/// The runs are the session's whole set because a run is placed by the call that spawned it
/// rather than by the thread it ran on, so any level may need any of them. The enrichment is read
/// for one thread — `view_enrichment` keys turns by source — so a turn on another thread reads by
/// its prompt while runs and the session, which are keyed by the session, do not.
pub struct Corpus {
    pub session_id: String,
    /// What the session spent and where its agent runs charged it: the basis every share on the
    /// NavTree is a share of, and the subtree totals the dual badge draws.
    pub held: Ledger,
    pub runs: Vec<Row>,
    pub described: Descriptions,
    /// The thread the enrichment was read for.
    pub source: String,
}

impl Corpus {
    /// What the pass called one turn, or nothing when it said nothing about it.
    pub fn turn_text(&self, source: &str, turn_id: &str) -> Option<&str> {
        if source != self.source {
            return None;
        }
        self.described.turn(turn_id)
    }

    /// What the pass called one run, or nothing when it said nothing about it.
    pub fn run_text(&self, run_id: &str) -> Option<&str> {
        self.described.run(run_id)
    }

    /// One run row of this session by id, or nothing where the session does not hold it.
    fn run(&self, run_id: &str) -> Option<&Row> {
        self.runs
            .iter()
            .find(|run| run.str("run_id").is_ok_and(|held| held == run_id))
    }
}

/// The children of one open node, and the queries that read them.
pub struct Level {
    pub nodes: Vec<Node>,
    pub ran: Ran,
}

/// A whole NavTree: its rows in document order, the open path, and every query it ran.
pub struct NavTree {
    pub rows: Vec<NavTreeRow>,
    /// The open path as rendered nodes, outermost first — what the crumbs above the pane show.
    pub chain: Vec<Node>,
    pub ran: Ran,
}

/// The whole path down to the last step of `trail`, session first.
///
/// `trail` is what the selection's own header already answered: a call and a tool know which turn
/// they sit under, and nothing else needs a read to place itself. Refuses past [`DEPTH`] rather
/// than opening a chain the response was never priced for.
pub fn ancestry(corpus: &Corpus, trail: &[Ref]) -> Result<Vec<Ref>, ViewError> {
    let mut whole = trail.to_vec();
    loop {
        let above = parents(corpus, &whole[0])?;
        if above.is_empty() {
            return Ok(whole);
        }
        whole.splice(0..0, above);
        if whole.len() > DEPTH {
            return Err(ViewError::Shape(format!(
                "a chain deeper than {DEPTH} is not a page this serves"
            )));
        }
    }
}

/// The steps above one node, outermost first — none for the session, which hangs nowhere.
///
/// More than one where a node hangs off rows its own header cannot name: a run sits under the
/// tool call that spawned it, under that call's api call, under the turn the call answered.
fn parents(corpus: &Corpus, at: &Ref) -> Result<Vec<Ref>, ViewError> {
    match at.kind {
        Kind::Session => Ok(Vec::new()),
        Kind::Unattached => Ok(vec![Ref::new(Kind::Session, None, &corpus.session_id)]),
        // A compaction reaches here only where it happened between two turns: one that happened
        // during a turn is that turn's child, and its page seeds the turn.
        Kind::Turn | Kind::Compaction | Kind::Unattributed => {
            Ok(vec![thread_parent(corpus, source_of(at))])
        }
        Kind::Run => run_parents(corpus, &at.node_id),
        Kind::Call | Kind::Tool => Err(ViewError::Shape(format!(
            "a {} node's header names its parent; seed the trail",
            at.kind
        ))),
    }
}

/// The thread a ref was recorded on. Every kind that reaches this carries one.
fn source_of(at: &Ref) -> &str {
    at.source
        .as_deref()
        .unwrap_or_else(|| panic!("a {} node was placed with no thread", at.kind))
}

/// What a thread hangs off: the session for `main`, else the run that thread belongs to.
fn thread_parent(corpus: &Corpus, source: &str) -> Ref {
    if source == MAIN_SOURCE {
        return Ref::new(Kind::Session, None, &corpus.session_id);
    }
    Ref::new(Kind::Run, Some(source), source)
}

/// The rows a run hangs under, by the spawning edge alone: its turn, api call and tool call.
///
/// A resolved spawning call puts the run under the tool row that asked for it, under the call that
/// ran the tool, under the turn that call answered — or that thread's unattributed bucket, where
/// the call answered none. No spawning call at all puts the run in the session's unattached bucket
/// with no row in between, and a run the session does not hold reads the same way. The two buckets
/// stay disjoint by the same edge.
///
/// One resolved edge resolves all three: `view_runs` reaches the turn through the tool call and
/// its api call, so a spawn source is what says the rows above exist.
fn run_parents(corpus: &Corpus, run_id: &str) -> Result<Vec<Ref>, ViewError> {
    let loose = vec![Ref::new(Kind::Unattached, None, &corpus.session_id)];
    let Some(row) = corpus.run(run_id) else {
        return Ok(loose);
    };
    let Some(source) = row.opt_str("spawn_source")? else {
        return Ok(loose);
    };
    Ok(vec![
        home(source, row.opt_str("spawn_turn_id")?),
        Ref::new(Kind::Call, Some(source), row.str("spawn_call_id")?),
        Ref::new(Kind::Tool, Some(source), row.str("tool_use_id")?),
    ])
}

/// A bucket's own row, beside the query line that produced it.
pub struct Standing {
    pub row: Row,
    pub ran: (&'static str, Bound),
}

/// Where an api call sits: under the turn it answers, else in its thread's bucket.
///
/// The disjointness rule at the call, which [`run_parents`] reads one edge further out for the run
/// that call spawned. A NULL turn is a home rather than a missing one.
pub fn home(source: &str, turn_id: Option<&str>) -> Ref {
    match turn_id {
        None => Ref::new(Kind::Unattributed, Some(source), source),
        Some(turn_id) => Ref::new(Kind::Turn, Some(source), turn_id),
    }
}

/// Which timeline answers for a thread, and what it binds: `main` has one of its own.
fn timeline(session_id: &str, source: &str) -> (Page, Bound) {
    let mut bound: Bound = vec![
        ("session_id", session_id.into()),
        ("log_chars", Param::Int(queries::LOG_CHARS as i64)),
    ];
    if source == MAIN_SOURCE {
        return (Page::Timeline, bound);
    }
    bound.push(("source", source.into()));
    (Page::RunTimeline, bound)
}

/// One thread's calls that answer no turn, as its timeline's own cursorless row reads them.
///
/// Nothing where every call on the thread answers a turn — and where the thread is not one this
/// session holds, which is the same answer: there is no bucket at that URL either way.
pub fn unattributed(
    store: &Store,
    corpus: &Corpus,
    source: &str,
) -> Result<Option<Standing>, ViewError> {
    let (query, bound) = timeline(&corpus.session_id, source);
    let rows = cursorless_rows(store, query, TURN_CURSOR, CURSORLESS_TURNS, &bound)?;
    Ok(rows.into_iter().next().map(|row| Standing {
        row,
        ran: (query.stem(), bound),
    }))
}

/// One thread's own children: its turns and the compactions between them, then its buckets.
///
/// A session and a run read alike — the difference is the source, and that only the session holds
/// the unattached bucket, which spans every thread rather than sitting on one. Only the
/// compactions that happened between two turns are here; one that happened *during* a turn is a
/// child of that turn ([`marks`]).
fn thread_level(
    store: &Store,
    corpus: &Corpus,
    source: &str,
    unattached: bool,
) -> Result<Level, ViewError> {
    // One binding list per query, because the two take different widths — and the mapping a query
    // runs under is the mapping it is cited by, so a reader re-running the line gets this page.
    let listed: Bound = vec![
        ("session_id", corpus.session_id.as_str().into()),
        ("source", source.into()),
        ("nav_chars", Param::Int(queries::NAV_CHARS as i64)),
    ];
    let chipped: Bound = vec![
        ("session_id", corpus.session_id.as_str().into()),
        ("source", source.into()),
        ("chip_chars", Param::Int(queries::NAV_CHARS as i64)),
    ];
    let turns = page_rows(store, Page::NavTreeTurns, &listed)?;
    let compactions = page_rows(store, Page::Compactions, &chipped)?;
    // The thread's calls that answer no turn, as one group — the bucket's own row, read the same
    // way the bucket's own page reads it.
    let standing = unattributed(store, corpus, source)?;
    let (query, bound) = timeline(&corpus.session_id, source);

    let mut ordered = Vec::with_capacity(turns.len());
    for row in &turns {
        let said = corpus.turn_text(source, row.str("turn_id")?);
        ordered.push((
            turn_node(&corpus.session_id, source, row, &corpus.held, said)?,
            row.opt_timestamp("started_at")?,
        ));
    }
    let mut placed = interleave(ordered, between(corpus, source, &compactions, None)?);
    if let Some(standing) = &standing {
        placed.push(unattributed_node(
            &corpus.session_id,
            source,
            &standing.row,
            &corpus.held,
        )?);
    }
    if unattached {
        let loose = loose_runs(corpus)?;
        if !loose.is_empty() {
            placed.push(unattached_node(&corpus.session_id, &loose, &corpus.held)?);
        }
    }
    Ok(Level {
        nodes: placed,
        ran: vec![
            (Page::NavTreeTurns.stem(), listed),
            (Page::Compactions.stem(), chipped),
            (query.stem(), bound),
        ],
    })
}

/// A level in its own order with the compactions of the same thread dropped in by time.
///
/// A compaction lands before the first row that started after it, which is where it happened. A
/// row the store has no start for does not move one, and whatever is left over trails the level —
/// a compaction after the last row is a compaction after the last row.
///
/// Generic in what it places because two levels want it: a thread's turns, and the calls or tool
/// calls under one turn.
fn interleave<T>(
    ordered: Vec<(T, Option<DateTime<Utc>>)>,
    marks: Vec<(T, DateTime<Utc>)>,
) -> Vec<T> {
    let mut placed: Vec<T> = Vec::with_capacity(ordered.len() + marks.len());
    let mut pending = marks.into_iter().peekable();
    for (item, started) in ordered {
        while let Some((_, at)) = pending.peek() {
            match started {
                Some(started) if *at < started => placed.push(pending.next().expect("peeked").0),
                _ => break,
            }
        }
        placed.push(item);
    }
    placed.extend(pending.map(|(item, _)| item));
    placed
}

/// One compaction node, and when it happened — what an [`interleave`] mark is made of.
type Mark = (Node, DateTime<Utc>);

/// The compactions of one thread that belong to `turn_id`, paired with the time they happened.
///
/// `None` is the thread's own level: the compactions that happened between two turns rather than
/// during one.
fn between(
    corpus: &Corpus,
    source: &str,
    rows: &[Row],
    turn_id: Option<&str>,
) -> Result<Vec<Mark>, ViewError> {
    let mut marks = Vec::new();
    for row in rows {
        if row.opt_str("turn_id")? != turn_id {
            continue;
        }
        marks.push((
            compaction_node(&corpus.session_id, source, row)?,
            row.timestamp("timestamp")?,
        ));
    }
    Ok(marks)
}

/// One turn's compactions, paired with their ids the way a level's own rows are.
///
/// A compaction is a child of the turn it happened during, so a turn's level holds its own —
/// interleaved with the calls or tool calls by time. Nothing at a NULL turn: a bucket holds calls
/// that answer no turn, and a compaction that answers none is the thread's.
fn marks(
    store: &Store,
    corpus: &Corpus,
    source: &str,
    turn_id: Option<&str>,
) -> Result<(Vec<Mark>, Ran), ViewError> {
    let Some(turn_id) = turn_id else {
        return Ok((Vec::new(), Vec::new()));
    };
    let keyed: Bound = vec![
        ("session_id", corpus.session_id.as_str().into()),
        ("source", source.into()),
        ("chip_chars", Param::Int(queries::NAV_CHARS as i64)),
    ];
    let rows = page_rows(store, Page::Compactions, &keyed)?;
    let placed = between(corpus, source, &rows, Some(turn_id))?;
    Ok((placed, vec![(Page::Compactions.stem(), keyed)]))
}

/// A run row per node, described by whatever the enrichment pass called it.
fn runs(corpus: &Corpus, rows: &[&Row]) -> Result<Vec<Node>, ViewError> {
    rows.iter()
        .map(|row| {
            let said = corpus.run_text(row.str("run_id")?);
            Ok(run_node(&corpus.session_id, row, &corpus.held, said)?)
        })
        .collect()
}

/// The runs whose spawning call resolved to one node — a turn, or a thread's bucket.
fn spawned<'a>(
    corpus: &'a Corpus,
    source: &str,
    turn_id: Option<&str>,
) -> Result<Vec<&'a Row>, ViewError> {
    let mut held = Vec::new();
    for run in &corpus.runs {
        if run.opt_str("spawn_source")? == Some(source) && run.opt_str("spawn_turn_id")? == turn_id
        {
            held.push(run);
        }
    }
    Ok(held)
}

/// The runs no spawning call resolved, which the session's own bucket gathers.
fn loose_runs(corpus: &Corpus) -> Result<Vec<&Row>, ViewError> {
    let mut held = Vec::new();
    for run in &corpus.runs {
        if run.opt_str("spawn_source")?.is_none() {
            held.push(run);
        }
    }
    Ok(held)
}

/// The runs one api call spawned, matched on the thread too: a fork's transcript replays its
/// parent's calls, so an id alone would hang the run under the replayed copy as well.
fn call_spawned<'a>(corpus: &'a Corpus, at: &Ref) -> Result<Vec<&'a Row>, ViewError> {
    let mut held = Vec::new();
    for run in &corpus.runs {
        if run.opt_str("spawn_call_id")? == Some(at.node_id.as_str())
            && run.opt_str("spawn_source")? == at.source.as_deref()
        {
            held.push(run);
        }
    }
    Ok(held)
}

/// The runs one tool call spawned, matched on the thread for the same reason as the call.
fn tool_spawned<'a>(corpus: &'a Corpus, at: &Ref) -> Result<Vec<&'a Row>, ViewError> {
    let mut held = Vec::new();
    for run in &corpus.runs {
        if run.opt_str("tool_use_id")? == Some(at.node_id.as_str())
            && run.opt_str("spawn_source")? == at.source.as_deref()
        {
            held.push(run);
        }
    }
    Ok(held)
}

/// The runs a closed row hides: the ones the rows under it would have led to.
///
/// The spawning edge and nothing else, in every preset — the same edge each cell places a run by,
/// so the copy a closed row stands and the copy an open one shows are never both drawn. A session
/// is the root of every page and a compaction the end of every path, so neither hides anything.
fn hanging<'a>(corpus: &'a Corpus, at: &Ref) -> Result<Vec<&'a Row>, ViewError> {
    match at.kind {
        Kind::Turn => spawned(corpus, source_of(at), Some(&at.node_id)),
        Kind::Unattributed => spawned(corpus, source_of(at), None),
        Kind::Call => call_spawned(corpus, at),
        Kind::Tool => tool_spawned(corpus, at),
        // A run hides whatever its own thread spawned, which is what its turns would show.
        Kind::Run => {
            let mut held = Vec::new();
            for run in &corpus.runs {
                if run.opt_str("spawn_source")? == Some(at.node_id.as_str()) {
                    held.push(run);
                }
            }
            Ok(held)
        }
        Kind::Unattached => loose_runs(corpus),
        Kind::Session | Kind::Compaction => Ok(Vec::new()),
    }
}

/// The runs one closed row owes the reader, each under the nearest row that is showing.
///
/// A run is always visible: where every row between it and the call that spawned it is shut, it
/// renders under the deepest one that is not, and the runs under it render under it. So opening a
/// row moves a run's indent rather than bringing the run into being.
pub fn spread(corpus: &Corpus, node: &Node, depth: usize) -> Result<Vec<NavTreeRow>, ViewError> {
    let mut rows = Vec::new();
    for run in runs(corpus, &hanging(corpus, &node.node_ref())?)? {
        let under = spread(corpus, &run, depth + 1)?;
        rows.push(NavTreeRow::node(run, depth, false, false));
        rows.extend(under);
    }
    Ok(rows)
}

/// The api calls under one turn, with its compactions among them.
///
/// A NULL `turn_id` is the unattributed bucket's level — the calls that answer no turn. One
/// function for both because the two differ by that binding. No run is here: a run hangs under the
/// tool call that spawned it, two levels down, and [`spread`] is what stands it against a shut row.
fn calls_level(
    store: &Store,
    corpus: &Corpus,
    source: &str,
    turn_id: Option<&str>,
) -> Result<Level, ViewError> {
    let bound: Bound = vec![
        ("session_id", corpus.session_id.as_str().into()),
        ("source", source.into()),
        ("turn_id", turn_id.into()),
        ("nav_chars", Param::Int(queries::NAV_CHARS as i64)),
    ];
    let calls = page_rows(store, Page::NavTreeCalls, &bound)?;
    let (placed, mark_ran) = marks(store, corpus, source, turn_id)?;
    let mut ordered = Vec::with_capacity(calls.len());
    for row in &calls {
        ordered.push((
            call_node(&corpus.session_id, source, row, &corpus.held)?,
            row.opt_timestamp("started_at")?,
        ));
    }
    let mut ran = vec![(Page::NavTreeCalls.stem(), bound)];
    ran.extend(mark_ran);
    Ok(Level {
        nodes: interleave(ordered, placed),
        ran,
    })
}

/// The tool calls under one api call, or — at a NULL `api_call_id` — under one turn.
///
/// The second is `noapi`'s level: the api calls are folded away, so their tool calls stand under
/// the turn in call-then-tool order and the turn's compactions interleave by time. A call's own
/// level holds no compaction, because that hangs off the turn.
fn tools_level(
    store: &Store,
    corpus: &Corpus,
    source: &str,
    api_call_id: Option<&str>,
    turn_id: Option<&str>,
) -> Result<Level, ViewError> {
    let bound: Bound = vec![
        ("session_id", corpus.session_id.as_str().into()),
        ("source", source.into()),
        ("api_call_id", api_call_id.into()),
        ("turn_id", turn_id.into()),
        ("nav_chars", Param::Int(queries::NAV_CHARS as i64)),
    ];
    let rows = page_rows(store, Page::NavTreeTools, &bound)?;
    let under = if api_call_id.is_some() { None } else { turn_id };
    let (placed, mark_ran) = marks(store, corpus, source, under)?;
    let mut ordered = Vec::with_capacity(rows.len());
    for row in &rows {
        ordered.push((
            tool_node(&corpus.session_id, source, row, &corpus.held)?,
            row.opt_timestamp("started_at")?,
        ));
    }
    let mut ran = vec![(Page::NavTreeTools.stem(), bound)];
    ran.extend(mark_ran);
    Ok(Level {
        nodes: interleave(ordered, placed),
        ran,
    })
}

/// The runs nothing placed. Already read with the session's runs, so this reads nothing.
fn unattached_level(_store: &Store, corpus: &Corpus, _at: &Ref) -> Result<Level, ViewError> {
    Ok(Level {
        nodes: runs(corpus, &loose_runs(corpus)?)?,
        ran: Vec::new(),
    })
}

/// A node nothing hangs under: a tool call, and a compaction.
fn leaf(_store: &Store, _corpus: &Corpus, _at: &Ref) -> Result<Level, ViewError> {
    Ok(Level {
        nodes: Vec::new(),
        ran: Vec::new(),
    })
}

/// What hangs under a ⚒ tool call in every preset: the run it asked for.
///
/// The one level no preset filters. A run is nested under the tool call that spawned it, so this
/// is where a run comes from wherever the tree is read; the presets differ only in how many rows
/// they leave standing between the two.
fn tool_runs(_store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    Ok(Level {
        nodes: runs(corpus, &tool_spawned(corpus, at)?)?,
        ran: Vec::new(),
    })
}

// The `agents` preset's levels, which read nothing: a run is placed by an edge `view_runs` already
// answered, so the whole spawn tree is arithmetic over the runs read for the request.

/// The runs the main thread spawned, then the runs nothing placed.
fn agent_session(_store: &Store, corpus: &Corpus, _at: &Ref) -> Result<Level, ViewError> {
    let mut spawned_here = Vec::new();
    for run in &corpus.runs {
        if run.opt_str("spawn_source")? == Some(MAIN_SOURCE) {
            spawned_here.push(run);
        }
    }
    let mut placed = runs(corpus, &spawned_here)?;
    let loose = loose_runs(corpus)?;
    if !loose.is_empty() {
        placed.push(unattached_node(&corpus.session_id, &loose, &corpus.held)?);
    }
    Ok(Level {
        nodes: placed,
        ran: Vec::new(),
    })
}

/// The runs a run spawned, by what the transcript says their parent was.
///
/// `parent_agent_id` rather than the spawning call, because this is the one level the preset is
/// about: a run whose spawning call resolved to nothing still names the run it came from.
fn agent_children(_store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    let mut held = Vec::new();
    for run in &corpus.runs {
        if run.opt_str("parent_agent_id")? == Some(at.node_id.as_str()) {
            held.push(run);
        }
    }
    Ok(Level {
        nodes: runs(corpus, &held)?,
        ran: Vec::new(),
    })
}

/// The runs one turn — or, at a bucket, one thread's turnless calls — spawned.
fn agent_thread(_store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    let turn_id = match at.kind {
        Kind::Unattributed => None,
        _ => Some(at.node_id.as_str()),
    };
    Ok(Level {
        nodes: runs(corpus, &spawned(corpus, source_of(at), turn_id)?)?,
        ran: Vec::new(),
    })
}

/// The runs one api call spawned.
fn agent_call(_store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    Ok(Level {
        nodes: runs(corpus, &call_spawned(corpus, at)?)?,
        ran: Vec::new(),
    })
}

fn session_level(store: &Store, corpus: &Corpus, _at: &Ref) -> Result<Level, ViewError> {
    thread_level(store, corpus, MAIN_SOURCE, true)
}

fn run_level(store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    thread_level(store, corpus, &at.node_id, false)
}

fn turn_calls(store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    calls_level(store, corpus, source_of(at), Some(&at.node_id))
}

fn bucket_calls(store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    calls_level(store, corpus, source_of(at), None)
}

fn turn_tools(store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    tools_level(store, corpus, source_of(at), None, Some(&at.node_id))
}

fn bucket_tools(store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    tools_level(store, corpus, source_of(at), None, None)
}

fn call_tools(store: &Store, corpus: &Corpus, at: &Ref) -> Result<Level, ViewError> {
    tools_level(store, corpus, source_of(at), Some(&at.node_id), None)
}

/// What reads one level of the tree: identity in, the children under it out.
type Builder = fn(&Store, &Corpus, &Ref) -> Result<Level, ViewError>;

/// What one kind of node holds under one filter preset — the design's kind × preset table, one arm
/// per cell.
///
/// Total over `Kind × Preset` on purpose, and spelled out rather than defaulted: the NavTree opens
/// whatever the path reaches, so a missing cell would be a page that renders and then raises
/// halfway down, and a cell a preset passes through is a decision either way.
fn cell(kind: Kind, preset: Preset) -> Builder {
    match (kind, preset) {
        (Kind::Session, Preset::Full | Preset::NoApi) => session_level,
        (Kind::Session, Preset::Agents) => agent_session,
        (Kind::Run, Preset::Full | Preset::NoApi) => run_level,
        (Kind::Run, Preset::Agents) => agent_children,
        (Kind::Turn, Preset::Full) => turn_calls,
        (Kind::Turn, Preset::NoApi) => turn_tools,
        (Kind::Turn, Preset::Agents) => agent_thread,
        (Kind::Unattributed, Preset::Full) => bucket_calls,
        (Kind::Unattributed, Preset::NoApi) => bucket_tools,
        (Kind::Unattributed, Preset::Agents) => agent_thread,
        (Kind::Call, Preset::Full | Preset::NoApi) => call_tools,
        (Kind::Call, Preset::Agents) => agent_call,
        (Kind::Tool, Preset::Full | Preset::NoApi | Preset::Agents) => tool_runs,
        (Kind::Compaction, Preset::Full | Preset::NoApi | Preset::Agents) => leaf,
        (Kind::Unattached, Preset::Full | Preset::NoApi | Preset::Agents) => unattached_level,
    }
}

/// What hangs under one node in one preset: the cell of the table above, read.
///
/// Identity is the whole of what a level needs — the cell is picked by kind and the query it runs
/// is keyed by ids — so a caller holding a ref can read a level without rendering the node it hangs
/// under. Which is what a tail row's own fetch does (`crate::app`).
///
/// `descends` is the key of the child the open path goes through, or nothing where this level is
/// not on it. A preset filters children and never the expanded chain: where the cell hides that
/// child, the level comes back in full instead, so a reader standing on a kind the preset hides
/// still sees where it sits. Adding the step to the filtered level would draw part of the NavTree
/// twice — `noapi` hoists a tool call to its turn, so an api call spliced back in would render its
/// own copy of a row already sitting a level higher.
pub fn children(
    store: &Store,
    corpus: &Corpus,
    at: &Ref,
    preset: Preset,
    descends: Option<&str>,
) -> Result<Level, ViewError> {
    let level = cell(at.kind, preset)(store, corpus, at)?;
    if let Some(descends) = descends
        && !level.nodes.iter().any(|child| child.key() == descends)
    {
        return cell(at.kind, Preset::Full)(store, corpus, at);
    }
    Ok(level)
}

/// The session's NavTree with `trail` open — its steps, their siblings, and its children.
///
/// `trail` runs outermost first and ends at the selection; `root` is the node its first step names,
/// which the page read for its own header. Every step is expanded and nothing else is, so a reader
/// sees one path and what sits beside each step of it. `preset` picks which children each level
/// shows, except that a level whose cell hides the path's own next step renders in full: a reader
/// standing on a folded-away kind still sees where it sits. `cap` bounds a level and a tail row
/// says what it left out, except that the row the path goes through is always kept: a cut that hid
/// the selection would leave the pane describing a node the NavTree does not show.
pub fn nav_tree(
    store: &Store,
    corpus: &Corpus,
    root: Node,
    trail: &[Ref],
    preset: Preset,
    cap: usize,
) -> Result<NavTree, ViewError> {
    let mut walk = Walk {
        store,
        corpus,
        preset,
        cap,
        open_keys: trail.iter().map(Ref::key).collect(),
        rows: Vec::new(),
        chain: Vec::new(),
        ran: Vec::new(),
    };
    walk.expand(root, 0)?;
    // The selection renders out of its parent's level, so a chain shorter than the trail means a
    // level did not hold the child the path named — a store shape, not a page to serve.
    if walk.chain.len() != trail.len() {
        let stopped = walk
            .chain
            .last()
            .map_or_else(|| "the root".to_owned(), Node::key);
        return Err(ViewError::Shape(format!(
            "nothing under {stopped} holds {}",
            walk.open_keys[walk.chain.len()]
        )));
    }
    Ok(NavTree {
        rows: walk.rows,
        chain: walk.chain,
        ran: walk.ran,
    })
}

/// One top-down expansion in progress: what has been drawn, and what is still open.
///
/// Rust has no closure that can call itself, so Python's inner `expand` is a method on the state it
/// was closing over.
struct Walk<'a> {
    store: &'a Store,
    corpus: &'a Corpus,
    preset: Preset,
    cap: usize,
    open_keys: Vec<String>,
    rows: Vec<NavTreeRow>,
    chain: Vec<Node>,
    ran: Ran,
}

impl Walk<'_> {
    fn expand(&mut self, node: Node, depth: usize) -> Result<(), ViewError> {
        let key = node.key();
        let at = self.open_keys.iter().position(|open| *open == key);
        let selected = self.open_keys.last().is_some_and(|last| *last == key);
        self.rows.push(NavTreeRow::node(
            node.clone(),
            depth,
            selected,
            at.is_some() && !selected,
        ));
        let Some(at) = at else {
            let under = spread(self.corpus, &node, depth + 1)?;
            self.rows.extend(under);
            return Ok(());
        };
        self.chain.push(node.clone());
        let descends = self.open_keys.get(at + 1).cloned();
        let level = children(
            self.store,
            self.corpus,
            &node.node_ref(),
            self.preset,
            descends.as_deref(),
        )?;
        self.ran.extend(level.ran);
        let shown = windowed(level.nodes, self.cap, &self.open_keys);
        for child in shown.kept {
            self.expand(child, depth + 1)?;
        }
        if !shown.cut.is_empty() {
            let mut tail = NavTreeRow::node(node, depth + 1, false, false);
            tail.cut = shown.cut.len() as i64;
            tail.opened = descends;
            self.rows.push(tail);
        }
        Ok(())
    }
}

/// One level split by the cap: the children a page draws, and the ones it leaves for the tail row
/// to fetch.
pub struct Window {
    pub kept: Vec<Node>,
    pub cut: Vec<Node>,
}

/// The first `cap` children, the one the path descends through among them, and the rest.
///
/// The path's child takes a slot rather than an extra row: `cap` is what the page's byte arithmetic
/// is priced on, so a level that renders `cap + 1` children is a page over the bound. Only one
/// child of a level can be on the path, so the rescue costs at most the level's last shown sibling
/// — a row the tail still counts and offers.
///
/// One rule for both halves: the tail row fetches what it says it left out, and the two would drift
/// apart if the fetch counted the window a second way.
///
/// A run under a cut child goes under with it, the one place "a run is always visible" stops:
/// [`spread`] runs on the rows a page rendered, and the tail row's `+N` counts the level's own
/// children, so nothing on the page says a run is behind the cut. The fetch stands it, so it is a
/// click away.
pub fn windowed(under: Vec<Node>, cap: usize, open_keys: &[String]) -> Window {
    let on_path = |node: &Node| open_keys.contains(&node.key());
    // The keys past the cap that the path goes through, in the order they stood.
    let keys: Vec<String> = under
        .iter()
        .skip(cap)
        .filter(|node| on_path(node))
        .map(Node::key)
        .collect();
    let shown = cap.saturating_sub(keys.len()).min(under.len());
    let mut kept: Vec<Node> = Vec::with_capacity(shown + keys.len());
    let mut rescued: Vec<Node> = Vec::new();
    let mut cut: Vec<Node> = Vec::new();
    for (at, node) in under.into_iter().enumerate() {
        if at < shown {
            kept.push(node);
        } else if keys.contains(&node.key()) {
            rescued.push(node);
        } else {
            cut.push(node);
        }
    }
    kept.extend(rescued);
    Window { kept, cut }
}
