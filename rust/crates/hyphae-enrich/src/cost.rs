//! What an enrichment pass would cost, before it spends anything.
//!
//! Ported from `src/hyphae/enrich/cost.py`. Arithmetic only: character counts the planner
//! already holds, a chars-per-token ratio, and a price table in this file. Nothing here reaches
//! the network, so a dry run works offline and answers in the time it takes to render the
//! prompts.
//!
//! Every request pays for its own instructions and its own transport scaffold, which under the
//! CLI is simply true: each item is a fresh subprocess with nothing left to cache. The estimate
//! still reads low on one axis — a run may cascade further than the plan can see — so it is not
//! a bound; quote it as an estimate.

use crate::prompts::{instructions, width};
use crate::schema::Level;

/// What one model charges, in US dollars per million tokens.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Rates {
    pub input_usd: f64,
    pub output_usd: f64,
}

/// Anthropic's list prices, read 2026-08-07. Only the models a `--model` flag is likely to
/// name: an unpriced one crashes rather than quoting zero for a pass nobody has costed.
pub const PRICES: &[(&str, Rates)] = &[
    (
        "claude-haiku-4-5-20251001",
        Rates {
            input_usd: 1.00,
            output_usd: 5.00,
        },
    ),
    (
        "claude-sonnet-4-5-20250929",
        Rates {
            input_usd: 3.00,
            output_usd: 15.00,
        },
    ),
];

/// The low end of the corpus's measured 3.3-4 range, so the token count reads high. Prompts are
/// dense with paths, ids and code fragments, which tokenize worse than prose.
pub const CHARS_PER_TOKEN: f64 = 3.3;

/// One answer is four short fields, and the schema caps the description. Measured at 229 on a
/// realistic render in the 2026-08-13 CLI probes, rounded up.
pub const OUTPUT_TOKENS: i64 = 230;

/// What a `claude -p` call costs before it has read a word of the item: the CLI's own framing
/// and the `--json-schema` payload. Measured at 684 with a tiny system prompt, so it counts no
/// instructions — [`estimate`] sums those separately, and double-counting them would add ~1.5K
/// tokens an item.
pub const TRANSPORT_TOKENS: i64 = 700;

/// One prompt a run would send: the content, and the level whose instructions it carries.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Prompt {
    pub level: Level,
    pub content: String,
}

/// What a pass would cost, at the one price there is to pay.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Estimate {
    pub items: usize,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub usd: f64,
}

/// A model this build has no rate for. Refusing to quote beats quoting zero.
///
/// Anthropic adds models faster than this file will be updated, and a silent zero would read as
/// "this pass is free" on exactly the run whose price nobody knows.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("no price recorded for {model} — add it to `hyphae_enrich::cost::PRICES`")]
pub struct UnpricedModel {
    pub model: String,
}

/// Price a set of prompts. Refuses a model this build does not have a rate for.
pub fn estimate(prompts: &[Prompt], model: &str) -> Result<Estimate, UnpricedModel> {
    let rates = PRICES
        .iter()
        .find(|(named, _)| *named == model)
        .map(|(_, rates)| *rates)
        .ok_or_else(|| UnpricedModel {
            model: model.to_owned(),
        })?;
    let characters: i64 = prompts
        .iter()
        .map(|prompt| (width(&prompt.content) + width(&instructions(prompt.level))) as i64)
        .sum();
    let items = prompts.len() as i64;
    let input_tokens = (characters as f64 / CHARS_PER_TOKEN) as i64 + items * TRANSPORT_TOKENS;
    let output_tokens = items * OUTPUT_TOKENS;
    Ok(Estimate {
        items: prompts.len(),
        input_tokens,
        output_tokens,
        usd: (input_tokens as f64 * rates.input_usd + output_tokens as f64 * rates.output_usd)
            / 1_000_000.0,
    })
}
