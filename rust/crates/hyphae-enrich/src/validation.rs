//! The output side: what a valid enrichment is, and how an item fails to produce one.
//!
//! Ported from `src/hyphae/enrich/validation.py`. Nothing here ever repeats what the model
//! wrote. The descriptions are derived from private transcripts, so a failure record carries
//! the item's key and a kind and has nowhere to put prose — the crash summary is keys-only by
//! construction rather than by discipline.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value};

use crate::taxonomy;

/// Why an item produced no row. The crash summary groups by these.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum FailureKind {
    /// The CLI refused or errored on the request.
    ApiError,
    /// The call was still running at the client's per-item deadline.
    Timeout,
    /// The CLI's answer envelope was not the shape the client is pinned to. Only after the
    /// round's canary has proved the shape once — the canary itself crashes the run.
    Drift,
    /// Never attempted: the client's breaker ended the round before this item was sent.
    Aborted,
    /// The model answered, but not in the shape the output schema requires.
    InvalidOutput,
    /// The answer carried something shaped like a credential.
    SecretShape,
}

impl FailureKind {
    /// The word a failure record and a crash summary print, as Python's `StrEnum` spells it.
    pub fn word(self) -> &'static str {
        match self {
            Self::ApiError => "api_error",
            Self::Timeout => "timeout",
            Self::Drift => "drift",
            Self::Aborted => "aborted",
            Self::InvalidOutput => "invalid_output",
            Self::SecretShape => "secret_shape",
        }
    }
}

impl std::fmt::Display for FailureKind {
    fn fmt(&self, into: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        into.write_str(self.word())
    }
}

/// Shapes that mean a credential leaked from a transcript into a description.
///
/// A heuristic, not a guarantee (`plans/enrichment/design.md` books that as an open question) —
/// but the instruction to describe rather than quote is not a control on its own.
static SECRET_SHAPES: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
        r"sk-[A-Za-z0-9_\-]{16,}", // Anthropic and OpenAI style API keys
        r"AKIA[0-9A-Z]{16}",       // AWS access key id
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----", // PEM private key
        r"gh[pousr]_[A-Za-z0-9]{20,}", // GitHub token
        r"github_pat_[A-Za-z0-9_]{20,}", // GitHub fine-grained token
        r"xox[abprs]-[A-Za-z0-9-]{10,}", // Slack token
        r"AIza[0-9A-Za-z_\-]{35}", // Google API key
    ]
    .into_iter()
    .map(|shape| Regex::new(shape).expect("the credential shapes compile"))
    .collect()
});

/// One accepted model answer about one item.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Enrichment {
    /// One or two sentences saying what the item did.
    pub description: String,
    /// A member of the closed vocabulary the generation bridge carries.
    pub category: String,
    pub outcome: String,
    /// One line naming visible struggle. None when the records show none, the common case.
    pub friction: Option<String>,
}

/// An item that wrote no row, and why. Carries no model output — there is no field for it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ItemFailure {
    pub key: String,
    pub kind: FailureKind,
}

/// The model's answer was rejected. The message names the field, never its value.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{kind}: {reason}")]
pub struct InvalidOutput {
    pub kind: FailureKind,
    /// Which field was wrong and how — never what it held.
    pub reason: String,
}

/// Turn one model answer into an [`Enrichment`], or reject it.
///
/// Returns the failure rather than building a record from it, so the caller — which holds the
/// item's key — is the only thing that can build one.
pub fn validate(output: &Map<String, Value>) -> Result<Enrichment, InvalidOutput> {
    let description = required_text(output, "description")?;
    let friction = optional_text(output, "friction")?;
    for (field, value) in [
        ("description", Some(&description)),
        ("friction", friction.as_ref()),
    ] {
        if value.is_some_and(|held| SECRET_SHAPES.iter().any(|shape| shape.is_match(held))) {
            return Err(InvalidOutput {
                kind: FailureKind::SecretShape,
                reason: format!("the {field} matched a credential shape"),
            });
        }
    }
    let vocabulary = taxonomy::enrichment();
    Ok(Enrichment {
        description,
        category: member(output, "category", &vocabulary.categories)?,
        outcome: member(output, "outcome", &vocabulary.outcomes)?,
        friction,
    })
}

fn required_text(output: &Map<String, Value>, field: &str) -> Result<String, InvalidOutput> {
    match output.get(field).and_then(Value::as_str) {
        Some(value) if !value.trim().is_empty() => Ok(value.trim().to_owned()),
        _ => Err(InvalidOutput {
            kind: FailureKind::InvalidOutput,
            reason: format!("{field} is missing or not text"),
        }),
    }
}

/// Absent, null, and blank all mean the same thing, and all become None.
fn optional_text(
    output: &Map<String, Value>,
    field: &str,
) -> Result<Option<String>, InvalidOutput> {
    match output.get(field) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => {
            Ok(Some(value.trim().to_owned()).filter(|held| !held.is_empty()))
        }
        Some(_) => Err(InvalidOutput {
            kind: FailureKind::InvalidOutput,
            reason: format!("{field} is not text"),
        }),
    }
}

fn member(
    output: &Map<String, Value>,
    field: &str,
    vocabulary: &[String],
) -> Result<String, InvalidOutput> {
    match output.get(field).and_then(Value::as_str) {
        Some(value) if taxonomy::is_member(vocabulary, value) => Ok(value.to_owned()),
        _ => Err(InvalidOutput {
            kind: FailureKind::InvalidOutput,
            reason: format!("{field} is not a member of the taxonomy"),
        }),
    }
}
