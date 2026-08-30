//! The one response every node page is: the NavTree with a path open, beside the pane reading it.
//!
//! Ported from `src/hyphae/view/browse.py`, at the width stage 3a serves: the session's own node.
//! Python's `browse` takes a `Reader` per kind and answers eight URLs through one body; the
//! shape is the same here, and [`session_page`] is its first caller. Stage 3b adds the other
//! seven — each is a header query, a trail, and a children log — and lifts what they share into
//! a `browse` of its own.
//!
//! What every node page reads is here: the session's header, the runs the ledger is climbed
//! over, and the level under the selection.

use hyphae_store::{Param, Row, RowError, queries};

use crate::builders;
use crate::components::nav_tree::{NavTreeRow, PresetChoice};
use crate::components::node_page::{Archived, Trail};
use crate::components::{Markup, node_page};
use crate::knobs::{self, BadAsk};
use crate::nav_tree::MAIN_SOURCE;
use crate::nodes::{self, Kind, Node, Preset};
use crate::store::{Page, Reader, TURN_CURSOR, ViewError, page_rows, window};

/// Where the session list lives, and where the crumb chain starts.
const LIST_URL: &str = "/sessions";

/// A node page could not be built. Separate from [`ViewError`] because these three are what a
/// reader did — a size out of bounds, a page past the end, an id not in the store — and each is
/// answered with its own status rather than with the 503 a store failure gets.
#[derive(Debug, thiserror::Error)]
pub enum PageError {
    #[error(transparent)]
    Store(#[from] ViewError),
    #[error(transparent)]
    Row(#[from] RowError),
    #[error(transparent)]
    Bad(#[from] BadAsk),
    #[error("{0}")]
    Missing(String),
}

/// The knobs a node-page request carried, already checked against their ceilings.
pub struct Asked {
    pub nav: Preset,
    pub kin: i64,
    pub log: i64,
    pub detail: i64,
    pub page: i64,
}

impl Asked {
    /// One request's knobs, or the 400 the reader earned.
    ///
    /// Checked before anything is read: a bad number is the reader's question answered, and a
    /// page below the first would otherwise bind a negative offset.
    pub fn checked(nav: &str, kin: i64, log: i64, detail: i64, page: i64) -> Result<Self, BadAsk> {
        let asked = Self {
            nav: knobs::viewed(nav)?,
            kin: knobs::checked(kin, knobs::KIN.ceiling)?,
            log: knobs::checked(log, knobs::LOG.ceiling)?,
            detail: knobs::checked(detail, knobs::DETAIL.ceiling)?,
            page,
        };
        if page < 1 {
            return Err(BadAsk(
                "Ask for a children log page from one upwards.".to_owned(),
            ));
        }
        Ok(asked)
    }

    /// The suffix every link this page mints carries: whatever is not a default.
    fn suffix(&self) -> String {
        knobs::knobs(self.nav, self.kin, self.log, self.detail)
    }
}

/// What `Page::SESSION_HEADER` binds for one session, named once for every reader of it.
fn header_bound(session_id: &str) -> Vec<(&'static str, Param)> {
    vec![
        ("session_id", session_id.into()),
        ("head_chars", (queries::HEADER_CHARS as i64).into()),
        ("item_chars", (queries::HEADER_ITEM_CHARS as i64).into()),
        ("head_items", (queries::HEADER_ITEMS as i64).into()),
    ]
}

/// A session's own node: what it was, and its main thread as the NavTree's first level.
pub fn session_page(reader: &Reader, session_id: &str, asked: &Asked) -> Result<Markup, PageError> {
    let store = reader.connect()?;
    let head = page_rows(&store, Page::SessionHeader, &header_bound(session_id))?;
    let Some(header) = head.first() else {
        return Err(PageError::Missing(
            "No session with that id is in this store.".to_owned(),
        ));
    };
    // The session's runs whole, once: a run is placed by the call that spawned it rather than by
    // the thread it ran on, so any level of the NavTree may need any of them. Cut to the wider of
    // the two widths that print them, and cut again at each.
    let runs = page_rows(
        &store,
        Page::Runs,
        &[
            ("session_id", session_id.into()),
            ("chip_chars", (queries::LOG_CHARS as i64).into()),
        ],
    )?;
    // The rollup once per page: every row the NavTree draws reads its subtree total out of this
    // one climb over the runs.
    let held = builders::ledger(
        session_id,
        header.opt_f64("cost_usd")?.unwrap_or(0.0),
        &runs,
    )?;
    let selection = builders::session_node(header, &held, None)?;

    // The thread's own children, at the width a NavTree row prints them. Not the timeline: a
    // NavTree row draws a context bar, and only this query answers with the `context` struct it
    // is drawn from.
    let level = page_rows(
        &store,
        Page::NavTreeTurns,
        &[
            ("session_id", session_id.into()),
            ("source", MAIN_SOURCE.into()),
            ("nav_chars", (queries::NAV_CHARS as i64).into()),
        ],
    )?;
    let rows = nav_tree_rows(session_id, &selection, &level, &held, asked)?;

    // The same level again, at the children log's width and through the keyset window: this is
    // what `?log=` and `?page=` bind, and what says a page past the level's end is a 404. Stage
    // 3b renders its rows as the log's table; the read and the guard are the page's either way.
    let logged = window(
        &store,
        Page::Timeline,
        TURN_CURSOR,
        knobs::skipped(asked.page, asked.log),
        asked.log,
        &[
            ("session_id", session_id.into()),
            ("log_chars", (queries::LOG_CHARS as i64).into()),
        ],
    )?;
    if asked.page > 1 && logged.rows.is_empty() {
        return Err(PageError::Missing(
            "This node's children do not run to that page.".to_owned(),
        ));
    }

    let facts = builders::node_facts(&selection, header)?;
    let trail = Trail {
        list_url: LIST_URL.to_owned(),
        project_dir: header.opt_str("project_dir")?.map(str::to_owned),
        // Linked where the list can be filtered down to it, which needs the path the store
        // holds rather than the one a crumb prints.
        project_url: header
            .opt_str("project_dir")?
            .map(|dir| format!("{LIST_URL}?project={}", urlencoded(dir))),
    };
    Ok(node_page::page(
        &selection,
        &preset_choices(&selection, asked),
        &rows,
        MAIN_SOURCE,
        &trail,
        // A session is the outermost node, so its own chain is one step long.
        std::slice::from_ref(&selection),
        &facts,
        &Archived {
            thread_url: nodes::thread_url(session_id, MAIN_SOURCE),
            // Only a turn is read from a line of its own thread's transcript.
            line_no: None,
        },
        &asked.suffix(),
    ))
}

/// The node the reader is on under each preset, so switching never costs them their place.
///
/// Here rather than beside the URL minting in [`knobs`]: a preset is a control the NavTree
/// draws, and the bounds module has no business knowing what a component looks like.
fn preset_choices(node: &Node, asked: &Asked) -> Vec<PresetChoice> {
    Preset::ALL
        .into_iter()
        .map(|choice| PresetChoice {
            preset: choice,
            url: format!(
                "{}{}",
                node.url(),
                knobs::knobs(choice, asked.kin, asked.log, asked.detail)
            ),
            current: choice == asked.nav,
        })
        .collect()
}

/// The session's node and the level under it, as the rows the NavTree draws.
///
/// The whole tree in stage 3b: `nav_tree.py` walks every level the open path passes through, and
/// this is the shape each of them lands in.
fn nav_tree_rows(
    session_id: &str,
    selection: &Node,
    level: &[Row],
    held: &nodes::Ledger,
    asked: &Asked,
) -> Result<Vec<NavTreeRow>, PageError> {
    let mut rows = vec![NavTreeRow::node(selection.clone(), 0, true, false)];
    for row in level.iter().take(asked.kin as usize) {
        let node = builders::turn_node(session_id, MAIN_SOURCE, row, held, None)?;
        rows.push(NavTreeRow::node(node, 1, false, false));
    }
    // What the cap left out, and the fetch that opens it. The level is bounded by `?kin=`, not by
    // the page's own log size, so the two counts differ whenever a reader narrows one of them.
    let cut = level.len() as i64 - asked.kin;
    if cut > 0 {
        let mut tail = NavTreeRow::node(selection.clone(), 1, false, false);
        tail.cut = cut;
        rows.push(tail);
    }
    Ok(rows)
}

/// One query-string value, escaped the way `urlencode` escapes it.
///
/// A project directory is an absolute path and nothing else, so the set that must not go through
/// raw is small, and the crumb above prints its own cut copy rather than this one.
fn urlencoded(value: &str) -> String {
    value
        .bytes()
        .map(|byte| match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                (byte as char).to_string()
            }
            _ => format!("%{byte:02X}"),
        })
        .collect()
}

/// The kinds stage 3a does not serve yet, named so 3b's list is in the code rather than only in
/// the handoff.
#[allow(dead_code)]
const REMAINING: [Kind; 7] = [
    Kind::Turn,
    Kind::Run,
    Kind::Call,
    Kind::Tool,
    Kind::Compaction,
    Kind::Unattributed,
    Kind::Unattached,
];
