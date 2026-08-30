//! The pages that are not a node's: an error, a query, the raw records, a file.
//!
//! Ported from `src/hyphae/view/components/pages.py`. Each answers a question no single node
//! holds, so each is a page of its own rather than a body inside the node frame.

use chrono::{DateTime, Utc};
use hypertext::prelude::*;

use crate::citation::Cited;
use crate::components::{Markup, citation, layout, parts};
use crate::errors::Failure;
use crate::format as fmt;
use crate::highlight::Syntax;
use crate::nodes::thread_url;
use crate::urls;

/// What every failure the app catches is answered with — a status, a sentence, a way back.
///
/// The message never repeats what was asked for: a request is untrusted text like any other.
pub fn error_page(status: u16, message: &str, dev: bool) -> Markup {
    let main = rsx! {
        <section id="error">
            <h1 data-field="status">(status)</h1>
            <p data-field="message">(message)</p>
            <p><a href="/">"Back to the projects"</a></p>
        </section>
    }
    .memoize();
    layout::page(&format!("{status} — hyphae"), None, main, None, dev)
}

/// One library query as the page that cited it links to.
///
/// The SQL this build ships, under the bindings that page ran it with. Nothing is executed here —
/// what a reader wants is what the numbers above meant, and the answer to that is the statement,
/// not another result set. `macro_setup` is what a shell has to run first where the statement
/// calls a library macro, and is empty where it calls none.
pub fn query_page(
    name: &str,
    sql: &str,
    macro_setup: &str,
    bindings: &[(String, String)],
    dev: bool,
) -> Markup {
    let main = rsx! {
        <article id="query" data-sql=(name)>
            <h1>(name)".sql"</h1>
            (query_bindings(bindings))
            (setup(macro_setup))
            (parts::code(sql, Syntax::Sql, "sql"))
        </article>
    }
    .memoize();
    layout::page(&format!("{name}.sql · hyphae"), None, main, None, dev)
}

/// What the citing page bound the statement to, or a line saying it bound nothing.
fn query_bindings(bindings: &[(String, String)]) -> Markup {
    if bindings.is_empty() {
        return rsx! { <p class="plain">"Cited with no bindings."</p> }.memoize();
    }
    rsx! {
        <dl class="facts">
            @for (key, value) in bindings {
                <div><dt>(key)</dt><dd data-binding=(key)>(value)</dd></div>
            }
        </dl>
    }
    .memoize()
}

/// The definitions the statement calls, above it — and nothing where it calls none.
///
/// Both consumers install these before they run anything, so a reader who pastes the statement
/// alone gets a catalog error and no way to find out why (`analyze/macros.py`).
fn setup(macro_setup: &str) -> Option<Markup> {
    if macro_setup.is_empty() {
        return None;
    }
    Some(
        rsx! {
            <p class="plain">
                "Run these first: the definitions this statement calls, which "
                <code>"hp query"</code>
                " and the viewer install before they run it."
            </p>
            (parts::code(macro_setup, Syntax::Sql, "macros"))
        }
        .memoize(),
    )
}

/// Where one session failed, whichever thread it happened on.
///
/// A list rather than a pane: a failure is not a place in the NavTree, so there is nothing to open
/// a path to. Each row leads to the tool call's own page, which carries the crumbs that place it.
pub fn errors_page(
    session_id: &str,
    listed: &[Failure],
    cut: i64,
    citations: &[(String, Cited)],
    dev: bool,
) -> Markup {
    let matched = fmt::count(Some(listed.len() as i64 + cut));
    let main = rsx! {
        <section id="errors">
            <h1>"Failed tool calls"</h1>
            <p class="numbers">
                <a href=(format!("/session/{session_id}"))>(session_id)</a>
                <span><span data-field="matched">(matched)</span>" failed call(s)"</span>
            </p>
            <ol class="errors">@for item in listed { (failure(item)) }</ol>
            // What the page left out, said rather than dropped: the store keeps every failure, and
            // this page shows the first of them in the order they happened.
            @if cut > 0 {
                <p class="more" data-more-errors=(cut)>
                    "+"<span data-field="cut">(fmt::count(Some(cut)))</span>" more failed call(s)"
                </p>
            }
        </section>
    }
    .memoize();
    layout::page(
        &format!("{session_id} errors — hyphae"),
        None,
        main,
        citation::footer(citations),
        dev,
    )
}

/// One failed tool call as the list shows it: where it reads, whose thread, and when.
fn failure(item: &Failure) -> Markup {
    rsx! {
        <li data-error=(item.node.key())>
            <a href=(item.node.url())><span data-field="title">(item.node.nav_tree_title())</span></a>
            // The thread it ran on, because the list spans all of them: two failures of one tool
            // name are told apart by which agent hit them.
            <span class="source" data-field="source">(item.node.source.as_deref().unwrap_or(""))</span>
            <span data-field="started_at">(fmt::when(Some(item.started_at)))</span>
        </li>
    }
    .memoize()
}

/// One archived transcript line as the records page prints it, built from its store row.
pub struct RecordRow {
    pub line_no: i64,
    pub kind: String,
    pub timestamp: Option<DateTime<Utc>>,
    pub raw_chars: i64,
    pub raw_head: String,
}

