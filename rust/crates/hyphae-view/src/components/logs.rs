//! The pane's children log: one numbered page of what is under a node, a row per child.
//!
//! Ported from `src/hyphae/view/components/logs.py`. A table, because the row is a handful of
//! numbers and a reader who cannot tell an api-call count from a tool-call count from a time of
//! day is reading nothing. The columns are the shape's own and come from
//! [`crate::columns::Shape::columns`] — the head below writes them and every row fills all of
//! them, so a cell is always under its own heading. The words above them are the registry's, the
//! same one a pane's facts read.
//!
//! A row is a line that gets a reader to the child's own page rather than a preview of it: what it
//! prints in the wide column is the child's title, the same words the NavTree row and the pane
//! heading use (`docs/viewer.md`).
//!
//! The four row types below are what a route hands in instead of a store row: the shape decides
//! the columns, and the type of the row decides the cells, so a query that stopped returning a
//! column is a wrong arm rather than a blank cell under a heading.

use chrono::{DateTime, Utc};
use hypertext::prelude::*;

use crate::columns::{Column, Shape};
use crate::components::{Markup, parts};
use crate::cuts;
use crate::format as fmt;
use crate::labels::label;
use crate::nodes::Node;
use crate::render;

/// A children log's place in its level, and the way to either side of it.
pub struct Pager {
    /// Which page of how many, in words — the label the control is read and heard by.
    pub place: String,
    pub previous: Option<String>,
    pub next: Option<String>,
}

/// One turn as its parent's log prints it.
pub struct LoggedTurn {
    pub turn_index: i64,
    pub api_calls: i64,
    pub tool_calls: i64,
}

/// One api call as its turn's log prints it.
///
/// `called` is the tools it went on to call, named the way their own rows name them: composed at
/// the route from the rows the query shipped, because naming a tool call is
/// [`crate::formatters`]'s.
pub struct LoggedCall {
    pub call_index: i64,
    pub model: Option<String>,
    pub text_head: Option<String>,
    pub tool_calls: i64,
    pub called: String,
    pub text_chars: i64,
}

/// One tool call as its call's log prints it.
///
/// `about` is what the call was for where its title already says what it did — the second line
/// under the wide column, empty for every tool whose title stands alone.
pub struct LoggedTool {
    pub tool_index: i64,
    pub name: Option<String>,
    pub about: String,
    pub is_error: bool,
    pub result_chars: Option<i64>,
}

/// One agent run as its parent's log prints it.
pub struct LoggedRun {
    pub agent_type: Option<String>,
    pub tool_errors: i64,
}

/// What a log row's own shape prints. Total over the four shapes a log has columns for, so a
/// fifth kind of row is a compile error at the call site.
pub enum Kind {
    Turn(LoggedTurn),
    Call(LoggedCall),
    Tool(LoggedTool),
    Run(LoggedRun),
}

/// One child as a log lists it: the node every shape leads to, when it ran, and its own cells.
pub struct Logged {
    pub node: Node,
    pub started_at: Option<DateTime<Utc>>,
    pub cells: Kind,
}

/// One page of a node's children, or nothing where the node has no level under it.
///
/// The heading counts the level and not the page: a reader who lands on page 2 of a turn's calls
/// is reading a turn of however many calls it made, not a turn of a hundred.
///
/// `opens` is whether a row here can be opened in place. False inside an expansion, which is
/// already one level opened: the rows carry no button and the table drops the column that holds
/// one, because an expansion inside an expansion is the accordion of accordions the pane is built
/// to avoid (`.claude/rules/viewer-ui.md`).
pub fn log(
    shape: Shape,
    rows: &[Logged],
    total: Option<i64>,
    suffix: &str,
    pager: Option<&Pager>,
    opens: bool,
) -> Option<Markup> {
    if shape == Shape::None {
        return None;
    }
    let heads = render::joined(
        shape
            .columns()
            .iter()
            .filter(|column| opens || column.field != "body")
            .map(head),
    );
    let body = render::joined(rows.iter().map(|row| line(row, suffix, opens)));
    Some(
        rsx! {
            <section class="log" data-log=(shape.word())>
                <h2><span data-field="children">(fmt::count(total))</span>(format!(" {shape}"))</h2>
                <table>
                    <thead><tr data-columns=(shape.word())>(heads)</tr></thead>
                    <tbody>(body)</tbody>
                </table>
                @if let Some(pager) = pager { (paged(shape, pager)) }
            </section>
        }
        .memoize(),
    )
}

/// One column head: the mark, the space after it, and the word the registry gives the field.
fn head(column: &Column) -> Markup {
    rsx! {
        <th scope="col" data-column=(column.field) class=[(!column.css.is_empty()).then_some(column.css)]>
            (parts::mark(column.icon))" "(label(column.field))
        </th>
    }
    .memoize()
}

/// The one wide column of a row: what the child is called, linking to the child's own page.
///
/// `second` is the line under it, in lower hierarchy — what the first line left out, which today
/// is what a tool call was for where its title already says what it did
/// ([`crate::builders::tool_about`]). Empty on every other shape, which has one line to give.
fn what(node: &Node, suffix: &str, field: &str, words: Markup, second: &str) -> Markup {
    let url = format!("{}{suffix}", node.url());
    rsx! {
        <td data-column=(field) class="what">
            <a
                class="primary"
                hx-get=(url)
                hx-target="#reading-pane"
                hx-swap="outerHTML"
                hx-select="#reading-pane"
                hx-select-oob="#nav-tree-rows"
                hx-push-url="true"
                href=(url)
            >
                (parts::glyph(node.enriched))<span data-field=(field)>(words)</span>
            </a>
            @if !second.is_empty() {
                <span class="secondary" data-field="about">(second)</span>
            }
        </td>
    }
    .memoize()
}

