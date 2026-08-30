//! Each cut a surface makes: the width a value is printed at, and the mark where it was cut.
//!
//! Ported from `src/hyphae/view/cuts.py`. A cut here is the render-time half of a query's
//! one-extra-character protocol — the query returns a string one character past the width,
//! and the cut marks it. Which width applies is a fact about the surface, so there is one
//! function per surface rather than one with a size argument.
//!
//! The two that read the world — the clock and the reader's home — read it per call, not per
//! process: a viewer left open is long-lived, and a frozen clock arrives in the environment.

use chrono::{DateTime, Utc};
use hyphae_store::queries;

use crate::format as fmt;

/// How long ago, against the clock at render rather than one captured at startup.
pub fn ago(value: Option<DateTime<Utc>>) -> String {
    fmt::ago(value, fmt::utcnow())
}

/// A project directory, with the home of whoever is reading the page folded to `~`.
pub fn project_path(value: Option<&str>) -> String {
    fmt::path(value, &fmt::home())
}

/// A row's string at the width a children log prints it, marked where it was cut.
pub fn line(value: Option<&str>) -> String {
    value.map_or_else(
        || fmt::ABSENT.to_owned(),
        |value| fmt::cut(value, queries::LOG_CHARS),
    )
}

/// A header's string as a pane prints it: cut at the pane's width, and marked.
///
/// Applied by `components::parts::fact` to every string that reaches it rather than at the
/// rows that need it, so a fact added beside them inherits the bound.
pub fn head(value: Option<&str>) -> String {
    value.map_or_else(
        || fmt::ABSENT.to_owned(),
        |value| fmt::cut(value, queries::HEADER_CHARS),
    )
}

/// One member of a header's list, marked where the query cut it.
pub fn member(value: &str) -> String {
    fmt::cut(value, queries::HEADER_ITEM_CHARS)
}

/// One member of a list on a row of the session list, marked where the query cut it.
///
/// What [`member`] does for a header's lists, at the width a row shows a skill or an agent type.
/// The kinds of work beside them do not come through here: their vocabulary is closed
/// (`enrich/taxonomy.py`), so a mark there could never be true.
pub fn item(value: &str) -> String {
    fmt::cut(value, queries::LIST_ITEM_CHARS)
}
