//! One node as the pane reads it, a shape of facts per kind.
//!
//! What is here is the node itself: the NavTree around it, the crumbs above it and the log under
//! it belong to the page, and the whole of a value it only previews is its own fetch. A pane and
//! a NavTree row that disagreed here would tell a reader two stories about one node.
//!
//! The facts a kind reads are a type rather than a store row, so a query that stopped returning
//! a column is a type error rather than a fact that quietly prints a dash. Python spells that as
//! a `NamedTuple` per kind under one union; here it is one struct per kind under [`Facts`], and
//! [`facts`] is the single `match` over them — the arm is what a kind costs to add.

use chrono::{DateTime, Utc};
use hypertext::prelude::*;

use crate::citation::Cited;
use crate::columns::Shape;
use crate::components::logs::{Logged, log};
use crate::components::{Markup, citation, parts};
use crate::format as fmt;
use crate::nodes::{Node, run_url, spanned};
use crate::urls;
use crate::{cuts, render};

/// The session everything else was recorded in: where it ran, and what it came to.
///
/// Neither the name it was recorded under nor the directory it ran in is here: the heading above
/// prints the one and the crumb above that links the other, and a fact is for what nothing else
/// on the page says.
pub struct SessionFacts {
    pub session_id: String,
    pub git_branch: Option<String>,
    pub version: Option<String>,
    pub entrypoint: Option<String>,
    pub started_at: Option<DateTime<Utc>>,
    pub wall_ms: Option<i64>,
    pub active_ms: Option<i64>,
    pub turns: i64,
    pub api_calls: i64,
    pub tool_calls: i64,
    pub tool_errors: i64,
    pub agent_runs: i64,
    pub compactions: i64,
    pub cost_usd: Option<f64>,
    pub unpriced_api_calls: i64,
    pub output_tokens: i64,
    /// The skills the session loaded, and the pull requests its commands touched. Each grows
    /// with the session, so the query cuts it and says how many it left: a pane is the one part
    /// of a page no size a reader types bounds.
    pub skills: Vec<String>,
    pub skills_cut: i64,
    pub pr_urls: Vec<String>,
    pub pr_urls_cut: i64,
}

/// One turn: what it was asked, when, and what answering it took.
///
/// `command_name` is set where the turn was typed as a slash command — its prompt is the
/// `<command-…>` wrapper Claude Code expanded it into, and what a reader is looking for is the
/// command.
pub struct TurnFacts {
    pub turn_id: String,
    pub command_name: Option<String>,
    pub turn_index: i64,
    pub started_at: Option<DateTime<Utc>>,
    pub replayed: bool,
    pub api_calls: i64,
    pub tool_calls: i64,
    pub tool_errors: i64,
    pub cost_usd: Option<f64>,
    pub unpriced_api_calls: i64,
}

/// One agent run: the definition it ran, where it was spawned, and what its thread came to.
pub struct RunFacts {
    pub run_id: String,
    pub agent_type: Option<String>,
    pub model: Option<String>,
    pub spawn_depth: i64,
    pub is_fork: bool,
    pub started_at: Option<DateTime<Utc>>,
    pub wall_ms: Option<i64>,
    pub turns: i64,
    pub api_calls: i64,
    pub tool_calls: i64,
    pub tool_errors: i64,
    pub compactions: i64,
    pub cost_usd: Option<f64>,
    pub unpriced_api_calls: i64,
    pub output_tokens: i64,
}

/// One api call: the request that was made, and what came back.
pub struct CallFacts {
    pub call_index: i64,
    pub model: Option<String>,
    pub fallback_from: Option<String>,
    pub effort: Option<String>,
    pub stop_reason: Option<String>,
    pub attribution_skill: Option<String>,
    pub started_at: Option<DateTime<Utc>>,
    pub tool_calls: i64,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cache_read_tokens: i64,
    pub cache_creation_tokens: i64,
    pub cost_usd: Option<f64>,
    pub unpriced_api_calls: i64,
}

/// One tool call. No cost of its own: what it took is the api call's.
///
/// `run_id` is set on a `Task` call, which is where an agent run begins; `offload_file` where the
/// result was too large for the transcript and Claude Code wrote it beside one.
pub struct ToolFacts {
    pub session_id: String,
    pub run_id: Option<String>,
    pub tool_index: i64,
    pub name: Option<String>,
    pub server_side: bool,
    pub is_error: bool,
    pub incomplete: bool,
    pub started_at: Option<DateTime<Utc>>,
    pub wall_ms: Option<i64>,
    pub offload_file: Option<String>,
}

