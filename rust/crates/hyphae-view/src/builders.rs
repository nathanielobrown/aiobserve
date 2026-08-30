//! How one store row becomes what a surface prints: the node it stands for.
//!
//! Ported from `src/hyphae/view/builders.py`. Every surface that names a node — a NavTree row,
//! a crumb, a children log row, a pane — calls one of these, so the title, the URL and the
//! share a reader sees are the same wherever they read it. [`crate::nodes`] holds the
//! vocabulary they build in.

use hyphae_store::{Row, RowError};

use crate::components::node_body::{Facts, SessionFacts};
use crate::nodes::{COST_PLACES, Context, Kind, Ledger, Node, Ref, Spend};

/// Where the row says its node left the window, or `None` where it says nothing.
///
/// A level of nodes that end on no window leaves the column out, and a node whose model our
/// table has no window for answers NULL inside it: both are a bar the NavTree does not draw,
/// the way a model we cannot price is a cost it does not print.
pub fn context(row: &Row) -> Result<Option<Context>, RowError> {
    if row.value("context").is_err() {
        return Ok(None);
    }
    let (Some(fill), Some(window)) = (
        row.member_i64("context", "fill")?,
        row.member_i64("context", "window")?,
    ) else {
        return Ok(None);
    };
    Ok(Some(Context {
        fill,
        added: row.member_i64("context", "added")?,
        window,
        // Only the query behind a turn returns one: only a turn's own growth is worth reading
        // against the context its session opened on.
        base: row.member_i64("context", "base")?,
    }))
}

/// A node's share of the session's spend, or `None` when there is no share to speak of.
fn share(cost: Option<f64>, whole: f64) -> Option<f64> {
    match cost {
        Some(cost) if whole != 0.0 => Some(cost / whole),
        _ => None,
    }
}

/// One node's badge: what it cost, and — where runs hang under it — what they cost with it.
fn spend(cost: Option<f64>, node: &Ref, held: &Ledger) -> Spend {
    let under = held.below(node);
    let total = match cost {
        Some(cost) if under != 0.0 => Some(rounded(cost + under)),
        _ => None,
    };
    Spend {
        own: cost,
        total,
        share: share(cost, held.whole),
        total_share: share(total, held.whole),
    }
}

/// A cost put back where the store hands them out, four decimals on.
fn rounded(cost: f64) -> f64 {
    let places = 10f64.powi(COST_PLACES);
    (cost * places).round() / places
}

/// The root of every NavTree: the session everything under it was recorded in.
///
/// The one node whose halves are read the other way round. What a session spent is the whole of
/// its subtree — there is nothing above it to gather it — so its own half is that less every run
/// under it, which is its main thread.
pub fn session_node(
    header: &Row,
    held: &Ledger,
    described: Option<&str>,
) -> Result<Node, RowError> {
    let session_id = header.str("session_id")?.to_owned();
    let whole = header.opt_f64("cost_usd")?.unwrap_or(0.0);
    let under = held.below(&Ref::new(Kind::Session, None, &session_id));
    let main = rounded(whole - under);
    let mut node = Node::bare(Kind::Session, &session_id, None, &session_id);
    // What the enrichment pass said it was, else the title Claude Code gave it, else the id —
    // which is what a reader pasted to arrive here, so the row is never blank.
    node.words = described
        .or(header.opt_str("title")?)
        .unwrap_or(&session_id)
        .to_owned();
    node.spend = Spend {
        own: Some(main),
        total: (under != 0.0).then_some(whole),
        share: share(Some(main), held.whole),
        total_share: (under != 0.0 && whole != 0.0).then_some(1.0),
    };
    node.unpriced_api_calls = header.i64("unpriced_api_calls")?;
    node.enriched = described.is_some();
    node.context = context(header)?;
    Ok(node)
}

/// One turn as a node, from a NavTree row, a timeline row, or the turn's own header.
pub fn turn_node(
    session_id: &str,
    source: &str,
    row: &Row,
    held: &Ledger,
    described: Option<&str>,
) -> Result<Node, RowError> {
    let turn_id = row.str("turn_id")?.to_owned();
    let mut node = Node::bare(Kind::Turn, session_id, Some(source), &turn_id);
    node.words = match described {
        Some(said) => said.to_owned(),
        None => turn_title(row)?,
    };
    node.spend = spend(
        row.opt_f64("cost_usd")?,
        &Ref::new(Kind::Turn, Some(source), &turn_id),
        held,
    );
    node.unpriced_api_calls = row.i64("unpriced_api_calls")?;
    node.enriched = described.is_some();
    node.context = context(row)?;
    Ok(node)
}

/// What to call a turn: the command it ran and what followed, else the prompt as typed.
///
/// The prompt is last because a slash command's prompt is the `<command-…>` wrapper Claude Code
/// put around it, which says nothing in the width of a NavTree.
fn turn_title(row: &Row) -> Result<String, RowError> {
    if let Some(name) = row.opt_str("command_name")? {
        let args = row.opt_str("command_args")?.unwrap_or_default();
        return Ok(format!("{name} {args}").trim().to_owned());
    }
    // The store declares a turn's prompt NOT NULL (`export/duckdb.py`), so this arm always has
    // something to say, even when what it says is the empty string.
    Ok(row.str("prompt")?.to_owned())
}

