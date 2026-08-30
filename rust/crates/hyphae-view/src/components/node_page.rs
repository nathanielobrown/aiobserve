//! One node of a session, whole: the NavTree it sits in, and the pane that reads it.
//!
//! Both arrive in one response, and a click on a NavTree row re-fetches this same URL — htmx
//! takes `#reading-pane` out of the response and swaps `#nav-tree-rows` out of band, so a pasted
//! link and a click serve the same bytes.
//!
//! Ported from `src/hyphae/view/components/node_page.py`.

use hypertext::prelude::*;

use crate::citation::Cited;
use crate::columns::Shape;
use crate::components::logs::{Logged, Pager};
use crate::components::nav_tree::{NavTreeRow, PresetChoice};
use crate::components::node_body::Facts;
use crate::components::{Markup, citation, layout, logs, nav_tree, node_body, parts};
use crate::cuts;
use crate::detail::{Detail, EnrichmentLines};
use crate::enrichment::Enrichment;
use crate::errors::Step as Failures;
use crate::format as fmt;
use crate::nodes::Node;
use crate::walk::Step as Walked;

/// The two scripts only this page needs: what no stylesheet can reach — where the tree opens,
/// where a popover stands, and the NavTree width a reader sets by dragging. The width is not a
/// knob on the URL: a column width belongs to the screen and not to the node a link names.
fn scripts() -> Markup {
    rsx! {
        <script src="/static/nav-tree-width.js" defer></script>
        <script src="/static/nav-tree.js" defer></script>
    }
    .memoize()
}

/// The way out of the session, above the nodes inside it.
///
/// Neither step is a node, so neither carries a node's marks or a knob suffix — a click on either
/// leaves the session. `project_url` is nothing where the store holds no path to filter the list
/// by, and the project then prints without a link.
pub struct Trail {
    pub list_url: String,
    pub project_dir: Option<String>,
    pub project_url: Option<String>,
}

/// The bytes behind the node: the thread's transcript, and the line it was read from.
pub struct Archived {
    pub thread_url: String,
    pub line_no: Option<i64>,
}

/// What a pass wrote about the node, and the way to the whole of each line it wrote.
///
/// One value rather than two: a pane that had the words without the links, or the links without
/// the words, is not a state [`crate::detail`] can produce.
pub struct Said {
    pub enrichment: Enrichment,
    pub lines: EnrichmentLines,
}

/// Everything one node page renders, gathered so the route hands over one value.
pub struct NodePage<'a> {
    pub selection: &'a Node,
    pub choices: &'a [PresetChoice],
    pub rows: &'a [NavTreeRow],
    pub thread: &'a str,
    pub trail: &'a Trail,
    pub chain: &'a [Node],
    pub facts: &'a Facts,
    pub said: Option<&'a Said>,
    pub details: &'a [Detail],
    pub archived: &'a Archived,
    pub walked_previous: Option<&'a Walked>,
    pub walked_next: Option<&'a Walked>,
    pub tool_errors: Option<i64>,
    pub failures: Option<&'a Failures>,
    pub shape: Shape,
    pub log_rows: &'a [Logged],
    pub total: Option<i64>,
    pub pager: Option<&'a Pager>,
    pub suffix: &'a str,
    pub citations: &'a [(String, Cited)],
    pub dev: bool,
}

