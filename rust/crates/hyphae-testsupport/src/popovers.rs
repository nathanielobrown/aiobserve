//! One NavTree row's popover, fetched and read back, beside the numbers the store itself holds.
//!
//! The twin of the helpers `tests/view/test_numbers.py` declares and its two siblings import.
//! Shared through this crate rather than through a module beside the tests because every
//! integration test is its own binary: `numbers.rs`, `numbers_planted.rs`, `numbers_spend.rs`
//! and `numbers_compaction.rs` all read a popover through what follows.
//!
//! The expectations are built out of `live_api_calls` in the caller's own SQL rather than out of
//! the columns the page reads, so a derivation that drifted between the bar and the popover has
//! nothing to agree with. The spend is priced one call at a time, which is the reading the page
//! cannot take: it groups a node's tokens by model and prices each group once, and that is the
//! same arithmetic only if the group's cache write splits the way every call in it did.

use std::collections::BTreeMap;
use std::path::Path;

use hyphae_extract::pricing::{CONTEXT_WINDOWS, CostSplit, TokenUsage, split_cost};
use hyphae_store::Param;
use hyphae_view::nodes::NUMBERS_URL;

use crate::html::{Markup, counted};
use crate::rows;
use crate::served::Served;

/// Where a node left the model's window: the last call it made that went to one.
///
/// Ordered by `"index"`, which is unique and ascending inside a thread. `extra` narrows the
/// selection to one turn or one call, as a SQL fragment the caller writes — the same shape the
/// Python helper takes, and over fixture ids either way.
fn last(extra: &str) -> String {
    format!(
        "SELECT model, cache_read_tokens, cache_creation_tokens, input_tokens, output_tokens \
         FROM live_api_calls WHERE session_id = $session AND source = $source AND NOT synthetic \
         {extra} ORDER BY \"index\" DESC LIMIT 1"
    )
}

/// And what a node's calls cost, a call at a time.
fn charges_of(extra: &str) -> String {
    format!(
        "SELECT model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, \
         cache_5m_tokens, cache_1h_tokens, cost_usd \
         FROM live_api_calls WHERE session_id = $session {extra}"
    )
}

/// One row's popover, as it was served.
pub async fn popped(served: &Served, path: &str) -> String {
    let (status, text) = served.page(&format!("{NUMBERS_URL}{path}")).await;
    assert!(status.is_success(), "{path}: {status} {text}");
    text
}

/// One row's popover, read back as its labelled fields.
pub async fn popover(served: &Served, path: &str, key: &str) -> BTreeMap<String, String> {
    Markup::of(&popped(served, path).await).fields("data-popover", key)
}

/// The window half of a popover, as the store's own columns give it.
pub fn held(db: &Path, session_id: &str, source: &str, extra: &str) -> BTreeMap<String, String> {
    let row = rows::one(
        db,
        &last(extra),
        &[
            ("session", Param::from(session_id)),
            ("source", Param::from(source)),
        ],
    );
    let number = |column: &str| row.i64(column).expect("a token count");
    let model = row.str("model").expect("a model").to_owned();
    let cached = number("cache_read_tokens");
    let creation = number("cache_creation_tokens");
    let sent = number("input_tokens");
    let out = number("output_tokens");
    let window = CONTEXT_WINDOWS
        .iter()
        .find(|(name, _)| *name == model)
        .map(|(_, window)| *window)
        .unwrap_or_else(|| panic!("no window recorded for {model}"));
    BTreeMap::from([
        ("model".to_owned(), model),
        ("cached".to_owned(), counted(cached)),
        ("new_input".to_owned(), counted(creation + sent)),
        ("output".to_owned(), counted(out)),
        ("fill".to_owned(), counted(cached + creation + sent + out)),
        ("window".to_owned(), counted(window)),
    ])
}

