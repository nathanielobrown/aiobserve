//! `hp query`: one library query run, and what it prints on each of the three channels.
//!
//! Ported from `src/hyphae/cli.py`'s `_query`. The split matters to a reader piping the
//! output: rows go to stdout and everything said *about* them goes to stderr, so an analysis
//! reading stdout never has a line of prose in its data.

use std::io::Write;

use chrono::{NaiveDate, Utc};
use hyphae_analyze::{QueryResult, Request};
use hyphae_model::clock;
use hyphae_store::Value;
use indexmap::IndexMap;

use crate::CliError;

/// Run the query and print it. `csv` sends rows to `out` and the citation to `err`.
pub fn query(
    db: &std::path::Path,
    name: &str,
    request: &Request,
    csv: bool,
    out: &mut dyn Write,
    err: &mut dyn Write,
) -> Result<(), CliError> {
    let result = hyphae_analyze::run(db, name, request).map_err(CliError::refusal_from)?;
    // The count the corpus predicate could not place, and the citation under `--csv`, go to
    // stderr: a piped analysis reads stdout, and a line of prose in it breaks silently.
    if let Some(excluded) = result.unplaceable_sessions {
        writeln!(err, "excluded {excluded} session(s) with no project_dir")?;
    }
    let citation = result.citation();
    if csv {
        writeln!(err, "{citation}")?;
        write_csv(&result, out)?;
    } else {
        writeln!(out, "{citation}")?;
        writeln!(out, "{}", table(&result))?;
    }
    Ok(())
}

/// The default `--as-of`: today, off the one clock the workspace reads.
pub fn today() -> NaiveDate {
    clock::utcnow().date_naive()
}

/// `KEY=VALUE` pairs as the runner takes them, in the order the command line gave them.
///
/// A repeated key keeps the last value, which is what Python's `dict(...)` comprehension over
/// the same pairs does.
pub fn params(given: &[String]) -> Result<IndexMap<String, String>, CliError> {
    given
        .iter()
        .map(|pair| {
            pair.split_once('=')
                .map(|(key, value)| (key.to_owned(), value.to_owned()))
                .ok_or_else(|| CliError::refusal("--param takes KEY=VALUE"))
        })
        .collect()
}

/// The rows as an aligned table, wide enough for the values it holds.
fn table(result: &QueryResult) -> String {
    let cells: Vec<Vec<String>> = result
        .rows
        .iter()
        .map(|row| row.values().iter().map(cell).collect())
        .collect();
    let widths: Vec<usize> = result
        .columns
        .iter()
        .enumerate()
        .map(|(at, column)| {
            cells
                .iter()
                .map(|row| row[at].chars().count())
                .chain([column.chars().count()])
                .max()
                .unwrap_or_default()
        })
        .collect();
    let mut lines = vec![pad(result.columns.iter().map(String::as_str), &widths)];
    lines.push(
        widths
            .iter()
            .map(|width| "-".repeat(*width))
            .collect::<Vec<_>>()
            .join("  "),
    );
    lines.extend(
        cells
            .iter()
            .map(|row| pad(row.iter().map(String::as_str), &widths)),
    );
    lines.join("\n")
}

/// One table line: each value left-justified in its column's width, two spaces between.
///
/// Trailing whitespace and all — Python's `str.ljust` pads the last column too, and a test
/// that stripped it here would stop seeing a column that had drifted wider.
fn pad<'a>(values: impl Iterator<Item = &'a str>, widths: &[usize]) -> String {
    values
        .zip(widths)
        .map(|(value, width)| {
            let padding = width.saturating_sub(value.chars().count());
            format!("{value}{}", " ".repeat(padding))
        })
        .collect::<Vec<_>>()
        .join("  ")
}

/// The result as CSV on `out`, header first.
///
/// Python writes it with `csv.writer`, whose `excel` dialect ends every record `\r\n` and
/// quotes only a field that needs it. Both are part of what a reader's parser sees, so they
/// are ported rather than tidied.
fn write_csv(result: &QueryResult, out: &mut dyn Write) -> std::io::Result<()> {
    let header: Vec<String> = result.columns.clone();
    let rows = std::iter::once(header).chain(
        result
            .rows
            .iter()
            .map(|row| row.values().iter().map(cell).collect()),
    );
    for row in rows {
        let record: Vec<String> = row.iter().map(|value| quoted(value)).collect();
        write!(out, "{}\r\n", record.join(","))?;
    }
    Ok(())
}

/// One CSV field: quoted where the `excel` dialect would quote it, with `"` doubled.
fn quoted(field: &str) -> String {
    if field.contains([',', '"', '\r', '\n']) {
        format!("\"{}\"", field.replace('"', "\"\""))
    } else {
        field.to_owned()
    }
}

/// One stored value as text, the way Python's `str` prints what DuckDB handed it.
///
/// NULL is the empty string rather than `None`: a cell holding nothing should read as a gap
/// in both the table and the CSV, and `NULL` would be indistinguishable from the text.
fn cell(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        // Python's `bool` prints capitalized, and DuckDB hands one back for a BOOLEAN column.
        Value::Boolean(flag) => if *flag { "True" } else { "False" }.to_owned(),
        Value::TinyInt(number) => number.to_string(),
        Value::SmallInt(number) => number.to_string(),
        Value::Int(number) => number.to_string(),
        Value::BigInt(number) => number.to_string(),
        Value::HugeInt(number) => number.to_string(),
        Value::UTinyInt(number) => number.to_string(),
        Value::USmallInt(number) => number.to_string(),
        Value::UInt(number) => number.to_string(),
        Value::UBigInt(number) => number.to_string(),
        Value::UHugeInt(number) => number.to_string(),
        Value::Float(number) => float(f64::from(*number)),
        Value::Double(number) => float(*number),
        Value::Decimal(number) => number.to_string(),
        Value::Text(text) | Value::Enum(text) => text.clone(),
        Value::Date32(days) => date(*days).to_string(),
        Value::Timestamp(unit, count) => instant(unit.to_micros(*count)),
        // Anything else is a shape no library query answers with today. Loud rather than
        // guessed: a `LIST` printed the wrong way would be a quiet parity gap in a report.
        other => panic!("no printed form for {other:?}"),
    }
}

/// A float the way Python's `str` prints it: shortest round trip, with a `.0` where the value
/// is whole. Rust's own `Display` gives the first and drops the second.
fn float(number: f64) -> String {
    let printed = format!("{number}");
    if printed.contains(['.', 'e', 'E']) || !number.is_finite() {
        printed
    } else {
        format!("{printed}.0")
    }
}

/// A DATE as `datetime.date.__str__` prints it.
fn date(days: i32) -> NaiveDate {
    NaiveDate::from_num_days_from_ce_opt(days + 719_163).expect("a DATE DuckDB stored")
}

/// A TIMESTAMPTZ as `datetime.__str__` prints one: a space between the date and the time, the
/// seconds' fraction only where there is one, and the offset written out.
///
/// Every timestamp column the store declares is TIMESTAMPTZ and every connection reads UTC
/// (`hyphae_store::schema`), so the offset is always zero.
fn instant(micros: i64) -> String {
    use chrono::TimeZone as _;
    let moment = Utc
        .timestamp_micros(micros)
        .single()
        .expect("a timestamp DuckDB stored");
    let fraction = if moment.timestamp_subsec_micros() == 0 {
        String::new()
    } else {
        format!(".{:06}", moment.timestamp_subsec_micros())
    };
    format!("{}{fraction}+00:00", moment.format("%Y-%m-%d %H:%M:%S"))
}