/// The whole document: the NavTree, the grip between the columns, and the reading pane.
pub fn page(shown: &NodePage<'_>) -> Markup {
    let NodePage {
        selection,
        choices,
        rows,
        thread,
        trail,
        chain,
        facts,
        archived,
        suffix,
        ..
    } = *shown;
    let tab_title = format!("{} {} · hyphae", selection.icon(), selection.tab_title());
    let main = rsx! {
        <div id="browser">
            <nav id="nav-tree" aria-label="Session NavTree">
                // What every row of the NavTree does on a click, written once: fetch the row's
                // own URL, take `#reading-pane` out of the response and put it where the pane is,
                // and swap these rows in out of band. htmx reads each of these off the closest
                // ancestor carrying it, so the rows below carry only the URL that differs between
                // them — 3,217 rows is four fifths of a node page's budget
                // (`.claude/rules/viewer-ui.md`).
                <div
                    hx-target="#reading-pane"
                    hx-swap="outerHTML"
                    hx-select="#reading-pane"
                    hx-select-oob="#nav-tree-rows"
                    hx-push-url="true"
                    id="nav-tree-rows"
                >
                    (nav_tree::presets(choices))
                    <ul class="rows">(nav_tree::lines(rows, suffix, thread))</ul>
                </div>
            </nav>
            // What the reader drags to widen the NavTree. A separator rather than a button: it
            // divides the two columns rather than doing anything, and arrow keys move it for a
            // reader who is not dragging. `static/nav-tree-width.js` is what it moves.
            <div
                id="nav-tree-grip"
                role="separator"
                aria-orientation="vertical"
                aria-label="NavTree width"
                tabindex="0"
            ></div>
            <article id="reading-pane">
                (crumbs(selection, trail, chain, suffix))
                (node_body::body(selection, facts, suffix))
                // What an enrichment pass said about this node, where a pass reached it.
                @if let Some(said) = shown.said { (parts::summary(&said.enrichment, &said.lines)) }
                // The node's own values, cut to the pane's width, each with the way to the whole
                // of it.
                @for item in shown.details { (parts::detail(item)) }
                (raw(archived))
                (logs::log(shown.shape, shown.log_rows, shown.total, suffix, shown.pager, true))
                (walk(shown.walked_previous, shown.walked_next, suffix))
                (stepper(&selection.session_id, shown.tool_errors, shown.failures, suffix))
                // What produced the page, last in the pane rather than under the document. This
                // page fills the viewport and the pane is what scrolls, so a footer outside it
                // would sit below a fold nobody can reach — and the swap takes `#reading-pane` out
                // of the response, so standing it here is also what keeps a clicked node's
                // citations current.
                (citation::footer(shown.citations))
            </article>
        </div>
    }
    .memoize();
    // Emptied: this page renders its citations inside the pane above.
    layout::page(&tab_title, Some(scripts()), main, None, shown.dev)
}

/// Reading in order, along the level the reader is standing on.
///
/// Going down is what the NavTree is for, so these two go along the level and back out of it,
/// never into a node. Buttons because they move the pane rather than leading anywhere new: the
/// swap they carry is the one a NavTree row carries, written here because the pane is what the
/// click replaces. A step that leaves the level says so with an up arrow.
fn walk(previous: Option<&Walked>, following: Option<&Walked>, suffix: &str) -> Markup {
    rsx! {
        <nav
            class="walk"
            hx-target="#reading-pane"
            hx-swap="outerHTML"
            hx-select="#reading-pane"
            hx-select-oob="#nav-tree-rows"
            hx-push-url="true"
            aria-label="Read in order"
        >
            @if let Some(step) = previous {
                (walked(step, "previous", if step.climbed { "↑" } else { "←" }, suffix))
            }
            @if let Some(step) = following {
                (walked(step, "next", if step.climbed { "↑" } else { "→" }, suffix))
            }
        </nav>
    }
    .memoize()
}

/// One control of the walk: where it goes, and whether taking it leaves the level.
fn walked(step: &Walked, way: &str, arrow: &str, suffix: &str) -> Markup {
    let named = rsx! {
        (parts::glyph(step.node.enriched))
        <span data-field="kind">(step.node.kind.word())</span>
        " "
        <span data-field="title">(step.node.nav_tree_title())</span>
    }
    .memoize();
    rsx! {
        <button
            class="button"
            type="button"
            data-walk=(way)
            data-node=(step.node.key())
            data-climb=[step.climbed.then_some(way)]
            hx-get=(format!("{}{suffix}", step.node.url()))
        >
            @if way == "previous" { (arrow)" "(named) } @else { (named)" "(arrow) }
        </button>
    }
    .memoize()
}

