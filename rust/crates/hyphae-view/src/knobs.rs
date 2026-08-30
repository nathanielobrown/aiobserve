//! The four things a node-page URL may name, and the suffix every link on the page carries.
//!
//! Ported from `src/hyphae/view/knobs.py` and the three node-page bounds of
//! `src/hyphae/view/bounds.py`. A size is something a reader types, so it is checked against a
//! ceiling before anything is read: a bad number is the reader's question answered, not a page
//! served at a size the payload bound never priced (`docs/viewer-bounds.md`).

use hyphae_store::queries;

use crate::nodes::Preset;

/// One size a reader may name: what a link omits, and what a URL may not exceed.
pub struct Bound {
    pub default: i64,
    pub ceiling: i64,
}

/// How many children the NavTree opens under one node.
pub const KIN: Bound = Bound {
    default: 200,
    ceiling: 200,
};

/// How many rows a children log lists.
pub const LOG: Bound = Bound {
    default: queries::LOG_ROWS,
    ceiling: queries::LOG_ROWS,
};

/// How much of a fat value the pane previews.
pub const DETAIL: Bound = Bound {
    default: queries::DETAIL_CHARS as i64,
    ceiling: queries::DETAIL_CHARS as i64,
};

/// A reader asked for something the viewer will not serve, and is told which.
///
/// Its own type rather than a `ViewError` arm: these are answered 400 where every store failure
/// is answered 503 or 404, and the response is what separates them.
#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct BadAsk(pub String);

/// A page size from a query string, or a 400 — every route's sizes go through here.
pub fn checked(size: i64, ceiling: i64) -> Result<i64, BadAsk> {
    if (1..=ceiling).contains(&size) {
        return Ok(size);
    }
    Err(BadAsk(format!(
        "Ask for a page size between 1 and {ceiling}."
    )))
}

/// The filter preset from a query string, or a 400 — every node route's `?nav=` comes here.
///
/// A 400 rather than a fallback to the full NavTree: a reader who typed a view the viewer does
/// not have should be told, not served a different one under the URL they asked for.
pub fn viewed(nav: &str) -> Result<Preset, BadAsk> {
    Preset::ALL
        .into_iter()
        .find(|preset| preset.word() == nav)
        .ok_or_else(|| {
            let named = Preset::ALL
                .iter()
                .map(|preset| preset.word())
                .collect::<Vec<_>>()
                .join(", ");
            BadAsk(format!("Filter the NavTree by one of: {named}."))
        })
}

/// The query string every link on a node page carries: whatever is not a default.
pub fn knobs(nav: Preset, kin: i64, log: i64, detail: i64) -> String {
    let mut given = Vec::new();
    if nav != Preset::Full {
        given.push(format!("nav={}", nav.word()));
    }
    if kin != KIN.default {
        given.push(format!("kin={kin}"));
    }
    if log != LOG.default {
        given.push(format!("log={log}"));
    }
    if detail != DETAIL.default {
        given.push(format!("detail={detail}"));
    }
    if given.is_empty() {
        return String::new();
    }
    format!("?{}", given.join("&"))
}

/// The rows a page number stands past, for the query that binds an offset.
pub fn skipped(page: i64, size: i64) -> i64 {
    (page - 1) * size
}