/// What a node's calls cost, priced one at a time, beside the total the store holds.
pub fn charged(db: &Path, session_id: &str, extra: &str) -> (CostSplit, f64) {
    let mut split = CostSplit::default();
    let mut stored = 0.0;
    for row in rows::all(
        db,
        &charges_of(extra),
        &[("session", Param::from(session_id))],
    ) {
        let model = row.str("model").expect("a model");
        let number = |column: &str| row.i64(column).expect("a token count");
        let priced = split_cost(
            model,
            &TokenUsage {
                input: number("input_tokens"),
                output: number("output_tokens"),
                cache_read: number("cache_read_tokens"),
                cache_creation: number("cache_creation_tokens"),
                cache_5m: row.opt_i64("cache_5m_tokens").expect("a nullable count"),
                cache_1h: row.opt_i64("cache_1h_tokens").expect("a nullable count"),
            },
        )
        .unwrap_or_else(|| panic!("{model} is priced"));
        split = CostSplit {
            input: split.input + priced.input,
            output: split.output + priced.output,
            cache_read: split.cache_read + priced.cache_read,
            cache_write: split.cache_write + priced.cache_write,
        };
        stored += row.f64("cost_usd").expect("a cost");
    }
    (split, stored)
}

/// What a split comes to, which is the figure the popover prints under the column.
pub fn total(split: &CostSplit) -> f64 {
    split.input + split.output + split.cache_read + split.cache_write
}

/// The dollars the popover prints beside its token counts, in the order they stand.
pub const CHARGES: [&str; 3] = ["cost_cached", "cost_new_input", "cost_output"];

/// How far a printed dollar may sit from the oracle's: one unit in the last place it prints.
///
/// The two price the same tokens in a different order — the page sums a model's tokens and prices
/// once, the oracle prices each call and sums the dollars — so a figure that lands on a tie rounds
/// either way. `SPINE`'s output comes to exactly $0.27305 and does.
pub const PRINTED_PLACE: f64 = 1e-4;

/// The dollar beside each token count, before it is printed.
///
/// Three charges and not the price table's four: the cache a call wrote is counted in the tokens on
/// the new-input line (`view_numbers.sql`), so its dollar is charged there too. A row of its own
/// would leave a column of dollars that does not come to the total under it, which is the one
/// arithmetic a reader can do in their head.
pub fn legend(split: &CostSplit) -> [(&'static str, f64); 3] {
    [
        (CHARGES[0], split.cache_read),
        (CHARGES[1], split.input + split.cache_write),
        (CHARGES[2], split.output),
    ]
}

/// The charges whose printed dollar and priced dollar disagree, printed side by side.
pub fn misread(
    printed: &BTreeMap<String, String>,
    split: &CostSplit,
) -> Vec<(&'static str, String, String)> {
    legend(split)
        .into_iter()
        .filter_map(|(field, dollars)| {
            let shown = printed
                .get(field)
                .unwrap_or_else(|| panic!("the popover prints {field}: {printed:?}"));
            ((amount(shown) - dollars).abs() > PRINTED_PLACE)
                .then(|| (field, shown.clone(), format!("${dollars:.4}")))
        })
        .collect()
}

/// A printed dollar figure read back as the number it is.
pub fn amount(shown: &str) -> f64 {
    shown
        .trim_start_matches('$')
        .parse()
        .unwrap_or_else(|_| panic!("{shown} is a printed dollar figure"))
}

/// Whether a turn was answered by a model at all, or only by Claude Code's placeholder.
pub fn reached(db: &Path, session_id: &str, source: &str, turn_id: &str) -> bool {
    rows::one(
        db,
        "SELECT count(*) AS answered FROM live_api_calls \
         WHERE session_id = $session AND source = $source AND turn_id = $turn AND NOT synthetic",
        &[
            ("session", Param::from(session_id)),
            ("source", Param::from(source)),
            ("turn", Param::from(turn_id)),
        ],
    )
    .i64("answered")
    .expect("a count")
        > 0
}

/// One printed count read back as the number it is.
pub fn tokens(printed: &BTreeMap<String, String>, field: &str) -> i64 {
    printed
        .get(field)
        .unwrap_or_else(|| panic!("the popover prints {field}: {printed:?}"))
        .replace(',', "")
        .parse()
        .unwrap_or_else(|_| panic!("{field} is a printed count"))
}

/// A signed count as a popover prints a delta: what a node added is a change, and a change that
/// prints bare reads as a total.
pub fn signed(value: i64) -> String {
    if value < 0 {
        counted(value)
    } else {
        format!("+{}", counted(value))
    }
}

/// Every field of `wanted` printed as the popover printed it — the port of Python's
/// `printed | held == printed`, which asserts one dict is a subset of another.
pub fn assert_holds(printed: &BTreeMap<String, String>, wanted: &BTreeMap<String, String>) {
    for (field, value) in wanted {
        assert_eq!(
            printed.get(field),
            Some(value),
            "{field} in {printed:?} wants {value}"
        );
    }
}
