//! The fetches a page makes for itself: one whole value, one popover, one enrichment line.
//!
//! Ported from `src/hyphae/view/fragments.py`. Nothing here serves a document. Every route
//! answers one element that a page already showing its head asks for — the rest of a cut value, or
//! the numbers behind a NavTree row — and every answer is a fragment of markup htmx swaps into the
//! page that asked (`docs/viewer.md`).
//!
//! The pane decides what to offer and mints the URL; a route here only reads the one column it was
//! asked for and renders it. A row that exists with nothing under it is a 404: nothing links here
//! unless there is a value to fetch.

use hyphae_store::{Row, queries};

use crate::browse::PageError;
use crate::builders;
use crate::components::values::{self as values, Whole as WholeValue};
use crate::components::{Markup, numbers as numbers_view};
use crate::enrichment::enriched;
use crate::highlight::{self, Syntax};
use crate::nav_tree::{Bound, MAIN_SOURCE};
use crate::nodes::{Kind, Ref};
use crate::numbers::{breakout, charges, spend, wash};
use crate::store::{Fragment, Query, ViewError, Whole, page_rows};
use crate::viewer::Viewer;

/// One node's numbers, for the popover its NavTree row fetches.
///
/// `source` is the thread the window is read on, which is not always the thread the node sits on:
/// a session's reader is reading `main`, and its spend is every thread's. What differs between the
/// kinds is inside the query; what differs here is only the tool call, which has no api calls to
/// be measured out of.
fn counted(
    viewer: &Viewer,
    kind: Kind,
    session_id: &str,
    source: &str,
    node_id: &str,
) -> Result<Markup, PageError> {
    let key = Ref::new(kind, Some(source), node_id).key();
    let store = viewer.reader.connect()?;
    if kind == Kind::Tool {
        let keyed: Bound = vec![
            ("session_id", session_id.into()),
            ("source", source.into()),
            ("tool_call_id", node_id.into()),
            ("item_chars", (queries::HEADER_ITEM_CHARS as i64).into()),
            ("head_items", (queries::HEADER_ITEMS as i64).into()),
        ];
        let rows = page_rows(&store, Fragment::ToolNumbers, &keyed)?;
        drop(store);
        let Some(row) = rows.first() else {
            return Err(PageError::Missing(
                "No tool call with that id is in this thread.".to_owned(),
            ));
        };
        return Ok(numbers_view::tool(
            &key,
            &queries::citation(Fragment::ToolNumbers.stem(), &keyed),
            &builders::tool_numbers(row)?,
        ));
    }
    let bound: Bound = vec![
        ("session_id", session_id.into()),
        ("source", source.into()),
        ("node_id", node_id.into()),
        ("kind", kind.word().into()),
        ("model_chars", queries::MODEL_CHARS.into()),
    ];
    let rows = page_rows(&store, Fragment::Numbers, &bound)?;
    drop(store);
    // The query aggregates, so it answers a row for a node that is not there as readily as for one
    // that is — a node with no api calls under it is a real reading, and the popover prints it as
    // the dashes it is.
    let row = rows.first().ok_or_else(|| {
        ViewError::Shape("view_numbers.sql aggregates, so it answers a row".to_owned())
    })?;
    let whole = row.opt_f64("session_usd")?;
    let spent: Vec<Row> = row.structs("spent")?;
    Ok(numbers_view::popover(
        &key,
        &queries::citation(Fragment::Numbers.stem(), &bound),
        &builders::window_numbers(row)?,
        // The three lines between the window and the total, each priced and washed here rather
        // than in the component: what a charge is made of is arithmetic ([`crate::numbers`]), and
        // the total under them takes the same ground.
        &charges(row, spend(&spent)?.as_ref(), whole)?,
        &wash(row.opt_f64("cost_usd")?, whole),
        // And the two lines under them, where agent runs hang below this node: nothing where none
        // does, which is what keeps the breakout off every other row.
        breakout(row.opt_f64("cost_usd")?, row.opt_f64("subtree_usd")?, whole).as_ref(),
    ))
}

