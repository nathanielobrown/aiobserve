//! A URL for every node of a session: the session, a turn, a run, a call, a tool, and the rest.
//!
//! Ported from `src/hyphae/view/node_pages.py`. Each route reads only what its own kind needs and
//! hands the rest to [`browse`], which is the page they all serve. The two buckets are here as
//! well — a thread's api calls that answer no turn, and the session's runs no tool call spawned —
//! because a bucket gets a page like anything else (`CONTEXT.md`).

use hyphae_store::{Param, Row, queries};

use crate::browse::{Asked, PageError, Reading, Seen, browse, call_log, run_log, turn_log};
use crate::columns::Shape;
use crate::components::Markup;
use crate::detail::detail_of;
use crate::highlight::Syntax;
use crate::nav_tree::{self, Bound, MAIN_SOURCE, Ran};
use crate::nodes::{self, Kind, Ledger, Ref};
use crate::store::{
    Fragment, Listed, Page, Query, TURN_CURSOR, ViewError, listed, page_rows, window,
};
use crate::viewer::Viewer;
use crate::{builders, highlight};

/// A session's own node: what it was, and its main thread as the NavTree's first level.
pub fn session_page(viewer: &Viewer, session_id: &str, asked: &Asked) -> Result<Markup, PageError> {
    let read: Reading<'_> = &|store, corpus, head| {
        let bound: Bound = vec![
            ("session_id", session_id.into()),
            ("log_chars", (queries::LOG_CHARS as i64).into()),
        ];
        let offset = asked.skipped();
        let turns = window(
            store,
            Page::Timeline,
            TURN_CURSOR,
            offset,
            asked.log,
            &bound,
        )?;
        Ok(Seen {
            header: head.clone(),
            trail: vec![Ref::new(Kind::Session, None, session_id)],
            shape: Shape::Turns,
            rows: turn_log(corpus, MAIN_SOURCE, &turns.rows)?,
            total: turns.total,
            details: vec![],
            record: None,
            ran: vec![(Page::Timeline.stem(), windowed(bound, offset, asked.log))],
        })
    };
    browse(viewer, session_id, MAIN_SOURCE, asked, read)
}

/// One turn: what it was asked, and the api calls that answered it.
pub fn turn_page(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    turn_id: &str,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let read: Reading<'_> = &|store, corpus, _head| {
        let bound = header_bound(
            &[
                ("session_id", session_id.into()),
                ("source", source.into()),
                ("turn_id", turn_id.into()),
            ],
            asked,
        );
        let at = format!("{}/turn/{turn_id}", nodes::thread_url(session_id, source));
        let rows = page_rows(store, Page::TurnHeader, &bound)?;
        let Some(row) = rows.first() else {
            return Err(missing("No turn with that id is in this thread."));
        };
        // Which line of the transcript each turn of this thread came from. Read for the whole
        // thread because that is what the query answers; two identifier columns per turn, and the
        // pane keeps the one row it is about.
        let thread: Bound = vec![("session_id", session_id.into()), ("source", source.into())];
        let archived = page_rows(store, Page::TurnRecords, &thread)?;
        let record = archived
            .iter()
            .find(|held| held.str("turn_id").is_ok_and(|held| held == turn_id))
            .map(|held| held.i64("line_no"))
            .transpose()?;
        let (calls, log_rows, ran) = call_log(store, corpus, source, Some(turn_id), asked)?;
        let details = [
            detail_of(
                "prompt",
                row.opt_str("prompt")?,
                row.opt_i64("prompt_chars")?,
                format!("/fragment/prompt{at}"),
                asked.detail as usize,
                None,
                true,
            ),
            detail_of(
                "command_args",
                row.opt_str("command_args")?,
                row.opt_i64("command_args_chars")?,
                format!("/fragment/args{at}"),
                asked.detail as usize,
                None,
                true,
            ),
        ];
        let mut cited: Ran = vec![(Page::TurnHeader.stem(), bound)];
        cited.extend(ran);
        cited.push((Page::TurnRecords.stem(), thread));
        Ok(Seen {
            header: row.clone(),
            trail: vec![Ref::new(Kind::Turn, Some(source), turn_id)],
            shape: Shape::Calls,
            rows: log_rows,
            total: calls.total,
            details: details.into_iter().flatten().collect(),
            record,
            ran: cited,
        })
    };
    browse(viewer, session_id, source, asked, read)
}

