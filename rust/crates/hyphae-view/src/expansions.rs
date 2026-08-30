//! A child opened in place: the body a log row's View button swaps in, and the rows under it.
//!
//! Ported from `src/hyphae/view/expansions.py`. An expansion is a node's own body without its
//! page — the same title, facts and details, read from the same header queries — so a reader can
//! open a child without losing the log they are reading. What it does not open is another level:
//! a count and a link stand in for one, except where the level below opens nothing further
//! (`docs/viewer.md`).

use hyphae_store::{Row, RowError, queries};

use crate::browse::{Asked, PageError, header_bound};
use crate::builders;
use crate::citation::citations;
use crate::columns::Shape;
use crate::components::nav_tree::NavTreeRow;
use crate::components::node_body::Expansion;
use crate::components::{Markup, nav_tree as nav_rows, node_body};
use crate::enrichment::described;
use crate::knobs::{self, BadAsk};
use crate::nav_tree::{Bound, Corpus, Ran, children, spread, windowed};
use crate::nodes::{Kind, Ledger, Node, Ref};
use crate::store::{Fragment, Page, Query, page_rows};
use crate::viewer::Viewer;

/// How one kind builds its node from its own header row.
///
/// The description is the last argument because only a turn has one: the other two ignore it,
/// which is what lets one signature serve every kind a log lists.
type Build = fn(&str, &str, &Row, Option<&str>) -> Result<Node, RowError>;

/// The level an expansion lists under the body instead of only counting.
///
/// One kind has one: an api call's expansion lists the tools it called, because a tool call opens
/// nothing further — the rows come with no opener on them, so the level a reader opens is still
/// the last one. `size` is what the query calls its page size, and `build` turns one row into the
/// node its row links to.
struct Listing {
    query: Fragment,
    size: &'static str,
    build: Build,
}

/// How one kind answers an expansion: the header it reads, and what it says is under it.
///
/// `children` is the column counting what the full view would have listed, and `shape` names those
/// children the way the full view's log heading does. A kind with neither ends the NavTree. Where
/// `listed` is `None` the count and a link stand in for the list.
struct Body {
    page: Page,
    /// The binding the header query takes the node's id as.
    keyed: &'static str,
    build: Build,
    shape: Shape,
    children: Option<&'static str>,
    /// Whether a pass can have described this kind, and so whether the title may be the model's.
    described: bool,
    listed: Option<Listing>,
}

fn turn_body(
    session_id: &str,
    source: &str,
    row: &Row,
    text: Option<&str>,
) -> Result<Node, RowError> {
    builders::turn_node(session_id, source, row, &Ledger::none(), text)
}

fn call_body(session_id: &str, source: &str, row: &Row, _: Option<&str>) -> Result<Node, RowError> {
    builders::call_node(session_id, source, row, &Ledger::none())
}

fn tool_body(session_id: &str, source: &str, row: &Row, _: Option<&str>) -> Result<Node, RowError> {
    builders::tool_node(session_id, source, row, &Ledger::none())
}

/// How a kind a children log lists answers an expansion, or nothing where none of them does.
///
/// Every such kind except the run: a run's URL carries its id where the others carry a thread, so
/// it has a mount of its own.
fn bodied(kind: Kind) -> Option<Body> {
    match kind {
        Kind::Turn => Some(Body {
            page: Page::TurnHeader,
            keyed: "turn_id",
            build: turn_body,
            shape: Shape::Calls,
            children: Some("api_calls"),
            described: true,
            listed: None,
        }),
        Kind::Call => Some(Body {
            page: Page::CallHeader,
            keyed: "api_call_id",
            build: call_body,
            shape: Shape::Tools,
            children: Some("tool_calls"),
            described: false,
            listed: Some(Listing {
                query: Fragment::CallTools,
                size: "page_tools",
                build: tool_body,
            }),
        }),
        Kind::Tool => Some(Body {
            page: Page::ToolHeader,
            keyed: "tool_call_id",
            build: tool_body,
            shape: Shape::None,
            children: None,
            described: false,
            listed: None,
        }),
        _ => None,
    }
}

/// One node's body alone, the way an expansion in someone else's log mounts it.
///
/// The same component the full view's pane renders through, so the two cannot drift apart; where
/// the page has the crumbs and prev/next, this has the way to the node's own page. `under` is the
/// level the expansion lists, empty for every kind that stops at the count. `marks` is the knobs
/// the page around the expansion was read under, which every link out of here carries on.
fn expanded(
    node: &Node,
    row: &Row,
    shape: Shape,
    children: Option<i64>,
    marks: &str,
    ran: &Ran,
    under: &[crate::components::logs::Logged],
) -> Result<Markup, PageError> {
    Ok(node_body::expansion(&Expansion {
        node,
        facts: &builders::node_facts(node, row)?,
        suffix: marks,
        shape,
        children,
        rows: under,
        citations: &citations(ran),
    }))
}

