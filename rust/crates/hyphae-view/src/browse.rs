//! The one response every node page is: the NavTree with a path open, beside the pane reading it.
//!
//! Ported from `src/hyphae/view/browse.py`. Eight URLs and one answer: what differs per kind is
//! the [`Reading`] the route passes — its own header, where it sits, and what its children log
//! lists — and everything else a node page needs is read here: the session, the corpus the NavTree
//! is built from, the enrichment a pass wrote, and the page of children under the selection
//! (`docs/viewer.md`).

use hyphae_store::{Param, Row, RowError, Store, queries};

use crate::citation::citations;
use crate::columns::Shape;
use crate::components::logs::Logged;
use crate::components::nav_tree::PresetChoice;
use crate::components::node_page::{Archived, NodePage, Said, Trail};
use crate::components::{Markup, node_page};
use crate::detail::{Detail, enrichment_lines};
use crate::enrichment::{Descriptions, Enrichment, described};
use crate::facts::node_facts;
use crate::knobs::{self, BadAsk};
use crate::nav_tree::{self, Bound, Corpus, Ran};
use crate::nodes::{self, Kind, Node, Preset, Ref};
use crate::store::{Fragment, Listed, Page, Query, ViewError, listed, page_rows};
use crate::viewer::Viewer;
use crate::{builders, errors, listing, walk};

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
    pub fn suffix(&self) -> String {
        knobs::knobs(self.nav, self.kin, self.log, self.detail)
    }

    /// How many rows this page's children log skips before its first.
    pub fn skipped(&self) -> i64 {
        knobs::skipped(self.page, self.log)
    }
}

/// What one node's own reads answered, whatever kind of node it is.
///
/// `trail` is what the node already knows about where it sits, innermost last — a call and a tool
/// name their turn in their own header, so neither costs a read to place; [`nav_tree::ancestry`]
/// resolves the rest. A kind that reads no children answers [`Shape::None`] and no rows.
pub struct Seen {
    pub header: Row,
    pub trail: Vec<Ref>,
    pub shape: Shape,
    pub rows: Vec<Logged>,
    /// How many children the level holds in all, which is more than the page shows whenever the
    /// level runs past `?log=`: the heading counts the level, and the pager divides it.
    pub total: i64,
    pub details: Vec<Detail>,
    /// The transcript line the node was read from, where the store archived one. Only a turn has
    /// one: `turns.id` is a record's `uuid`, which is the store's own join down to the bytes
    /// Claude Code wrote.
    pub record: Option<i64>,
    pub ran: Ran,
}

/// What one node route does beyond the reads every node page makes: its own header, its trail, and
/// its children log. The session header is passed in because every page reads it already.
pub type Reading<'a> = &'a dyn Fn(&Store, &Corpus, &Row) -> Result<Seen, PageError>;

/// What `Page::SESSION_HEADER` binds for one session, named once for every reader of it.
///
/// A node page reads the row whole; `errors_page` reads it only to word a 404, but both have to
/// bind the same params or a change to one silently stops answering for the other.
pub fn header_bound(session_id: &str) -> Bound {
    vec![
        ("session_id", session_id.into()),
        ("head_chars", (queries::HEADER_CHARS as i64).into()),
        ("item_chars", (queries::HEADER_ITEM_CHARS as i64).into()),
        ("head_items", (queries::HEADER_ITEMS as i64).into()),
    ]
}

/// What an enrichment pass said about the node a pane is about, when it said anything.
///
/// Three of the eight kinds are describable, and the pass keys turns by thread — which is the
/// thread the page was read for, so the selection is always in reach of its own description.
fn described_node(descriptions: &Descriptions, node: &Node) -> Option<Enrichment> {
    match node.kind {
        Kind::Session => descriptions.session.clone(),
        Kind::Turn => descriptions.turns.get(&node.node_id).cloned(),
        Kind::Run => descriptions.runs.get(&node.node_id).cloned(),
        _ => None,
    }
}

/// How a pane names the node it is about, per kind, from the header its own route read.
///
/// The NavTree built the row the pane stands on and cut its words where a NavTree row ends, which
/// is a third of what a title has to spend — so a page that took the NavTree's word for it would
/// head a turn with the first line of the prompt and stop. The kinds absent are the ones no cut
/// reaches: a session's node is read from its own header already, a compaction is named by its
/// trigger, and a bucket is named by the viewer.
fn titled(
    kind: Kind,
    session_id: &str,
    source: &str,
    row: &Row,
    corpus: &Corpus,
) -> Result<Option<Node>, ViewError> {
    let node = match kind {
        Kind::Turn => builders::turn_node(
            session_id,
            source,
            row,
            &corpus.held,
            corpus.turn_text(source, row.str("turn_id")?),
        )?,
        Kind::Call => builders::call_node(session_id, source, row, &corpus.held)?,
        Kind::Tool => builders::tool_node(session_id, source, row, &corpus.held)?,
        Kind::Run => builders::run_node(
            session_id,
            row,
            &corpus.held,
            corpus.run_text(row.str("run_id")?),
        )?,
        _ => return Ok(None),
    };
    Ok(Some(node))
}

