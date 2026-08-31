//! What a recorded reply cost, from our own price table.
//!
//! The port of `tests/extract/test_pricing.py`. The table is ours, not Claude Code's — a
//! model it lacks is a gap in our list, not a schema change, so the leaves below pin
//! behaviour on both sides of that line. What they cannot pin is whether the numbers match
//! Anthropic's published prices: no seam reaches that, and a test asserting a constant against
//! itself proves nothing. `pricing.py` records the check date. The claim that the price table
//! and the window table answer one census is a unit test beside them, the price table being
//! private.

use hyphae_extract::pricing::{SYNTHETIC_MODEL, TokenUsage, compute_cost, split_cost};

/// Lifted verbatim from `tests/fixtures/spine/`, CC 2.1.221 — the usage of
/// `msg_011CdmMjFXDofyYSMxYtXa5n`, a `claude-fable-5` reply that put its whole cache write on
/// the 1-hour TTL.
const SPINE_SPLIT: TokenUsage = TokenUsage {
    input: 2,
    output: 415,
    cache_read: 9_768,
    cache_creation: 20_257,
    cache_5m: Some(0),
    cache_1h: Some(20_257),
};

/// Two dollar amounts agree to the cent a thousand times over, which is as close as two
/// orderings of the same float addition get.
#[track_caller]
fn close(charged: f64, expected: f64) {
    assert!(
        (charged - expected).abs() < 1e-9,
        "{charged} is not {expected}"
    );
}

/// Input, output, cache read and cache write each price at their own rate.
///
/// The four are also readable one at a time: the viewer's popover prints a legend saying where
/// a phase's dollars went, and a legend derived from anything but this arithmetic would be a
/// second answer to what a call cost (`docs/viewer.md`).
#[test]
fn a_reply_is_priced_by_its_model_and_its_four_token_kinds() {
    // If a Fable 5 reply reports the four token kinds — $10/MTok in, $50/MTok out, cache
    // reads at 0.1x input and a 1-hour cache write at 2x input...
    let cost = compute_cost("claude-fable-5", &SPINE_SPLIT).expect("the table prices fable");
    let split = split_cost("claude-fable-5", &SPINE_SPLIT).expect("the table prices fable");

    // ...then each is charged at its own rate and the four are summed.
    let kinds = [
        2.0 * 10.0,            // input
        415.0 * 50.0,          // output
        9_768.0 * 10.0 * 0.1,  // cache read
        20_257.0 * 10.0 * 2.0, // 1-hour cache write
    ];
    close(cost, kinds.iter().sum::<f64>() / 1_000_000.0);
    close(cost, 0.435_678);
    // ...and the split hands back the same four separately, in the same USD the total is in.
    for (charged, kind) in [
        split.input,
        split.output,
        split.cache_read,
        split.cache_write,
    ]
    .iter()
    .zip(kinds)
    {
        close(*charged, kind / 1_000_000.0);
    }
    // The total is the split summed, so the legend can never disagree with the badge above it.
    close(
        split.input + split.output + split.cache_read + split.cache_write,
        cost,
    );
}

/// A 1-hour cache write costs 2x input; a 5-minute one costs 1.25x.
#[test]
fn the_cache_write_splits_by_ttl() {
    // If two replies write the same number of cache tokens under different TTLs...
    let written = |five, hour| TokenUsage {
        input: 0,
        output: 0,
        cache_read: 0,
        cache_creation: 1_000,
        cache_5m: Some(five),
        cache_1h: Some(hour),
    };

    // ...then the 1-hour write costs 2x the base input rate and the 5-minute one 1.25x, so
    // the same tokens cost 60% more on the longer TTL.
    close(
        compute_cost("claude-opus-5", &written(0, 1_000)).expect("the table prices opus"),
        1_000.0 * 5.0 * 2.0 / 1_000_000.0,
    );
    close(
        compute_cost("claude-opus-5", &written(1_000, 0)).expect("the table prices opus"),
        1_000.0 * 5.0 * 1.25 / 1_000_000.0,
    );
}

/// When a reply reports no TTL split, its whole cache write prices as 5-minute.
///
/// INVENTED shape: every assistant record in the mycelia corpus carries `usage.cache_creation`
/// (scanned 2026-08-07), so the unsplit reading is a fallback we chose, not one the corpus
/// shows.
#[test]
fn an_unsplit_cache_write_is_charged_at_the_five_minute_rate() {
    let unsplit = TokenUsage {
        input: 0,
        output: 0,
        cache_read: 0,
        cache_creation: 1_000,
        cache_5m: None,
        cache_1h: None,
    };

    close(
        compute_cost("claude-opus-5", &unsplit).expect("the table prices opus"),
        1_000.0 * 5.0 * 1.25 / 1_000_000.0,
    );
}

/// An unpriced model reports no cost at all, so a query can find it and fill it in.
///
/// The fail-fast rule guards Claude Code's schema, not our price list: a model released
/// mid-backfill must not kill the backfill, and pricing it at zero would report a free
/// session — the prior importer's bug.
#[test]
fn a_model_the_table_lacks_costs_nothing_rather_than_zero() {
    assert_eq!(compute_cost("claude-mythos-9", &SPINE_SPLIT), None);
    // And the split says the same nothing, so a legend cannot print four zeroes where the
    // badge beside it printed no number at all.
    assert!(split_cost("claude-mythos-9", &SPINE_SPLIT).is_none());
}

/// Claude Code's own placeholder replies are priced, and priced at nothing.
///
/// The 205 `<synthetic>` records in the corpus all report zero tokens, so the zero is
/// over-determined — but the table names the model so the cost is a stated zero rather than an
/// unpriced null.
#[test]
fn a_synthetic_reply_costs_zero() {
    assert_eq!(compute_cost(SYNTHETIC_MODEL, &SPINE_SPLIT), Some(0.0));
}
