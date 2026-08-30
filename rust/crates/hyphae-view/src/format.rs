//! How the pages print numbers and times.
//!
//! Ported from `src/hyphae/view/format.py`. Every one of these takes an absent value, because
//! a store column that can be NULL reaches a component as `None` and an empty cell says less
//! than a dash.

use std::sync::LazyLock;

use chrono::{DateTime, Utc};

/// What a page prints where the store holds nothing. One character, so a column of them reads
/// as a gap rather than as a value.
pub const ABSENT: &str = "—";

/// What a page prints where it cut a value short. One character, and the only thing that tells
/// a value that ended from a value that was stopped.
pub const ELLIPSIS: &str = "…";

const MINUTE: i64 = 60_000;
const HOUR: i64 = 60 * MINUTE;
const DAY: i64 = 24 * HOUR;

/// The clock the pages read, frozen for a test that names an instant.
///
/// The Python gallery freezes `fmt.utcnow` after import so a relative time is stable across
/// runs (`tests/gallery/serve.py`); a Rust server cannot be patched that way, so the freeze
/// arrives in the environment. Read once, at first use: a viewer left open must not have its
/// clock stopped by a variable someone exported later.
const FIXED_NOW: &str = "HYPHAE_FIXED_NOW";

static FROZEN: LazyLock<Option<DateTime<Utc>>> = LazyLock::new(|| {
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

/// The clock the pages read, in the store's zone — the one place that asks for it.
pub fn utcnow() -> DateTime<Utc> {
    FROZEN.unwrap_or_else(Utc::now)
}

/// The directory a path folds to `~` — the one place the pages ask whose machine this is.
pub fn home() -> String {
    std::env::var("HOME").unwrap_or_default()
}

/// A cost in dollars, at cent precision — the scale a session is read at.
pub fn money(value: Option<f64>) -> String {
    value.map_or_else(|| ABSENT.to_owned(), |value| format!("${value:.2}"))
}

/// One category of a cost, at the precision a category is worth: `$0.0431`.
pub fn charge(value: Option<f64>) -> String {
    value.map_or_else(|| ABSENT.to_owned(), |value| format!("${value:.4}"))
}

/// A count, with thousands separated.
pub fn count(value: Option<i64>) -> String {
    value.map_or_else(|| ABSENT.to_owned(), |value| separated(value, ""))
}

/// A change, with its sign kept: `+30,442`, `-80,900`.
///
/// What a count prints as where the number is a difference. A delta printed bare reads as a
/// total, and the negative one — a turn that compacted, and gave the window back — is the
/// reading the bar beside it cannot draw.
pub fn signed(value: Option<i64>) -> String {
    value.map_or_else(
        || ABSENT.to_owned(),
        |value| separated(value, if value < 0 { "" } else { "+" }),
    )
}

/// Python's `format(value, ',')`, which has no `std` spelling.
fn separated(value: i64, sign: &str) -> String {
    let digits = value.unsigned_abs().to_string();
    let mut written = String::with_capacity(digits.len() + digits.len() / 3 + 2);
    if value < 0 {
        written.push('-');
    }
    written.push_str(sign);
    for (at, digit) in digits.char_indices() {
        if at > 0 && (digits.len() - at).is_multiple_of(3) {
            written.push(',');
        }
        written.push(digit);
    }
    written
}

/// A string at the width a page reads it, marked where the rest was left behind.
///
/// The one-extra-character protocol every cut query rides: a query selects `$n + 1`
/// characters, so a value longer than `n` is a value with more behind it. Counted in
/// characters rather than bytes, which is what Python slices by.
pub fn cut(value: &str, size: usize) -> String {
    let mut kept: String = value.chars().take(size).collect();
    if value.chars().nth(size).is_some() {
        kept.push_str(ELLIPSIS);
    }
    kept
}

/// A string column as a cell: whatever the store holds, or the dash a NULL prints.
pub fn text(value: Option<&str>) -> String {
    value.map_or_else(|| ABSENT.to_owned(), str::to_owned)
}

/// A boolean column as a cell, in the words the pane has always printed it in.
pub fn flag(value: bool) -> String {
    if value { "True" } else { "False" }.to_owned()
}

/// A directory as a cell, with the reader's own home folded to `~`.
///
/// Only the reader's home, and only whole segments of it: a directory under someone else's is
/// theirs, and `~` over it would be a claim about this machine the session cannot support.
pub fn path(value: Option<&str>, home: &str) -> String {
    let Some(value) = value else {
        return ABSENT.to_owned();
    };
    if home.is_empty() {
        return value.to_owned();
    }
    if value == home {
        return "~".to_owned();
    }
    match value.strip_prefix(home) {
        Some(rest) if rest.starts_with('/') => format!("~{rest}"),
        _ => value.to_owned(),
    }
}

/// A timestamp in the store's zone (UTC), to the minute.
pub fn when(value: Option<DateTime<Utc>>) -> String {
    value.map_or_else(
        || ABSENT.to_owned(),
        |value| value.format("%Y-%m-%d %H:%M").to_string(),
    )
}

/// A timestamp as time of day, for rows already under a dated heading.
pub fn clock(value: Option<DateTime<Utc>>) -> String {
    value.map_or_else(
        || ABSENT.to_owned(),
        |value| value.format("%H:%M:%S").to_string(),
    )
}

/// Milliseconds as a span someone reads: `2h 05m`, `4m 12s`, `0.8s`.
pub fn duration(value: Option<i64>) -> String {
    let Some(value) = value else {
        return ABSENT.to_owned();
    };
    if value >= HOUR {
        return format!("{}h {:02}m", value / HOUR, value % HOUR / MINUTE);
    }
    if value >= MINUTE {
        return format!("{}m {:02}s", value / MINUTE, value % MINUTE / 1000);
    }
    format!("{:.1}s", value as f64 / 1000.0)
}

/// How long before `now` something happened, in the largest unit it fills: `3d ago`.
///
/// One unit, not two: a list is scanned for which session is recent, and `3d 4h` answers a
/// question nobody asked of it.
pub fn ago(value: Option<DateTime<Utc>>, now: DateTime<Utc>) -> String {
    let Some(value) = value else {
        return ABSENT.to_owned();
    };
    // A timestamp ahead of the reader's clock is skew between the machine that wrote the
    // session and the one showing it, and the present is the honest reading of it.
    let elapsed = (now - value).num_milliseconds();
    if elapsed < MINUTE {
        return "just now".to_owned();
    }
    if elapsed < HOUR {
        return format!("{}m ago", elapsed / MINUTE);
    }
    if elapsed < DAY {
        return format!("{}h ago", elapsed / HOUR);
    }
    format!("{}d ago", elapsed / DAY)
}

/// A fraction as a percentage, to one decimal: `0.022` prints `2.2%`.
pub fn percent(value: Option<f64>) -> String {
    value.map_or_else(
        || ABSENT.to_owned(),
        |value| format!("{:.1}%", 100.0 * value),
    )
}

/// A part of a whole as a percentage, to one decimal: `2.2%`.
///
/// A whole of zero or NULL is a gap rather than 0%: no errors in no tool calls, and no spend
/// to take a share of, are things the store does not know rather than rates it recorded.
pub fn share(part: Option<f64>, whole: Option<f64>) -> String {
    match (part, whole) {
        (Some(part), Some(whole)) if whole != 0.0 => percent(Some(part / whole)),
        _ => ABSENT.to_owned(),
    }
}
