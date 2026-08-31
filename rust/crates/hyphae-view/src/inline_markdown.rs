//! One line of what a session wrote, rendered as a title: bold, italic, code, a link.
//!
//! Ported from `src/hyphae/view/inline_markdown.py`. The second of the viewer's two escape
//! paths, beside [`crate::render`] — and the one that reaches furthest. A title is printed in
//! a NavTree row, a crumb, a walk control, the pane's own heading and the browser tab, so a
//! mistake here lands on every page at once. Which is why the walk below is an allowlist
//! rather than a configuration: markdown-it parses the line, and this module decides what each
//! node it hands back may become. A node kind it does not know is a panic, not a silent drop.
//!
//! Four rules, three of them [`crate::render`]'s own:
//!
//! - **No block element.** Only the inline rules run, so a heading, a list and a fence are the
//!   characters they were typed as — a `<p>` inside a NavTree row is not a row any more
//! - **HTML passthrough is off**, and an **image renders as a placeholder** rather than a fetch
//! - A URL is **a link only when its scheme is `http` or `https`**, and only where the caller
//!   says the surface can carry one. Every surface but the pane's heading prints its title
//!   inside a link already, and an `<a>` inside an `<a>` is markup a browser takes apart
//! - A **width is spent on what a reader sees**. [`cut`] counts visible characters and closes
//!   what it cut inside, so `**` never eats a row's budget and a stopped title never bolds the
//!   page. Which is why it is also told the width the *query* cut at
//!
//! Escaping is [`crate::render::escape`] rather than markdown-it's, so a line with no markdown
//! in it serves the bytes the Python page serves: markdown-it spells a quote `&quot;` where
//! every other value on a page spells it `&#34;`, and a NavTree row is measured in bytes.

use std::sync::LazyLock;

use hypertext::Raw;
use markdown_it::MarkdownIt;
use markdown_it::Node;
use markdown_it::parser::inline::{Text, TextSpecial};
use markdown_it::plugins::cmark::block::paragraph::{self, Paragraph};
use markdown_it::plugins::cmark::inline::autolink::Autolink;
use markdown_it::plugins::cmark::inline::backticks::CodeInline;
use markdown_it::plugins::cmark::inline::emphasis::{Em, Strong};
use markdown_it::plugins::cmark::inline::image::Image;
use markdown_it::plugins::cmark::inline::link::Link;
use markdown_it::plugins::cmark::inline::newline::{Hardbreak, Softbreak};
use markdown_it::plugins::cmark::inline::{
    autolink, backticks, emphasis, entity, escape, image, link, newline,
};

use crate::format::ELLIPSIS;
use crate::render::{self, IMAGE_CLASS, LINK_SCHEMES, Markup};

/// One line as the markup a surface prints, whole.
///
/// `links` is the surface's answer to whether it may carry an `<a>`: true in the reading
/// pane's heading, false everywhere a title is already inside a link. No default — a caller
/// that does not know which surface it is cannot answer it.
pub fn render(text: Option<&str>, links: bool) -> Markup {
    line(text, links, None, None).markup
}

/// One line as markup, at `size` visible characters, marked where the rest was left.
///
/// Two cuts can stop a line and the mark stands for either. `size` is the surface's, spent on
/// what a reader sees: the syntax a line is written in costs the reader nothing, so it costs
/// the width nothing, and a cut landing inside a `<strong>` closes it before the mark.
/// `source_cap` is the *query's* — every one of them ships a character past the width it cut
/// at, so a raw string longer than the cap is one the store stopped, however short it renders.
pub fn cut(text: Option<&str>, size: usize, links: bool, source_cap: usize) -> Markup {
    line(text, links, Some(size), Some(source_cap)).markup
}

/// One line as plain text — what [`cut`] measures, and what an attribute may carry.
///
/// The browser tab and every `title=` attribute take this: markup in either is either printed
/// as characters or acted on, and neither is what the line says.
pub fn strip(text: Option<&str>) -> String {
    line(text, false, None, None).shown
}

/// One rendered line, both ways: what the page prints and what a width is measured on.
struct Line {
    markup: Markup,
    shown: String,
}

