//! How a tool call is named, wherever the viewer names one.
//!
//! Ported from `src/hyphae/view/formatters.py`. A tool call's title is read from the input field
//! that tells two of that tool's calls apart — a path for a file tool, the command for `Bash` —
//! under a glyph that stands for the tool, so a NavTree row says which tool ran without spending
//! the width on its name. The store extracts the fields (`analyze/macros.py:tool_fields`) and
//! this module composes the name out of them.
//!
//! [`name_tool`] is the entry point and [`crate::builders`] its only caller. A tool absent from
//! the table below is not a gap: it takes the shape rule, which names a tool nobody here has
//! heard of.

use hyphae_store::row::member;
use hyphae_store::{Row, Value};

/// What the store extracts from a tool call's input for the rules below, as one row holds it.
///
/// A borrowed `STRUCT` value rather than a map: every member is present on every row, NULL where
/// the call carried nothing under that name. Empty where the query shipped no such column, which
/// is Python's `fields or {}`.
#[derive(Debug, Clone, Copy, Default)]
pub struct Fields<'a>(Option<&'a Value>);

impl<'a> Fields<'a> {
    /// One `fields` struct as a nested value holds it — a list member's, say.
    pub fn of(held: Option<&'a Value>) -> Self {
        Self(held.filter(|held| !matches!(held, Value::Null)))
    }

    /// The `fields` struct of one row, or nothing where the query did not ship one.
    pub fn read(row: &'a Row, column: &str) -> Self {
        Self(
            row.value(column)
                .ok()
                .filter(|held| !matches!(held, Value::Null)),
        )
    }

    /// One member as the words it holds, or nothing where the struct left it out or NULL.
    ///
    /// Every member `tool_fields` composes is text but `todos`, which [`Fields::count`] reads
    /// instead — so a member of any other type is a macro that changed under us.
    fn named(&self, key: &str) -> Option<&'a str> {
        match self.0.and_then(|held| member(held, key))? {
            Value::Null => None,
            Value::Text(text) | Value::Enum(text) => Some(text),
            other => panic!("tool field `{key}` is not words: {other:?}"),
        }
    }

    /// One extracted field as words, whatever the query left NULL.
    pub fn text(&self, key: &str) -> &'a str {
        self.named(key).unwrap_or_default()
    }

    /// One member as the number it holds, or nothing where it holds anything else.
    fn count(&self, key: &str) -> Option<i64> {
        match self.0.and_then(|held| member(held, key))? {
            Value::TinyInt(number) => Some(i64::from(*number)),
            Value::SmallInt(number) => Some(i64::from(*number)),
            Value::Int(number) => Some(i64::from(*number)),
            Value::BigInt(number) => Some(*number),
            _ => None,
        }
    }
}

/// A tool call named by its own tool: the glyph that stands for the tool, and the words.
///
/// An empty `mark` is the shape rule below — no glyph stands for the tool, so the caller leads
/// the row with the tool's name instead (`crate::builders`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Formatted {
    pub mark: &'static str,
    pub words: String,
}

/// What one tool call is called: its tool's own rule, else the shape of its input.
pub fn name_tool(name: &str, fields: Fields<'_>) -> Formatted {
    formatted(name, fields).unwrap_or_else(|| Formatted {
        mark: "",
        words: shaped(fields),
    })
}

