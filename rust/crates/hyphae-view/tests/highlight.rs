//! What `view::highlight` hands a component: marked-up code, and plain text where it must be.
//!
//! Ported from `tests/view/test_highlight.py`. The unit rather than a page, because two of the
//! three arms are unreachable through the fixture corpus: no recorded value nests deep enough to
//! blow the indent budget, and none is a quarter of a million characters long. Both are invented
//! here and labelled as such.
//!
//! **The adaptation, in one place.** Python paints with Pygments; this crate paints with syntect,
//! whose tokenizer draws different boundaries and whose scopes are TextMate's rather than
//! Pygments'. So the leaves below assert the *contract* — that the class on a span is one
//! `static/pygments.css` paints, on the run of characters that deserves it — rather than
//! Pygments' exact token stream. Byte parity with the Python markup is a non-goal, and
//! `render.rs`'s generated cases say so where a fenced block reaches this module.
//!
//! Escaping is the load-bearing part, and it is not adapted. A tool's arguments are a string a
//! model wrote, so a `<img onerror=…>` inside one has to arrive at the browser as text whichever
//! arm rendered it.

use hyphae_store::queries;
use hyphae_testsupport::html::{classed, plain};
use hyphae_view::highlight::{PAINTED, Syntax, by_suffix, lit};
use hyphae_view::knobs::HIGHLIGHT_CHARS;

/// One tool argument in the shape a recorded one has — a path and a pattern — with markup put
/// inside it. Invented: redaction flattens the recorded strings, so no fixture carries a `<`.
const HOSTILE: &str = r#"{"pattern": "</script><img src=x onerror=y>", "path": "/tmp/a.py"}"#;

