//! How a page says what it ran: the line a reader re-runs, and the link to the query page.
//!
//! Ported from `src/hyphae/view/citation.py`. Every footer in the viewer carries one. The page
//! composes its bindings, [`cited`] writes them both ways, and `components::citation` prints them
//! — so what the comment says was bound and what the link binds are one thing (`docs/viewer.md`).

use hyphae_store::queries;

use crate::nav_tree::Bound;
use crate::urls;

/// Where the SQL behind a page is read. Every citation in a footer links here, so the path is
/// written once and the route takes the query's name from it.
pub const QUERY_URL: &str = "/query";

/// One query a page ran, as the footer shows it: the line to re-run, and where to read it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cited {
    pub line: String,
    pub url: String,
}

/// What produced a page, both ways a reader follows it.
///
/// The line is what a report quotes and a shell re-runs; the URL is the same query as a page,
/// bindings and all. Both spell a binding the one way [`queries::shown`] does, so the link a
/// footer carries and the comment beside it cannot disagree about what was bound.
pub fn cited(name: &str, bindings: &Bound) -> Cited {
    let written: Vec<(&str, String)> = bindings
        .iter()
        .map(|(key, value)| (*key, queries::shown(value)))
        .collect();
    Cited {
        line: queries::citation(name, bindings),
        url: format!("{QUERY_URL}/{name}?{}", urls::query(&written)),
    }
}

/// Every query one page ran, in the order it ran them, ready for the footer.
///
/// Keyed by the query's own name, as Python's dict comprehension in `browse` is: a page that
/// ran one query twice cites the last binding, which is the one its rows came back under.
pub fn citations(ran: &[(&'static str, Bound)]) -> Vec<(String, Cited)> {
    let mut named: Vec<(String, Cited)> = Vec::new();
    for (name, bound) in ran {
        let line = cited(name, bound);
        match named.iter_mut().find(|(held, _)| held == name) {
            Some(held) => held.1 = line,
            None => named.push(((*name).to_owned(), line)),
        }
    }
    named
}