/// The walk every entry point above shares: one pass over the line's inline nodes.
///
/// Both halves come out of the same walk so they cannot disagree — a width measured on one
/// spelling of the line and spent on another is a row that stops in the wrong place.
fn line(text: Option<&str>, links: bool, size: Option<usize>, source_cap: Option<usize>) -> Line {
    let Some(text) = text.filter(|text| !text.is_empty()) else {
        return Line {
            markup: render::nothing(),
            shown: String::new(),
        };
    };
    let mut tree = parser().parse(text);
    // A raw string past the cap is one the query cut, and its cut landed wherever it landed:
    // the run it broke has no closing delimiter, so markdown-it hands it back as the
    // characters it was typed as. Only the last node can hold that run — everything before it
    // closed.
    let source_cut = source_cap.is_some_and(|cap| text.chars().count() > cap);
    if source_cut {
        unbreak_last(&mut tree);
    }
    let mut walk = Walk {
        written: String::new(),
        seen: String::new(),
        size,
        links,
        stopped: false,
    };
    walk.paragraphs(&tree.children, text);
    // The mark goes outside what it cut, so a stopped title reads as the page stopping it
    // rather than as a word the session wrote. Either cut earns it.
    let mark = if walk.stopped || source_cut {
        ELLIPSIS
    } else {
        ""
    };
    Line {
        // XSS SAFETY: every run in `written` is either one of this module's own literals or a
        // value `Walk::write` escaped on the way in.
        markup: Raw::dangerously_create(walk.written.clone() + mark),
        shown: walk.seen + mark,
    }
}

/// The reader that runs the inline rules and nothing else.
///
/// `block::paragraph` is the only block rule, and it is here because markdown-it has no public
/// inline-only entry point: a paragraph is the wrapper that lets the inline rules run at all.
/// With no other block rule installed, `# heading`, `- item` and a fence are the characters
/// they were typed as, which is what `parseInline` gives the Python.
fn parser() -> &'static MarkdownIt {
    static PARSER: LazyLock<MarkdownIt> = LazyLock::new(|| {
        let mut reader = MarkdownIt::new();
        newline::add(&mut reader);
        escape::add(&mut reader);
        backticks::add(&mut reader);
        emphasis::add(&mut reader);
        link::add(&mut reader);
        image::add(&mut reader);
        autolink::add(&mut reader);
        entity::add(&mut reader);
        paragraph::add(&mut reader);
        reader
    });
    &PARSER
}

/// Cut the run a query's cut left open off the end of the line.
fn unbreak_last(tree: &mut Node) {
    let Some(last) = tree
        .children
        .last_mut()
        .and_then(|block| block.children.last_mut())
    else {
        return;
    };
    if let Some(text) = last.cast_mut::<Text>() {
        text.content = unbroken(&text.content);
    }
}

/// What a markdown run opens with. One of these surviving in a *text* node is one markdown-it
/// could not pair, which after a query's cut is where that cut landed.
const OPENERS: [char; 4] = ['*', '_', '`', '['];

/// `shown` up to the run a cut left open, or the whole of it where none is open.
///
/// Read from the right, because the broken run is the last one: everything before it paired.
fn unbroken(shown: &str) -> String {
    let chars: Vec<char> = shown.chars().collect();
    let mut at = chars.len();
    while at > 0 {
        at -= 1;
        let character = chars[at];
        if !OPENERS.contains(&character) {
            continue;
        }
        // The whole run, so `**` goes at once rather than one star at a time.
        let mut start = at;
        while start > 0 && chars[start - 1] == character {
            start -= 1;
        }
        if opens(&chars, start, at) {
            return chars[..start]
                .iter()
                .collect::<String>()
                .trim_end()
                .to_owned();
        }
        at = start;
    }
    shown.to_owned()
}

/// Whether the run of one character between `start` and `at` is one a cut left open.
///
/// Narrow on purpose. A title is mostly paths and commands, and dropping the tail of one to
/// close a run nobody opened costs a reader more than a stray delimiter does — so an emphasis
/// run counts at two characters and up, never the `*` of `*.tmp` or the `_` of `handoff_2`.
fn opens(chars: &[char], start: usize, at: usize) -> bool {
    let character = chars[start];
    let after = chars.get(at + 1);
    if character == '*' || character == '_' {
        return at > start && after.is_some_and(|after| !after.is_whitespace());
    }
    if character == '`' {
        return !chars[at + 1..].contains(&'`');
    }
    dangling(chars, start)
}

/// Whether the bracket at `at` opens a link the cut took the end off.
///
/// A bracket the line closes is typing — `[WIP] rewrite` is a title — unless the `](` after it
/// opened a URL that never closes, which is a link the query stopped in the middle of.
fn dangling(chars: &[char], at: usize) -> bool {
    let Some(closed) = chars[at..]
        .iter()
        .position(|char| *char == ']')
        .map(|off| at + off)
    else {
        return true;
    };
    chars.get(closed + 1) == Some(&'(') && !chars[closed..].contains(&')')
}

/// The state one line's walk carries: what it has written, and what a reader has seen of it.
struct Walk {
    written: String,
    seen: String,
    size: Option<usize>,
    links: bool,
    stopped: bool,
}

impl Walk {
    /// How many visible characters are left, or `None` where the surface set no width.
    fn room(&self) -> Option<usize> {
        self.size
            .map(|size| size.saturating_sub(self.seen.chars().count()))
    }