/// The body of a turn, an api call, or a tool call, for an expansion in its parent.
///
/// The knobs come along for the links this serves, not for what it reads: the mount carries the
/// page's own query string so a reader who opens an expansion and clicks through it keeps the
/// preset and the sizes they were reading under.
pub fn thread_body(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    kind: &str,
    node_id: &str,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let Some(shaped) = Kind::spelled(kind).and_then(bodied) else {
        return Err(PageError::Missing(
            "No expansion is served for that kind of node.".to_owned(),
        ));
    };
    let bound: Bound = vec![
        ("session_id", session_id.into()),
        ("source", source.into()),
        (shaped.keyed, node_id.into()),
        ("head_chars", (queries::HEADER_CHARS as i64).into()),
        // A body renders facts and no fat value, so the columns a pane would preview are read at
        // the width the title is cut from rather than at the reader's `?detail=`.
        ("detail_chars", (queries::HEADER_CHARS as i64).into()),
    ];
    let keyed: Bound = vec![("session_id", session_id.into()), ("source", source.into())];
    // The level the expansion lists, where its kind lists one: the first page of it, at the size
    // the reader is reading logs under. Which page is not a question an expansion asks — the way
    // past the first is the link to the node's own page.
    let mut level: Bound = vec![
        ("session_id", session_id.into()),
        ("source", source.into()),
        (shaped.keyed, node_id.into()),
        ("skipped", 0i64.into()),
        ("log_chars", (queries::LOG_CHARS as i64).into()),
    ];
    if let Some(listed) = &shaped.listed {
        level.push((listed.size, asked.log.into()));
    }
    let store = viewer.reader.connect()?;
    let rows = page_rows(&store, shaped.page, &bound)?;
    let Some(row) = rows.first() else {
        return Err(PageError::Missing(
            "No node with that id is in this thread.".to_owned(),
        ));
    };
    let under = match &shaped.listed {
        Some(listed) => page_rows(&store, listed.query, &level)?
            .iter()
            .map(|item| {
                let node = (listed.build)(session_id, source, item, None)?;
                builders::logged(shaped.shape, node, item)
            })
            .collect::<Result<Vec<_>, _>>()?,
        None => vec![],
    };
    // The title is the model's words wherever a pass reached the node, exactly as the log row
    // that opened this expansion has it.
    let describes = if shaped.described {
        Some(described(&store, session_id, source)?)
    } else {
        None
    };
    drop(store);
    let told = describes.as_ref().and_then(|said| said.turn(node_id));
    let mut ran: Ran = vec![(shaped.page.stem(), bound)];
    if let Some(listed) = &shaped.listed {
        ran.push((listed.query.stem(), level));
    }
    if describes.as_ref().is_some_and(|said| said.queried) {
        ran.push((Page::Enrichment.stem(), keyed));
    }
    let node = (shaped.build)(session_id, source, row, told)?;
    let counted = match shaped.children {
        Some(column) => row.opt_i64(column)?,
        None => None,
    };
    expanded(
        &node,
        row,
        shaped.shape,
        counted,
        &asked.suffix(),
        &ran,
        &under,
    )
}

/// One agent run's body. Its own mount: a run's URL carries its id where a thread goes.
pub fn run_body(
    viewer: &Viewer,
    session_id: &str,
    run_id: &str,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let bound: Bound = vec![
        ("session_id", session_id.into()),
        ("run_id", run_id.into()),
        ("head_chars", (queries::HEADER_CHARS as i64).into()),
        ("detail_chars", (queries::HEADER_CHARS as i64).into()),
    ];
    let keyed: Bound = vec![("session_id", session_id.into()), ("source", run_id.into())];
    let store = viewer.reader.connect()?;
    let rows = page_rows(&store, Page::RunHeader, &bound)?;
    let Some(row) = rows.first() else {
        return Err(PageError::Missing(
            "No agent run with that id is in this session.".to_owned(),
        ));
    };
    // A run's id is the thread its own rows carry, so it is what the pass keyed on too.
    let describes = described(&store, session_id, run_id)?;
    drop(store);
    let mut ran: Ran = vec![(Page::RunHeader.stem(), bound)];
    if describes.queried {
        ran.push((Page::Enrichment.stem(), keyed));
    }
    let node = builders::run_node(session_id, row, &Ledger::none(), describes.run(run_id))?;
    expanded(
        &node,
        row,
        Shape::Turns,
        row.opt_i64("turns")?,
        &asked.suffix(),
        &ran,
        &[],
    )
}