/// The control under a log, where the level runs past one page.
///
/// Only there: a control offering no page to go to is a control a reader has to read to learn
/// there is nothing under it. Plain links, because turning a page is a page load — the NavTree
/// beside it opens on the child the reader is on.
fn paged(shape: Shape, pager: &Pager) -> Markup {
    rsx! {
        <nav class="pager" data-pager=(shape.word()) aria-label=(format!("{shape} pager"))>
            @if let Some(previous) = &pager.previous {
                <a data-page="previous" href=(previous)>"← previous page"</a>" "
            }
            <span data-field="place">(pager.place)</span>
            @if let Some(next) = &pager.next {
                " "<a data-page="next" href=(next)>"next page →"</a>
            }
        </nav>
    }
    .memoize()
}

/// One child's row: the shape's own cells, the time it started, and the way to open it.
fn line(row: &Logged, suffix: &str, opens: bool) -> Markup {
    rsx! {
        <tr data-child=(row.node.key())>
            (cells(row, suffix))
            (cell("started_at", &fmt::clock(row.started_at), "when"))
            @if opens { (opener(&row.node, suffix)) }
        </tr>
    }
    .memoize()
}

/// The cells the row's own shape prints, in the order its columns head them.
///
/// Total over the four kinds of row a log lists: a shape with no arm would print a row of the time
/// it started and nothing else, under headings for the columns it dropped.
fn cells(row: &Logged, suffix: &str) -> Markup {
    let node = &row.node;
    match &row.cells {
        Kind::Turn(turn) => rsx! {
            (cell("turn_index", &fmt::count(Some(turn.turn_index)), "number"))
            (what(node, suffix, "title", node.log_title(), ""))
            (cell("api_calls", &fmt::count(Some(turn.api_calls)), "number"))
            (cell("tool_calls", &fmt::count(Some(turn.tool_calls)), "number"))
            (cell("cost_usd", &fmt::money(node.cost_usd()), "number"))
        }
        .memoize(),
        Kind::Call(call) => rsx! {
            (cell("call_index", &fmt::count(Some(call.call_index)), "number"))
            (what(node, suffix, "model", render::text(&cuts::line(call.model.as_deref())), ""))
            (cell("text", &cuts::line(call.text_head.as_deref()), "said"))
            (cell("tool_calls", &fmt::count(Some(call.tool_calls)), "number"))
            (cell("tool_titles", &cuts::line(Some(&call.called)), "called"))
            (cell("text_chars", &fmt::count(Some(call.text_chars)), "number"))
            (cell("cost_usd", &fmt::money(node.cost_usd()), "number"))
        }
        .memoize(),
        // The title alone, with the name already in its own column beside it. What the call was
        // for reads through the same cut, and is left out rather than dashed where the record says
        // nothing: a dash under a command is a line of nothing where the second line means "and
        // this is what it was for".
        Kind::Tool(tool) => rsx! {
            (cell("tool_index", &fmt::count(Some(tool.tool_index)), "number"))
            (cell("name", &cuts::line(tool.name.as_deref()), ""))
            (what(
                node,
                suffix,
                "title",
                node.log_title(),
                &if tool.about.is_empty() { String::new() } else { cuts::line(Some(&tool.about)) },
            ))
            (cell("is_error", &fmt::text(tool.is_error.then_some("error")), ""))
            (cell("result_chars", &fmt::count(tool.result_chars), "number"))
        }
        .memoize(),
        Kind::Run(run) => rsx! {
            (cell("agent_type", &cuts::line(run.agent_type.as_deref()), ""))
            (what(node, suffix, "title", node.log_title(), ""))
            (cell("tool_errors", &fmt::count(Some(run.tool_errors)), "number"))
            (cell("cost_usd", &fmt::money(node.cost_usd()), "number"))
        }
        .memoize(),
    }
}

/// One cell of a row: the value under its own column, labelled for a test to read.
fn cell(field: &str, value: &str, css: &str) -> Markup {
    rsx! {
        <td data-column=(field) class=[(!css.is_empty()).then_some(css)]>
            <span data-field=(field)>(value)</span>
        </td>
    }
    .memoize()
}

/// The child's body, opened in place: one body, two mounts.
///
/// It arrives on click, one request, and stops one level down: an api call's body lists the tools
/// it called, as rows of this same table with no `View` of their own, and every other kind stands
/// a count and a link to its own page. A button rather than a disclosure triangle, because a
/// reader has to be able to see that a row opens.
///
/// The swap vocabulary is spelled here rather than shared, because the button in the last column
/// must not swap the pane: hoisting it onto the table would have to be undone on this element,
/// which is a line either way. `hx-trigger="click once"` because a second fetch would stand a
/// second copy of the same body under the row.
fn opener(node: &Node, suffix: &str) -> Markup {
    rsx! {
        <td data-column="body">
            <button
                class="button"
                hx-get=(format!("{}{suffix}", node.expansion()))
                hx-trigger="click once"
                hx-target="closest tr"
                hx-swap="afterend"
                type="button"
                data-view=(node.key())
            >"View"</button>
        </td>
    }
    .memoize()
}