/// One compaction: where the thread's context was rewritten, and what it cost in tokens.
pub struct CompactionFacts {
    pub trigger: Option<String>,
    pub timestamp: Option<DateTime<Utc>>,
    pub pre_tokens: Option<i64>,
    pub post_tokens: Option<i64>,
    pub duration_ms: Option<i64>,
}

/// A bucket, which is not a row of the store.
///
/// It stands for what attached to nothing, so it has a spend and a count and no fields of its own
/// — both of them read off the node rather than off a query.
pub struct BucketFacts {
    pub cost_usd: Option<f64>,
    pub unpriced_api_calls: i64,
}

/// What a body may be handed. Total over the kinds a URL can name — a kind with no arm would
/// render a heading and nothing under it, which reads as a node with no facts.
///
/// The two buckets share a shape because neither is a row of the store: what they hold is counted
/// on the node itself.
pub enum Facts {
    Session(SessionFacts),
    Turn(TurnFacts),
    Run(RunFacts),
    Call(CallFacts),
    Tool(ToolFacts),
    Compaction(CompactionFacts),
    Bucket(BucketFacts),
}

/// One node's title and the facts its kind reads, for either mount.
pub fn body(node: &Node, shaped: &Facts, suffix: &str) -> Markup {
    rsx! {
        <section class="body" data-body=(node.kind.word())>
            <h1>
                (parts::mark(node.icon()))" "
                <span data-field="title">(node.pane_title())</span>
            </h1>
            (facts(shaped, suffix))
        </section>
    }
    .memoize()
}

/// One node's body alone, for a log row on somebody else's page.
///
/// The same section a node page wraps, with the way to the node's own page where the page has the
/// NavTree and the crumbs. A call lists the tools it called under its facts, through the log the
/// page itself renders; every other kind stands a count and a link in the list's place. Either way
/// the nesting stops here — an expansion that opened an expansion is an accordion of accordions,
/// and the node already has a page.
///
/// A row of the log's own table, swapped in after the row that asked for it, spanning every column
/// that row fills: the parent's shape is not in the URL, so the span comes from the kind of node
/// this is — [`spanned`] maps it back to the log that lists it.
pub struct Expansion<'a> {
    pub node: &'a Node,
    pub facts: &'a Facts,
    pub suffix: &'a str,
    pub shape: Shape,
    pub children: Option<i64>,
    pub rows: &'a [Logged],
    pub citations: &'a [(String, Cited)],
}

pub fn expansion(opened: &Expansion<'_>) -> Markup {
    let listed = (!opened.rows.is_empty())
        .then(|| {
            log(
                opened.shape,
                opened.rows,
                opened.children,
                opened.suffix,
                None,
                false,
            )
        })
        .flatten();
    rsx! {
        <tr class="expansion" data-expansion=(opened.node.kind.word())>
            <td colspan=(spanned(opened.node.kind))>
                (body(opened.node, opened.facts, opened.suffix))
                (listed)
                (way_out(opened))
                // What the fragment ran, beside what it rendered — the same provenance a page's
                // footer carries, on the element that was swapped in.
                (citation::listed(opened.citations))
            </td>
        </tr>
    }
    .memoize()
}

/// The link out of an expansion.
///
/// It carries the count wherever nothing above it listed the level; where the log did, the log's
/// own heading counts it and the link says the one thing left to say.
fn way_out(opened: &Expansion<'_>) -> Markup {
    let counted = opened.children.is_some() && opened.rows.is_empty();
    rsx! {
        <p class="children" data-children=(opened.node.kind.word())>
            <a href=(format!("{}{}", opened.node.url(), opened.suffix))>
                @if counted {
                    <span data-field="children">(fmt::count(opened.children))</span>
                    (format!(" {}", opened.shape))
                } @else {
                    "its own page"
                }
            </a>
        </p>
    }
    .memoize()
}

/// The body's one dispatch: the facts of whichever kind of node this is.
fn facts(shaped: &Facts, suffix: &str) -> Markup {
    match shaped {
        Facts::Session(shaped) => session(shaped),
        Facts::Turn(shaped) => turn(shaped),
        Facts::Run(shaped) => run(shaped),
        Facts::Call(shaped) => call(shaped),
        Facts::Tool(shaped) => tool(shaped, suffix),
        Facts::Compaction(shaped) => compaction(shaped),
        Facts::Bucket(shaped) => bucket(shaped),
    }
}

/// One count as a fact reads it, which every numeric fact but a money one goes through.
fn counted(name: &str, value: i64) -> Markup {
    parts::fact(name, Some(&fmt::count(Some(value))))
}

