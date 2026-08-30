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
        excused, 1,
        "one case stands for the highlighter, and it is still in the set"
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
