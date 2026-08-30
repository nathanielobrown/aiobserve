//! Turning what a transcript wrote into HTML that cannot act.
//!
//! Ported from `src/hyphae/view/render.py`. Every helper hands back [`Markup`], which a
//! component prints unescaped — so this module owns the escaping for every value it renders,
//! and a mistake here is a live one. Three rules make the difference:
//!
//! - HTML passthrough is **off**. `markdown-it` keeps its HTML plugin out of the CommonMark
//!   set on purpose, so a `<script>` a transcript wrote is text; this module never adds it
//! - An image **renders as a placeholder**. `![](https://host/px?d=1)` needs no passthrough
//!   and no click: the browser fetches the URL on load, which is egress a transcript controls.
//!   The CSP header in [`crate::app`] is the second wall behind the same hole
//! - A URL is **a link only when its scheme is `http` or `https`**. Escaping leaves a
//!   `javascript:` URL intact, and an `href` is the one place a transcript's text is acted on
//!
//! `crate::inline_markdown` is the other escape path, for a line printed as a title.

use std::sync::LazyLock;

use hypertext::Raw;
use markdown_it::plugins::cmark;
use markdown_it::plugins::cmark::block::fence::CodeFence;
use markdown_it::plugins::cmark::inline::image::Image;
use markdown_it::{MarkdownIt, Node, NodeValue, Renderer};

use crate::format::ELLIPSIS;

/// HTML a component prints as it stands.
///
/// The one escaping opt-out in the crate, and the reason it is a type: a value only becomes
/// one by passing through this module, through [`crate::inline_markdown`], or by having been
/// rendered already (`rsx! { … }.memoize()`), so a `String` a query returned cannot be
/// mistaken for markup on the way to a page. Mirrors htpy's `Markup`.
pub type Markup = Raw<String>;

/// Markup that prints nothing — what an absent value renders as.
pub fn nothing() -> Markup {
    Raw::dangerously_create(String::new())
}

/// The schemes a rendered URL may carry into an `href`. Everything else a transcript can
/// write there — `javascript:`, `data:`, `file:` — is shown as text instead.
/// [`crate::inline_markdown`] reads it too: one answer to where a browser may be pointed.
pub const LINK_SCHEMES: [&str; 2] = ["http://", "https://"];

/// The class an image placeholder wears, so the stylesheet paints one thing wherever it lands.
pub const IMAGE_CLASS: &str = "image";

/// What an image shows here instead of fetching: its alt text and its URL, in words.
///
/// Written once because both renderers print it — a title and the paragraph that title opens —
/// and a reader meeting two wordings would read them as two different things.
pub fn image_text(alt: &str, src: &str) -> String {
    let named = if alt.is_empty() { "untitled" } else { alt };
    format!("[image: {named} — {src}]")
}

/// Escape a value the way `markupsafe` does, which is the spelling every surface of the Python
/// viewer serves and what `view/bounds.py` measures a NavTree row in.
///
/// Wider than hypertext's own node escaping, which leaves a quote alone because a node is not
/// an attribute. Both are safe; only one of them is byte-for-byte the page we already serve.
pub fn escape(value: &str) -> String {
    let mut written = String::with_capacity(value.len());
    for character in value.chars() {
        match character {
            '&' => written.push_str("&amp;"),
            '<' => written.push_str("&lt;"),
            '>' => written.push_str("&gt;"),
            '"' => written.push_str("&#34;"),
            '\'' => written.push_str("&#39;"),
            _ => written.push(character),
        }
    }
    written
}

/// One value's markdown as HTML, with its markup rendered inert.
pub fn markdown(text: Option<&str>) -> Markup {
    let Some(text) = text.filter(|text| !text.is_empty()) else {
        return nothing();
    };
    let mut tree = parser().parse(text);
    inert(&mut tree);
    // XSS SAFETY: passthrough is off, and `inert` has replaced every node that would have put
    // a transcript's URL somewhere the browser acts on.
    Raw::dangerously_create(tree.render())
}

/// A URL as a link when a browser should follow it, and as text when it should not.
///
/// The one value the viewer puts in an `href` is a PR URL, and a transcript wrote it. Escaping
/// does not settle that: an escaped `javascript:` URL is still a `javascript:` URL in an
/// attribute the browser acts on. Nor does a value the page cut: half a URL is a URL somewhere
/// else, so a value carrying the mark a cut leaves is text like anything else.
pub fn link(url: Option<&str>) -> Markup {
    let Some(url) = url.filter(|url| !url.is_empty()) else {
        return nothing();
    };
    let lowered = url.to_lowercase();
    let followable = LINK_SCHEMES
        .iter()
        .any(|scheme| lowered.starts_with(scheme));
    if !followable || url.ends_with(ELLIPSIS) {
        return Raw::dangerously_create(escape(url));
    }
    let shown = escape(url);
    // XSS SAFETY: the scheme is one of two, and both halves are escaped above.
    Raw::dangerously_create(format!("<a href=\"{shown}\">{shown}</a>"))
}

/// The CommonMark reader both entry points above run.
///
/// `MarkdownIt::new()` starts empty and `cmark::add` leaves HTML out — the crate keeps its
/// HTML plugin separate "for security reasons", which is the `html=False` the Python builds
/// explicitly. Linkify is a plugin of its own and is likewise never added: a bare URL in a
/// transcript is a string someone typed, not an invitation to make it clickable.
pub(crate) fn parser() -> &'static MarkdownIt {
    static PARSER: LazyLock<MarkdownIt> = LazyLock::new(|| {
        let mut reader = MarkdownIt::new();
        cmark::add(&mut reader);
        reader
    });
    &PARSER
}

/// Replace every node that would reach out of the page with one that stays on it.
fn inert(tree: &mut Node) {
    tree.walk_mut(|node, _| {
        if let Some(image) = node.cast::<Image>() {
            let shown = image_text(&node.collect_text(), &image.url);
            node.children.clear();
            node.replace(Placeholder { shown });
        } else if let Some(fence) = node.cast::<CodeFence>() {
            let content = fence.content.clone();
            node.children.clear();
            node.replace(PlainCode { content });
        }
    });
}

/// An image, as the words it would have shown.
///
/// The node is replaced rather than the rule removed. Removing it hands `![x](url)` to the
/// link rule instead, which puts the transcript's host straight back in an `href`.
#[derive(Debug)]
struct Placeholder {
    shown: String,
}

impl NodeValue for Placeholder {
    fn render(&self, _node: &Node, formatter: &mut dyn Renderer) {
        formatter.open("span", &[("class", IMAGE_CLASS.to_owned())]);
        formatter.text(&self.shown);
        formatter.close("span");
    }
}

/// A fenced block in the same `<pre>` the rest of the viewer prints code in.
#[derive(Debug)]
struct PlainCode {
    content: String,
}

impl NodeValue for PlainCode {
    fn render(&self, _node: &Node, formatter: &mut dyn Renderer) {
        formatter.cr();
        formatter.open("pre", &[("class", "code".to_owned())]);
        formatter.text(&self.content);
        formatter.close("pre");
        formatter.cr();
    }
}
