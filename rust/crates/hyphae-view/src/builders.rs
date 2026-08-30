//! How one store row becomes what a surface prints: the node it stands for.
//!
//! Ported from `src/hyphae/view/builders.py`. Every surface that names a node — a NavTree row,
//! a crumb, a children log row, a pane — calls one of these, so the title, the URL and the
//! share a reader sees are the same wherever they read it. [`crate::nodes`] holds the
//! vocabulary they build in.

use hyphae_store::row::member;
use hyphae_store::{Row, RowError, Value};

use crate::columns::Shape;
use crate::components::logs::{
    Kind as LoggedKind, Logged, LoggedCall, LoggedRun, LoggedTool, LoggedTurn,
};
use crate::components::numbers::{Compaction as CompactionNumbers, Tool, Window};
use crate::components::values::Record;
use crate::format::ELLIPSIS;
use crate::formatters::{Fields, Formatted, name_tool};
use crate::nodes::{
    COST_PLACES, Context, Kind, LEAD_SEPARATOR, Ledger, Node, Ref, SPEECH_MARK, Spend, TALLY_CHARS,
    UNATTACHED_TITLE, UNATTRIBUTED_TITLE,
};
use crate::store::ViewError;

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
pub fn rounded(cost: f64) -> f64 {
    let places = 10f64.powi(COST_PLACES);
    (cost * places).round() / places
}

/// One column a query may not have shipped at all, which is Python's `row.get`.
///
/// A children log's query and a NavTree row's query read the same builder with different
/// columns, so an absent column is a shape the caller means rather than one it forgot.
fn optional_str<'a>(row: &'a Row, column: &str) -> Result<Option<&'a str>, RowError> {
    match row.value(column) {
        Ok(_) => row.opt_str(column),
        Err(_) => Ok(None),
    }
}

fn optional_i64(row: &Row, column: &str) -> Result<Option<i64>, RowError> {
    match row.value(column) {
        Ok(_) => row.opt_i64(column),
        Err(_) => Ok(None),
    }
}

fn optional_f64(row: &Row, column: &str) -> Result<Option<f64>, RowError> {
    match row.value(column) {
        Ok(_) => row.opt_f64(column),
        Err(_) => Ok(None),
    }
}

/// The members of a `LIST` column, or nothing where the query shipped none.
pub fn members<'a>(row: &'a Row, column: &str) -> &'a [Value] {
    match row.value(column) {
        Ok(Value::List(held) | Value::Array(held)) => held,
        _ => &[],
    }
}

/// One tool call's lead and words, for the two kinds of node that print a tool's name.
///
/// Where the registry names the tool, its glyph stands in for the name and rides in the words
/// rather than the lead: a children log heads its lead in a column of its own, and a mark saying
/// which tool this is has to survive that (`Node::log_title`). Where it does not, the tool's name
/// leads the shape-driven words instead.
fn named(name: &str, fields: Fields<'_>) -> (String, String) {
    let Formatted { mark, words } = name_tool(name, fields);
    if mark.is_empty() {
        (name.to_owned(), words)
    } else {
        (String::new(), format!("{mark} {words}"))
    }
}

/// A list of tool calls named one at a time, for the surfaces that print them on one line.
///
/// An api call's row in a children log says which tools it called, and a tool call's popover says
/// what was asked for beside it. Both are lists of the rows the tools log holds, so both are named
/// through [`named`] — the lead and the words joined the way [`Node::title`] joins them, because a
/// list of tool calls that read differently from the rows it stands for would be a second answer
/// to what a call is called.
pub fn tool_titles(called: &[Value]) -> Vec<String> {
    called
        .iter()
        .map(|one| {
            let name = match member(one, "name") {
                Some(Value::Text(text) | Value::Enum(text)) => text.as_str(),
                _ => "",
            };
            let (lead, words) = named(name, Fields::of(member(one, "fields")));
            [lead, words]
                .into_iter()
                .filter(|part| !part.is_empty())
                .collect::<Vec<_>>()
                .join(LEAD_SEPARATOR)
        })
        .collect()
}