/// What each tool the viewer knows names its calls by (`plans/viewer-polish/design.md`).
///
/// A tool absent here is not a gap: its calls take the shape rule, which names any input at all
/// and is what a registry keyed by name cannot do. So this holds the tools whose input we have
/// read enough of to beat that default.
fn formatted(name: &str, fields: Fields<'_>) -> Option<Formatted> {
    match name {
        "Read" => one("📖", fields.text("path")),
        "Write" => one("✏️", fields.text("path")),
        "Edit" => one("📝", fields.text("path")),
        "Bash" => bash(fields),
        "Agent" => agent(fields),
        "Skill" => skill(fields),
        "SendMessage" => send_message(fields),
        "Grep" => one("🔎", fields.text("pattern")),
        "Glob" => one("🗂", fields.text("pattern")),
        "WebFetch" => one("🌐", fields.text("url")),
        "WebSearch" => one("🔍", fields.text("query")),
        // The two names read off session `4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b` (Claude Code
        // 2.1.221): a tool search carries `query`, a notification `message`.
        "ToolSearch" => one("🧰", fields.text("query")),
        "PushNotification" => one("🔔", fields.text("message")),
        "TodoWrite" => todo_write(fields),
        _ => None,
    }
}

/// The common rule: a glyph for the tool, and the one field the design names for it.
fn one(mark: &'static str, words: &str) -> Option<Formatted> {
    (!words.is_empty()).then(|| Formatted {
        mark,
        words: words.to_owned(),
    })
}

/// What ran, not what it was called: `description` is the agent's summary of itself.
fn bash(fields: Fields<'_>) -> Option<Formatted> {
    // And the first line of it. A heredoc or a chained pipeline is a screenful, and the row that
    // has to hold it is one line — so the cut is at the newline rather than at a width.
    let command = fields.text("command");
    one("⚡", command.split('\n').next().unwrap_or_default())
}

/// The type the run was spawned as, then the brief: a tree of runs reads as a column.
fn agent(fields: Fields<'_>) -> Option<Formatted> {
    let kind = fields.text("subagent_type");
    let said = fields.text("description");
    let words = if kind.is_empty() {
        said.to_owned()
    } else {
        format!("[{kind}] {said}").trim().to_owned()
    };
    one("👉", &words)
}

/// The skill invoked, and what it was invoked with where the caller passed anything.
fn skill(fields: Fields<'_>) -> Option<Formatted> {
    let skill = fields.text("skill");
    if skill.is_empty() {
        return None;
    }
    let args = fields.text("args");
    let words = format!("{skill} {args}");
    one("📕", words.trim())
}

/// Who it went to and what it said.
///
/// `to` holds either an agent run's id or a name the caller typed. The query resolves the id
/// against the session's runs and leaves `addressed` NULL where nothing matched — one lookup and
/// one fallback, because a name that resolves to nothing is already fit to print.
fn send_message(fields: Fields<'_>) -> Option<Formatted> {
    let addressed = fields.text("addressed");
    let who = if addressed.is_empty() {
        fields.text("to")
    } else {
        addressed
    };
    if who.is_empty() {
        return None;
    }
    let summary = fields.text("summary");
    let words = if summary.is_empty() {
        format!("to {who}")
    } else {
        format!("to {who}: {summary}")
    };
    one("📬", &words)
}

/// How many items the list holds. The items are the model's plan; the first one alone says less
/// about the call than the count does.
fn todo_write(fields: Fields<'_>) -> Option<Formatted> {
    let count = fields.count("todos")?;
    let plural = if count == 1 { "" } else { "s" };
    Some(Formatted {
        mark: "☑️",
        words: format!("{count} todo{plural}"),
    })
}

/// What a call the registry has no rule for is named by, in the order the arms are tried: the
/// fields that say what a call was whichever tool made it. `input_head` is the head of the input
/// as the store holds it — JSON for every tool we have seen — so the last arm names a call whose
/// input carried none of the names above it.
const SHAPE: [&str; 3] = ["path", "description", "input_head"];

/// The shape-driven name: the first of [`SHAPE`] the record answers.
///
/// A field the record left out falls through; one it carried empty does not. That is the
/// `coalesce` this was ported from — a caller who sent an empty description described the call as
/// nothing, and printing its raw input instead would be the viewer overruling it.
fn shaped(fields: Fields<'_>) -> String {
    for key in SHAPE {
        if let Some(value) = fields.named(key) {
            return value.to_owned();
        }
    }
    String::new()
}