/// One agent run: the brief it was given, and its own thread of turns.
///
/// A run's id is also the `source` its rows carry, which is why the URL needs no thread segment
/// and why the enrichment is read at the run.
pub fn run_page(
    viewer: &Viewer,
    session_id: &str,
    run_id: &str,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let read: Reading<'_> = &|store, corpus, _head| {
        let bound = header_bound(
            &[("session_id", session_id.into()), ("run_id", run_id.into())],
            asked,
        );
        let rows = page_rows(store, Page::RunHeader, &bound)?;
        let Some(row) = rows.first() else {
            return Err(missing("No run with that id is in this session."));
        };
        let offset = asked.skipped();
        let timeline: Bound = vec![
            ("session_id", session_id.into()),
            ("source", run_id.into()),
            ("log_chars", (queries::LOG_CHARS as i64).into()),
        ];
        let turns = window(
            store,
            Page::RunTimeline,
            TURN_CURSOR,
            offset,
            asked.log,
            &timeline,
        )?;
        let at = nodes::run_url(session_id, run_id);
        let details = [
            detail_of(
                "brief",
                row.opt_str("brief")?,
                row.opt_i64("brief_chars")?,
                format!("/fragment/brief{at}"),
                asked.detail as usize,
                None,
                true,
            ),
            // The ask and the answer, both markdown: one was written by whoever spawned the run
            // and the other by the run itself.
            detail_of(
                "prompt",
                row.opt_str("prompt")?,
                row.opt_i64("prompt_chars")?,
                format!("/fragment/prompt{at}"),
                asked.detail as usize,
                None,
                true,
            ),
            detail_of(
                "result",
                row.opt_str("result")?,
                row.opt_i64("result_chars")?,
                format!("/fragment/result{at}"),
                asked.detail as usize,
                None,
                true,
            ),
        ];
        Ok(Seen {
            header: row.clone(),
            trail: vec![Ref::new(Kind::Run, Some(run_id), run_id)],
            shape: Shape::Turns,
            rows: turn_log(corpus, run_id, &turns.rows)?,
            total: turns.total,
            details: details.into_iter().flatten().collect(),
            record: None,
            ran: vec![
                (Page::RunHeader.stem(), bound),
                (
                    Page::RunTimeline.stem(),
                    windowed(timeline, offset, asked.log),
                ),
            ],
        })
    };
    browse(viewer, session_id, run_id, asked, read)
}

/// One api call: what it answered, what it thought, and the tools it called.
pub fn call_page(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    api_call_id: &str,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let read: Reading<'_> = &|store, _corpus, _head| {
        let bound = header_bound(
            &[
                ("session_id", session_id.into()),
                ("source", source.into()),
                ("api_call_id", api_call_id.into()),
            ],
            asked,
        );
        let at = format!(
            "{}/call/{api_call_id}",
            nodes::thread_url(session_id, source)
        );
        let rows = page_rows(store, Page::CallHeader, &bound)?;
        let Some(row) = rows.first() else {
            return Err(missing("No api call with that id is in this thread."));
        };
        let tools: Bound = vec![
            ("session_id", session_id.into()),
            ("source", source.into()),
            ("api_call_id", api_call_id.into()),
            ("skipped", asked.skipped().into()),
            ("page_tools", asked.log.into()),
            ("log_chars", (queries::LOG_CHARS as i64).into()),
        ];
        let called: Listed = listed(
            page_rows(store, Fragment::CallTools, &tools)?,
            "matched_tool_calls",
        )
        .map_err(ViewError::from)?;
        let logged = called
            .rows
            .iter()
            .map(|item| {
                let node = builders::tool_node(session_id, source, item, &Ledger::none())?;
                builders::logged(Shape::Tools, node, item)
            })
            .collect::<Result<Vec<_>, _>>()?;
        let details = [
            detail_of(
                "text",
                row.opt_str("text_head")?,
                row.opt_i64("text_chars")?,
                format!("/fragment/text{at}"),
                asked.detail as usize,
                None,
                true,
            ),
            detail_of(
                "thinking",
                row.opt_str("thinking_head")?,
                row.opt_i64("thinking_chars")?,
                format!("/fragment/thinking{at}"),
                asked.detail as usize,
                None,
                true,
            ),
        ];
        Ok(Seen {
            header: row.clone(),
            // The call's own header says which turn it answers, so its place costs no read: a NULL
            // turn puts it in its thread's unattributed bucket instead.
            trail: vec![
                nav_tree::home(source, row.opt_str("turn_id")?),
                Ref::new(Kind::Call, Some(source), api_call_id),
            ],
            shape: Shape::Tools,
            rows: logged,
            total: called.total,
            details: details.into_iter().flatten().collect(),
            record: None,
            ran: vec![
                (Page::CallHeader.stem(), bound),
                (Fragment::CallTools.stem(), tools),
            ],
        })
    };
    browse(viewer, session_id, source, asked, read)
}

