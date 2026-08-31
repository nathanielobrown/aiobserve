//! What a children log heads and fills: one table of columns per shape of log.
//!
//! Ported from `src/hyphae/view/columns.py`. A pane lists one kind of child at a time, and each
//! kind is read by different columns — what tells two turns apart is not what tells two tool
//! calls apart. This module is that table, plus the marks a column head and a node's own kind
//! both carry ([`crate::nodes::Kind::icon`]).

use std::fmt;

/// What the pane's children log lists, which decides the cells a row renders.
///
/// A function of the selection's kind rather than a choice: a turn's children are api calls
/// however the reader arrived at the turn, and a node with nothing under it has no log.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Shape {
    Turns,
    Calls,
    Tools,
    Runs,
    None,
}

impl Shape {
    /// Every shape there is, for a sweep that has to answer for each one.
    ///
    /// The runtime half of what the checker's exhaustiveness promises: a shape added here with no
    /// arm behind it lists no rows, and a sweep over a served corpus is what catches that.
    pub const ALL: [Self; 5] = [
        Self::Turns,
        Self::Calls,
        Self::Tools,
        Self::Runs,
        Self::None,
    ];

    /// The word the log's `data-` attributes and its heading spell this shape with.
    pub fn word(self) -> &'static str {
        match self {
            Self::Turns => "turns",
            Self::Calls => "calls",
            Self::Tools => "tools",
            Self::Runs => "runs",
            Self::None => "none",
        }
    }

    /// What this shape of log shows, column by column, in the order it shows them.
    pub fn columns(self) -> &'static [Column] {
        match self {
            Self::Turns => &TURNS,
            Self::Calls => &CALLS,
            Self::Tools => &TOOLS,
            Self::Runs => &RUNS,
            Self::None => &[],
        }
    }
}

impl fmt::Display for Shape {
    fn fmt(&self, into: &mut fmt::Formatter<'_>) -> fmt::Result {
        into.write_str(self.word())
    }
}

/// One column of the pane's children log: what it prints, and how it heads itself.
///
/// The word above the column is not here — it comes from [`crate::labels::label`], the registry
/// every header on every page reads, so a column and a pane's fact call the same store column
/// the same thing. What is here is the icon beside that word and the class the cell carries.
#[derive(Debug, Clone, Copy)]
pub struct Column {
    pub field: &'static str,
    pub icon: &'static str,
    /// `number` right-aligns and figures the digits, `when` keeps a time on one line, `what` is
    /// the one wide column a row is identified by and links from. Plain text otherwise.
    pub css: &'static str,
}

/// A column, written the way the Python table writes one.
const fn column(field: &'static str, icon: &'static str, css: &'static str) -> Column {
    Column { field, icon, css }
}

// The marks a column head and a node's own kind both carry. Written once, so the `⇄` over a
// turn's api-call count and the `⇄` on an api call's row in the NavTree cannot drift apart.
pub const CALL_ICON: &str = "⇄";
pub const TOOL_ICON: &str = "⚒";
pub const RUN_ICON: &str = "◎";
pub const ERROR_ICON: &str = "⚠";

// Every row fills every column of its shape — a log that skipped a cell where the store held
// nothing would slide every later value under the wrong heading. One column of each shape is
// `what`: the wide one carrying the node's own words and the link to its page. The last is the
// control that opens the child's body in place.
const TURNS: [Column; 7] = [
    column("turn_index", "#", "number"),
    column("title", "☰", "what"),
    column("api_calls", CALL_ICON, "number"),
    column("tool_calls", TOOL_ICON, "number"),
    column("cost_usd", "$", "number"),
    column("started_at", "◷", "when"),
    column("body", "⌄", ""),
];

const CALLS: [Column; 9] = [
    column("call_index", "#", "number"),
    // The row is named by the model that answered, with what it answered beside it: two lines
    // of the call's own words, which is what tells two calls of one model apart.
    column("model", "◈", "what"),
    column("text", "☰", "said"),
    column("tool_calls", TOOL_ICON, "number"),
    // What those tool calls were, named the way the log inside the call names them.
    column("tool_titles", "⌨", "called"),
    column("text_chars", "¶", "number"),
    column("cost_usd", "$", "number"),
    column("started_at", "◷", "when"),
    column("body", "⌄", ""),
];

const TOOLS: [Column; 7] = [
    column("tool_index", "#", "number"),
    column("name", TOOL_ICON, ""),
    column("title", "⌨", "what"),
    column("is_error", ERROR_ICON, ""),
    column("result_chars", "¶", "number"),
    column("started_at", "◷", "when"),
    column("body", "⌄", ""),
];

const RUNS: [Column; 6] = [
    column("agent_type", RUN_ICON, ""),
    column("title", "☰", "what"),
    column("tool_errors", ERROR_ICON, "number"),
    column("cost_usd", "$", "number"),
    column("started_at", "◷", "when"),
    column("body", "⌄", ""),
];
