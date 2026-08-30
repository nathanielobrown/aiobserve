//! The NavTree: the one open path through a session, as one flat list of rows.
//!
//! Flat rather than nested because a click swaps the whole list out of band in one go, and
//! because a row's own attributes have to be readable without a descendant's answering for it.
//! `data-depth` is what the stylesheet indents by, so nesting would buy the markup nothing.
//!
//! This is the page's byte budget: a node page draws thousands of these rows, and every
//! attribute written here is written that many times (`.claude/rules/viewer-ui.md`).

use hypertext::prelude::*;

use crate::components::{Markup, parts};
use crate::format as fmt;
use crate::nodes::{Node, Preset};
use crate::render;

/// One line of the NavTree: a node at its depth, or the tail standing for what a cap cut.
pub struct NavTreeRow {
    pub node: Node,
    pub depth: usize,
    pub selected: bool,
    /// Whether this row is a step of the open path above the selection: the stylesheet clamps
    /// those at the top of the scroller, so a reader deep in a level sees what they are inside.
    pub ancestor: bool,
    /// On a tail row, how many of `node`'s children the cap left out. Zero on a node's own row,
    /// which is what tells the two apart.
    pub cut: i64,
    /// On a tail row, the key of the child the open path descends through, when this level holds
    /// one. The row's own fetch carries it: the cap keeps that child whatever its place in the
    /// level, so the fetch has to know it to leave it out of what it sends back.
    pub opened: Option<String>,
}

impl NavTreeRow {
    /// A node's own row, at `depth`, neither cut nor descended through.
    pub fn node(node: Node, depth: usize, selected: bool, ancestor: bool) -> Self {
        Self {
            node,
            depth,
            selected,
            ancestor,
            cut: 0,
            opened: None,
        }
    }
}

/// One preset as the control above the NavTree offers it: where it goes, and whether we are in it.
pub struct PresetChoice {
    pub preset: Preset,
    pub url: String,
    pub current: bool,
}

/// What a click on a NavTree row or a log row's wide column does: swap the reading pane, and the
/// NavTree beside it, for the child's own. Written once here and read by every surface that links
/// a node into the pane. `hx-get` is not in it: the URL is the row's.
///
/// One attribute per line rather than a map the way the Python spells it, because `rsx!` writes
/// attributes and not dictionaries — the swap is inherited from `#nav-tree-rows`, and this is
/// the element that carries it.
pub fn pane_swap() -> Markup {
    rsx! {
        <div
            id="nav-tree-rows"
            hx-target="#reading-pane"
            hx-swap="outerHTML"
            hx-select="#reading-pane"
            hx-select-oob="#nav-tree-rows"
            hx-push-url="true"
        ></div>
    }
    .memoize()
}

/// The preset the NavTree is in, and the ones the reader can switch to.
///
/// The same node under each `?nav=`, so a switch never costs them their place. Inside the
/// swapped element rather than above it, which is what keeps the links pointing at the node a
/// click just landed on.
pub fn presets(choices: &[PresetChoice]) -> Markup {
    rsx! {
        <p class="presets" aria-label="NavTree presets">
            @for choice in choices {
                <a
                    class="button"
                    data-nav=(choice.preset.word())
                    href=(choice.url)
                    aria-current=[choice.current.then_some("true")]
                >(choice.preset.label())</a>
            }
        </p>
    }
    .memoize()
}

/// Every row of one NavTree, in document order — a node's own, or a level's tail.
///
/// `thread` is the thread the rows are described for, which is what a tail row's fetch has to
/// carry: a level may hold nodes of another thread, and a row is described by the thread the
/// reader is on rather than by the one its node ran on.
pub fn lines(rows: &[NavTreeRow], suffix: &str, thread: &str) -> Markup {
    render::joined(rows.iter().map(|row| {
        if row.cut != 0 {
            tail(row, suffix, thread)
        } else {
            line(row, suffix)
        }
    }))
}

/// What this level's window left out, and the way to it.
///
/// htmx fetches those rows and stands them where this one stands, so the level opens without the
/// reader losing the pane. A button rather than a link — there is no page at the other end, only
/// the rest of a level. The fetch carries the reader's knobs, the thread they are reading on, the
/// depth these rows sit at, and the child the open path descends through, which the cap kept and
/// this must not send twice.
fn tail(row: &NavTreeRow, suffix: &str, thread: &str) -> Markup {
    let joiner = if suffix.is_empty() { "?" } else { "&" };
    let mut fetch = format!(
        "{}{suffix}{joiner}thread={thread}&depth={}",
        row.node.rest(),
        row.depth
    );
    if let Some(opened) = &row.opened {
        fetch.push_str(&format!("&opened={opened}"));
    }
    rsx! {
        <li class="row more" data-depth=(row.depth) data-more=(row.node.key())>
            <button
                type="button"
                hx-get=(fetch)
                hx-target="closest li"
                hx-swap="outerHTML"
                hx-select="unset"
                hx-select-oob="unset"
                hx-push-url="false"
            >"+"<span data-field="cut">(fmt::count(Some(row.cut)))</span>" more"</button>
        </li>
    }
    .memoize()
}

