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

use hypertext::prelude::*;

use crate::components::{Markup, parts};
use crate::format as fmt;
use crate::nodes::Node;
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
    pub started_at: Option<chrono::DateTime<chrono::Utc>>,
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

/// What a body may be handed. Total over the kinds a URL can name — a kind with no arm would
/// render a heading and nothing under it, which reads as a node with no facts.
///
/// Stage 3b adds `Turn`, `Run`, `Call`, `Tool`, `Compaction` and `Bucket` beside this one; each
/// is a struct above and an arm in [`facts`].
pub enum Facts {
    Session(SessionFacts),
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

/// The body's one dispatch: the facts of whichever kind of node this is.
fn facts(shaped: &Facts, _suffix: &str) -> Markup {
    match shaped {
        Facts::Session(shaped) => session(shaped),
    }
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
                @for url in &shaped.pr_urls {
                    @let shown = cuts::member(url);
                    <li data-pr=(url)>(render::link(Some(&shown)))</li>
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