    /// Every paragraph the block rule wrapped the line's inline nodes in, and the whitespace
    /// it trimmed on the way.
    ///
    /// The Python runs the inline parser over the whole line, so it keeps the indent a title
    /// opens with, the spaces it ends on, and one break per newline between two of its
    /// paragraphs. All three cost a reader width, so all three are put back here rather than
    /// the paragraphs kept — a `<p>` inside a NavTree row is not a row any more.
    fn paragraphs(&mut self, blocks: &[Node], text: &str) {
        // A line of nothing but whitespace is no paragraph at all, and still a line.
        if blocks.is_empty() {
            self.write(text, "", "");
            return;
        }
        let mut wrote_to = 0;
        for (at, block) in blocks.iter().enumerate() {
            assert!(
                block.is::<Paragraph>(),
                "a title parsed to a {}",
                block.name()
            );
            if self.stopped {
                return;
            }
            let (from, to) = block
                .srcmap
                .expect("the block parser maps every node it builds")
                .get_byte_offsets();
            let skipped = &text[wrote_to..from];
            let gap = if at == 0 {
                skipped.to_owned()
            } else {
                "\n".repeat(skipped.matches('\n').count())
            };
            if !gap.is_empty() {
                self.write(&gap, "", "");
            }
            self.nodes(&block.children);
            wrote_to = to;
        }
        // The last paragraph's own source map runs to the end of its line, so what it
        // trimmed is read off the line rather than off the map.
        let kept = text.trim_end();
        if !self.stopped && kept.len() < text.len() {
            self.write(&text[kept.len()..], "", "");
        }
    }

    fn nodes(&mut self, nodes: &[Node]) {
        for node in nodes {
            if self.stopped {
                return;
            }
            self.node(node);
        }
    }

    fn node(&mut self, node: &Node) {
        if let Some(text) = node.cast::<Text>() {
            self.write(&text.content, "", "");
        } else if let Some(text) = node.cast::<TextSpecial>() {
            self.write(&text.content, "", "");
        } else if node.is::<CodeInline>() {
            self.write(&node.collect_text(), "<code>", "</code>");
        // A line break inside a title is whitespace to every surface that prints one, and the
        // character the store holds — so it is neither dropped nor turned into a `<br>`.
        } else if node.is::<Softbreak>() || node.is::<Hardbreak>() {
            self.write("\n", "", "");
        } else if let Some(image) = node.cast::<Image>() {
            let shown = render::image_text(&node.collect_text(), &image.url);
            self.write(
                &shown,
                &format!("<span class=\"{IMAGE_CLASS}\">"),
                "</span>",
            );
        } else if node.is::<Strong>() {
            self.wrap(node, "<strong>", "</strong>");
        } else if node.is::<Em>() {
            self.wrap(node, "<em>", "</em>");
        } else if let Some(anchor) = node.cast::<Link>() {
            self.linked(node, &anchor.url);
        // `<https://example.com>`, which markdown-it gives its own node and markdown-it-py
        // gives the link rule's. Same surface answer either way.
        } else if let Some(anchor) = node.cast::<Autolink>() {
            self.linked(node, &anchor.url);
        } else {
            panic!("a title has no rule for a {} node", node.name());
        }
    }

    /// One node's children between two of this module's own literals.
    ///
    /// The closing literal is written even when the width ran out inside, which is what closes
    /// a `<strong>` the cut landed in.
    fn wrap(&mut self, node: &Node, before: &str, after: &str) {
        self.written.push_str(before);
        self.nodes(&node.children);
        self.written.push_str(after);
    }

    /// One link's children inside an `<a>`, or bare where the surface or the URL forbids one.
    fn linked(&mut self, node: &Node, url: &str) {
        let opened = self.anchor(url);
        let closed = if opened.is_empty() { "" } else { "</a>" };
        self.wrap(node, &opened, closed);
    }

    /// A link's opening tag, or nothing where this surface or this URL may not carry one.
    ///
    /// Nothing rather than a rendered `href` the browser refuses: escaping does not settle a
    /// `javascript:` URL, so the scheme decides, and the words the transcript wrote still print.
    fn anchor(&self, url: &str) -> String {
        let lowered = url.to_lowercase();
        if !self.links || url.is_empty() {
            return String::new();
        }
        if !LINK_SCHEMES
            .iter()
            .any(|scheme| lowered.starts_with(scheme))
        {
            return String::new();
        }
        format!("<a href=\"{}\">", render::escape(url))
    }

    /// One run of visible characters onto the line, stopping the walk where the width ran out.
    ///
    /// `before` and `after` are this module's own literals rather than anything a session
    /// wrote, which is what makes them safe to write beside an escaped value.
    fn write(&mut self, shown: &str, before: &str, after: &str) {
        let kept: String = match self.room() {
            None => shown.to_owned(),
            Some(room) => shown.chars().take(room).collect(),
        };
        let whole = kept.chars().count() == shown.chars().count();
        self.written.push_str(before);
        self.written.push_str(&render::escape(&kept));
        self.written.push_str(after);
        self.seen.push_str(&kept);
        if !whole {
            self.stopped = true;
        }
    }
}
