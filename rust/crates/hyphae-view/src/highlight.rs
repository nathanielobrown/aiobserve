//! Code as a page shows it: a value's own syntax, and how much of the value there is.
//!
//! Ported from `src/hyphae/view/highlight.py`, with one thing left behind. Python marks a value
//! up with Pygments, whose classes `static/pygments.css` paints. Nothing in Rust writes those
//! classes: `syntect` derives its own from TextMate scopes, so painting them needs a second
//! stylesheet beside the one both viewers share. So this prototype prints every value as it was
//! stored, which is the arm Python already takes past its ceiling and for a value that does not
//! parse — the markup is byte-identical there and misses the spans everywhere else. `render.rs`
//! made the same call for a fenced block in stage 3a.
//!
//! What is here is the rest of the module, which is what a page's shape depends on: which syntax
//! a file's name or a fence claims, and the JSON re-layout that makes a tool's arguments
//! readable.

use std::fmt;

use crate::knobs::{HIGHLIGHT_CHARS, INDENT_CHARS};
use crate::render::escape;

/// The syntaxes the viewer names, which is also what a component may ask for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Syntax {
    Json,
    Sql,
    Bash,
    Markdown,
    Python,
}

impl Syntax {
    /// The word the class on a `<pre>` and a fence's info string spell this syntax with.
    pub fn word(self) -> &'static str {
        match self {
            Self::Json => "json",
            Self::Sql => "sql",
            Self::Bash => "bash",
            Self::Markdown => "markdown",
            Self::Python => "python",
        }
    }
}

impl fmt::Display for Syntax {
    fn fmt(&self, into: &mut fmt::Formatter<'_>) -> fmt::Result {
        into.write_str(self.word())
    }
}

/// What a file's name says its contents are. Only the suffixes this viewer has a syntax for: a
/// `Read` result carries no type of its own, so the path the call asked for is the only evidence
/// of what came back, and anything this list misses is shown as it was stored.
const SUFFIXES: &[(&str, Syntax)] = &[
    (".md", Syntax::Markdown),
    (".markdown", Syntax::Markdown),
    (".py", Syntax::Python),
    (".sql", Syntax::Sql),
    (".json", Syntax::Json),
    (".sh", Syntax::Bash),
    (".bash", Syntax::Bash),
    (".zsh", Syntax::Bash),
];

/// What a model writes after the three backticks of a fence, for the syntaxes this viewer reads:
/// each syntax's own word, plus the short spellings a model actually types.
const FENCED: &[(&str, Syntax)] = &[
    ("json", Syntax::Json),
    ("sql", Syntax::Sql),
    ("bash", Syntax::Bash),
    ("markdown", Syntax::Markdown),
    ("python", Syntax::Python),
    ("py", Syntax::Python),
    ("sh", Syntax::Bash),
    ("shell", Syntax::Bash),
    ("zsh", Syntax::Bash),
    ("md", Syntax::Markdown),
];

/// How far JSON is indented before it stops being readable and starts being a scroll.
const INDENT: usize = 2;

/// One value as a page prints it: the markup, and why it is plain where it is plain.
pub struct Lit {
    /// Already escaped, and safe to write into a `<pre>` as it stands.
    pub html: String,
    /// The syntax the value is classed by, or `None` where it is printed as it was stored.
    pub syntax: Option<Syntax>,
    /// How long the value is where its length is the reason it was not marked up, else 0. What
    /// the page says instead of highlighting it, so a reader knows the plainness is deliberate.
    pub over: i64,
}

/// The syntax a read file's name implies, or `None` where the viewer shows it as stored.
///
/// Takes the suffix rather than the path because the queries that ask this extract one: a header
/// query cuts every column it returns, and a path cut to a pane's width would lose the end that
/// names it. Case is folded here so the store keeps what the session wrote.
pub fn by_suffix(suffix: Option<&str>) -> Option<Syntax> {
    let named = suffix?.to_lowercase();
    found(SUFFIXES, &named)
}

