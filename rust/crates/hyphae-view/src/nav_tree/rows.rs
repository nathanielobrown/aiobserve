//! Which of a session's rows belong to one level of the NavTree, and in what order.
//!
//! Split off [`super`] rather than ported apart: `src/hyphae/view/nav_tree.py` holds both halves.
//! Everything here is arithmetic over the rows a level's query returned — where a run hangs, where
//! a compaction falls between two turns — and none of it renders. The tree those rows form, and
//! the kind × preset table that decides which level to read, are the parent module's.

use chrono::{DateTime, Utc};
use hyphae_store::{Param, Row, Store, queries};

use super::{Bound, Corpus, Level, Ran, source_of, timeline, unattributed};
use crate::builders::{compaction_node, run_node, turn_node, unattached_node, unattributed_node};
use crate::nodes::{Kind, Node, Ref};
use crate::store::{Page, Query, ViewError, page_rows};

/// One thread's own children: its turns and the compactions between them, then its buckets.
///
/// A session and a run read alike — the difference is the source, and that only the session holds
/// the unattached bucket, which spans every thread rather than sitting on one. Only the
/// compactions that happened between two turns are here; one that happened *during* a turn is a
/// child of that turn ([`marks`]).
pub(super) fn thread_level(
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
pub(super) fn interleave<T>(
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
pub(super) fn between(
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
pub(super) fn marks(
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
pub(super) fn runs(corpus: &Corpus, rows: &[&Row]) -> Result<Vec<Node>, ViewError> {
    rows.iter()
        .map(|row| {
            let said = corpus.run_text(row.str("run_id")?);
            Ok(run_node(&corpus.session_id, row, &corpus.held, said)?)
        })
        .collect()
}

/// The runs whose spawning call resolved to one node — a turn, or a thread's bucket.
pub(super) fn spawned<'a>(
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
pub(super) fn loose_runs(corpus: &Corpus) -> Result<Vec<&Row>, ViewError> {
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
pub(super) fn call_spawned<'a>(corpus: &'a Corpus, at: &Ref) -> Result<Vec<&'a Row>, ViewError> {
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
pub(super) fn tool_spawned<'a>(corpus: &'a Corpus, at: &Ref) -> Result<Vec<&'a Row>, ViewError> {
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
pub(super) fn hanging<'a>(corpus: &'a Corpus, at: &Ref) -> Result<Vec<&'a Row>, ViewError> {
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
