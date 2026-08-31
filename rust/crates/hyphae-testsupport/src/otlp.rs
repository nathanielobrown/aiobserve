//! Reading a shipped span back: the assertion surface the OTLP tiers share.
//!
//! The twin of `tests/export/conftest.py`'s reading half. Every id here is recomputed from
//! the design's key rather than imported from the mapper, so a leaf comparing the two is
//! comparing two derivations and not one.

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use hyphae_export::otlp::{METADATA_ONLY, session_spans};
use hyphae_model::SessionTrace;
use opentelemetry_proto::tonic::common::v1::{AnyValue, KeyValue, any_value};
use opentelemetry_proto::tonic::trace::v1::Span;
use sha2::{Digest, Sha256};

/// Every span one trace ships, with no text in them — what most leaves shape.
///
/// # Panics
/// When the trace holds a shape the mapper refuses; a fixture that cannot ship is a leaf's
/// premise, not its subject.
pub fn emitted(trace: &SessionTrace) -> Vec<Span> {
    session_spans(trace, &METADATA_ONLY).expect("the fixture shapes")
}

/// One attribute value, read back off the wire.
///
/// `Bytes` and the two composite kinds have no `PartialEq`-friendly reading and this exporter
/// emits none, so a leaf meeting one is looking at a mapper bug.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Bool(bool),
    Int(i64),
    Double(f64),
    Str(String),
}

/// The one field an OTLP `AnyValue` set. An empty one is a mapper bug, so it panics.
///
/// # Panics
/// When the attribute carries no value, or one of the kinds this exporter never emits.
pub fn any_value(value: &AnyValue) -> Value {
    match value.value.as_ref() {
        Some(any_value::Value::BoolValue(value)) => Value::Bool(*value),
        Some(any_value::Value::IntValue(value)) => Value::Int(*value),
        Some(any_value::Value::DoubleValue(value)) => Value::Double(*value),
        Some(any_value::Value::StringValue(value)) => Value::Str(value.clone()),
        Some(other) => panic!("an attribute arrived carrying {other:?}, which nothing emits"),
        None => panic!("an attribute arrived carrying no value at all"),
    }
}

/// A list of OTLP attributes as a plain map, so a leaf can compare the whole set at once.
///
/// # Panics
/// When two attributes share a key, which would silently drop one.
pub fn read(attributes: &[KeyValue]) -> BTreeMap<String, Value> {
    let mut read = BTreeMap::new();
    for attribute in attributes {
        let value = any_value(
            attribute
                .value
                .as_ref()
                .expect("an attribute carries a value"),
        );
        assert!(
            read.insert(attribute.key.clone(), value).is_none(),
            "{} arrived twice on one attribute list",
            attribute.key
        );
    }
    read
}

/// One span's attributes as a plain map.
pub fn attributes(span: &Span) -> BTreeMap<String, Value> {
    read(&span.attributes)
}

/// The span id the design specifies, recomputed here rather than imported.
///
/// Digest **bytes** sliced to 8 — `hex()[..8]` is also 8 bytes and would pass any length-only
/// assertion while giving 32-bit ids.
pub fn digest(session_id: &str, kind: &str, source: &str, natural_id: &str) -> Vec<u8> {
    let key = format!("{session_id}/{kind}/{source}/{natural_id}");
    Sha256::digest(key.as_bytes())[..8].to_vec()
}

/// The single span carrying an id, so a miss reads as a missing span, not an index error.
///
/// # Panics
/// When the id names no span, or more than one.
pub fn one<'a>(spans: &'a [Span], span: &[u8]) -> &'a Span {
    let found: Vec<&Span> = spans
        .iter()
        .filter(|candidate| candidate.span_id == span)
        .collect();
    assert_eq!(
        found.len(),
        1,
        "expected one span keyed {}, found {}",
        hex(span),
        found.len()
    );
    found[0]
}

/// A timestamp in the units a span carries it, at the microsecond resolution transcripts
/// record — computed in integers, since a float loses the microseconds.
pub fn nanos(value: DateTime<Utc>) -> u64 {
    (value.timestamp_micros() * 1_000) as u64
}

/// An id as the hex a failure message reads by.
pub fn hex(id: &[u8]) -> String {
    id.iter().map(|byte| format!("{byte:02x}")).collect()
}