/// Where each run's spend lands, walked once for a page. `view_runs.sql` holds the edges.
///
/// A run's cost is charged to every node it hangs under: the ⚒ tool call that asked for it, the
/// api call that made that tool call, the turn that call answers, each run above it, and the
/// session. Which makes `total >= own` true by construction, and makes a level of parallel
/// spawns sum past the call that made them, because one api call is the nearest priced thing to
/// each of them (`docs/viewer.md`).
///
/// The unattached bucket is left out on purpose: its own is already the sum of the loose runs it
/// gathers, so a second total over the same rows would say the same thing twice.
pub fn ledger(session_id: &str, whole: f64, runs: &[Row]) -> Result<Ledger, RowError> {
    let mut held = Ledger {
        whole,
        under: std::collections::HashMap::new(),
    };
    let by_id: std::collections::HashMap<&str, &Row> = runs
        .iter()
        .map(|run| Ok((run.str("run_id")?, run)))
        .collect::<Result<_, RowError>>()?;
    for run in runs {
        let own = run.f64("cost_usd")?;
        // Every run is under the session, whether or not the transcript placed it anywhere else.
        charge(&mut held, Ref::new(Kind::Session, None, session_id), own);
        let mut at = Some(*by_id.get(run.str("run_id")?).expect("the run is its own"));
        let mut climbed: std::collections::HashSet<String> = std::collections::HashSet::new();
        while let Some(here) = at {
            let here_id = here.str("run_id")?.to_owned();
            if !climbed.insert(here_id) {
                break;
            }
            for asked in asked_for(here)? {
                charge(&mut held, asked, own);
            }
            // Up to the run this one hangs under: the parent the transcript names, else the
            // thread the spawning call was made from, which is a run's id wherever it is not the
            // session's own. A run already climbed ends the walk rather than looping.
            let parent = here.opt_str("parent_agent_id")?;
            let above = match parent.filter(|id| by_id.contains_key(id)) {
                Some(id) => Some(id),
                None => here.opt_str("spawn_source")?,
            };
            at = above.and_then(|id| by_id.get(id).copied());
            if let Some(above) = at {
                let above_id = above.str("run_id")?;
                charge(
                    &mut held,
                    Ref::new(Kind::Run, Some(above_id), above_id),
                    own,
                );
            }
        }
    }
    Ok(held)
}

/// Add one run's spend to what hangs under a node.
fn charge(held: &mut Ledger, node: Ref, cost: f64) {
    *held.under.entry(node).or_insert(0.0) += cost;
}

/// The nodes that asked for one run, on the thread that asked: its ⚒ call, and up from there.
///
/// Nothing where the spawning call resolved to nothing — an unattached run hangs off no tool
/// call, no api call and no turn, which is the whole definition of one.
fn asked_for(run: &Row) -> Result<Vec<Ref>, RowError> {
    let Some(source) = run.opt_str("spawn_source")? else {
        return Ok(Vec::new());
    };
    let mut asked = vec![
        Ref::new(Kind::Tool, Some(source), run.str("tool_use_id")?),
        Ref::new(Kind::Call, Some(source), run.str("spawn_call_id")?),
    ];
    // The turn that call answers, or — where it answers none — that thread's own bucket.
    asked.push(match run.opt_str("spawn_turn_id")? {
        Some(turn_id) => Ref::new(Kind::Turn, Some(source), turn_id),
        None => Ref::new(Kind::Unattributed, Some(source), source),
    });
    Ok(asked)
}

/// The facts a node's body prints, read off the row its header query answered.
///
/// Where a store row stops being a bag of columns: past here a body reads named fields of a
/// type, so a query that dropped one fails here rather than printing a dash under its label.
/// Total over [`Kind`] once 3b adds the other six arms.
pub fn node_facts(node: &Node, row: &Row) -> Result<Facts, RowError> {
    match node.kind {
        Kind::Session => Ok(Facts::Session(SessionFacts {
            session_id: row.str("session_id")?.to_owned(),
            git_branch: row.opt_str("git_branch")?.map(str::to_owned),
            version: row.opt_str("version")?.map(str::to_owned),
            entrypoint: row.opt_str("entrypoint")?.map(str::to_owned),
            started_at: row.opt_timestamp("started_at")?,
            wall_ms: row.opt_i64("wall_ms")?,
            active_ms: row.opt_i64("active_ms")?,
            turns: row.i64("turns")?,
            api_calls: row.i64("api_calls")?,
            tool_calls: row.i64("tool_calls")?,
            tool_errors: row.i64("tool_errors")?,
            agent_runs: row.i64("agent_runs")?,
            compactions: row.i64("compactions")?,
            cost_usd: row.opt_f64("cost_usd")?,
            unpriced_api_calls: row.i64("unpriced_api_calls")?,
            output_tokens: row.i64("output_tokens")?,
            skills: row
                .strings("skills")?
                .iter()
                .map(|s| (*s).to_owned())
                .collect(),
            skills_cut: row.i64("skills_cut")?,
            pr_urls: row
                .strings("pr_urls")?
                .iter()
                .map(|s| (*s).to_owned())
                .collect(),
            pr_urls_cut: row.i64("pr_urls_cut")?,
        })),
        kind => unimplemented!("stage 3b: the facts of a {kind}"),
    }
}
