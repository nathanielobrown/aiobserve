//! The four things a node-page URL may name, and the suffix every link on the page carries.
//!
//! Ported from `src/hyphae/view/knobs.py` and the three node-page bounds of
//! `src/hyphae/view/bounds.py`. A size is something a reader types, so it is checked against a
//! ceiling before anything is read: a bad number is the reader's question answered, not a page
//! served at a size the payload bound never priced (`docs/viewer-bounds.md`).

use hyphae_store::queries;

use crate::components::logs::Pager;
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

/// How deep a chain the NavTree will open, the selection counted.
///
/// A session's nesting is a transcript's, and a transcript can nest as far as an agent spawns: the
/// corpus reaches five, and a chain past this is a store shape nothing here has seen rather than a
/// page to render, so [`crate::nav_tree::ancestry`] refuses instead of building it. The response's
/// bound is arithmetic over this and [`KIN`], which is what makes it a bound rather than a
/// preference.
pub const DEPTH: usize = 16;

/// How much indentation a JSON value may gain before it is served as stored instead
/// ([`crate::highlight`]). Indenting is quadratic in nesting — 10 KB of nothing but `[` indents to
/// 50 MB — while real values gain very little: across the canonical store on 2026-08-07, the worst
/// of a 2,000-record sample gained 3,418 characters and the largest values in it gained 352.
pub const INDENT_CHARS: usize = 20_000;

/// How long a value may be and still be marked up in its own syntax ([`crate::highlight`]).
///
/// Characters rather than bytes: what the ceiling guards is the tokenizer's time and the markup a
/// span per token adds, and neither of those is counted in bytes. So a multibyte value under this
/// ceiling is marked up even where its bytes run past it, which is deliberate.
pub const HIGHLIGHT_CHARS: usize = 256_000;

/// What one row of the NavTree may weigh, whole: its markup, a title of [`queries::NAV_CHARS`]
/// characters that each escape to five bytes, and the knobs every link repeats.
///
/// The NavTree is what multiplies — `1 + DEPTH * (KIN + 1)` rows spend this 3,217 times, four
/// fifths of the ceiling — so it is a price to defend rather than a knob to turn: a row that grows
/// past it is a page over the bound, and the answer is a slimmer row.
///
/// Measured through the app rather than budgeted, at every title full of `&` and the longest query
/// string a link can carry (`hyphae-view/tests/bounds_node.rs`). Pinned at exactly what it
/// measures, with no slack, for the same reason: a byte of slack here is 3,217 bytes of page. That
/// leaf holds it from below as well as from above, so slack cannot hide in the room the node
/// page's ceiling keeps for this row's next addition.
///
/// Most of the row is its URL, written three times: the href a reader sees, the `hx-get` htmx
/// fetches, and the popover's own path under a prefix. The click's swap is written once on
/// `#nav-tree-rows` and inherited; the popover's cannot be, because a swap written on the row
/// would be inherited by the link inside it — so its five attributes are spelled out on every row.
/// The rest is the title, the mark saying what kind of node the row is, the spend beside it, and
/// the three classes the context bar is drawn from. A store whose agent runs carry longer ids than
/// the recorded corpus does is a re-measure.
pub const NAV_TREE_ROW_BYTES: usize = 1703;

/// The turn rows a page renders that no cursor reaches.
///
/// `session_timeline` gives one — the calls that answer no turn are a single group — and the
/// NavTree reads it as the unattributed bucket's row. Bound because a level renders it: a timeline
/// answering with more than one raises rather than serving a row nothing counted.
pub const CURSORLESS_TURNS: usize = 1;

/// How much of a fat value the pane previews.
pub const DETAIL: Bound = Bound {
    default: queries::DETAIL_CHARS as i64,
    ceiling: queries::DETAIL_CHARS as i64,
};

/// The records browser, whose row is a preview and the `hx-get` that fetches the record whole.
pub const RECORDS: Bound = Bound {
    default: queries::PAGE_RECORDS,
    ceiling: 200,
};

/// How long the record a page opens by itself may be.
///
/// The first row arrives open, because a citation's cursor puts the record it names there — and a
/// fetch nobody clicked is priced against the page that triggers it rather than against the value
/// route it goes to.
pub const OPENED_RECORD_CHARS: usize = 15_000;

/// The offload page, the one ceiling set by escaping alone rather than by a row's markup: the
/// content is a file some tool wrote, and a chunk of nothing but `&` weighs five bytes a character.
pub const CHUNK: Bound = Bound {
    default: queries::CHUNK_CHARS,
    ceiling: 60_000,
};

/// The session list, the one page a corpus grows. The maximum is what fits under the page ceiling
/// at the *worst* cost of a row rather than the measured one, so the two are the same number.
pub const SESSIONS: Bound = Bound {
    default: 113,
    ceiling: 113,
};

/// The landing page, which a corpus grows the way it grows sessions — one row per project it
/// holds, worktrees folded in. Not a size a URL carries.
pub const PROJECTS: Bound = Bound {
    default: queries::PAGE_PROJECTS,
    ceiling: queries::PAGE_PROJECTS,
};

/// One session's failed tool calls, bound like the landing page rather than paged: a reader jumps
/// to a failure rather than paging through them. The stepper reads the same capped list.
pub const ERRORS: Bound = Bound {
    default: queries::PAGE_ERRORS,
    ceiling: queries::PAGE_ERRORS,
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

/// One page of a node's children log as a URL: the node, its knobs, and the page number.
///
/// Page one is the node's own URL. A reader who pages back to the start has to land on the
/// document a link to the node serves, and it is the one the payload sweep prices.
pub fn numbered(url: &str, marks: &str, page: i64) -> String {
    if page == 1 {
        return format!("{url}{marks}");
    }
    let joiner = if marks.is_empty() { "?" } else { "&" };
    format!("{url}{marks}{joiner}page={page}")
}

/// The control under a children log, or `None` where the level is one page long.
pub fn pager(url: &str, marks: &str, page: i64, pages: i64) -> Option<Pager> {
    if pages < 2 {
        return None;
    }
    Some(Pager {
        place: format!("Page {page} of {pages}"),
        previous: (page > 1).then(|| numbered(url, marks, page - 1)),
        next: (page < pages).then(|| numbered(url, marks, page + 1)),
    })
}

/// How many pages a level of `total` children runs to at `size` a page.
///
/// Python reaches for `math.ceil` over a float division; this is the same arithmetic in integers,
/// which is what the two are comparing.
pub fn pages(total: i64, size: i64) -> i64 {
    total.div_euclid(size) + i64::from(total.rem_euclid(size) != 0)
}