/// What the records page prints: one thread's page of raw lines, and where the next page starts.
pub struct RecordsPage<'a> {
    pub session_id: &'a str,
    pub source: &'a str,
    pub rows: &'a [RecordRow],
    pub matched: i64,
    /// The one row that arrives open, or `None` where the row a citation named is too wide to
    /// open unasked ([`crate::knobs::OPENED_RECORD_CHARS`]).
    pub opened: Option<i64>,
    pub after: Option<i64>,
    pub more: i64,
    pub size: i64,
}

/// One page of a thread's raw transcript — where a report's citation lands.
pub fn records_page(page: &RecordsPage<'_>, citations: &[(String, Cited)], dev: bool) -> Markup {
    let thread = thread_url(page.session_id, page.source);
    let main = rsx! {
        <section id="records">
            <h1>"Raw records"</h1>
            <p class="numbers">
                <a href=(format!("/session/{}", page.session_id))>(page.session_id)</a>
                <span data-field="source">(page.source)</span>
                <span>
                    <span data-field="matched">(fmt::count(Some(page.matched)))</span>
                    " record(s) from here"
                </span>
            </p>
            <ol class="records">
                @for row in page.rows { (record(row, &thread, page.opened)) }
            </ol>
            @if let Some(after) = page.after {
                <p class="more" data-more-records=(after)>
                    <a href=(format!("{thread}/records?after={after}&size={}", page.size))>
                        <span data-field="count">"+"(fmt::count(Some(page.more)))" more"</span>
                    </a>
                </p>
            }
        </section>
    }
    .memoize();
    layout::page(
        &format!("{} records — hyphae", page.source),
        None,
        main,
        citation::footer(citations),
        dev,
    )
}

/// One record's row, and the fetch that brings the whole of it.
///
/// The whole record on first open, one request per record: a page of them whole is the one payload
/// nothing here bounds. One row is the exception — the one the route picked as `opened`, the record
/// a citation named — which arrives open and fetches itself as the page loads. Still a fetch and
/// not inlined: the page stays bounded, and what is unbounded stays one record at a time.
fn record(row: &RecordRow, thread: &str, opened: Option<i64>) -> Markup {
    let open = opened == Some(row.line_no);
    // The anchor is the line number, which is what a citation carries: `#L42` lands here.
    rsx! {
        <li id=(format!("L{}", row.line_no)) data-record=(row.line_no)>
            <span class="line">(row.line_no)</span>
            // Spaces, one per gap: the row is no flex line and only `.line` carries a margin
            // (`view/static/style.css`), so these are what hold the five values apart.
            " "
            <span class="type" data-field="type">(row.kind.as_str())</span>
            " "
            <span data-field="timestamp">(fmt::clock(row.timestamp))</span>
            " "
            <span><span data-field="raw_chars">(fmt::count(Some(row.raw_chars)))</span>" chars"</span>
            " "
            <code data-field="raw_head">(row.raw_head.as_str())</code>
            <details
                class="whole"
                open[open]
                data-open-record=(row.line_no)
                hx-get=(format!("/fragment/record{thread}/line/{}", row.line_no))
                hx-trigger=(if open { "load" } else { "toggle once" })
                hx-target="find .value"
            >
                <summary>"whole record"</summary>
                <div class="value"></div>
            </details>
        </li>
    }
    .memoize()
}

/// One offloaded tool result as its page prints it, built from its store row.
pub struct OffloadFile {
    pub name: String,
    pub size_bytes: i64,
    pub content_chars: i64,
    pub lossy_decode: bool,
    pub chunk: String,
}

/// One chunk of a tool result Claude Code wrote to a file beside the transcript.
///
/// `after` is where the next chunk starts, or `None` where this one reached the end.
pub fn offload_page(
    session_id: &str,
    file: &OffloadFile,
    after: Option<i64>,
    size: i64,
    citations: &[(String, Cited)],
    dev: bool,
) -> Markup {
    let main = rsx! {
        <section id="offload" data-offload=(file.name.as_str())>
            <h1 data-field="name">(file.name.as_str())</h1>
            <p class="numbers">
                <a href=(format!("/session/{session_id}"))>(session_id)</a>
                <span>
                    <span data-field="size_bytes">(fmt::count(Some(file.size_bytes)))</span>
                    " bytes on disk"
                </span>
                <span>
                    <span data-field="content_chars">(fmt::count(Some(file.content_chars)))</span>
                    " chars stored"
                </span>
                // Only when it happened: the extractor could not decode the file as text and
                // replaced what it could not read, so what is shown here is not what the tool wrote.
                @if file.lossy_decode {
                    <span data-field="lossy_decode">"some bytes did not decode as text"</span>
                }
            </p>
            <pre data-field="content">(file.chunk.as_str())</pre>
            @if let Some(after) = after {
                <p class="more" data-more-offload=(after)>
                    <a href=(format!(
                        "/session/{session_id}/offload/{}?after={after}&size={size}",
                        urls::quoted_path(&file.name),
                    ))>"next "(fmt::count(Some(size)))" chars"</a>
                </p>
            }
        </section>
    }
    .memoize();
    layout::page(
        &format!("{} — hyphae", file.name),
        None,
        main,
        citation::footer(citations),
        dev,
    )
}