fn session(shaped: &SessionFacts) -> Markup {
    rsx! {
        <dl class="facts">
            (parts::fact("session_id", Some(&shaped.session_id)))
            (parts::fact("git_branch", shaped.git_branch.as_deref()))
            (parts::fact("version", shaped.version.as_deref()))
            (parts::fact("entrypoint", shaped.entrypoint.as_deref()))
            (parts::fact("started_at", Some(&fmt::when(shaped.started_at))))
            (parts::fact("wall_ms", Some(&fmt::duration(shaped.wall_ms))))
            (parts::fact("active_ms", Some(&fmt::duration(shaped.active_ms))))
            (parts::fact("turns", Some(&fmt::count(Some(shaped.turns)))))
            (parts::fact("api_calls", Some(&fmt::count(Some(shaped.api_calls)))))
            (parts::fact("tool_calls", Some(&fmt::count(Some(shaped.tool_calls)))))
            (parts::fact("tool_errors", Some(&fmt::count(Some(shaped.tool_errors)))))
            (parts::fact("agent_runs", Some(&fmt::count(Some(shaped.agent_runs)))))
            (parts::fact("compactions", Some(&fmt::count(Some(shaped.compactions)))))
            (parts::fact("cost_usd", Some(&fmt::money(shaped.cost_usd))))
            // Beside the cost rather than folded into it: a total missing calls our price table
            // could not price is not what the node cost.
            (parts::fact("unpriced_api_calls", Some(&fmt::count(Some(shaped.unpriced_api_calls)))))
            (parts::fact("output_tokens", Some(&fmt::count(Some(shaped.output_tokens)))))
            (skills(shaped))
        </dl>
        (prs(shaped))
    }
    .memoize()
}

/// The skills the session loaded, and the count of what its query left behind.
///
/// Composed rather than printed, so the pane's own cut cannot take the count off the end of it:
/// a list already bounded by its query loses what it left rather than a tail of its last member.
fn skills(shaped: &SessionFacts) -> Markup {
    if shaped.skills.is_empty() {
        return parts::fact("skills", None);
    }
    let listed = shaped
        .skills
        .iter()
        .map(|skill| cuts::member(skill))
        .collect::<Vec<_>>()
        .join(", ");
    parts::labelled(
        "skills",
        rsx! {
            <span>(listed)(parts::more(shaped.skills_cut))</span>
        }
        .memoize(),
    )
}

/// The pull requests the session's commands touched, cut the way the skills are.
///
/// Links rather than a fact row, because a reader follows them off the page.
fn prs(shaped: &SessionFacts) -> Option<Markup> {
    if shaped.pr_urls.is_empty() {
        return None;
    }
    Some(
        rsx! {
            <ul class="prs">
                // The cut copy in the attribute too: Python lists the members it shows and
                // keys each row by the one it printed.
                @for url in &shaped.pr_urls {
                    @let shown = cuts::member(url);
                    <li data-pr=(shown)>(render::link(Some(&shown)))</li>
                }
                @if shaped.pr_urls_cut != 0 {
                    <li data-field="prs_cut">
                        "and "(fmt::count(Some(shaped.pr_urls_cut)))" more"
                    </li>
                }
            </ul>
        }
        .memoize(),
    )
}

fn turn(shaped: &TurnFacts) -> Markup {
    rsx! {
        @if let Some(name) = &shaped.command_name {
            <p class="command" data-command=(shaped.turn_id)>
                <span data-field="command_name">(cuts::head(Some(name)))</span>
            </p>
        }
        <dl class="facts">
            (counted("turn_index", shaped.turn_index))
            (parts::fact("started_at", Some(&fmt::clock(shaped.started_at))))
            (parts::fact("replayed", Some(&fmt::flag(shaped.replayed))))
            (counted("api_calls", shaped.api_calls))
            (counted("tool_calls", shaped.tool_calls))
            (counted("tool_errors", shaped.tool_errors))
            (parts::fact("cost_usd", Some(&fmt::money(shaped.cost_usd))))
            (counted("unpriced_api_calls", shaped.unpriced_api_calls))
        </dl>
    }
    .memoize()
}

