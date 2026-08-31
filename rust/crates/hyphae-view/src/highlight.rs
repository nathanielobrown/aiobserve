//! Code as a page shows it: a value's own syntax, marked by class rather than by color.
//!
//! A syntax is here because a session writes it: the JSON a tool was passed and returned, the SQL
//! behind a page, the shell a `Bash` call ran, the markdown a `Read` returned, and the languages a
//! model fences a block of code in. Everything else a transcript wrote is prose, and `render.rs`
//! renders it — marking up a file the viewer shows is a reading aid over the source, never a
//! rendering of it: a tool result is evidence, and it prints as it was stored, character for
//! character.
//!
//! Classes rather than inline colors because the policy in `app::CSP` allows no `style`
//! attribute; `static/pygments.css` is where they are painted, and both viewers share that sheet.
//!
//! **The port's one deviation.** Python tokenizes with Pygments, whose short class names the sheet
//! is written around. This crate tokenizes with `syntect`, which hands back TextMate scopes
//! instead — so [`SCOPES`] maps those scopes onto the sheet's vocabulary and this module writes
//! the spans itself. The class on a run of characters is the same claim about that run; the token
//! boundaries either side of it are the tokenizer's, and are not the Python's byte for byte.
//! `tests/highlight.rs` says which leaves that adapts and `render.rs`'s generated cases carry the
//! exemption where a fenced block reaches here.

use std::fmt;
use std::sync::LazyLock;

use syntect::parsing::{ParseState, Scope, ScopeStack, SyntaxSet};

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

    /// The name syntect's bundled definitions file this syntax under.
    ///
    /// Its own spelling rather than the viewer's: the set is Sublime's default packages, where
    /// bash is "Bourne Again Shell (bash)" and nothing answers to "bash".
    fn definition(self) -> &'static str {
        match self {
            Self::Json => "JSON",
            Self::Sql => "SQL",
            Self::Bash => "Bourne Again Shell (bash)",
            Self::Markdown => "Markdown",
            Self::Python => "Python",
        }
    }
}

/// Every class this module can write, which is the image of [`SCOPES`] and nothing wider.
///
/// A page's byte budget prices a class at three characters
/// (`tests/view/budgets.py:MARKED_CHAR_BYTES`), so the width of a class is a property of this
/// viewer rather than of whatever a tokenizer was asked to read. Pygments earns that by walking an
/// unnamed token type up to a named one; here it falls out of the table being the only source of a
/// class at all.
pub const PAINTED: &[&str] = &[
    "c1", "ge", "gh", "gs", "gu", "k", "kc", "kn", "m", "mf", "mi", "nb", "nt", "nv", "o", "ow",
    "p", "s", "s1", "s2", "sa", "sb", "sd", "se", "si", "ss",
];