/// One tool call: what it was passed, and what it returned. Nothing hangs under it.
pub fn tool_page(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    tool_call_id: &str,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let read: Reading<'_> = &|store, _corpus, _head| {
        let bound = header_bound(
            &[
                ("session_id", session_id.into()),
                ("source", source.into()),
                ("tool_call_id", tool_call_id.into()),
            ],
            asked,
        );
        let at = format!(
            "{}/tool/{tool_call_id}",
            nodes::thread_url(session_id, source)
        );
        let rows = page_rows(store, Page::ToolHeader, &bound)?;
        let Some(row) = rows.first() else {
            return Err(missing("No tool call with that id is in this thread."));
        };
        let details = [
            // The command first, where the call ran one: it is what the input is about, and the
            // input below it is the record it was read out of.
            detail_of(
                "command",
                row.opt_str("command")?,
                row.opt_i64("command_chars")?,
                format!("/fragment/command{at}"),
                asked.detail as usize,
                Some(Syntax::Bash),
                false,
            ),
            // What a tool was passed is JSON — Claude Code records every tool's arguments as an
            // object — so the preview is marked up as JSON without asking the row, which is the
            // same syntax its own fetch reads it under.
            detail_of(
                "input",
                row.opt_str("input")?,
                row.opt_i64("input_chars")?,
                format!("/fragment/input{at}"),
                asked.detail as usize,
                Some(Syntax::Json),
                false,
            ),
            // And what it answered is that file's syntax where the record names a file, else JSON:
            // a tool that does not answer in prose answers in JSON, and `highlight::lit` prints a
            // value that does not parse as the characters the store holds rather than lexing it as
            // broken JSON.
            detail_of(
                "result",
                row.opt_str("result_head")?,
                row.opt_i64("result_chars")?,
                format!("/fragment/result{at}"),
                asked.detail as usize,
                Some(highlight::by_suffix(row.opt_str("result_type")?).unwrap_or(Syntax::Json)),
                false,
            ),
        ];
        Ok(Seen {
            header: row.clone(),
            // The whole path down, out of one read: the call that made it, and the turn that call
            // answers — else that thread's bucket, by the same rule.
            trail: vec![
                nav_tree::home(source, row.opt_str("turn_id")?),
                Ref::new(Kind::Call, Some(source), row.str("api_call_id")?),
                Ref::new(Kind::Tool, Some(source), tool_call_id),
            ],
            shape: Shape::None,
            rows: vec![],
            total: 0,
            details: details.into_iter().flatten().collect(),
            record: None,
            ran: vec![(Page::ToolHeader.stem(), bound)],
        })
    };
    browse(viewer, session_id, source, asked, read)
}

