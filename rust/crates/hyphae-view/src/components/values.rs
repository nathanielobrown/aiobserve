//! The whole of one value, arriving as the block that previewed it.
//!
//! Ported from `src/hyphae/view/components/values.py`. A pane prints the head of a fat value and
//! offers the rest behind a link; these are what comes back. Each replaces the block it was fetched
//! from, so it arrives wearing that block's own class and key — and carries its own citation,
//! because a fragment is a page's answer too (`docs/viewer.md`).

use chrono::{DateTime, Utc};
use hypertext::prelude::*;

use crate::components::{Markup, parts};
use crate::format as fmt;
use crate::highlight::Syntax;

/// One fat value fetched on its own: what it says, what it is filed under, and its query.
///
/// `detail` is the name the pane filed the value under, and nothing for a value that is nobody's
/// detail — the archived record. The styling that tells an ask from an answer reads it, which is
/// why the fragment carries it back out.
pub struct Whole {
    pub value: Option<String>,
    pub detail: Option<String>,
    pub citation: String,
}

impl Whole {
    /// How long the value is, which the block carries so a test can read it without the value.
    fn weight(&self) -> usize {
        self.value.as_deref().unwrap_or_default().chars().count()
    }
}

/// One raw transcript record, whole: its own header line, and the JSON under it.
pub struct Record {
    pub line_no: i64,
    pub kind: String,
    pub uuid: Option<String>,
    pub timestamp: Option<DateTime<Utc>>,
    pub raw_chars: Option<i64>,
    pub raw: String,
    pub citation: String,
}

/// The whole of one line an enrichment pass wrote — what it said, or the friction it saw.
///
/// No link out, because there is no rest left to offer: the block it replaces held a head and the
/// ask, and this is the whole of it.
pub fn enrichment_line(node: &Whole) -> Markup {
    rsx! {
        <span
            class="enrichment-line"
            data-enrichment-line=[node.detail.as_deref()]
            data-value=(node.weight())
            data-query=(&node.citation)
        ><span data-field=[node.detail.as_deref()]>(node.value.as_deref())</span></span>
    }
    .memoize()
}

/// The whole of one value as the markdown it was written in — an api call's text or thought.
///
/// Through the same component the pane's own preview went through: one value, two mounts, and one
/// escaping policy over both ([`crate::render`]).
pub fn prose(node: &Whole) -> Markup {
    mount(
        node,
        "value detail quoted",
        parts::prose(
            node.detail.as_deref().unwrap_or("value"),
            node.value.as_deref(),
        ),
    )
}

/// The whole of one value that was never prose — what a tool was passed, ran, or returned.
///
/// Marked up as whatever the row said it was written in, and as JSON otherwise: a tool's arguments
/// are JSON, and JSON put through a markdown renderer stops being the thing a reader came to
/// re-read.
pub fn code(node: &Whole, syntax: Syntax) -> Markup {
    mount(
        node,
        "value detail",
        parts::code(node.value.as_deref().unwrap_or_default(), syntax, "value"),
    )
}

/// One raw transcript record whole, as the browser's preview was cut from.
///
/// Indented and marked up as the JSON it is rather than rendered as markdown: what a reader wants
/// here is the shape Claude Code wrote, field by field.
pub fn record(node: &Record) -> Markup {
    rsx! {
        <div class="value" data-record-value=(node.line_no) data-query=(&node.citation)>
            <p class="numbers">
                <span class="type" data-field="type">(&node.kind)</span>
                // Not every record carries one: a summary record has no uuid, and a turn links to
                // the record whose uuid is the turn's id.
                @if let Some(uuid) = node.uuid.as_deref().filter(|held| !held.is_empty()) {
                    <span data-field="uuid">(uuid)</span>
                }
                <span data-field="timestamp">(fmt::clock(node.timestamp))</span>
                <span><span data-field="raw_chars">(fmt::count(node.raw_chars))</span>" chars"</span>
            </p>
            (parts::code(&node.raw, Syntax::Json, "raw"))
        </div>
    }
    .memoize()
}

/// The block a fetched detail arrives as, keyed to the section it replaces.
fn mount(node: &Whole, classes: &str, held: Markup) -> Markup {
    rsx! {
        <div
            class=(classes)
            data-detail=[node.detail.as_deref()]
            data-value=(node.weight())
            data-query=(&node.citation)
        >(held)</div>
    }
    .memoize()
}