/// The line under a tool call's title in a children log: what the call was *for*.
///
/// A `Bash` row heads with the command it ran, so the description the caller wrote reads
/// underneath it. Empty where the record carried no description, and where the title is already
/// that description: a row does not print one value twice.
pub fn tool_about(name: &str, fields: Fields<'_>) -> String {
    let said = fields.text("description");
    if said.is_empty() || named(name, fields).1.contains(said) {
        return String::new();
    }
    said.to_owned()
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

/// One agent run as a node, hoisted to wherever its spawning call sits.
pub fn run_node(
    session_id: &str,
    row: &Row,
    held: &Ledger,
    described: Option<&str>,
) -> Result<Node, RowError> {
    let run_id = row.str("run_id")?.to_owned();
    // A run's id is the source its own rows carry.
    let mut node = Node::bare(Kind::Run, session_id, Some(&run_id), &run_id);
    // Which agent ran leads the name wherever no column heads it (`Node::lead`), bracketed so a
    // tree of runs reads as a column of types — and after it what the pass said the run did, else
    // the brief it was given, else nothing.
    node.lead = match row.opt_str("agent_type")? {
        Some(agent_type) => format!("[{agent_type}]"),
        None => String::new(),
    };
    node.separator = " ";
    node.words = described
        .or(row.opt_str("brief")?)
        .unwrap_or_default()
        .to_owned();
    node.spend = spend(
        row.opt_f64("cost_usd")?,
        &Ref::new(Kind::Run, Some(&run_id), &run_id),
        held,
    );
    node.unpriced_api_calls = row.i64("unpriced_api_calls")?;
    node.enriched = described.is_some();
    node.context = context(row)?;
    // A run that compacted ran its window out, whatever the last call it made says it held — and
    // how often it did is what the row's badge says, since a run's own compactions are recorded
    // on a thread the reader is not looking at.
    node.compactions = row.i64("compactions")?;
    node.maxed = node.compactions > 0;
    Ok(node)
}

/// How many of each tool an api call invoked, in the order each tool first appears.
///
/// The half of an api call's title that survives every cut (`Node::tail`), so it is bounded here
/// rather than by the surface: a group that will not fit is dropped whole and the drop marked,
/// because `+2(Ba…` counts calls of a tool the reader cannot name.
fn tally(names: &[&str], chars: usize) -> String {
    let mut counted: Vec<(&str, i64)> = Vec::new();
    for name in names {
        match counted.iter_mut().find(|(held, _)| held == name) {
            Some((_, made)) => *made += 1,
            None => counted.push((name, 1)),
        }
    }
    let mut tallied = String::new();
    for (name, made) in counted {
        let group = format!(" +{made}({name})");
        if tallied.chars().count() + group.chars().count() > chars {
            return tallied + ELLIPSIS;
        }
        tallied += &group;
    }
    tallied
}

/// One api call as a node: what it said, else the tools it called, else the model.
pub fn call_node(
    session_id: &str,
    source: &str,
    row: &Row,
    held: &Ledger,
) -> Result<Node, RowError> {
    let api_call_id = row.str("api_call_id")?.to_owned();
    // What the call went on to do, where the query that read it fetched that: the tool names in
    // the order they were called, and the first call's own title. A children log's query fetches
    // neither — its rows are named by the model, and its node is only ever a link.
    let tools = row
        .value("tools")
        .ok()
        .filter(|held| !matches!(held, Value::Null));
    let names: Vec<&str> = match tools.and_then(|held| member(held, "names")) {
        Some(Value::List(held) | Value::Array(held)) => held
            .iter()
            .filter_map(|one| match one {
                Value::Text(text) | Value::Enum(text) => Some(text.as_str()),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    };
    // A call that answered with tool calls and no text has nothing to quote, so it is named by
    // what it did: the tool it called first, that call's own name, and a count of the rest. One
    // that neither spoke nor called a tool is named by the model that answered.
    let spoken = optional_str(row, "text_head")?.filter(|said| !said.is_empty());
    let silent = spoken.is_none() && !names.is_empty();
    let first = tools.and_then(|held| member(held, "first"));
    // Named through the same derivation the tool row under it takes, so the glyph a reader picks
    // a `Read` out of a tree by leads here too ([`named`]).
    let (lead, called) = if silent {
        let name = match first.and_then(|held| member(held, "name")) {
            Some(Value::Text(text) | Value::Enum(text)) => text.as_str(),
            _ => "",
        };
        named(
            name,
            Fields::of(first.and_then(|held| member(held, "fields"))),
        )
    } else {
        (String::new(), String::new())
    };
    let mut node = Node::bare(Kind::Call, session_id, Some(source), &api_call_id);
    node.lead = lead;
    // Marked where the words are speech, including on a call that also ran tools: what the model
    // said is the one thing on the row nothing else on the page says.
    node.words = if silent {
        called
    } else if let Some(said) = spoken {
        format!("{SPEECH_MARK} {said}")
    } else {
        row.opt_str("model")?.unwrap_or_default().to_owned()
    };
    node.tail = if silent {
        tally(&names[1..], TALLY_CHARS)
    } else {
        String::new()
    };
    node.spend = spend(
        Some(row.opt_f64("cost_usd")?.unwrap_or(0.0)),
        &Ref::new(Kind::Call, Some(source), &api_call_id),
        held,
    );
    node.unpriced_api_calls = row.i64("unpriced_api_calls")?;
    node.context = context(row)?;
    Ok(node)
}

/// One tool call as a node. No cost of its own: what it took is the api call's.
///
/// Except a ⚒ row, which asked for a run and is charged what the api call holding it cost — the
/// nearest thing the store prices to what the reader is looking at. Costless wherever no run hangs
/// under it, which is every other tool there is.
pub fn tool_node(
    session_id: &str,
    source: &str,
    row: &Row,
    held: &Ledger,
) -> Result<Node, RowError> {
    let tool_call_id = row.str("tool_call_id")?.to_owned();
    let (lead, words) = named(row.str("name")?, Fields::read(row, "fields"));
    // `view_nav_tree_tools.sql` is the one query that reads the call's price, because the NavTree
    // is the one surface that draws the badge. A row that asked for nothing takes neither the
    // price nor the mark saying our table could not complete it.
    let node_ref = Ref::new(Kind::Tool, Some(source), &tool_call_id);
    let asked = held.below(&node_ref) != 0.0;
    let spent = optional_f64(row, "call_cost_usd")?.unwrap_or(0.0);
    let mut node = Node::bare(Kind::Tool, session_id, Some(source), &tool_call_id);
    // The tool's name leads, and its title says which call of that tool this is — a page of twenty
    // `Read` rows otherwise says twenty times that a file was read ([`named`]).
    node.lead = lead;
    node.words = words;
    node.spend = spend(asked.then_some(spent), &node_ref, held);
    node.unpriced_api_calls = if asked {
        optional_i64(row, "unpriced_api_calls")?.unwrap_or(0)
    } else {
        0
    };
    // Every query a tool node is built from selects it, and the column is NOT NULL, so a row
    // arriving without it is a query that forgot rather than a call that may have failed.
    node.is_error = row.bool("is_error")?;
    Ok(node)
}

/// One compaction as a node. A stop on the walk, and a node with no spend of its own.
pub fn compaction_node(session_id: &str, source: &str, row: &Row) -> Result<Node, RowError> {
    let compaction_id = row.str("compaction_id")?.to_owned();
    let mut node = Node::bare(Kind::Compaction, session_id, Some(source), &compaction_id);
    node.words = format!("compaction · {}", row.str("trigger")?);
    // The one node whose bar reads backwards: what it freed, between the two fills it recorded,
    // against the window of the call its thread made nearest to it.
    node.context = context(row)?;
    Ok(node)
}

/// One thread's calls that answer no turn, as the timeline's own cursorless row reads them.
pub fn unattributed_node(
    session_id: &str,
    source: &str,
    row: &Row,
    held: &Ledger,
) -> Result<Node, RowError> {
    let mut node = Node::bare(Kind::Unattributed, session_id, Some(source), source);
    node.words = UNATTRIBUTED_TITLE.to_owned();
    node.spend = spend(
        row.opt_f64("cost_usd")?,
        &Ref::new(Kind::Unattributed, Some(source), source),
        held,
    );
    node.unpriced_api_calls = row.i64("unpriced_api_calls")?;
    Ok(node)
}

/// The session's runs no spawning call resolved, gathered under one node.
///
/// Spans every thread rather than sitting on one: what makes a run unattached is that nothing says
/// which thread spawned it, so the bucket hangs off the session. One number and not two: its own
/// is already the sum of the runs it gathers, so a subtree half over the same rows would say the
/// same thing twice.
pub fn unattached_node(session_id: &str, rows: &[&Row], held: &Ledger) -> Result<Node, RowError> {
    let mut cost = 0.0;
    let mut unpriced = 0;
    for row in rows {
        cost += row.f64("cost_usd")?;
        unpriced += row.i64("unpriced_api_calls")?;
    }
    let cost = rounded(cost);
    let mut node = Node::bare(Kind::Unattached, session_id, None, session_id);
    node.words = UNATTACHED_TITLE.to_owned();
    node.spend = Spend {
        own: Some(cost),
        total: None,
        share: share(Some(cost), held.whole),
        total_share: None,
    };
    node.unpriced_api_calls = unpriced;
    Ok(node)
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

/// One row of a children log: the node its wide column links to, beside what the row prints.
///
/// Keyed by the log's shape rather than the node's kind, because the shape is what decides the
/// columns the row has to fill. [`Shape::None`] lists nothing, so it has no row to build.
pub fn logged(shape: Shape, node: Node, row: &Row) -> Result<Logged, ViewError> {
    let cells = match shape {
        Shape::Turns => LoggedKind::Turn(LoggedTurn {
            turn_index: row.i64("turn_index")?,
            api_calls: row.i64("api_calls")?,
            tool_calls: row.i64("tool_calls")?,
        }),
        Shape::Calls => LoggedKind::Call(LoggedCall {
            call_index: row.i64("call_index")?,
            model: row.opt_str("model")?.map(str::to_owned),
            text_head: row.opt_str("text_head")?.map(str::to_owned),
            tool_calls: row.i64("tool_calls")?,
            // The words rather than the rows: naming a tool call is [`crate::formatters`]'s, so
            // the query ships the fields and this composes them.
            called: tool_titles(members(row, "called_tools")).join(", "),
            text_chars: row.i64("text_chars")?,
        }),
        Shape::Tools => LoggedKind::Tool(LoggedTool {
            tool_index: row.i64("tool_index")?,
            name: row.opt_str("name")?.map(str::to_owned),
            about: tool_about(
                row.opt_str("name")?.unwrap_or_default(),
                Fields::read(row, "fields"),
            ),
            is_error: row.bool("is_error")?,
            result_chars: row.opt_i64("result_chars")?,
        }),
        Shape::Runs => LoggedKind::Run(LoggedRun {
            agent_type: row.opt_str("agent_type")?.map(str::to_owned),
            tool_errors: row.i64("tool_errors")?,
        }),
        Shape::None => {
            return Err(ViewError::Shape(
                "A log of no shape lists no rows.".to_owned(),
            ));
        }
    };
    Ok(Logged {
        node,
        started_at: row.opt_timestamp("started_at")?,
        cells,
    })
}

/// A popover's readings for a node made of api calls, off the row `view_numbers` answered.
pub fn window_numbers(row: &Row) -> Result<Window, RowError> {
    Ok(Window {
        model: row.opt_str("model")?.map(str::to_owned),
        fill: row.opt_i64("fill")?,
        window_tokens: row.opt_i64("window_tokens")?,
        added: row.opt_i64("added")?,
        cost_usd: row.opt_f64("cost_usd")?,
        api_calls: row.opt_i64("api_calls")?,
        unpriced_api_calls: row.opt_i64("unpriced_api_calls")?,
    })
}

/// A popover's readings for one tool call, off the row `view_numbers_tool` answered.
///
/// The siblings are named here rather than in the query: what a tool call is called is
/// [`crate::formatters`]'s, and the query ships the fields each name is composed of.
pub fn tool_numbers(row: &Row) -> Result<Tool, RowError> {
    Ok(Tool {
        input_chars: row.opt_i64("input_chars")?,
        result_chars: row.opt_i64("result_chars")?,
        offload_file: row.opt_str("offload_file")?.map(str::to_owned),
        spawned_run: row.bool("spawned_run")?,
        siblings: tool_titles(members(row, "siblings")),
        siblings_cut: row.i64("siblings_cut")?,
    })
}

/// A popover's readings for one compaction, off `view_numbers_compaction`'s row.
pub fn compaction_numbers(row: &Row) -> Result<CompactionNumbers, RowError> {
    Ok(CompactionNumbers {
        pre_tokens: row.opt_i64("pre_tokens")?,
        post_tokens: row.opt_i64("post_tokens")?,
        freed: row.opt_i64("freed")?,
        trigger: row.opt_str("trigger")?.map(str::to_owned),
    })
}

/// One archived record as its fragment prints it, off the record value query's row.
pub fn record_value(row: &Row, citation: String) -> Result<Record, RowError> {
    Ok(Record {
        line_no: row.i64("line_no")?,
        kind: row.str("type")?.to_owned(),
        uuid: row.opt_str("uuid")?.map(str::to_owned),
        timestamp: row.opt_timestamp("timestamp")?,
        raw_chars: row.opt_i64("raw_chars")?,
        raw: row.str("raw")?.to_owned(),
        citation,
    })
}
