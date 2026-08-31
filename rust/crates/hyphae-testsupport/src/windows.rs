//! The dates and counts the analysis tier measures against, and the corpus figures behind them.
//!
//! The twin of `tests/analyze/conftest.py`'s constants. A windowed query measures back from
//! `--as-of`, so a leaf that let it default to today passes while the corpus is recent and
//! goes red the morning it recedes. Every one of these is a measurement over the cached
//! corpus store, not a guess: re-take it by running the query rather than adjusting it to
//! whatever a failure printed.

use chrono::NaiveDate;

/// Long after the last fixture was recorded — where a leaf that must not depend on the corpus
/// still being recent pins the clock (`hyphae_model::clock::freeze`).
pub const FAR_FUTURE: &str = "2030-01-01";

/// Sessions recorded under `landmarks::MYCELIA`, which is every fixture but the three
/// `landmarks::NON_CORPUS` names. Measured by `hp query sessions --project`.
pub const MYCELIA_SESSIONS: usize = 15;

/// An `$as_of` whose trailing 28 days opens at 2026-06-30 and covers the whole corpus.
pub const AS_OF_WHOLE: &str = "2026-07-28";
/// An `$as_of` past the corpus: the window opens at 2026-07-10 and covers eight sessions.
pub const AS_OF_PARTIAL: &str = "2026-08-07";
pub const IN_WINDOW_AT_PARTIAL: usize = 8;
/// An `$as_of` inside the corpus, so the window's far edge has something to exclude: it opens
/// before the earliest session and closes at the end of 2026-07-19, leaving out the three
/// recorded after it.
pub const AS_OF_MID: &str = "2026-07-19";
pub const IN_WINDOW_AT_MID: usize = 12;

/// A `--since` inside the corpus, and how many mycelia sessions started on or after it.
pub const SINCE: &str = "2026-07-15";
pub const SESSIONS_SINCE: usize = 7;

/// One of the dates above as a date, for a caller that binds rather than prints it.
pub fn date(spelling: &str) -> NaiveDate {
    NaiveDate::parse_from_str(spelling, "%Y-%m-%d").expect("a date this module declares")
}