/// One node page: the NavTree with the path to the node open, beside the pane reading it.
///
/// Every kind serves through here, because a node page is one response whatever the node is.
/// `source` is the thread the enrichment is read for — the enrichment view keys turns by thread,
/// and the NavTree spans the session, so a turn on another thread falls back to its prompt. What
/// differs per kind is `read`, which answers the node's own header, where it sits, and what its
/// children log lists, and 404s when the node is not in the store.
pub fn browse(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    asked: &Asked,
    read: Reading<'_>,
) -> Result<Markup, PageError> {
    let store = viewer.reader.connect()?;
    let bound = header_bound(session_id);
    let head = page_rows(&store, Page::SessionHeader, &bound)?;
    let Some(header) = head.first() else {
        return Err(PageError::Missing(
            "No session with that id is in this store.".to_owned(),
        ));
    };
    // The session's runs are read once and printed twice: as a NavTree row at its width and as a
    // children log row at the log's. Cut to the wider of the two here, and cut again at each — a
    // row cut to the narrower would print a line already stopped.
    let runs_bound: Bound = vec![
        ("session_id", session_id.into()),
        ("chip_chars", (queries::LOG_CHARS as i64).into()),
    ];
    // The session's runs whole, once: a run is placed by the call that spawned it rather than by
    // the thread it ran on, so any level of the NavTree may need any of them, and both buckets are
    // defined against the same set.
    let runs = page_rows(&store, Page::Runs, &runs_bound)?;
    let corpus = Corpus {
        session_id: session_id.to_owned(),
        // The rollup once per page: every row the NavTree draws reads its subtree total out of
        // this one climb over the runs.
        held: builders::ledger(
            session_id,
            header.opt_f64("cost_usd")?.unwrap_or(0.0),
            &runs,
        )?,
        runs,
        described: described(&store, session_id, source)?,
        source: source.to_owned(),
    };
    let seen = read(&store, &corpus, header)?;
    let built = nav_tree::nav_tree(
        &store,
        &corpus,
        builders::session_node(
            header,
            &corpus.held,
            corpus
                .described
                .session
                .as_ref()
                .map(|said| said.description.as_str()),
        )?,
        &nav_tree::ancestry(&corpus, &seen.trail)?,
        asked.nav,
        asked.kin as usize,
    )?;
    // What the reader reads before and after this node, off the same open path.
    let walked = walk::neighbours(&store, &corpus, &built.chain)?;
    // The failures either side of this one, read only where the pane is standing on a failure. A
    // session-wide list is a query per page load and the step it answers does not exist anywhere
    // else, so every other node page asks the store nothing.
    let standing = built
        .chain
        .last()
        .ok_or_else(|| ViewError::Shape("a NavTree with no chain".to_owned()))?;
    let failed = if standing.kind == Kind::Tool && standing.is_error {
        Some(errors::failures(&store, session_id)?)
    } else {
        None
    };
    drop(store);

    // A page past the last of a level and a node that never had one are the same answer. The first
    // page is not: a node with no children still has its own facts to show.
    if asked.page > 1 && seen.rows.is_empty() {
        return Err(PageError::Missing(
            "This node's children do not run to that page.".to_owned(),
        ));
    }
    let mut selection = standing.clone();
    // Named from its own header rather than from the NavTree row it stands on. The words alone:
    // what the node cost and what share of the session that is are the NavTree's to work out,
    // against the whole session rather than against one header.
    if let Some(named) = titled(selection.kind, session_id, source, &seen.header, &corpus)? {
        selection.words = named.words;
    }
    let mut ran: Ran = vec![
        (Page::SessionHeader.stem(), bound.clone()),
        (Page::Runs.stem(), runs_bound),
    ];
    ran.extend(seen.ran);
    ran.extend(built.ran);
    ran.extend(walked.ran);
    // Only when the store held the tables to ask: a page cites what it ran, and over an un-enriched
    // store this query is not one of them.
    if corpus.described.queried {
        ran.push((
            Page::Enrichment.stem(),
            vec![("session_id", session_id.into()), ("source", source.into())],
        ));
    }
    // The same rule for the stepper's own read: a page cites what it ran, and most node pages do
    // not run this one.
    if let Some(failed) = &failed {
        ran.extend(failed.ran.clone());
    }
    let marks = asked.suffix();
    let about = described_node(&corpus.described, &selection);
    let said = about.as_ref().and_then(|about| {
        enrichment_lines(Some(about), session_id, source).map(|lines| Said {
            enrichment: about.clone(),
            lines,
        })
    });
    let trail = Trail {
        list_url: listing::LIST_URL.to_owned(),
        project_dir: header.opt_str("project_dir")?.map(str::to_owned),
        // Off `project_filter` rather than the path the crumb shows: the list's filter matches a
        // path prefix, and a cut one matches nothing.
        project_url: listing::project_link(header.opt_str("project_filter")?),
    };
    let stepped = failed
        .as_ref()
        .map(|failed| errors::stepped(&failed.listed, &selection));
    Ok(node_page::page(&NodePage {
        selection: &selection,
        choices: &preset_choices(&selection, asked),
        rows: &built.rows,
        // The thread the enrichment was read for, which is what a tail row's fetch carries.
        thread: source,
        trail: &trail,
        chain: &built.chain,
        facts: &node_facts(&selection, &seen.header)?,
        said: said.as_ref(),
        details: &seen.details,
        // The bytes behind the node: the thread's transcript, and — for a turn — the one line it
        // was read from.
        archived: &Archived {
            thread_url: nodes::thread_url(session_id, source),
            line_no: seen.record,
        },
        // Where the reading order goes from here, in both directions.
        walked_previous: walked.previous.as_ref(),
        walked_next: walked.next.as_ref(),
        // And where the session failed: how many failures it holds, which is what the way into the
        // list says, beside the step to the next one where there is one.
        tool_errors: header.opt_i64("tool_errors")?,
        failures: stepped.as_ref(),
        shape: seen.shape,
        log_rows: &seen.rows,
        // The level's own size, and where in it this page sits — the heading counts the first, the
        // control under the log reads the second.
        total: Some(seen.total),
        pager: knobs::pager(
            &selection.url(),
            &marks,
            asked.page,
            knobs::pages(seen.total, asked.log),
        )
        .as_ref(),
        // What every href on the page carries, so a click serves the URL it displays.
        suffix: &marks,
        citations: &citations(&ran),
        dev: viewer.dev,
    }))
}