#[test]
fn a_value_is_marked_up_by_class_and_never_by_style() {
    // JSON comes back as classed spans, which is what the CSP leaves room for. An inline `style`
    // attribute would be blocked by `app::CSP` and the value would render unstyled; a class is
    // painted by `static/pygments.css`, which is served from this app.
    let shown = lit(Some(HOSTILE), Syntax::Json);
    assert_eq!(shown.syntax, Some(Syntax::Json));
    assert_eq!(shown.over, 0);
    // The key and the string each carry a class of their own — the key's is `nt` rather than the
    // string class syntect scopes it as, which is the mapping table's first non-obvious row.
    assert!(
        shown.html.contains(r#"<span class="nt">"#),
        "{}",
        shown.html
    );
    assert!(
        shown.html.contains(r#"<span class="s2">"#),
        "{}",
        shown.html
    );
    // ...and nothing carries a color the policy would refuse.
    assert!(!shown.html.contains("style="), "{}", shown.html);
}

#[test]
fn the_markup_inside_a_value_arrives_as_text() {
    // A tool argument holding markup is readable on the page and inert in it.
    let shown = lit(Some(HOSTILE), Syntax::Json);
    assert!(
        shown
            .html
            .contains("&lt;/script&gt;&lt;img src=x onerror=y&gt;"),
        "{}",
        shown.html
    );
    assert!(!shown.html.contains("<img"), "{}", shown.html);
    assert!(!shown.html.contains("</script>"), "{}", shown.html);
    // Indented for reading, which is what makes a recorded argument scannable at all.
    assert!(shown.html.contains("\n  "), "{}", shown.html);
}

#[test]
fn a_value_that_is_not_json_is_shown_as_it_was_stored() {
    // Not every stored value parses — a tool's plain-text output is shown, not swallowed. Marked
    // up as JSON it would be a line of error tokens, so the arm is plain and escaped.
    let shown = lit(
        Some("Traceback: <module> failed\n  at line 3"),
        Syntax::Json,
    );
    assert_eq!(shown.syntax, None);
    assert_eq!(shown.over, 0);
    assert!(shown.html.contains("&lt;module&gt; failed"));
    assert!(shown.html.contains("at line 3"));
}

#[test]
fn a_deeply_nested_value_is_shown_at_the_size_it_was_stored() {
    // A value nested past what anyone reads costs its own length to serve, not more. Indenting is
    // quadratic in nesting, so these invented values — and they have to be invented; nothing
    // recorded nests near this deep — are the whole risk in one line.
    //
    // **Adapted depths.** Python draws its two arms at 5,000 and 10,000 because `json.loads`
    // reaches that far; `serde_json` refuses at a nesting of 128, which is the line this parser
    // draws and so the line the leaf pins. 120 parses and would indent past `INDENT_CHARS`; 200
    // is what the parser itself refuses.
    for depth in [120usize, 200] {
        let value = "[".repeat(depth) + &"]".repeat(depth);
        let shown = lit(Some(&value), Syntax::Json);
        // Nothing was added — no newline, no indentation, and every character still there.
        assert!(!shown.html.contains('\n'), "{depth}");
        assert_eq!(plain(&shown.html), value, "{depth}");
    }
    // Unindented is not unmarked: a value that parses is still JSON, and the page still classes
    // it. Only the value the parser itself refused comes back as plain text.
    let parses = "[".repeat(120) + &"]".repeat(120);
    let refused = "[".repeat(200) + &"]".repeat(200);
    assert_eq!(lit(Some(&parses), Syntax::Json).syntax, Some(Syntax::Json));
    assert_eq!(lit(Some(&refused), Syntax::Json).syntax, None);
    // Nothing beside the nesting excuses it. The walk is a stack, so the *last* member is the
    // first thing it reaches — and whether that member is an empty container or a number, the
    // measuring has to step over it and carry on to the deep one behind it.
    let deep = "[".repeat(119) + &"]".repeat(119);
    for neighbour in ["[]", "1"] {
        let beside = format!(r#"{{"deep": {deep}, "beside": {neighbour}}}"#);
        assert!(
            !lit(Some(&beside), Syntax::Json).html.contains('\n'),
            "{neighbour}"
        );
    }
    // ...while a value that nests as deep as a real record does is still indented.
    assert!(
        lit(Some(r#"{"a": {"b": {"c": [1, 2]}}}"#), Syntax::Json)
            .html
            .contains("\n    ")
    );
}

#[test]
fn whitespace_is_written_bare_rather_than_wrapped_in_a_span_of_its_own() {
    // The one thing this viewer changes about a tokenizer's markup, and it is worth 10 KB.
    // Pygments classes every run of whitespace `w` and syntect hands the space inside a construct
    // out under that construct's own scope; `static/pygments.css` paints neither. On the widest
    // query the repo ships that is 10 KB of markup for nothing, so a token that is nothing but
    // whitespace is written out bare. Read on SQL, which is indented enough for the spans to be
    // most of it.
    let shown = lit(Some(queries::load("view_sessions")), Syntax::Sql);
    assert!(
        shown.html.contains(r#"<span class="k">"#),
        "the tokens that are painted are still classed"
    );
    assert!(!shown.html.contains(r#"class="w""#));
    // And the whitespace is still there — written, not dropped.
    assert!(plain(&shown.html).contains("\n"));
}

#[test]
fn every_class_the_markup_carries_is_one_the_shared_stylesheet_knows() {
    // How wide a class can be is a term in the page's byte budget, so the viewer sets it, and
    // `tests/view/budgets.py:MARKED_CHAR_BYTES` prices a class at three characters.
    //
    // **Adapted.** Python's claim is about a Pygments hook: left alone the formatter walks an
    // unnamed token type up to a named one and joins a class per step (`l l-Scalar l-Scalar-Plain`),
    // which a delegated fenced block makes reachable. Rust has no such walk — every class comes
    // out of `highlight::PAINTED`, a table this viewer writes — so the leaf pins the property the
    // hook existed to give: the image of the table is short, and nothing outside it reaches a page.
    for name in PAINTED {
        assert!(name.len() <= 3, "{name} is wider than the budget prices");
    }
    // A fence inside markdown is the input that reached past Pygments' vocabulary, so it is still
    // the input here: whatever syntect makes of it, the classes are the table's and the
    // characters are the value's.
    let fenced = lit(Some("```yaml\na: {b: c, d: e}\n```\n"), Syntax::Markdown);
    assert_eq!(fenced.syntax, Some(Syntax::Markdown));
    assert_eq!(plain(&fenced.html), "```yaml\na: {b: c, d: e}\n```\n");
    for name in classed(&fenced.html) {
        assert!(PAINTED.contains(&name.as_str()), "{name} is off the table");
    }
}

#[test]
fn a_value_past_the_ceiling_is_printed_as_stored_and_says_how_long_it_is() {
    // The ceiling is a line, not a slope: one character over and the markup stops. A JSON string
    // is the value either side, because indenting one changes nothing about its length — so the
    // two cases differ in the one character the ceiling is about. Invented: the largest recorded
    // tool result is far shorter than a quarter of a million characters.
    let at = format!("\"{}\"", "x".repeat(HIGHLIGHT_CHARS - 2));
    assert_eq!(at.chars().count(), HIGHLIGHT_CHARS);
    assert_eq!(lit(Some(&at), Syntax::Json).syntax, Some(Syntax::Json));
    let over = format!("\"{}\"", "x".repeat(HIGHLIGHT_CHARS - 1));
    let shown = lit(Some(&over), Syntax::Json);
    assert_eq!(shown.syntax, None);
    // It says its own length rather than that it was cut: the whole value is still served.
    assert_eq!(shown.over, over.chars().count() as i64);
    assert!(shown.html.len() >= over.len());
}

#[test]
fn the_ceiling_counts_characters_rather_than_bytes() {
    // A multibyte value under the ceiling is marked up though its bytes run past it. The
    // deliberate deviation (`plans/viewer-node-browser/design.md`): what the ceiling guards is the
    // tokenizer's work and the markup a span per token adds, and both follow the tokens. Invented,
    // for the same reason as the leaf above.
    let value = format!("\"{}\"", "é".repeat(HIGHLIGHT_CHARS - 2));
    assert_eq!(value.chars().count(), HIGHLIGHT_CHARS);
    assert!(value.len() > HIGHLIGHT_CHARS);
    assert_eq!(lit(Some(&value), Syntax::Json).syntax, Some(Syntax::Json));
}

#[test]
fn an_absent_value_renders_to_nothing() {
    // A NULL column reaches the component as None, and an empty block beats a crash.
    for shown in [lit(None, Syntax::Json), lit(Some(""), Syntax::Sql)] {
        assert_eq!(shown.html, "");
        assert_eq!(shown.syntax, None);
        assert_eq!(shown.over, 0);
    }
}

#[test]
fn sql_is_marked_up_whole_and_loses_nothing() {
    // A query file comes back as the same characters, spans and all — SQL is not reformatted. The
    // value the `/query` page serves is a file this repo ships, so the strong check is available
    // here and nowhere else: every character of it survives the markup.
    let sql = queries::load("view_sessions");
    let shown = lit(Some(sql), Syntax::Sql);
    assert_eq!(shown.syntax, Some(Syntax::Sql));
    assert_eq!(plain(&shown.html), sql);
}

#[test]
fn a_shell_command_is_marked_up_as_a_shell_reads_it() {
    // What a `Bash` call ran is code, and the densest line a tool call holds. Real: a command this
    // repo's own tasks run, rather than one out of a session — the store's commands are private
    // and a fixture's are redacted. Every character survives the markup, which is what makes a
    // marked-up command still quotable as evidence.
    let command = "cd /tmp && rg -n 'x' *.py | head -3";
    let shown = lit(Some(command), Syntax::Bash);
    assert_eq!(shown.syntax, Some(Syntax::Bash));
    assert_eq!(plain(&shown.html), command);
    // The builtin, the operator and the quoted argument each carry a class of their own.
    assert!(
        shown.html.contains(r#"<span class="nb">cd</span>"#),
        "{}",
        shown.html
    );
    assert!(
        shown.html.contains(r#"<span class="o">&amp;&amp;</span>"#),
        "{}",
        shown.html
    );
    // ...and the quotes around the argument are the string's, escaped rather than written raw.
    assert!(
        shown
            .html
            .contains(r#"<span class="s1">&#39;x&#39;</span>"#),
        "{}",
        shown.html
    );
}

#[test]
fn a_line_number_gutter_is_peeled_off_before_the_lexer_reads_the_line() {
    // A file the `Read` tool returned arrives behind a gutter, and the gutter is not the file.
    // Claude Code writes `12\t` down the left of every line it returns (verified against the
    // canonical store on 2026-08-20), and a lexer that meets it reads a different language: a
    // heading whose `#` follows a number is no longer a heading, and neither is a list's `-`. So
    // the number is peeled off, classed as the gutter it is, and the line behind it is lexed on
    // its own. The markdown here is invented — a fixture's strings are redacted flat — but the
    // numbering is the recorded shape.
    let read = "1\t# Title\n2\t\n3\t- an item\n";
    let shown = lit(Some(read), Syntax::Markdown);
    assert_eq!(shown.syntax, Some(Syntax::Markdown));
    // Every character comes back, gutter included: the result is evidence before it is markup.
    assert_eq!(plain(&shown.html), read);
    assert!(
        shown.html.contains("<span class=\"lineno\">1\t</span>"),
        "{}",
        shown.html
    );
    // ...and the heading behind the number is read as a heading, which is the whole point.
    assert!(
        shown.html.contains(r#"<span class="gh"># Title</span>"#),
        "{}",
        shown.html
    );
    assert!(
        shown.html.contains(r#"<span class="k">-</span>"#),
        "{}",
        shown.html
    );
}

/// The character shapes a transcript writes that a marker must carry through unchanged.
///
/// Pygments moves three of them before a lexer ever sees them — it strips the newlines at either
/// end of what it lexes, rewrites `\r\n` and a lone `\r` as `\n`, and drops a leading byte-order
/// mark — which is what `highlight.py`'s `_EXACT` and `_run` are for. syntect preprocesses
/// nothing, so the Rust marker needs no such guard and the sweep is what proves it. Invented, and
/// they have to be — redaction flattened every string the fixture corpus holds — but the first is
/// recorded: 27 of 107,253 `Bash` commands in the canonical store begin with a newline
/// (read 2026-08-20).
const EXACT: &[(&str, &str)] = &[
    ("a leading newline", "\n\ncd /tmp\n"),
    ("trailing newlines", "cd /tmp\n\n\n"),
    ("no newline at the end", "cd /tmp"),
    ("windows line endings", "cd /tmp\r\nls\r\n"),
    ("a lone carriage return", "cd /tmp\rls"),
    ("a byte-order mark", "\u{feff}cd /tmp\n"),
    ("nothing but newlines", "\n\n"),
    // Multibyte, combining and right-to-left characters, and a tab in the middle of a line — what
    // a transcript holds that a byte count and a character count disagree about.
    (
        "characters wider than a byte",
        "echo 'é\u{301} — 👋 שלום'\t# naïve\n",
    ),
];

#[test]
fn a_marked_up_value_prints_the_characters_that_were_stored() {
    // The whole of the module's promise in one leaf: markup adds, and never edits. A tool result
    // is evidence, so a viewer that quietly dropped the newline a command began with would make a
    // page unquotable. Swept over every syntax but JSON, which is re-laid-out for reading before
    // it is marked up and so is exact against the indented text rather than the stored one
    // (`the_markup_inside_a_value_arrives_as_text` reads that arm).
    for syntax in [Syntax::Sql, Syntax::Bash, Syntax::Markdown, Syntax::Python] {
        for (shape, value) in EXACT {
            let shown = lit(Some(value), syntax);
            assert_eq!(plain(&shown.html), *value, "{syntax} lost {shape}");
            // And it went through the tokenizer rather than out the plain arm, which would print
            // the same characters while proving nothing about the markup.
            assert_eq!(
                shown.syntax,
                Some(syntax),
                "{syntax} did not mark up {shape}"
            );
        }
    }
    // The gutter path is the other way a value reaches the tokenizer — line by line — so it is
    // swept too, over a file whose lines carry the same shapes.
    let read = "1\t# Title\r\n2\t\n3\tshalom שלום\n4\t";
    assert_eq!(plain(&lit(Some(read), Syntax::Markdown).html), read);
}

#[test]
fn a_value_with_no_gutter_is_lexed_whole() {
    // The gutter is a shape, not a syntax: a query file is lexed in one pass and loses none. Real,
    // and the strongest check available — the value is a file this repo ships, so the round trip
    // is exact. Line by line, a lexer forgets what the line before it opened.
    let sql = queries::load("view_sessions");
    assert_eq!(plain(&lit(Some(sql), Syntax::Sql).html), sql);
}

#[test]
fn a_file_name_is_what_says_which_syntax_a_read_returned() {
    // A result is marked up by what the call asked for, because the result itself says nothing.
    // The `Read` tool returns text with no type on it, so the only evidence of what the file holds
    // is the name that was read — a false positive is a `.md` file that is not markdown, and a
    // false negative is markdown in a file named anything else. Both show the file as it was
    // stored, which is what the viewer does with every value it cannot place.
    let markdown = by_suffix(Some(".md"));
    assert_eq!(markdown, Some(Syntax::Markdown));
    assert_eq!(by_suffix(Some(".py")), Some(Syntax::Python));
    assert_eq!(
        by_suffix(Some(".MD")),
        Some(Syntax::Markdown),
        "the store keeps the case the session wrote"
    );
    assert_eq!(
        by_suffix(Some(".bin")),
        None,
        "a suffix with no syntax here is shown as it was stored"
    );
    assert_eq!(
        by_suffix(None),
        None,
        "and a tool that read no file at all has no suffix"
    );
    // And a name this map does place is one the marker can read: a suffix with no tokenizer
    // behind it would panic on the first file that carried it.
    assert_eq!(
        lit(Some("# Title"), markdown.expect("a syntax")).syntax,
        Some(Syntax::Markdown)
    );
}