fn run(shaped: &RunFacts) -> Markup {
    rsx! {
        <dl class="facts">
            (parts::fact("run_id", Some(&shaped.run_id)))
            (parts::fact("agent_type", shaped.agent_type.as_deref()))
            (parts::fact("model", shaped.model.as_deref()))
            (counted("spawn_depth", shaped.spawn_depth))
            (parts::fact("is_fork", Some(&fmt::flag(shaped.is_fork))))
            (parts::fact("started_at", Some(&fmt::clock(shaped.started_at))))
            (parts::fact("wall_ms", Some(&fmt::duration(shaped.wall_ms))))
            (counted("turns", shaped.turns))
            (counted("api_calls", shaped.api_calls))
            (counted("tool_calls", shaped.tool_calls))
            (counted("tool_errors", shaped.tool_errors))
            (counted("compactions", shaped.compactions))
            (parts::fact("cost_usd", Some(&fmt::money(shaped.cost_usd))))
            (counted("unpriced_api_calls", shaped.unpriced_api_calls))
            (counted("output_tokens", shaped.output_tokens))
        </dl>
    }
    .memoize()
}

fn call(shaped: &CallFacts) -> Markup {
    rsx! {
        <dl class="facts">
            (counted("call_index", shaped.call_index))
            (parts::fact("model", shaped.model.as_deref()))
            // Only where the call fell back: a blank here would read as a call that did.
            @if shaped.fallback_from.as_deref().is_some_and(|from| !from.is_empty()) {
                (parts::fact("fallback_from", shaped.fallback_from.as_deref()))
            }
            (parts::fact("effort", shaped.effort.as_deref()))
            (parts::fact("stop_reason", shaped.stop_reason.as_deref()))
            (parts::fact("attribution_skill", shaped.attribution_skill.as_deref()))
            (parts::fact("started_at", Some(&fmt::clock(shaped.started_at))))
            (counted("tool_calls", shaped.tool_calls))
            (counted("input_tokens", shaped.input_tokens))
            (counted("output_tokens", shaped.output_tokens))
            (counted("cache_read_tokens", shaped.cache_read_tokens))
            (counted("cache_creation_tokens", shaped.cache_creation_tokens))
            (parts::fact("cost_usd", Some(&fmt::money(shaped.cost_usd))))
            (counted("unpriced_api_calls", shaped.unpriced_api_calls))
        </dl>
    }
    .memoize()
}

fn tool(shaped: &ToolFacts, suffix: &str) -> Markup {
    rsx! {
        // A `Task` call is where an agent run begins, so the run leads the body: it is what a
        // reader came to the call to reach, and everything else about the call is what it took to
        // start it.
        @if let Some(run_id) = shaped.run_id.as_deref().filter(|id| !id.is_empty()) {
            <p class="spawned" data-spawned=(run_id)>
                <a href=(format!("{}{suffix}", run_url(&shaped.session_id, run_id)))>
                    "the run it started"
                </a>
            </p>
        }
        <dl class="facts">
            (counted("tool_index", shaped.tool_index))
            (parts::fact("name", shaped.name.as_deref()))
            (parts::fact("server_side", Some(&fmt::flag(shaped.server_side))))
            (parts::fact("is_error", Some(&fmt::flag(shaped.is_error))))
            (parts::fact("incomplete", Some(&fmt::flag(shaped.incomplete))))
            (parts::fact("started_at", Some(&fmt::clock(shaped.started_at))))
            (parts::fact("wall_ms", Some(&fmt::duration(shaped.wall_ms))))
        </dl>
        // A result too large for the transcript was written to a file beside it, and that file has
        // a page of its own — so the pane says where it went rather than showing an empty result.
        @if let Some(offload) = shaped.offload_file.as_deref().filter(|file| !file.is_empty()) {
            <p class="offload">
                "result offloaded to "
                <a
                    data-field="offload_file"
                    href=(format!(
                        "/session/{}/offload/{}",
                        shaped.session_id,
                        urls::quoted_path(offload),
                    ))
                >(offload)</a>
            </p>
        }
    }
    .memoize()
}

fn compaction(shaped: &CompactionFacts) -> Markup {
    rsx! {
        <dl class="facts">
            (parts::fact("trigger", shaped.trigger.as_deref()))
            (parts::fact("timestamp", Some(&fmt::clock(shaped.timestamp))))
            (parts::fact("pre_tokens", Some(&fmt::count(shaped.pre_tokens))))
            (parts::fact("post_tokens", Some(&fmt::count(shaped.post_tokens))))
            (parts::fact("duration_ms", Some(&fmt::duration(shaped.duration_ms))))
        </dl>
    }
    .memoize()
}

fn bucket(shaped: &BucketFacts) -> Markup {
    rsx! {
        <dl class="facts">
            (parts::fact("cost_usd", Some(&fmt::money(shaped.cost_usd))))
            (counted("unpriced_api_calls", shaped.unpriced_api_calls))
        </dl>
    }
    .memoize()
}