/// One node's row: what it is, what it is called, and what it cost.
///
/// `ancestor` is what the stylesheet clamps: a step of the open path above the selection stays at
/// the top of the scroller while the rows under it go by. A class rather than a key of its own,
/// like the bar's — it is a thing the stylesheet paints and not a value the store holds.
fn line(row: &NavTreeRow, suffix: &str) -> Markup {
    let node = &row.node;
    let url = format!("{}{suffix}", node.url());
    let selected = row.selected.then(|| node.key());
    // Byte-for-byte what htpy writes, trailing space and all: a node with no context bar leaves
    // the class list ending on the separator, and the two viewers serve one page.
    let mut class = format!("row node {} {}", node.kind, node.bar());
    if row.ancestor {
        class.push_str("ancestor");
    }
    rsx! {
        <li
            class=(class)
            data-depth=(row.depth)
            data-nav-tree=(node.key())
            data-selected=[selected.as_deref()]
            aria-current=[row.selected.then_some("true")]
        >
            (peek(&node.numbers()))
            // A row links where it fetches: one URL, whether the reader clicks it, pastes it, or
            // comes back to it from a bookmark. What the click does with the response is written
            // once, on `#nav-tree-rows`, and inherited from here.
            <a href=(url) hx-get=(url)>
                (parts::mark(node.icon()))
                (parts::glyph(node.enriched))
                <span data-field="title">(node.nav_tree_title())</span>
                (error(node.is_error))
                (compacted(node.compactions))
                (cost(node))
            </a>
        </li>
    }
    .memoize()
}

/// What the bar and the badge on this row stand for, fetched when a reader reaches the row.
///
/// A sibling of the link rather than the row itself, because htmx attributes are inherited: the
/// overrides a popover needs written on the `<li>` would be inherited by the link inside it, and
/// the click would swap a pane's worth of markup into the row. The trigger listens on the row all
/// the same — `from:` is what separates where an event is heard from what answers it — and on
/// `focusin`, which bubbles where `focus` does not, so a reader tabbing to the link reaches what
/// the pointer reaches. Once apiece: the popover is markup that stays.
fn peek(numbers: &str) -> Option<Markup> {
    if numbers.is_empty() {
        return None;
    }
    Some(
        rsx! {
            <span
                class="peek"
                hx-get=(numbers)
                // The three that undo the swap `#nav-tree-rows` hands down, written before the
                // rest in the order htpy spreads them: the two viewers serve one row, byte for
                // byte, which is what lets a diff of the two trees mean something.
                hx-select="unset"
                hx-select-oob="unset"
                hx-push-url="false"
                hx-trigger="mouseenter from:closest li once delay:200ms, focusin from:closest li once"
                hx-target="this"
                hx-swap="beforeend"
            ></span>
        }
        .memoize(),
    )
}

/// The one thing the NavTree says about a node beyond what it is and what it cost.
///
/// Spelled the way the children log spells it, so the stylesheet's one alarm rule paints both and
/// a test reads either the same way.
fn error(is_error: bool) -> Option<Markup> {
    is_error.then(|| rsx! { <span data-field="is_error">"error"</span> }.memoize())
}

/// How often this run's own thread ran its window out.
///
/// Drawn on a run's row alone, because a compaction of the thread the reader is on is already a ⊟
/// row of the tree and this one is not: a subagent compacts unasked, on a transcript nobody
/// opened. The count rides the labelled span and the word beside it does not, and the pill is
/// absent rather than zero.
fn compacted(compactions: i64) -> Option<Markup> {
    if compactions == 0 {
        return None;
    }
    let unit = if compactions == 1 {
        " compaction"
    } else {
        " compactions"
    };
    Some(
        rsx! {
            <span class="compacted">
                <span data-field="compactions">(fmt::count(Some(compactions)))</span>(unit)
            </span>
        }
        .memoize(),
    )
}

/// What the row spent, and — where a run hangs under it — what its whole subtree did.
///
/// Two badges rather than one number because the two answer different questions: a turn that
/// spawned four agents cost little itself and drove a lot. Each wears its own step class, so the
/// deeper ground is the bigger share whichever half carries it.
fn cost(node: &Node) -> Option<Markup> {
    let own = node.cost_usd()?;
    Some(
        rsx! {
            <span class="secondary">
                (parts::badge(&node.meter(), "cost_usd", own))
                @if let Some(total) = node.total_usd() {
                    "/"(parts::badge(&node.total_meter(), "total_usd", total))
                }
                (parts::unpriced(node.unpriced_api_calls))
            </span>
        }
        .memoize(),
    )
}
