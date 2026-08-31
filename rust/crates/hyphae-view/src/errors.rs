//! Where a session failed: the list of every failed tool call, and the step between two.
//!
//! Ported from `src/hyphae/view/errors.py`. The NavTree opens one path and the walk reads the
//! session in order, so neither gets a reader to the third failure of a run five spawns down
//! without reading everything in front of it. This module is the way that does not: one list of
//! every `is_error` tool call the session holds, whichever thread it ran on, in the order they
//! happened — and, where the pane is already standing on one of them, the failure read before it
//! and the one after.
//!
//! Session-wide for the reason the unattached bucket is: what a subagent failed at is what the
//! session failed at. The list is capped like the landing page ([`crate::knobs::ERRORS`]) rather
//! than paged, and the stepper walks that same capped list — a failure past the cap is one neither
//! surface reaches, rather than one the stepper offers and the list denies.
//!
//! [`crate::walk`] is the neighbouring concern, and reads the same way: what is beside the pane,
//! answered from the store rather than from the rows the page happens to have drawn.

use chrono::{DateTime, Utc};
use hyphae_store::{Param, Store, queries};

use crate::builders::tool_node;
use crate::knobs::ERRORS;
use crate::nav_tree::{Bound, Ran};
use crate::nodes::{Ledger, Node};
use crate::store::{Page, Query, ViewError, page_rows};

/// One failed tool call as both surfaces read it: the node it leads to, and when it ran.
pub struct Failure {
    pub node: Node,
    pub started_at: DateTime<Utc>,
}

/// One session's failures in reading order, what the cap left, and the query behind them.
pub struct Failures {
    pub listed: Vec<Failure>,
    /// How many the session failed beyond what the cap admits, for the tail that says so.
    pub cut: i64,
    pub ran: Ran,
}

/// Every failed tool call of one session, capped at what a page of them shows.
///
/// Read at the NavTree's title width rather than a log's: a row here leads to a node, so it is
/// named the way that node is named everywhere else it appears.
pub fn failures(store: &Store, session_id: &str) -> Result<Failures, ViewError> {
    let bound: Bound = vec![
        ("session_id", session_id.into()),
        ("nav_chars", Param::Int(queries::NAV_CHARS as i64)),
        ("errors", Param::Int(ERRORS.default)),
    ];
    let rows = page_rows(store, Page::SessionErrors, &bound)?;
    // No ledger: a failure is a place to go, and what it cost is the NavTree's arithmetic over the
    // whole session rather than anything this list can see.
    let empty = Ledger::default();
    let mut listed = Vec::with_capacity(rows.len());
    for row in &rows {
        listed.push(Failure {
            node: tool_node(session_id, row.str("source")?, row, &empty)?,
            started_at: row.timestamp("started_at")?,
        });
    }
    // Counted by the query before its LIMIT bit, so a page that cut some says how many rather than
    // reading as the whole list.
    let cut = match rows.first() {
        None => 0,
        Some(row) => row.i64("matched_errors")? - rows.len() as i64,
    };
    Ok(Failures {
        listed,
        cut,
        ran: vec![(Page::SessionErrors.stem(), bound)],
    })
}

/// What the stepper points at: the failure read before this one, and the one after.
pub struct Step {
    pub previous: Option<Node>,
    pub next: Option<Node>,
}

/// The failures either side of `node` in the list, either `None` at an end of it.
///
/// Both `None` where the node is not in the list at all, which is what a failure past the cap is:
/// the store holds it, and no surface here claims to reach what comes next.
pub fn stepped(listed: &[Failure], node: &Node) -> Step {
    let key = node.key();
    let Some(place) = listed.iter().position(|failure| failure.node.key() == key) else {
        return Step {
            previous: None,
            next: None,
        };
    };
    Step {
        previous: place.checked_sub(1).map(|at| listed[at].node.clone()),
        next: listed.get(place + 1).map(|failure| failure.node.clone()),
    }
}
