//! What a dry run quotes: arithmetic over rendered prompts and a price table in the code.
//!
//! Ported from `tests/enrich/test_cost.py`. No estimate here asks anything what it charges. The
//! rates are a dated constant a reader can check against Anthropic's price page, and everything
//! else is multiplication over character counts the planner already holds — so a dry run costs
//! nothing and works offline.

use hyphae_enrich::Level;
use hyphae_enrich::cost::{
    CHARS_PER_TOKEN, Estimate, OUTPUT_TOKENS, PRICES, Prompt, TRANSPORT_TOKENS, estimate,
};
use hyphae_enrich::prompts::{instructions, width};

const MODEL: &str = "claude-haiku-4-5-20251001";

/// The quoted price is the rendered characters, the instructions, the scaffold, and rates.
#[test]
fn an_estimate_is_multiplication_a_reader_can_redo() {
    // If a run would send two prompts — one turn and one session, of known length...
    let prompts = [
        Prompt {
            level: Level::Turn,
            content: "x".repeat(1_000),
        },
        Prompt {
            level: Level::Session,
            content: "y".repeat(3_000),
        },
    ];
    // ...then every number is derived from those lengths: each prompt pays for its content and
    // for the instructions its level carries, since a fresh subprocess caches nothing...
    let characters = 4_000
        + width(&instructions(Level::Turn)) as i64
        + width(&instructions(Level::Session)) as i64;
    // ...plus the transport scaffold, which is a flat count per item and holds no instructions
    // of its own — priced with a tiny system prompt for exactly that reason, so summing it here
    // on top of the characters above counts nothing twice...
    let input_tokens = (characters as f64 / CHARS_PER_TOKEN) as i64 + 2 * TRANSPORT_TOKENS;
    let output_tokens = 2 * OUTPUT_TOKENS;
    let rates = PRICES
        .iter()
        .find(|(named, _)| *named == MODEL)
        .map(|(_, rates)| *rates)
        .expect("the table prices the model");
    let full =
        (input_tokens as f64 * rates.input_usd + output_tokens as f64 * rates.output_usd) / 1e6;
    let quote = estimate(&prompts, MODEL).expect("a priced model quotes");
    // ...the token counts are exact...
    assert_eq!(
        Estimate {
            items: 2,
            input_tokens,
            output_tokens,
            usd: quote.usd,
        },
        quote
    );
    // ...and the price is those tokens at the table's rate, to within what float arithmetic can
    // state. There is one price now: every item is a `claude -p` call at list rate, and the
    // batch discount went with the API.
    assert!(
        (quote.usd - full).abs() < 1e-12,
        "{} is not {full}",
        quote.usd
    );
}

/// The two numbers no arithmetic derives: both came off the 2026-08-13 CLI probes.
///
/// Every other assertion here spends them symbolically, so an edit to either would leave the
/// suite green while every quote moved. Changing one means re-measuring first.
#[test]
fn the_measured_constants_are_pinned_to_their_probe() {
    assert_eq!((TRANSPORT_TOKENS, OUTPUT_TOKENS), (700, 230));
}

/// A run with nothing stale quotes zero rather than a floor price.
#[test]
fn an_empty_plan_costs_nothing() {
    assert_eq!(
        estimate(&[], MODEL).expect("a priced model quotes"),
        Estimate {
            items: 0,
            input_tokens: 0,
            output_tokens: 0,
            usd: 0.0,
        }
    );
}

/// A model the table does not price refuses to be quoted, rather than quoting zero.
///
/// Anthropic adds models faster than this file will be updated, and a silent zero would read as
/// "this pass is free" on exactly the run whose price nobody knows.
#[test]
fn an_unpriced_model_crashes() {
    let refused = estimate(
        &[Prompt {
            level: Level::Turn,
            content: "x".to_owned(),
        }],
        "claude-opus-9",
    )
    .expect_err("an unpriced model is refused");
    assert!(refused.to_string().contains("claude-opus-9"));
}
