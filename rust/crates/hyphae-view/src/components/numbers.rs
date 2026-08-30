//! The popover behind a NavTree row: what the badge and the bar on it stand for.
//!
//! Ported from `src/hyphae/view/components/numbers.py`. Three shapes, because three kinds of node
//! are measured three ways. A node made of api calls has a window and a price; a tool call has
//! neither — its tokens are its api call's — and reports the size of what it gave back; a
//! compaction has no calls at all and reports the window it dropped. Each arrives as one element
//! htmx swaps under the row that asked (`docs/viewer.md`).

use hypertext::prelude::*;

use crate::components::{Markup, parts};
use crate::format as fmt;
use crate::numbers::{Breakout, Charge};

/// A node measured in api calls: where it left the context window, and what it cost.
pub struct Window {
    pub model: Option<String>,
    pub fill: Option<i64>,
    pub window_tokens: Option<i64>,
    pub added: Option<i64>,
    pub cost_usd: Option<f64>,
    pub api_calls: Option<i64>,
    pub unpriced_api_calls: Option<i64>,
}

/// A tool call measured in characters: what it was passed, and what it gave back.
pub struct Tool {
    pub input_chars: Option<i64>,
    pub result_chars: Option<i64>,
    pub offload_file: Option<String>,
    pub spawned_run: bool,
    pub siblings: Vec<String>,
    pub siblings_cut: i64,
}

/// A compaction measured in the window it dropped: both ends, and the word recorded for why.
pub struct Compaction {
    pub pre_tokens: Option<i64>,
    pub post_tokens: Option<i64>,
    pub freed: Option<i64>,
    pub trigger: Option<String>,
}

/// The numbers behind a turn, an api call, an agent run or a session.
///
/// Two columns that each come to a total. The counts are the node's last answering call and come to
/// the window above them; the dollars are every call it made and come to the total under them. Each
/// dollar carries the badge's own ground, so a share read here and a share read on the row are
/// drawn at one depth ([`crate::numbers`]).
pub fn popover(
    key: &str,
    citation: &str,
    node: &Window,
    charges: &[Charge],
    total_wash: &str,
    breakout: Option<&Breakout>,
) -> Markup {
    let over = node.api_calls.is_some_and(|calls| calls > 1);
    let unpriced = node.unpriced_api_calls.is_some_and(|calls| calls != 0);
    shell(
        key,
        citation,
        rsx! {
            <dl class="context">
                (line("model", rsx! { <dd data-field="model">(fmt::text(node.model.as_deref()))</dd> }.memoize()))
                // Where the node left the window, over the window itself. The scale is named
                // rather than assumed: a session that asked for a larger one still reports its
                // base model, so a window we hold no number for is said out loud instead of
                // scaling the counts to a guess.
                (line("context used", rsx! {
                    <dd>
                        <span data-field="fill">(fmt::count(node.fill))</span>
                        " / "
                        <span data-field="window">
                            @if let Some(window) = node.window_tokens.filter(|held| *held != 0) {
                                (fmt::count(Some(window)))
                            } @else { "unknown" }
                        </span>
                    </dd>
                }.memoize()))
            </dl>
            <dl class="charges">
                @for charged in charges { (charge(charged)) }
                <div class="sum">
                    <dt>"total added"</dt>
                    // Signed, always: what a node put into the window is a change, and a change
                    // printed bare reads as a total. A session has nothing before it to have added
                    // to, and prints the dash.
                    <dd data-field="added">(fmt::signed(node.added))</dd>
                    (cost("cost_usd", total_wash, node.cost_usd))
                </div>
                (broken_out(breakout))
            </dl>
            // How many calls the dollars cover, where they cover more than the counts above do.
            // Absent at one call, which is every api-call row: `over 1 api call` says nothing a
            // reader asked.
            @if over {
                <p class="beside">
                    "over "<span data-field="api_calls">(fmt::count(node.api_calls))</span>
                    " api calls"
                </p>
            }
            @if unpriced {
                <p class="beside">
                    <span data-field="unpriced_api_calls">
                        (fmt::count(node.unpriced_api_calls))
                    </span>
                    " at a model our price table lacks"
                </p>
            }
        }
        .memoize(),
    )
}

