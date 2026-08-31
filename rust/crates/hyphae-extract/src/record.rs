//! Reading a transcript record: the dict accesses the Python walk makes, in one place.
//!
//! The design asks for `extract/transcript.py`'s defensive `Value` walk ported guard for
//! guard rather than replaced by structs, because Claude Code changes these shapes without
//! notice and the fields a record carries depend on the fields it carries. So the readers
//! here mirror Python's two access styles exactly:
//!
//! - [`field`] and its typed followers are `record["key"]` — the key must be there
//! - [`opt`] and its followers are `record.get("key")` — absent and JSON `null` both read as
//!   "no value", which is what Python's `None` collapses them to
//!
//! No reader ever puts a *value* in an error message. Transcripts are private and these
//! errors reach logs; the key and the type are all a message may carry.

use chrono::{DateTime, Utc};
use serde_json::Value;

use crate::ExtractError;

type Result<T> = std::result::Result<T, ExtractError>;

/// `record[key]`, which raises when the key is absent. A `null` value is a value.
pub fn field<'a>(record: &'a Value, key: &str) -> Result<&'a Value> {
    record
        .get(key)
        .ok_or_else(|| ExtractError::Schema(format!("a record carries no `{key}`")))
}

/// `record.get(key)`: absent and `null` are one answer, as they are in Python.
pub fn opt<'a>(record: &'a Value, key: &str) -> Option<&'a Value> {
    match record.get(key) {
        None | Some(Value::Null) => None,
        some => some,
    }
}

pub fn text<'a>(record: &'a Value, key: &str) -> Result<&'a str> {
    as_text(field(record, key)?, key)
}

pub fn opt_text<'a>(record: &'a Value, key: &str) -> Result<Option<&'a str>> {
    opt(record, key)
        .map(|value| as_text(value, key))
        .transpose()
}

pub fn int(record: &Value, key: &str) -> Result<i64> {
    as_int(field(record, key)?, key)
}

pub fn opt_int(record: &Value, key: &str) -> Result<Option<i64>> {
    opt(record, key).map(|value| as_int(value, key)).transpose()
}

/// A `message.content` list, or the record's own `content` list.
pub fn array<'a>(record: &'a Value, key: &str) -> Result<&'a Vec<Value>> {
    match field(record, key)? {
        Value::Array(items) => Ok(items),
        other => Err(wrong_type(key, other)),
    }
}

/// Python's truthiness on an optional flag: absent, `null`, `false`, `0` and `""` all mean no.
///
/// The record flags this walk reads — `isMeta`, `isSidechain`, `is_error` — are all written
/// this way, and the Python readers test them with a bare `if`.
pub fn truthy(record: &Value, key: &str) -> bool {
    match record.get(key) {
        None | Some(Value::Null) | Some(Value::Bool(false)) => false,
        Some(Value::Bool(true)) => true,
        Some(Value::Number(number)) => number.as_f64().is_some_and(|value| value != 0.0),
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Array(items)) => !items.is_empty(),
        Some(Value::Object(members)) => !members.is_empty(),
    }
}

pub fn as_text<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value.as_str().ok_or_else(|| wrong_type(key, value))
}

pub fn as_int(value: &Value, key: &str) -> Result<i64> {
    value.as_i64().ok_or_else(|| wrong_type(key, value))
}

/// The instant an ISO-8601 timestamp names, truncated to the microsecond the store keeps.
///
/// Claude Code writes `...Z`; an offset is accepted too, since `datetime.fromisoformat` takes
/// one. Anything else stops the run rather than being read as a different moment.
pub fn parse_timestamp(moment: &str) -> Result<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(moment)
        .map(|parsed| parsed.with_timezone(&Utc))
        .map_err(|_| ExtractError::Schema(format!("a timestamp this parser cannot read: {moment}")))
}

fn wrong_type(key: &str, value: &Value) -> ExtractError {
    ExtractError::Schema(format!(
        "`{key}` is {}, which this reader cannot read",
        type_of(value)
    ))
}

/// What a value is, for a message that may not carry the value itself.
fn type_of(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "a boolean",
        Value::Number(_) => "a number",
        Value::String(_) => "a string",
        Value::Array(_) => "a list",
        Value::Object(_) => "an object",
    }
}

/// `record[key]` where the schema allows the value to be `null`: the key must be there, but
/// `null` reads as no value, exactly as Python's subscript then hands back `None`.
pub fn nullable_text<'a>(record: &'a Value, key: &str) -> Result<Option<&'a str>> {
    match field(record, key)? {
        Value::Null => Ok(None),
        value => as_text(value, key).map(Some),
    }
}
