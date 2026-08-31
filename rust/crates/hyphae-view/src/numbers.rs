//! Where a node's dollars went, by category, for the popover behind a NavTree row.
//!
//! Ported from `src/hyphae/view/numbers.py`. A row's badge prints one number, and one number cannot
//! say whether a phase spent its money reading a cache or writing one. The split is the four
//! charges `hyphae_extract::pricing` already computes, summed across the models the node used.
//!
//! Every line here is the node's own thread, whatever kind of node it is. What the agent runs below
//! it spent stands apart, in the two lines [`breakout`] composes.
//!
//! Composed here rather than in SQL because the rates are the extractor's: a phase can mix models —
//! a session runs Haiku sub-agents under an Opus main thread — so one row of summed tokens times
//! one price would charge them all at whichever rate won. `view_numbers.sql` groups the node's
//! tokens by model instead, and each group is priced once.

use hyphae_extract::pricing::{CostSplit, TokenUsage, split_cost};
use hyphae_store::{Row, RowError};

use crate::builders::rounded;
use crate::nodes::meter;

/// One line of the popover: a count of tokens, and what those tokens cost.
pub struct Charge {
    /// What the popover calls the line, and the fields its two numbers are labelled with.
    pub label: &'static str,
    pub field: &'static str,
    pub cost_field: String,
    pub tokens: Option<i64>,
    /// None where our price table holds no rate for the model the node answered on. The count
    /// beside it still prints: a reading we have no price for is not a reading we do not have.
    pub cost: Option<f64>,
    /// The step class the dollar's ground is drawn at — the badge's own, so the popover and the row
    /// it opened from wash one number the same way.
    pub wash: String,
}

/// What the popover calls each line, beside the name its two numbers are labelled with: the store's
/// own column less its `_tokens`, and that same name under a `cost_` for the dollar.
const LINES: [(&str, &str); 3] = [
    ("cache read", "cached"),
    ("new input", "new_input"),
    ("output", "output"),
];

/// The two lines under the total, on a node with agent runs hanging below it.
///
/// What the node's own thread spent is the column above; this is what the runs it asked for spent,
/// and the two together. Absent where no run hangs there — see [`breakout`].
pub struct Breakout {
    /// What the runs below the node spent, and what that is with the node's own added back.
    pub subagents: f64,
    pub total: f64,
    /// The ground each is drawn on, the badge's own, as every other dollar here takes it.
    pub subagents_wash: String,
    pub total_wash: String,
}

/// The subagent and total lines, or `None` where nothing hangs under the node.
///
/// None rather than a pair of zeroes: a subagent charge of nothing and a total repeating the figure
/// above it are two ways of saying what the node already said, and a reader who sees the lines on
/// every row stops reading them. Rounded back to where a cost is stored so the sum of two
/// four-decimal figures is one too ([`crate::nodes::COST_PLACES`]).
pub fn breakout(own: Option<f64>, under: Option<f64>, whole: Option<f64>) -> Option<Breakout> {
    let under = under.filter(|held| *held != 0.0)?;
    let total = rounded(own.unwrap_or(0.0) + under);
    Some(Breakout {
        subagents: under,
        total,
        subagents_wash: wash(Some(under), whole),
        total_wash: wash(Some(total), whole),
    })
}

/// The three lines the popover prints between the window and the total.
///
/// The counts come off the node's last answering call and add up to the window it left; the dollars
/// are every call the node made and add up to the total under them. The cache a call wrote rides on
/// the new-input line rather than on one of its own, because that is where its tokens are counted
/// (`view_numbers.sql`) — a fourth dollar would leave a column of charges coming to nothing the
/// reader can see.
pub fn charges(
    row: &Row,
    split: Option<&CostSplit>,
    whole: Option<f64>,
) -> Result<Vec<Charge>, RowError> {
    let priced = match split {
        Some(split) => [
            Some(split.cache_read),
            Some(split.input + split.cache_write),
            Some(split.output),
        ],
        None => [None, None, None],
    };
    LINES
        .iter()
        .zip(priced)
        .map(|((label, field), cost)| {
            Ok(Charge {
                label,
                field,
                cost_field: format!("cost_{field}"),
                tokens: row.opt_i64(&format!("{field}_tokens"))?,
                cost,
                wash: wash(cost, whole),
            })
        })
        .collect()
}

/// The ground one dollar figure is drawn on: its share of what the session spent.
///
/// The badge's own ladder ([`meter`]), so a number in a popover and the same number on the row
/// behind it are washed at one depth. A session that spent nothing, or a dollar we have none of,
/// takes no share and draws no ground.
pub fn wash(cost: Option<f64>, whole: Option<f64>) -> String {
    let share = match (cost, whole) {
        (Some(cost), Some(whole)) if cost != 0.0 && whole != 0.0 => Some(cost / whole),
        _ => None,
    };
    meter(share)
}

/// The four charges a node's tokens come to, or `None` where nothing in it could be priced.
///
/// `groups` is `view_numbers.sql`'s `spent` — one member per model, with that model's tokens summed.
/// A group our price table lacks is left out rather than counted as zero, which is the same nothing
/// the badge above prints: the popover says how many calls went unpriced.
pub fn spend(groups: &[Row]) -> Result<Option<CostSplit>, RowError> {
    let mut summed = CostSplit::default();
    let mut any = false;
    for group in groups {
        let Some(charged) = split_cost(group.str("model")?, &usage(group)?) else {
            continue;
        };
        any = true;
        summed.input += charged.input;
        summed.output += charged.output;
        summed.cache_read += charged.cache_read;
        summed.cache_write += charged.cache_write;
    }
    Ok(any.then_some(summed))
}

/// One model's summed tokens as the price table takes them.
///
/// The TTL split is summed per call in SQL, under the same fallback the extractor applies to one —
/// a call that reported no split puts its whole write on the 5-minute rate — so the group carries a
/// split whether or not every call in it did.
fn usage(group: &Row) -> Result<TokenUsage, RowError> {
    Ok(TokenUsage {
        input: group.i64("input_tokens")?,
        output: group.i64("output_tokens")?,
        cache_read: group.i64("cache_read_tokens")?,
        cache_creation: group.i64("cache_creation_tokens")?,
        cache_5m: group.opt_i64("cache_5m_tokens")?,
        cache_1h: group.opt_i64("cache_1h_tokens")?,
    })
}