/// One compaction's numbers: the window it dropped, and the word recorded for why.
///
/// Its own route rather than a branch of [`counted`], because a compaction shares nothing with the
/// kinds made of api calls — no window to stand on, no model, no dollar. It must stay above the
/// route below it, whose `{kind}` matches this path too: which of the two answers is decided by
/// the order they are registered in.
pub fn compaction_numbers(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    compaction_id: &str,
) -> Result<Markup, PageError> {
    let keyed: Bound = vec![
        ("session_id", session_id.into()),
        ("source", source.into()),
        ("compaction_id", compaction_id.into()),
        ("chip_chars", queries::CHIP_CHARS.into()),
    ];
    let store = viewer.reader.connect()?;
    let rows = page_rows(&store, Fragment::CompactionNumbers, &keyed)?;
    drop(store);
    let Some(row) = rows.first() else {
        return Err(PageError::Missing(
            "No compaction with that id is on this thread.".to_owned(),
        ));
    };
    Ok(numbers_view::compaction(
        &Ref::new(Kind::Compaction, Some(source), compaction_id).key(),
        &queries::citation(Fragment::CompactionNumbers.stem(), &keyed),
        &builders::compaction_numbers(row)?,
    ))
}

/// The numbers behind a turn, an api call, or a tool call recorded on a thread.
pub fn node_numbers(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    kind: &str,
    node_id: &str,
) -> Result<Markup, PageError> {
    let Some(kind) = Kind::spelled(kind).filter(|kind| kind.numbered()) else {
        return Err(PageError::Missing(
            "No numbers are served for that kind of node.".to_owned(),
        ));
    };
    counted(viewer, kind, session_id, source, node_id)
}

/// One agent run's numbers, read on the thread the run's id also names.
pub fn run_numbers(viewer: &Viewer, session_id: &str, run_id: &str) -> Result<Markup, PageError> {
    counted(viewer, Kind::Run, session_id, run_id, run_id)
}

/// A whole session's numbers: the main thread's window, and every thread's spend.
pub fn session_numbers(viewer: &Viewer, session_id: &str) -> Result<Markup, PageError> {
    counted(viewer, Kind::Session, session_id, MAIN_SOURCE, session_id)
}

/// The one row a per-value fragment is for, and the query that found it.
///
/// `column` is where the query puts the value this fragment is for. A row can exist with nothing
/// under it — a `Read` has no command, a turn no prompt — and that is a 404 and not an empty page:
/// nothing on a pane links here unless there is a value to fetch, so a request for one that is not
/// there is a URL somebody typed or a link somebody kept.
fn fetched(
    viewer: &Viewer,
    value: Whole,
    keyed: &Bound,
    column: &str,
) -> Result<(Row, String), PageError> {
    let store = viewer.reader.connect()?;
    let rows = page_rows(&store, value, keyed)?;
    drop(store);
    match rows.first() {
        Some(row) if !row.is_null(column)? => {
            Ok((row.clone(), queries::citation(value.stem(), keyed)))
        }
        _ => Err(PageError::Missing(
            "Nothing in this store is stored under that id.".to_owned(),
        )),
    }
}

/// One whole value that was written as markdown, in the block its head was previewed in.
///
/// `detail` is the name the pane files this value under, and the fragment replaces that whole
/// section, so it carries the name out with it — the styling that tells an ask from an answer
/// reads it.
fn prose(
    viewer: &Viewer,
    value: Whole,
    keyed: &Bound,
    column: &str,
    detail: &str,
) -> Result<Markup, PageError> {
    let (row, citation) = fetched(viewer, value, keyed, column)?;
    Ok(values::prose(&WholeValue {
        value: row.opt_str(column)?.map(str::to_owned),
        detail: Some(detail.to_owned()),
        citation,
    }))
}

