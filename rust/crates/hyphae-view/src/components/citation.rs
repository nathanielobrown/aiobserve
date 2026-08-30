//! What produced a page, at the end of it: the query it ran, and the link to read the SQL.
//!
//! Ported from `src/hyphae/view/components/citation.py`. Its own module rather than a function in
//! [`crate::components::parts`] because two mounts stand it in different places — a list page's
//! footer ends the document, and a node page's ends the reading pane, which is the scroller that
//! page has. [`crate::citation`] composes what goes in it.

use hypertext::prelude::*;

use crate::citation::Cited;
use crate::components::Markup;
use crate::render;

/// Every query a page ran, folded shut — and nothing where a page ran none.
///
/// Folded because it is provenance rather than content. Each line links to the query page for the
/// statement it names, so a reader who wants to know what a column means reads the SQL.
pub fn footer(citations: &[(String, Cited)]) -> Option<Markup> {
    if citations.is_empty() {
        return None;
    }
    Some(
        rsx! {
            <footer id="citation">
                <details data-citations=(citations.len())>
                    <summary>"what produced this page"</summary>
                    <ul>(lines(citations))</ul>
                </details>
            </footer>
        }
        .memoize(),
    )
}

/// The same lines on a fragment, unfolded: what one swapped-in element ran.
///
/// A fragment has no footer to end and nothing to fold away from — it is a handful of lines inside
/// somebody else's page — so the provenance stands open. The lines are [`footer`]'s, so the two
/// mounts cannot cite one query two ways.
pub fn listed(citations: &[(String, Cited)]) -> Markup {
    rsx! {
        <ul class="citations" data-citations=(citations.len())>(lines(citations))</ul>
    }
    .memoize()
}

/// One line per query: the statement, linking to the page that prints its SQL.
fn lines(citations: &[(String, Cited)]) -> Markup {
    render::joined(citations.iter().map(|(name, ran)| {
        rsx! {
            <li><a data-field=(name) href=(ran.url)><code>(ran.line)</code></a></li>
        }
        .memoize()
    }))
}