/// Where this session failed.
///
/// Written once here rather than per kind of node, because it is a fact about the session and
/// every node page reads the session's header: the way to the whole list rides every page, and the
/// step between two failures only appears where the pane is standing on one — the walk above
/// reaches the next *node*, and a reader hunting failures wants the next *failure*, five spawns and
/// two threads away.
fn stepper(
    session_id: &str,
    tool_errors: Option<i64>,
    failures: Option<&Failures>,
    suffix: &str,
) -> Option<Markup> {
    let tool_errors = tool_errors.filter(|count| *count != 0)?;
    Some(
        rsx! {
            <nav class="error-stepper" aria-label="Where this session failed">
                @if let Some(node) = failures.and_then(|held| held.previous.as_ref()) {
                    (failure(node, "previous", suffix))
                }
                <a data-step="all" href=(format!("/session/{session_id}/errors"))>
                    <span data-field="tool_errors">(fmt::count(Some(tool_errors)))</span>
                    " tool error(s)"
                </a>
                @if let Some(node) = failures.and_then(|held| held.next.as_ref()) {
                    (failure(node, "next", suffix))
                }
            </nav>
        }
        .memoize(),
    )
}

/// One step of the error stepper: the failure read before this one, or the one after.
fn failure(node: &Node, way: &str, suffix: &str) -> Markup {
    let named = rsx! { <span data-field="title">(node.nav_tree_title())</span> }.memoize();
    rsx! {
        <a data-step=(way) data-node=(node.key()) href=(format!("{}{suffix}", node.url()))>
            @if way == "previous" { "← "(named) } @else { (named)" →" }
        </a>
    }
    .memoize()
}

/// Where the node sits, outermost first: the same chain the NavTree has open.
fn crumbs(selection: &Node, trail: &Trail, chain: &[Node], suffix: &str) -> Markup {
    rsx! {
        <nav
            class="crumbs"
            data-crumbs=(selection.kind.word())
            aria-label="Where this node sits"
        >
            <a data-crumb-head="home" href=(trail.list_url)>(parts::mark("🏠"))</a>" "
            (project(trail))
            @for step in chain {
                <a data-crumb=(step.key()) href=(format!("{}{suffix}", step.url()))>
                    (parts::mark(step.icon()))" "
                    (parts::glyph(step.enriched))
                    <span data-field=(step.kind.word())>(step.crumb_title())</span>
                </a>" "
            }
        </nav>
    }
    .memoize()
}

/// The session's project, linked where the list can be filtered down to it.
fn project(trail: &Trail) -> Option<Markup> {
    let named = cuts::project_path(Some(trail.project_dir.as_deref()?));
    Some(match &trail.project_url {
        Some(url) => rsx! {
            <a data-crumb-head="project" href=(url)>
                <span data-field="project_dir">(named)</span>
            </a>" "
        }
        .memoize(),
        None => rsx! {
            <span data-crumb-head="project">
                <span data-field="project_dir">(named)</span>
            </span>" "
        }
        .memoize(),
    })
}

/// What the extractor read, under what it made of it.
///
/// The record arrives on open, one request: a raw line is the least filtered thing the viewer
/// shows and the widest, so a pane that carried it unasked would be a pane priced for a
/// transcript.
fn raw(archived: &Archived) -> Markup {
    rsx! {
        <p class="raw">
            <a data-field="records" href=(format!("{}/records", archived.thread_url))>
                "this thread's transcript"
            </a>
            @if let Some(line) = archived.line_no.filter(|held| *held != 0) {
                " "
                <a
                    data-field="record"
                    href=(format!("{}/records?after={}#L{line}", archived.thread_url, line - 1))
                >"line "(fmt::count(Some(line)))</a>
            }
        </p>
        @if let Some(line) = archived.line_no.filter(|held| *held != 0) {
            <details
                class="raw"
                data-open-record=(line)
                hx-get=(format!("/fragment/record{}/line/{line}", archived.thread_url))
                hx-trigger="toggle once"
                hx-target="find .value"
            >
                <summary>"archived record"</summary>
                <div class="value"></div>
            </details>
        }
    }
    .memoize()
}