/// One whole value that was never prose, marked up in the syntax it was written in.
///
/// `syntax` is what the route knows the value is written in. A value whose language is a property
/// of the row instead — the file a `Read` returned — carries it in the query's own `result_type`,
/// so the fetch is marked up the way its preview on the pane was, and falls back to JSON: a tool's
/// arguments are JSON far more often than they are anything.
fn code(
    viewer: &Viewer,
    value: Whole,
    keyed: &Bound,
    column: &str,
    detail: &str,
    syntax: Option<Syntax>,
) -> Result<Markup, PageError> {
    let (row, citation) = fetched(viewer, value, keyed, column)?;
    let suffix = row
        .columns()
        .iter()
        .any(|held| held == "result_type")
        .then(|| row.opt_str("result_type"))
        .transpose()?
        .flatten();
    let written = syntax
        .or_else(|| highlight::by_suffix(suffix))
        .unwrap_or(Syntax::Json);
    Ok(values::code(
        &WholeValue {
            value: row.opt_str(column)?.map(str::to_owned),
            detail: Some(detail.to_owned()),
            citation,
        },
        written,
    ))
}

/// One whole line an enrichment pass wrote, or a 404 where no pass wrote one.
///
/// A pass creates the enrichment tables rather than the exporter, so a store none has touched
/// holds no such line — the same nothing a missing row is, and the same answer
/// ([`crate::enrichment`]). Asked per request and not at startup, because a pass can run against
/// the store while the viewer is reading it.
fn enrichment_line(
    viewer: &Viewer,
    value: Whole,
    keyed: &Bound,
    field: &str,
) -> Result<Markup, PageError> {
    let store = viewer.reader.connect()?;
    let written = enriched(&store)?;
    drop(store);
    if !written {
        return Err(PageError::Missing(
            "No enrichment pass has written to this store.".to_owned(),
        ));
    }
    let (row, citation) = fetched(viewer, value, keyed, field)?;
    Ok(values::enrichment_line(&WholeValue {
        value: row.opt_str(field)?.map(str::to_owned),
        detail: Some(field.to_owned()),
        citation,
    }))
}

/// What a turn's three fetches bind, which is the same three every time.
fn turn_keyed(session_id: &str, source: &str, turn_id: &str) -> Bound {
    vec![
        ("session_id", session_id.into()),
        ("source", source.into()),
        ("turn_id", turn_id.into()),
    ]
}

/// And what a run's do.
fn run_keyed(session_id: &str, run_id: &str) -> Bound {
    vec![("session_id", session_id.into()), ("run_id", run_id.into())]
}

/// The whole of what a pass said one turn did, or the friction it saw in it.
pub fn turn_said(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    turn_id: &str,
    field: &str,
) -> Result<Markup, PageError> {
    enrichment_line(
        viewer,
        Whole::TurnSaid,
        &turn_keyed(session_id, source, turn_id),
        field,
    )
}

/// The same for one agent run.
pub fn run_said(
    viewer: &Viewer,
    session_id: &str,
    run_id: &str,
    field: &str,
) -> Result<Markup, PageError> {
    enrichment_line(
        viewer,
        Whole::RunSaid,
        &run_keyed(session_id, run_id),
        field,
    )
}

/// And for the session itself.
pub fn session_said(viewer: &Viewer, session_id: &str, field: &str) -> Result<Markup, PageError> {
    enrichment_line(
        viewer,
        Whole::SessionSaid,
        &vec![("session_id", session_id.into())],
        field,
    )
}

/// What one api call said, whole.
pub fn call_text(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    api_call_id: &str,
) -> Result<Markup, PageError> {
    prose(
        viewer,
        Whole::CallText,
        &call_keyed(session_id, source, api_call_id),
        "value",
        "text",
    )
}

/// What one api call thought, whole.
pub fn call_thinking(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    api_call_id: &str,
) -> Result<Markup, PageError> {
    prose(
        viewer,
        Whole::CallThinking,
        &call_keyed(session_id, source, api_call_id),
        "value",
        "thinking",
    )
}