/// The node the reader is on under each preset, so switching never costs them their place.
///
/// Here rather than beside the URL minting in [`knobs`]: a preset is a control the NavTree draws,
/// and the bounds module has no business knowing what a component looks like.
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

/// A page of one thread's timeline as a children log reads it: a row per turn.
pub fn turn_log(corpus: &Corpus, source: &str, rows: &[Row]) -> Result<Vec<Logged>, ViewError> {
    rows.iter()
        .map(|row| {
            let node = builders::turn_node(
                &corpus.session_id,
                source,
                row,
                &corpus.held,
                corpus.turn_text(source, row.str("turn_id")?),
            )?;
            builders::logged(Shape::Turns, node, row)
        })
        .collect()
}

/// One page of the api calls under a turn — or, at `turn_id` NULL, under a bucket.
///
/// One function for both because the two differ by that binding alone, which is the same rule the
/// NavTree's level reads by: a call answering no turn sits in its thread's bucket.
pub fn call_log(
    store: &Store,
    corpus: &Corpus,
    source: &str,
    turn_id: Option<&str>,
    asked: &Asked,
) -> Result<(Listed, Vec<Logged>, Ran), ViewError> {
    let bound: Bound = vec![
        ("session_id", corpus.session_id.as_str().into()),
        ("source", source.into()),
        ("turn_id", turn_id.map_or(Param::Absent, Into::into)),
        ("skipped", asked.skipped().into()),
        ("page_calls", asked.log.into()),
        ("log_chars", (queries::LOG_CHARS as i64).into()),
    ];
    let calls = listed(
        page_rows(store, Fragment::TurnCalls, &bound)?,
        "matched_api_calls",
    )?;
    let rows = calls
        .rows
        .iter()
        .map(|row| {
            let node = builders::call_node(&corpus.session_id, source, row, &corpus.held)?;
            builders::logged(Shape::Calls, node, row)
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok((calls, rows, vec![(Fragment::TurnCalls.stem(), bound)]))
}

/// A list of agent runs as a children log reads it: a row per run.
pub fn run_log(corpus: &Corpus, rows: &[Row]) -> Result<Vec<Logged>, ViewError> {
    rows.iter()
        .map(|row| {
            let node = builders::run_node(
                &corpus.session_id,
                row,
                &corpus.held,
                corpus.run_text(row.str("run_id")?),
            )?;
            builders::logged(Shape::Runs, node, row)
        })
        .collect()
}