/// TextMate scopes as the classes `static/pygments.css` paints.
///
/// A row's left side is a dotted *prefix* of a scope. Every scope on a token's stack is tried and
/// the row matching the most atoms wins, ties going to the innermost scope — which is TextMate's
/// own selector rule, and the reason a row can be made more specific without disturbing its
/// neighbours. A token no row matches is written bare, which is where a SQL name belongs and where
/// the sheet's comment already puts it.
///
/// Most rows read straight across. The ones that do not carry their reason.
pub const SCOPES: &[(&str, &str)] = &[
    // A JSON object key is a string to syntect and a *tag* to Pygments — the field name a reader
    // scans down the left edge, which the sheet is the only place that says. The row is longer
    // than `string.quoted.double` so it wins over it on the same token.
    ("meta.structure.dictionary.key", "nt"),
    // The same field-name role inside a delegated block: yaml and xml both scope it here.
    ("entity.name.tag", "nt"),
    ("string.quoted.double", "s2"),
    ("string.quoted.single", "s1"),
    ("string.quoted.backtick", "sb"),
    // Markdown's backticked span. Pygments spells a run of inline code `sb` too, so the sheet
    // already paints prose's code and a shell's backticks the same way.
    ("markup.raw.inline", "sb"),
    // A fenced block, whole. Pygments hands one to the lexer its info string names and falls back
    // to a plain String when no lexer answers to it; syntect's Markdown embeds nothing, so every
    // fence takes that fallback and the fence line goes with the block it opens.
    ("markup.raw.code-fence", "s"),
    ("string", "s"),
    ("constant.character.escape", "se"),
    // The `{…}` inside an f-string, and the `f` in front of it: Pygments' String.Interpol and
    // String.Affix, which are the two pieces of a formatted string that are not the string.
    ("meta.interpolation", "si"),
    ("storage.type.string", "sa"),
    // A quoted identifier — SQL's `"column"`, a Ruby-style symbol. Pygments' String.Symbol.
    ("constant.other.symbol", "ss"),
    ("entity.name.constant", "ss"),
    // SQL is the language where double quotes name a thing rather than quote a value, and
    // Pygments spells that difference the same way. The row is longer than the string one above,
    // so it takes the token back for SQL alone.
    ("string.quoted.double.sql", "ss"),
    ("constant.numeric.integer", "mi"),
    ("constant.numeric.float", "mf"),
    ("constant.numeric", "m"),
    // `true`, `false`, `null`, `None`: a keyword that is a value. Pygments' Keyword.Constant.
    ("constant.language", "kc"),
    ("variable.other", "nv"),
    // An `import` line is Pygments' Keyword.Namespace, which the sheet paints like any keyword.
    ("keyword.control.import", "kn"),
    // Python's `and` / `or` / `not` / `in` are words, and Pygments classes them Operator.Word. The
    // row names the language because a shell's `&&` scopes as a logical operator too and is
    // punctuation to read, not a word.
    ("keyword.operator.logical.python", "ow"),
    ("keyword.operator", "o"),
    ("keyword", "k"),
    // What a language ships rather than what a statement says: SQL's aggregates, a shell's `cd`
    // and `export`, Python's `int` and `str`. Pygments' Name.Builtin.
    ("support.function", "nb"),
    ("support.type", "nb"),
    ("storage.modifier", "nb"),
    // `def`, `class`, `lambda`: syntect files them under storage, Pygments under Keyword.
    ("storage.type", "k"),
    ("markup.heading.1", "gh"),
    ("markup.heading", "gu"),
    ("markup.bold", "gs"),
    ("markup.italic", "ge"),
    // A bullet is the structure of a list rather than a word in it, and Pygments marks it with the
    // keyword class. The row stops at `.bullet` so an item's own text stays unclassed.
    ("markup.list.unnumbered.bullet", "k"),
    ("markup.list.numbered.bullet", "k"),
    // A docstring is a string to Pygments (String.Doc) and a comment to syntect. `sd` follows
    // Pygments; the sheet paints neither, so a docstring reads in the body color either way.
    ("comment.block.documentation", "sd"),
    ("comment", "c1"),
    // Only the punctuation that stands *between* things. `punctuation.definition` is deliberately
    // absent: it is the scope on a string's own quotes, a heading's `#` and a comment's `--`, and
    // leaving it out is what lets those characters fall through to the construct they open.
    ("punctuation.section", "p"),
    ("punctuation.separator", "p"),
    ("punctuation.terminator", "p"),
    ("punctuation.accessor", "p"),
];

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

/// One value ready for a `<pre>`: marked up in `syntax`, or printed as it was stored.
///
/// Past [`HIGHLIGHT_CHARS`] the value comes back plain and says how long it is. The ceiling is
/// characters rather than bytes on purpose: it guards the tokenizer's time and the markup's
/// inflation — a span per token multiplies a value about fourfold — and neither of those is
/// counted in bytes. A multibyte value under the ceiling is still marked up.
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
    if !known {
        return Lit {
            html: escape(&text),
            syntax: None,
            over: 0,
        };
    }
    let length = text.chars().count();
    if length > HIGHLIGHT_CHARS {
        return Lit {
            html: escape(&text),
            syntax: None,
            over: length as i64,
        };
    }
    Lit {
        html: marked(&text, syntax),
        syntax: Some(syntax),
        over: 0,
    }
}

/// The bundled syntax definitions, unpacked once for the life of the process.
fn syntaxes() -> &'static SyntaxSet {
    static SET: LazyLock<SyntaxSet> = LazyLock::new(SyntaxSet::load_defaults_newlines);
    &SET
}

/// [`SCOPES`] with each prefix parsed once, beside how many atoms it is long.
fn rows() -> &'static [(Scope, usize, &'static str)] {
    static ROWS: LazyLock<Vec<(Scope, usize, &'static str)>> = LazyLock::new(|| {
        SCOPES
            .iter()
            .map(|(prefix, class)| {
                let scope = Scope::new(prefix).expect("every row names a scope syntect can hold");
                (scope, prefix.split('.').count(), *class)
            })
            .collect()
    });
    &ROWS
}