fn call_keyed(session_id: &str, source: &str, api_call_id: &str) -> Bound {
    vec![
        ("session_id", session_id.into()),
        ("source", source.into()),
        ("api_call_id", api_call_id.into()),
    ]
}

fn tool_keyed(session_id: &str, source: &str, tool_call_id: &str) -> Bound {
    vec![
        ("session_id", session_id.into()),
        ("source", source.into()),
        ("tool_call_id", tool_call_id.into()),
    ]
}

/// One raw transcript record whole, as the browser's preview was cut from.
///
/// Its own renderer rather than a value fragment: a record arrives with a header line of its own,
/// and it is the line a node was read from rather than one of the node's values, so nothing on a
/// pane files it under a name and nothing swaps it into a detail.
pub fn record_value(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    line_no: i64,
) -> Result<Markup, PageError> {
    let keyed: Bound = vec![
        ("session_id", session_id.into()),
        ("source", source.into()),
        ("line_no", line_no.into()),
    ];
    // The record itself, which the store holds NOT NULL.
    let (row, citation) = fetched(viewer, Whole::Record, &keyed, "raw")?;
    Ok(values::record(&builders::record_value(&row, citation)?))
}

/// What one tool call was passed, whole.
pub fn tool_input(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    tool_call_id: &str,
) -> Result<Markup, PageError> {
    code(
        viewer,
        Whole::ToolInput,
        &tool_keyed(session_id, source, tool_call_id),
        "value",
        "input",
        None,
    )
}

/// What one tool call returned, whole — the largest single fetch the viewer makes.
pub fn tool_result(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    tool_call_id: &str,
) -> Result<Markup, PageError> {
    let mut keyed = tool_keyed(session_id, source, tool_call_id);
    // Not a cut of the answer, which rides whole: the bound on the file suffix beside it, which is
    // what says how the answer is marked up.
    keyed.push(("head_chars", (queries::HEADER_CHARS as i64).into()));
    code(viewer, Whole::ToolResult, &keyed, "value", "result", None)
}

/// What one `Bash` call ran, whole — read as the shell reads it.
pub fn tool_command(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    tool_call_id: &str,
) -> Result<Markup, PageError> {
    code(
        viewer,
        Whole::ToolCommand,
        &tool_keyed(session_id, source, tool_call_id),
        "value",
        "command",
        Some(Syntax::Bash),
    )
}

/// What one turn was asked, whole.
pub fn turn_prompt(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    turn_id: &str,
) -> Result<Markup, PageError> {
    prose(
        viewer,
        Whole::TurnPrompt,
        &turn_keyed(session_id, source, turn_id),
        "value",
        "prompt",
    )
}

/// What followed the slash command one turn ran, whole.
pub fn turn_command_args(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    turn_id: &str,
) -> Result<Markup, PageError> {
    prose(
        viewer,
        Whole::TurnCommandArgs,
        &turn_keyed(session_id, source, turn_id),
        "value",
        "command_args",
    )
}

/// The whole brief one agent run was given.
pub fn run_brief(viewer: &Viewer, session_id: &str, run_id: &str) -> Result<Markup, PageError> {
    prose(
        viewer,
        Whole::RunBrief,
        &run_keyed(session_id, run_id),
        "value",
        "brief",
    )
}

/// The whole of what one agent run was asked, off the call that spawned it.
pub fn run_prompt(viewer: &Viewer, session_id: &str, run_id: &str) -> Result<Markup, PageError> {
    prose(
        viewer,
        Whole::RunPrompt,
        &run_keyed(session_id, run_id),
        "value",
        "prompt",
    )
}

/// The whole of what one agent run sent back to the agent that spawned it.
pub fn run_result(viewer: &Viewer, session_id: &str, run_id: &str) -> Result<Markup, PageError> {
    prose(
        viewer,
        Whole::RunResult,
        &run_keyed(session_id, run_id),
        "value",
        "result",
    )
}