/// The syntax a fenced block claims, or `None` where this viewer has no name for it.
///
/// The info string is what a model typed above its code, so it is a claim rather than a fact: an
/// unknown one prints the block as it was written. Only the first word is read — a fence can carry
/// a filename or attributes after the language.
pub fn by_fence(info: Option<&str>) -> Option<Syntax> {
    let said = info.unwrap_or_default().trim();
    let named = said.split(' ').next().unwrap_or_default().to_lowercase();
    found(FENCED, &named)
}

fn found(table: &[(&str, Syntax)], named: &str) -> Option<Syntax> {
    table
        .iter()
        .find(|(word, _)| *word == named)
        .map(|(_, syntax)| *syntax)
}

/// One value ready for a `<pre>`: escaped, and re-laid-out where it is JSON.
///
/// Past [`HIGHLIGHT_CHARS`] the value says how long it is, so a reader knows the plainness is
/// deliberate rather than a value that happens to look unmarked.
pub fn lit(value: Option<&str>, syntax: Syntax) -> Lit {
    let Some(value) = value.filter(|held| !held.is_empty()) else {
        return Lit {
            html: String::new(),
            syntax: None,
            over: 0,
        };
    };
    let (text, known) = if syntax == Syntax::Json {
        readable(value)
    } else {
        (value.to_owned(), true)
    };
    let over = if known && text.chars().count() > HIGHLIGHT_CHARS {
        text.chars().count() as i64
    } else {
        0
    };
    Lit {
        html: escape(&text),
        syntax: None,
        over,
    }
}

/// A stored JSON value indented for reading, and whether it was JSON at all.
///
/// Tool arguments and raw records are JSON *most* of the time. A value that does not parse is
/// shown as it was stored rather than hidden: what it holds is the reason someone opened the
/// fragment, and prose lexed as JSON is every other word marked as an error.
///
/// A value nested deeply enough that indenting it would explode is shown as stored too, so what a
/// fragment serves stays proportional to what the store holds. [`INDENT_CHARS`] sets the line.
fn readable(value: &str) -> (String, bool) {
    let Ok(parsed) = serde_json::from_str::<serde_json::Value>(value) else {
        return (value.to_owned(), false);
    };
    if !indent_fits(&parsed) {
        return (value.to_owned(), true);
    }
    let mut written = Vec::new();
    let indent = vec![b' '; INDENT];
    let mut printer = serde_json::Serializer::with_formatter(
        &mut written,
        serde_json::ser::PrettyFormatter::with_indent(&indent),
    );
    serde::Serialize::serialize(&parsed, &mut printer).expect("a parsed value re-serializes");
    (
        String::from_utf8(written).expect("serde_json writes UTF-8"),
        true,
    )
}

/// Whether indenting a parsed value would add less than [`INDENT_CHARS`].
///
/// Counts what pretty-printing adds — a newline and one level of padding per member, plus a line
/// for each closing bracket — and stops at the budget, so measuring a hostile value costs no more
/// than the budget. The walk carries its own stack because the nesting that makes indenting
/// expensive is the nesting that would overflow a recursive one.
fn indent_fits(parsed: &serde_json::Value) -> bool {
    let mut added = 0usize;
    let mut stack = vec![(parsed, 0usize)];
    while let Some((item, depth)) = stack.pop() {
        let children: Vec<&serde_json::Value> = match item {
            serde_json::Value::Object(held) => held.values().collect(),
            serde_json::Value::Array(held) => held.iter().collect(),
            _ => continue,
        };
        if children.is_empty() {
            continue;
        }
        added += children.len() * (1 + (depth + 1) * INDENT) + 1 + depth * INDENT;
        if added >= INDENT_CHARS {
            return false;
        }
        stack.extend(children.into_iter().map(|child| (child, depth + 1)));
    }
    true
}
