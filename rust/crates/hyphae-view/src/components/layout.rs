//! The frame every page is served in, and the three slots a page fills.

use hypertext::Raw;
use hypertext::prelude::*;

use crate::components::Markup;

/// What htpy writes above `<html>`, in its own spelling: lowercase, and no space before the `>`.
const DOCTYPE: &str = "<!doctype html>";

/// The dev reload client, which `hp view --dev` alone puts on a page: it listens on `/dev/reload`
/// and reloads when a stylesheet or a component is saved.
fn dev_script() -> Markup {
    rsx! { <script src="/static/dev-reload.js" defer></script> }.memoize()
}

/// One whole document: `tab_title` in the tab, `main` under the masthead, `footer` last.
///
/// `scripts` is what a page needs beyond htmx — only the node page has any. `footer` is the
/// citation frame, which the node page leaves empty and stands inside its reading pane instead,
/// because that page's scrollers are its two columns and a document footer under them would
/// never come into view.
pub fn page(
    tab_title: &str,
    scripts: Option<Markup>,
    main: Markup,
    footer: Option<Markup>,
    dev: bool,
) -> Markup {
    let framed = rsx! {
        <html lang="en">
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>(tab_title)</title>
                <link rel="stylesheet" href="/static/style.css">
                // What paints the classes the highlighter writes. Its own file rather than a
                // block of `style.css`, because the classes are Pygments' vocabulary and not
                // this viewer's.
                <link rel="stylesheet" href="/static/pygments.css">
                // htmx writes a style element for its indicator class as it loads, which
                // `crate::app::CSP` blocks and the browser reports as an error on every page.
                // Nothing here wears that class, so the styles are turned off rather than
                // allowed: a hash in the policy would pin this htmx build, and a nonce would
                // open the door for the transcript text every page renders.
                // The one attribute on any page holding a quote. hypertext writes it `&quot;`
                // where markupsafe writes `&#34;` — the same character either way, and the one
                // escaping dialect the two viewers do not share.
                <meta name="htmx-config" content=r#"{"includeIndicatorStyles": false}"#>
                // htmx, vendored — the version is in the filename because that is where an
                // upgrade has to be seen. No CDN: the viewer reads private transcripts on a
                // laptop that may be offline, and the CSP that keeps a transcript from calling
                // out would refuse a remote script anyway.
                <script src="/static/htmx-2.0.6.min.js" defer></script>
                (scripts)
                @if dev { (dev_script()) }
            </head>
            <body>
                <nav id="masthead"><a href="/">"hyphae"</a></nav>
                <main>(main)</main>
                (footer)
            </body>
        </html>
    }
    .memoize();
    Raw::dangerously_create(format!("{DOCTYPE}{}", framed.into_inner()))
}