/// The class one token earns, or `None` where it is written out bare.
///
/// There is no row for whitespace, which is deliberate: Pygments wraps every run of it in a
/// `<span class="w">` the sheet paints nothing, and on the widest query this repo ships that is
/// 10 KB of markup in 35 KB of output. Here the space *between* constructs sits at a syntax's root
/// scope, which no row matches, so it is written bare — while the space *inside* one is part of
/// the construct and keeps its class, as it is in Pygments' markup too.
fn class(stack: &[Scope]) -> Option<&'static str> {
    let mut best: Option<(usize, &'static str)> = None;
    for scope in stack {
        for (prefix, width, class) in rows() {
            // The innermost scope wins a tie, which is why the comparison is not strict: the
            // stack is walked outermost first.
            if prefix.is_prefix_of(*scope) && best.is_none_or(|(held, _)| *width >= held) {
                best = Some((*width, class));
            }
        }
    }
    best.map(|(_, class)| class)
}

/// The line-number gutter Claude Code writes down the left of a file it read — `12\t`, one per
/// line — as the bytes it occupies, or `None` where the line carries none.
///
/// It is not part of the file: a tokenizer that meets it reads a different language, where a
/// heading whose `#` follows a number is no longer a heading.
fn gutter(line: &str) -> Option<usize> {
    let spaced = line.len() - line.trim_start_matches([' ', '\t']).len();
    let digits = line[spaced..]
        .bytes()
        .take_while(u8::is_ascii_digit)
        .count();
    (digits > 0 && line.as_bytes().get(spaced + digits) == Some(&b'\t'))
        .then_some(spaced + digits + 1)
}

/// A value marked up: in one pass, or a line at a time behind a `Read` result's gutter.
///
/// Reading line by line is what peeling the gutter costs — a tokenizer reading one line forgets
/// what the line before it opened — so it is done only for a value whose first line is numbered,
/// and the numbers are classed as the gutter they are. They hold digits and a tab by construction,
/// so there is nothing in them to escape.
fn marked(text: &str, syntax: Syntax) -> String {
    let mut lines = text.split_inclusive('\n');
    let Some(first) = lines.next() else {
        return String::new();
    };
    if gutter(first).is_none() {
        return run(text, syntax);
    }
    let mut written = String::with_capacity(text.len() * 2);
    for line in text.split_inclusive('\n') {
        // What is left once the gutter is its own span: the whole line when there is none.
        let cut = gutter(line).unwrap_or(0);
        if cut > 0 {
            written.push_str(r#"<span class="lineno">"#);
            written.push_str(&line[..cut]);
            written.push_str("</span>");
        }
        written.push_str(&run(&line[cut..], syntax));
    }
    written
}

/// The class one chunk of a line wears.
///
/// A line ending wears none, so no span crosses a line. Pygments' formatter closes every open span
/// at a newline and this keeps the two markups the same shape — a construct that runs to the end of
/// its line, a heading above all, would otherwise wrap a newline it is not part of.
fn worn(stack: &[Scope], chunk: &str) -> Option<&'static str> {
    if chunk.chars().all(|held| held == '\n' || held == '\r') {
        return None;
    }
    class(stack)
}

/// One stretch of text through the tokenizer, ending where the stretch ended.
///
/// Adjacent tokens of one class are written as one span. Pygments' formatter does the same inside
/// a token and not across them, and a run of characters that share a class is one claim about
/// those characters however many pieces the tokenizer cut them into.
fn run(text: &str, syntax: Syntax) -> String {
    if text.is_empty() {
        return String::new();
    }
    let set = syntaxes();
    let definition = set
        .find_syntax_by_name(syntax.definition())
        .expect("syntect's default set holds every syntax this viewer names");
    let mut state = ParseState::new(definition);
    let mut stack = ScopeStack::new();
    let mut pieces: Vec<(Option<&'static str>, &str)> = Vec::new();
    for line in text.split_inclusive('\n') {
        let ops = state
            .parse_line(line, set)
            .expect("syntect reads a line of the syntax it was given");
        let mut at = 0usize;
        for (index, operation) in ops {
            if index > at {
                pieces.push((worn(stack.as_slice(), &line[at..index]), &line[at..index]));
            }
            at = index;
            stack
                .apply(&operation)
                .expect("syntect's own scope operations balance");
        }
        if at < line.len() {
            pieces.push((worn(stack.as_slice(), &line[at..]), &line[at..]));
        }
    }
    let mut written = String::with_capacity(text.len() * 2);
    let mut open: Option<&'static str> = None;
    for (wearing, chunk) in pieces {
        if wearing != open {
            if open.is_some() {
                written.push_str("</span>");
            }
            if let Some(name) = wearing {
                written.push_str(r#"<span class=""#);
                written.push_str(name);
                written.push_str(r#"">"#);
            }
            open = wearing;
        }
        written.push_str(&escape(chunk));
    }
    if open.is_some() {
        written.push_str("</span>");
    }
    written
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
