//! What an API call cost, from a price table we maintain.
//!
//! This table is **ours**, not Claude Code's. A model missing from it is a gap in our list,
//! not a schema change to surface, so [`compute_cost`] returns `None` and the caller records
//! the model name with no cost. Crashing here would kill a backfill the day a new model ships.
//!
//! Ported rate-for-rate from `src/hyphae/extract/pricing.py`, which stays the authority and
//! carries the date the published page was last read. The arithmetic below is ordered exactly
//! as Python's is: float addition does not commute, and every stored `cost_usd` has to be the
//! same bits the Python exporter wrote for the parity diff to mean anything.

/// Claude Code's own placeholder replies — an interrupt notice, a cancelled request — are
/// recorded as assistant records under this model name. They report zero tokens.
pub const SYNTHETIC_MODEL: &str = "<synthetic>";

// Cache multipliers on the base input rate. The TTL is what separates them: a 1-hour cache
// pays off after two reads, a 5-minute one after one.
const CACHE_READ: f64 = 0.1;
const CACHE_WRITE_5M: f64 = 1.25;
const CACHE_WRITE_1H: f64 = 2.0;

const PER_MILLION: f64 = 1_000_000.0;

/// One model's base rates, USD per million tokens. Cache rates derive from `input`.
struct ModelPrice {
    input: f64,
    output: f64,
}

/// The token counts one reply reported, as the pricing table charges them.
#[derive(Debug, Clone, Copy)]
pub struct TokenUsage {
    pub input: i64,
    pub output: i64,
    pub cache_read: i64,
    pub cache_creation: i64,
    /// The cache-creation total split by TTL. Both `None` when the reply reported no split at
    /// all, which prices the whole write at the 5-minute rate.
    pub cache_5m: Option<i64>,
    pub cache_1h: Option<i64>,
}

/// Every model the corpus records, plus the placeholder. Keyed by the exact `message.model`
/// string, since that is what the transcript carries.
const PRICES: &[(&str, ModelPrice)] = &[
    (
        SYNTHETIC_MODEL,
        ModelPrice {
            input: 0.0,
            output: 0.0,
        },
    ),
    (
        "claude-fable-5",
        ModelPrice {
            input: 10.0,
            output: 50.0,
        },
    ),
    (
        "claude-opus-5",
        ModelPrice {
            input: 5.0,
            output: 25.0,
        },
    ),
    (
        "claude-opus-4-8",
        ModelPrice {
            input: 5.0,
            output: 25.0,
        },
    ),
    (
        "claude-opus-4-1-20250805",
        ModelPrice {
            input: 15.0,
            output: 75.0,
        },
    ),
    // Introductory pricing, in effect through 2026-08-31; it rises to $3/$15 on 2026-09-01.
    (
        "claude-sonnet-5",
        ModelPrice {
            input: 2.0,
            output: 10.0,
        },
    ),
    (
        "claude-sonnet-4-6",
        ModelPrice {
            input: 3.0,
            output: 15.0,
        },
    ),
    (
        "claude-haiku-4-5-20251001",
        ModelPrice {
            input: 1.0,
            output: 5.0,
        },
    ),
];

/// The window each model answers in, in tokens, in the order `extract/pricing.py` writes it.
///
/// The order is load-bearing. `analyze/macros.py` writes this table out as the SQL macro
/// `context_window`, one `WHEN` arm per entry, so a reordering here installs a macro whose
/// text differs from the one the Python viewer installs. A model the table lacks answers
/// NULL, which is a bar the viewer does not draw rather than a scale it invents.
pub const CONTEXT_WINDOWS: &[(&str, i64)] = &[
    ("claude-fable-5", 200_000),
    ("claude-opus-5", 200_000),
    ("claude-opus-4-8", 200_000),
    ("claude-opus-4-1-20250805", 200_000),
    ("claude-sonnet-5", 200_000),
    ("claude-sonnet-4-6", 200_000),
    ("claude-haiku-4-5-20251001", 200_000),
];

/// What one model's tokens cost in USD, category by category.
///
/// The four rates a reply is billed at, kept apart rather than summed: the viewer's popover prints
/// them as a legend saying where a phase's dollars went (`docs/viewer.md`), and the total below is
/// the only number the store keeps.
#[derive(Debug, Clone, Copy, Default)]
pub struct CostSplit {
    pub input: f64,
    pub output: f64,
    pub cache_read: f64,
    pub cache_write: f64,
}

/// The four charges in USD per million tokens, or `None` for a model the table lacks.
///
/// The one place the rates are applied. Both callers below divide it down to dollars; what they
/// differ in is whether they hand back the four or their sum.
fn charges(model: &str, tokens: &TokenUsage) -> Option<CostSplit> {
    let price = PRICES
        .iter()
        .find(|(name, _)| *name == model)
        .map(|(_, price)| price)?;
    let write = match (tokens.cache_5m, tokens.cache_1h) {
        (Some(five), Some(hour)) => five as f64 * CACHE_WRITE_5M + hour as f64 * CACHE_WRITE_1H,
        _ => tokens.cache_creation as f64 * CACHE_WRITE_5M,
    };
    Some(CostSplit {
        input: tokens.input as f64 * price.input,
        output: tokens.output as f64 * price.output,
        cache_read: tokens.cache_read as f64 * price.input * CACHE_READ,
        cache_write: write * price.input,
    })
}

/// What one reply cost by category, or `None` when the table does not price its model.
pub fn split_cost(model: &str, tokens: &TokenUsage) -> Option<CostSplit> {
    let charged = charges(model, tokens)?;
    Some(CostSplit {
        input: charged.input / PER_MILLION,
        output: charged.output / PER_MILLION,
        cache_read: charged.cache_read / PER_MILLION,
        cache_write: charged.cache_write / PER_MILLION,
    })
}

/// What one reply cost in USD, or `None` when the table does not price its model.
///
/// Summed before the division rather than after, which is what keeps every stored cost the
/// number it was: four divisions rounded and then added is not always the same float.
pub fn compute_cost(model: &str, tokens: &TokenUsage) -> Option<f64> {
    let charged = charges(model, tokens)?;
    // The four added left to right, which is the order Python's `sum()` takes them in. Every term
    // is non-negative, so starting from the first rather than from Python's literal zero is the
    // same float.
    let summed = charged.input + charged.output + charged.cache_read + charged.cache_write;
    Some(summed / PER_MILLION)
}
