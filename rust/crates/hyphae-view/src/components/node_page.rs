//! One node of a session, whole: the NavTree it sits in, and the pane that reads it.
//!
//! Both arrive in one response, and a click on a NavTree row re-fetches this same URL — htmx
//! takes `#reading-pane` out of the response and swaps `#nav-tree-rows` out of band, so a pasted
//! link and a click serve the same bytes.
//!
//! Stage 3a serves the session's own node page. The pane's later sections go in the order the
//! Python names them, between the body and the raw links: the enrichment block, the details, and
//! after the raw links the children log, the walk, the error stepper and the citation footer.

use hypertext::prelude::*;

use crate::components::nav_tree::{NavTreeRow, PresetChoice};
use crate::components::node_body::Facts;
use crate::components::{Markup, layout, nav_tree, node_body, parts};
use crate::cuts;
use crate::format as fmt;
use crate::nodes::Node;

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

/// The whole document: the NavTree, the grip between the columns, and the reading pane.
#[allow(clippy::too_many_arguments)]
pub fn page(
    selection: &Node,
    choices: &[PresetChoice],
    rows: &[NavTreeRow],
    thread: &str,
    trail: &Trail,
    chain: &[Node],
    facts: &Facts,
    archived: &Archived,
    suffix: &str,
) -> Markup {
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
                    id="nav-tree-rows"
                    hx-target="#reading-pane"
                    hx-swap="outerHTML"
                    hx-select="#reading-pane"
                    hx-select-oob="#nav-tree-rows"
                    hx-push-url="true"
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
                (raw(archived))
            </article>
        </div>
    }
    .memoize();
    layout::page(&tab_title, Some(scripts()), main, None)
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
            @if let Some(line) = archived.line_no {
                " "
                <a
                    data-field="record"
                    href=(format!("{}/records?after={}#L{line}", archived.thread_url, line - 1))
                >"line "(fmt::count(Some(line)))</a>
            }
        </p>
        @if let Some(line) = archived.line_no {
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
