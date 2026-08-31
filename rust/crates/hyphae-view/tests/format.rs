//! What a page prints for one value, against the Python that prints it today.
//!
//! The cases live in `tests/fixtures/format_cases.json`; `format_cases_from_python.py` beside
//! it writes them. Nothing here is invented: the leaf compares two implementations of the same
//! function, so what it can catch is a divergence rather than a preference.

use std::fs;
use std::path::PathBuf;

use chrono::{DateTime, Duration, Utc};
use hyphae_view::format as fmt;
use serde_json::Value;

fn cases() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/format_cases.json");
    let text =
        fs::read_to_string(&path).expect("the generated cases are committed beside the test");
    serde_json::from_str(&text).expect("the generator writes JSON")
}

fn shown<'a>(case: &'a Value, name: &str) -> &'a str {
    case[name]
        .as_str()
        .unwrap_or_else(|| panic!("the generator writes `{name}` for every case"))
}

fn stamp(text: &str) -> DateTime<Utc> {
    text.parse().expect("the generator writes RFC 3339")
}

#[test]
fn a_number_prints_the_way_the_python_viewer_prints_it() {
    let cases = cases();
    for case in cases["money"].as_array().expect("money is a list") {
        let value = case["value"].as_f64().expect("a cost is a float");
        assert_eq!(
            fmt::money(Some(value)),
            shown(case, "money"),
            "money {value}"
        );
        assert_eq!(
            fmt::charge(Some(value)),
            shown(case, "charge"),
            "charge {value}"
        );
    }
    for case in cases["counts"].as_array().expect("counts is a list") {
        let value = case["value"].as_i64().expect("a count is an integer");
        assert_eq!(
            fmt::count(Some(value)),
            shown(case, "count"),
            "count {value}"
        );
        assert_eq!(
            fmt::signed(Some(value)),
            shown(case, "signed"),
            "signed {value}"
        );
    }
    for case in cases["shares"].as_array().expect("shares is a list") {
        let part = case["part"].as_f64().expect("a part is a float");
        let whole = case["whole"].as_f64().expect("a whole is a float");
        assert_eq!(
            fmt::share(Some(part), Some(whole)),
            shown(case, "share"),
            "share {part}"
        );
        assert_eq!(
            fmt::percent(Some(part)),
            shown(case, "percent"),
            "percent {part}"
        );
    }
}

#[test]
fn a_span_prints_the_way_the_python_viewer_prints_it() {
    let cases = cases();
    for case in cases["durations"].as_array().expect("durations is a list") {
        let value = case["value"].as_i64().expect("a span is milliseconds");
        assert_eq!(
            fmt::duration(Some(value)),
            shown(case, "shown"),
            "duration {value}"
        );
    }
    // The generator's own instant, so the relative times are against a clock that cannot move.
    let now = stamp(
        cases["now"]
            .as_str()
            .expect("the generator writes its clock"),
    );
    for case in cases["agos"].as_array().expect("agos is a list") {
        let before = case["before_ms"].as_i64().expect("a span is milliseconds");
        let at = now - Duration::milliseconds(before);
        assert_eq!(
            fmt::ago(Some(at), now),
            shown(case, "shown"),
            "ago {before}"
        );
    }
    for case in cases["stamps"].as_array().expect("stamps is a list") {
        let at = stamp(shown(case, "value"));
        assert_eq!(fmt::when(Some(at)), shown(case, "when"), "when {at}");
        assert_eq!(fmt::clock(Some(at)), shown(case, "clock"), "clock {at}");
    }
}

#[test]
fn a_string_prints_the_way_the_python_viewer_prints_it() {
    let cases = cases();
    for case in cases["paths"].as_array().expect("paths is a list") {
        let (value, home) = (shown(case, "value"), shown(case, "home"));
        assert_eq!(
            fmt::path(Some(value), home),
            shown(case, "shown"),
            "path {value}"
        );
    }
    for case in cases["cuts"].as_array().expect("cuts is a list") {
        let value = shown(case, "value");
        let size = case["size"].as_u64().expect("a width is a count") as usize;
        assert_eq!(
            fmt::cut(value, size),
            shown(case, "shown"),
            "cut {value:?} at {size}"
        );
    }
    for case in cases["flags"].as_array().expect("flags is a list") {
        let value = case["value"].as_bool().expect("a flag is a boolean");
        assert_eq!(fmt::flag(value), shown(case, "shown"), "flag {value}");
    }
}

/// The absent case for every function that takes one, which the generator cannot write.
#[test]
fn an_absent_value_prints_the_dash() {
    assert_eq!(fmt::money(None), fmt::ABSENT);
    assert_eq!(fmt::charge(None), fmt::ABSENT);
    assert_eq!(fmt::count(None), fmt::ABSENT);
    assert_eq!(fmt::signed(None), fmt::ABSENT);
    assert_eq!(fmt::text(None), fmt::ABSENT);
    assert_eq!(fmt::path(None, "/Users/someone"), fmt::ABSENT);
    assert_eq!(fmt::when(None), fmt::ABSENT);
    assert_eq!(fmt::clock(None), fmt::ABSENT);
    assert_eq!(fmt::duration(None), fmt::ABSENT);
    #[expect(
        clippy::disallowed_methods,
        reason = "the real clock: this leaf asserts the absent-value case, which no instant changes"
    )]
    let now = Utc::now();
    assert_eq!(fmt::ago(None, now), fmt::ABSENT);
    assert_eq!(fmt::percent(None), fmt::ABSENT);
    // A whole of zero is a gap rather than 0%, which is the one absent case with a value in it.
    assert_eq!(fmt::share(Some(1.0), Some(0.0)), fmt::ABSENT);
    assert_eq!(fmt::share(None, Some(1.0)), fmt::ABSENT);
    assert_eq!(fmt::share(Some(1.0), None), fmt::ABSENT);
}

/// The clock the gallery freezes, which is the one thing here with no Python twin: the
/// Python patches `fmt.utcnow` after import, and a compiled server has no such seam.
///
/// Its own process, which is what nextest gives every leaf — the clock is read once and a
/// second test setting the variable afterwards would find it already answered.
#[test]
fn a_named_instant_freezes_the_clock() {
    // SAFETY: nextest runs each test in its own process, and no thread has started here.
    unsafe { std::env::set_var("HYPHAE_FIXED_NOW", "2026-08-30T12:00:00Z") };
    let frozen = stamp("2026-08-30T12:00:00+00:00");
    assert_eq!(fmt::utcnow(), frozen);
    // And it stays frozen: a second read is the same instant, not a moved one.
    assert_eq!(fmt::utcnow(), frozen);
    assert_eq!(
        fmt::ago(Some(frozen - Duration::hours(3)), fmt::utcnow()),
        "3h ago"
    );
}