/// The children one level's window left out: the rows a `+N more` row stands in for.
///
/// The NavTree draws a window on a level and a tail row saying how many it left out; this serves
/// the rest of that level, at the depth the NavTree had reached, so a click can stand them where
/// the tail row stood. `opened` is the key of the child the open path descends through, which the
/// window keeps wherever in the level it sits — the page sent it so that the two halves of one
/// split agree, and this is the half that must not repeat it.
///
/// `thread` is the reader's, not the level's: the enrichment is keyed by thread, so a page draws a
/// turn of any other thread by its prompt, and a row served here has to read the way the page
/// beside it would have drawn it.
///
/// Unbounded on purpose: what comes back is a level less a window, so a node with ten thousand
/// children answers with ten thousand rows.
fn spilled(
    viewer: &Viewer,
    session_id: &str,
    at: &Ref,
    spill: &Spill,
    asked: &Asked,
) -> Result<Markup, PageError> {
    if spill.depth < 1 || spill.depth > knobs::DEPTH {
        return Err(PageError::Bad(BadAsk(format!(
            "A NavTree row sits between depth 1 and {}.",
            knobs::DEPTH
        ))));
    }
    let store = viewer.reader.connect()?;
    let bound = header_bound(session_id);
    let head = page_rows(&store, Page::SessionHeader, &bound)?;
    let Some(header) = head.first() else {
        return Err(PageError::Missing(
            "No session with that id is in this store.".to_owned(),
        ));
    };
    let runs = page_rows(
        &store,
        Page::Runs,
        &[
            ("session_id", session_id.into()),
            ("chip_chars", (queries::NAV_CHARS as i64).into()),
        ],
    )?;
    let corpus = Corpus {
        session_id: session_id.to_owned(),
        held: builders::ledger(
            session_id,
            header.opt_f64("cost_usd")?.unwrap_or(0.0),
            &runs,
        )?,
        runs,
        described: described(&store, session_id, &spill.thread)?,
        source: spill.thread.clone(),
    };
    let opened = spill.opened.as_deref().unwrap_or_default();
    let level = children(
        &store,
        &corpus,
        at,
        asked.nav,
        (!opened.is_empty()).then_some(opened),
    )?;
    drop(store);
    // Each row shut, and under it whatever a shut row stands: the runs it hides come back with it,
    // the way the page's own rows carry them. None of them is a step of the open path — the cap
    // keeps the child the path descends through inside the window, and this fetch is what it left
    // out.
    //
    // They arrive with no wrapper of their own: inside the list the tail row was in, each inherits
    // the NavTree's swap from `#nav-tree-rows` like every other row.
    let mut rows: Vec<NavTreeRow> = Vec::new();
    for node in windowed(level.nodes, asked.kin as usize, &[opened.to_owned()]).cut {
        let under = spread(&corpus, &node, spill.depth + 1)?;
        rows.push(NavTreeRow::node(node, spill.depth, false, false));
        rows.extend(under);
    }
    Ok(nav_rows::lines(&rows, &asked.suffix(), &spill.thread))
}

/// Where a spilled level lands, which only the row that asked for it knows.
///
/// Neither `thread` nor `depth` has a default: these rows are going somewhere in a NavTree that
/// already exists, and only that row knows where they land and which thread's descriptions the
/// NavTree around them was drawn by.
#[derive(serde::Deserialize)]
pub struct Spill {
    pub thread: String,
    pub depth: usize,
    pub opened: Option<String>,
}

/// The rest of one level, under a node recorded on a thread.
pub fn node_kin(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    kind: &str,
    node_id: &str,
    spill: &Spill,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let Some(kind) = Kind::spelled(kind) else {
        return Err(PageError::Missing(
            "No level is served for that kind of node.".to_owned(),
        ));
    };
    spilled(
        viewer,
        session_id,
        &Ref::new(kind, Some(source), node_id),
        spill,
        asked,
    )
}

/// The rest of one level, under a node that carries no thread of its own.
///
/// The session, an agent run, and the unattached bucket: their URLs have no room for the thread
/// the node was recorded on, and the level does not need it — each builder reads the thread out of
/// the node it hangs under.
pub fn loose_kin(
    viewer: &Viewer,
    session_id: &str,
    kind: &str,
    node_id: &str,
    spill: &Spill,
    asked: &Asked,
) -> Result<Markup, PageError> {
    let Some(kind) = Kind::spelled(kind) else {
        return Err(PageError::Missing(
            "No level is served for that kind of node.".to_owned(),
        ));
    };
    spilled(
        viewer,
        session_id,
        &Ref::new(kind, None, node_id),
        spill,
        asked,
    )
}