/// One compaction: where a thread's context was rewritten, and what that cost it.
///
/// Read out of the thread's markers rather than by id — a compaction has no query of its own
/// because the thread's whole set is what the NavTree beside it renders anyway.
pub fn compaction_page(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    compaction_id: &str,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let read: Reading<'_> = &|store, _corpus, _head| {
        let bound: Bound = vec![
            ("session_id", session_id.into()),
            ("source", source.into()),
            ("chip_chars", (queries::HEADER_CHARS as i64).into()),
        ];
        let found = page_rows(store, Page::Compactions, &bound)?
            .into_iter()
            .find(|row| {
                row.str("compaction_id")
                    .is_ok_and(|held| held == compaction_id)
            });
        let Some(row) = found else {
            return Err(missing("No compaction with that id is in this thread."));
        };
        // Where it hangs is what the query already answered: under the turn it happened during,
        // else beside the turns of its thread. Seeded rather than resolved, because a turn a
        // timestamp lands in is a read this row has made.
        let mut trail: Vec<Ref> = row
            .opt_str("turn_id")?
            .map(|turn_id| Ref::new(Kind::Turn, Some(source), turn_id))
            .into_iter()
            .collect();
        trail.push(Ref::new(Kind::Compaction, Some(source), compaction_id));
        Ok(Seen {
            header: row,
            trail,
            shape: Shape::None,
            rows: vec![],
            total: 0,
            details: vec![],
            record: None,
            ran: vec![(Page::Compactions.stem(), bound)],
        })
    };
    browse(viewer, session_id, source, asked, read)
}

/// One thread's api calls that answer no turn — a resume's calls answer turns that live in the
/// session it resumed, and this is where they are read.
pub fn unattributed_page(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let read: Reading<'_> = &|store, corpus, _head| {
        let Some(standing) = nav_tree::unattributed(store, corpus, source)? else {
            return Err(missing("Every api call on this thread answers a turn."));
        };
        let (calls, log_rows, ran) = call_log(store, corpus, source, None, asked)?;
        let mut cited: Ran = vec![standing.ran];
        cited.extend(ran);
        Ok(Seen {
            header: standing.row,
            trail: vec![Ref::new(Kind::Unattributed, Some(source), source)],
            shape: Shape::Calls,
            rows: log_rows,
            total: calls.total,
            details: vec![],
            record: None,
            ran: cited,
        })
    };
    browse(viewer, session_id, source, asked, read)
}

/// The session's agent runs no spawning call resolved.
///
/// Session-scoped rather than per thread: what makes a run unattached is that nothing says which
/// thread spawned it, so the bucket hangs off the session itself.
pub fn unattached_page(
    viewer: &Viewer,
    session_id: &str,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let read: Reading<'_> = &|_store, corpus, head| {
        let mut loose = Vec::new();
        for run in &corpus.runs {
            if run.is_null("spawn_source")? {
                loose.push(run.clone());
            }
        }
        if loose.is_empty() {
            return Err(missing("Every agent run in this session was placed."));
        }
        let runs = sliced(&loose, asked);
        Ok(Seen {
            header: head.clone(),
            trail: vec![Ref::new(Kind::Unattached, None, session_id)],
            shape: Shape::Runs,
            rows: run_log(corpus, &runs.rows)?,
            total: runs.total,
            details: vec![],
            record: None,
            ran: vec![],
        })
    };
    browse(viewer, session_id, MAIN_SOURCE, asked, read)
}

/// What every header query binds beyond the ids that name the node: the widths it cuts to.
fn header_bound(named: &[(&'static str, Param)], asked: &Asked) -> Bound {
    let mut bound = named.to_vec();
    bound.push(("head_chars", (queries::HEADER_CHARS as i64).into()));
    bound.push(("detail_chars", asked.detail.into()));
    bound
}

/// What a windowed query cited: its own bindings, plus the two the window added.
fn windowed(mut bound: Bound, offset: i64, limit: i64) -> Bound {
    bound.push(("offset", offset.into()));
    bound.push(("limit", limit.into()));
    bound
}

/// One numbered page of rows already in memory, cut the way a query's OFFSET cuts one.
///
/// The unattached runs are the case: they arrive with the session's runs, which every level of the
/// NavTree needs anyway, so paging them is slicing rather than a second read.
fn sliced(items: &[Row], asked: &Asked) -> Listed {
    let start = asked.skipped() as usize;
    let end = start.saturating_add(asked.log as usize).min(items.len());
    Listed {
        rows: items[start.min(items.len())..end].to_vec(),
        total: items.len() as i64,
    }
}

/// The 404 a reader earns by naming a node this store does not hold.
fn missing(said: &str) -> PageError {
    PageError::Missing(said.to_owned())
}
