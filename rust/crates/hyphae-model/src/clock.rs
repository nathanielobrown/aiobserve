//! The one clock every side of hyphae reads, and the two ways a test stops it.
//!
//! Python freezes a clock by patching the module that reads it — `tests/gallery/serve.py`
//! replaces `fmt.utcnow` after import, and `tests/analyze/conftest.py` swaps `datetime`
//! itself. A compiled binary has neither seam, so the freeze arrives by one of two routes,
//! and [`utcnow`] resolves them in this order:
//!
//! 1. the process-global cell [`freeze`] sets — how a test in this workspace stops the clock;
//! 2. `HYPHAE_FIXED_NOW` — how the gallery stops a viewer it starts as a child process;
//! 3. the real clock.
//!
//! The cell wins because a test that froze the clock deliberately should not be overruled by
//! a variable its parent exported. It is never unset: a clock that could start again would
//! make a leaf's result depend on what ran before it.

use std::sync::LazyLock;
use std::sync::atomic::{AtomicI64, Ordering};

use chrono::{DateTime, TimeZone, Utc};

/// The environment variable naming an instant, as an RFC 3339 string.
pub const FIXED_NOW: &str = "HYPHAE_FIXED_NOW";

/// Epoch microseconds, or [`UNSET`]. An instant rather than an offset, so two reads a
/// millisecond apart answer the same.
static OVERRIDE: AtomicI64 = AtomicI64::new(UNSET);

/// Not an instant any clock can name — the year 292277026596 — so it can stand for "nothing
/// froze this" without a second atomic beside it.
const UNSET: i64 = i64::MIN;

static FROM_ENV: LazyLock<Option<DateTime<Utc>>> = LazyLock::new(|| {
    let named = std::env::var(FIXED_NOW)
        .ok()
        .filter(|set| !set.is_empty())?;
    // Fail fast: a misspelled instant would otherwise serve a live clock under a name that
    // promises a frozen one, and every relative time in a snapshot would drift.
    Some(
        named
            .parse::<DateTime<Utc>>()
            .unwrap_or_else(|error| panic!("{FIXED_NOW} is not an RFC 3339 instant: {error}")),
    )
});

/// Read the environment's frozen instant now, so a misspelled one refuses to launch.
///
/// Without this the lazy read below first runs inside whichever request prints a relative
/// time, which turns a typo into one page's 500 rather than a process that will not start.
pub fn check_clock() {
    LazyLock::force(&FROM_ENV);
}

/// Stop this process's clock at `instant`, for good.
///
/// What a test calls instead of exporting a variable: `hp` run in process reads the same
/// clock the pages do, so a leaf about a trailing window can put the corpus in the past
/// without a spawn and without touching the environment other threads share.
pub fn freeze(instant: DateTime<Utc>) {
    OVERRIDE.store(instant.timestamp_micros(), Ordering::Relaxed);
}

/// The current instant, in the store's zone — the one place anything asks for it.
pub fn utcnow() -> DateTime<Utc> {
    match OVERRIDE.load(Ordering::Relaxed) {
        #[expect(
            clippy::disallowed_methods,
            reason = "the one real clock read in the workspace; this function is the seam"
        )]
        UNSET => FROM_ENV.unwrap_or_else(Utc::now),
        micros => Utc
            .timestamp_micros(micros)
            .single()
            .expect("a frozen instant came from a `DateTime<Utc>`"),
    }
}