/// The numbers behind one tool call's row: what it returned, and what ran beside it.
pub fn tool(key: &str, citation: &str, node: &Tool) -> Markup {
    let offloaded = node.offload_file.as_deref().filter(|held| !held.is_empty());
    shell(
        key,
        citation,
        rsx! {
            <dl class="context">
                (line("asked", rsx! {
                    <dd data-field="input_chars">(fmt::count(node.input_chars))</dd>
                }.memoize()))
                (line("returned", rsx! {
                    <dd data-field="result_chars">(fmt::count(node.result_chars))</dd>
                }.memoize()))
                @if let Some(file) = offloaded {
                    (line("offloaded to", rsx! {
                        <dd data-field="offload_file">(file)</dd>
                    }.memoize()))
                }
            </dl>
            // Where the badge on a ⚒ row comes from. A tool call is billed nothing of its own, so
            // the one tool row that draws a cost draws an attribution rather than a measurement —
            // and a reader who cannot see that reads it as what the tool spent.
            @if node.spawned_run {
                <p class="beside" data-attribution="spawn_call">
                    "its own cost is the api call that spawned this run"
                </p>
            }
            // What the same api call asked for beside this one, named the way every other surface
            // names a tool call. Parallel work is the reading a row alone cannot give: a call that
            // took a minute took it beside these.
            <p class="beside">
                @if node.siblings.is_empty() {
                    "the only tool call its api call made"
                } @else {
                    "with "
                    <span data-field="siblings">(node.siblings.join(", "))</span>
                    (parts::more(node.siblings_cut))
                }
            </p>
        }
        .memoize(),
    )
}

/// The numbers behind one compaction's row: both ends of the window, and the span between.
///
/// No window scale here and no price — either would charge the drop with what the calls around it
/// did. The bar on the row draws the span alone, which is why both ends are printed: what a drop
/// was worth is the two it ran between.
pub fn compaction(key: &str, citation: &str, node: &Compaction) -> Markup {
    shell(
        key,
        citation,
        rsx! {
            <dl class="context">
                (line("context before", rsx! {
                    <dd data-field="pre_tokens">(fmt::count(node.pre_tokens))</dd>
                }.memoize()))
                (line("context after", rsx! {
                    <dd data-field="post_tokens">(fmt::count(node.post_tokens))</dd>
                }.memoize()))
                (line("freed", rsx! {
                    <dd data-field="freed">(fmt::count(node.freed))</dd>
                }.memoize()))
                // What Claude Code recorded as the reason, in its own word: a compaction the model
                // asked for and one the window forced read the same on the row and differently here.
                (line("trigger", rsx! {
                    <dd data-field="trigger">(fmt::text(node.trigger.as_deref()))</dd>
                }.memoize()))
            </dl>
        }
        .memoize(),
    )
}

/// The box every popover arrives in, keyed to the row that fetched it.
///
/// `tabindex="-1"` is the copy affordance. The popover is a descendant of the row, so the row stays
/// hovered while the pointer is inside it — and a click that lands here focuses it, which is what
/// holds it open under `:focus-within` while a reader drags across the numbers.
fn shell(key: &str, citation: &str, held: Markup) -> Markup {
    rsx! {
        <div class="popover" data-popover=(key) data-query=(citation) tabindex="-1">(held)</div>
    }
    .memoize()
}

/// One labelled reading of a popover's left column.
fn line(term: &str, body: Markup) -> Markup {
    rsx! { <div><dt>(term)</dt>(body)</div> }.memoize()
}

/// One category of a cost: what it counted, and what that came to.
fn charge(held: &Charge) -> Markup {
    rsx! {
        <div>
            <dt>(held.label)</dt>
            <dd data-field=(held.field)>(fmt::count(held.tokens))</dd>
            // No dollar at all where our price table lacks the model, rather than a zero: the count
            // beside it is still the store's, and a charge of nothing reads as a measurement.
            @if held.cost.is_some() { (cost(&held.cost_field, &held.wash, held.cost)) }
        </div>
    }
    .memoize()
}

/// One dollar on the ground its share of the session earns it.
fn cost(field: &str, wash: &str, held: Option<f64>) -> Markup {
    rsx! {
        <dd class=(format!("badge {wash}")) data-field=(field)>(fmt::charge(held))</dd>
    }
    .memoize()
}

/// What the agent runs below this node spent, and the two together.
///
/// Drawn only where runs hang there: on every other row the first line would be nothing and the
/// second would repeat the one above it. The share is the subagents' of the total, printed where a
/// charge line prints its tokens — it is what a reader is after, and a token count for threads they
/// are not reading is not.
fn broken_out(breakout: Option<&Breakout>) -> Option<Markup> {
    let breakout = breakout?;
    Some(
        rsx! {
            <div class="sum">
                <dt>"subagent spend"</dt>
                <dd data-field="subagent_share">
                    (fmt::share(Some(breakout.subagents), Some(breakout.total)))
                </dd>
                (cost("cost_subagents", &breakout.subagents_wash, Some(breakout.subagents)))
            </div>
            <div class="sum">
                <dt>"total spend"</dt>
                // Empty, and still drawn: the middle column is where the rule over a sum runs, and
                // a cell left out would break it. There is no share to print — the total is the
                // whole.
                <dd></dd>
                (cost("cost_total", &breakout.total_wash, Some(breakout.total)))
            </div>
        }
        .memoize(),
    )
}
