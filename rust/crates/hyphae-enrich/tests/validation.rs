//! What the enricher accepts back from the model, and how an item fails.
//!
//! Ported from `tests/enrich/test_validation.py`. Every payload here is **invented**, and
//! labelled as such at each call site: model output is not transcript data, so there is no
//! recorded session to draw it from, and a real credential could never be committed.

use hyphae_enrich::taxonomy;
use hyphae_enrich::validation::{Enrichment, FailureKind, validate};
use serde_json::{Map, Value, json};

/// A well-formed model output (invented), with fields replaced per test.
fn payload(overrides: Value) -> Map<String, Value> {
    let mut built = json!({
        "description": "Fixed a failing parser test and re-ran the suite.",
        "category": "fix_bug",
        "outcome": "completed",
        "friction": Value::Null,
    });
    let built = built.as_object_mut().expect("the payload is an object");
    for (field, value) in overrides.as_object().expect("the overrides are an object") {
        built.insert(field.clone(), value.clone());
    }
    built.clone()
}

/// The vocabulary the validator accepts is exactly the taxonomy's members, both ways.
#[test]
fn every_taxonomy_member_validates() {
    let vocabulary = taxonomy::enrichment();
    // If every member is round-tripped through the validator as a raw string...
    let categories: Vec<String> = vocabulary
        .categories
        .iter()
        .map(|member| {
            validate(&payload(json!({ "category": member })))
                .expect("a taxonomy member validates")
                .category
        })
        .collect();
    let outcomes: Vec<String> = vocabulary
        .outcomes
        .iter()
        .map(|member| {
            validate(&payload(json!({ "outcome": member })))
                .expect("a taxonomy member validates")
                .outcome
        })
        .collect();
    // ...then the accepted set is the vocabulary, exactly — a member added without a
    // definition cannot widen it quietly, and one dropped cannot linger...
    assert_eq!(categories, vocabulary.categories);
    assert_eq!(outcomes, vocabulary.outcomes);
    assert!(!categories.is_empty() && !outcomes.is_empty());
    // ...every member has the one-line definition the prompt is written from, since a member
    // the classifier is never told about is a member it will not use...
    for member in &vocabulary.categories {
        assert!(!taxonomy::definition(&vocabulary.category_definitions, member).is_empty());
    }
    for member in &vocabulary.outcomes {
        assert!(!taxonomy::definition(&vocabulary.outcome_definitions, member).is_empty());
    }
    // ...and nothing beyond the members carries one, so a definition cannot outlive the
    // member it defines.
    assert_eq!(
        vocabulary.category_definitions.len(),
        vocabulary.categories.len()
    );
    assert_eq!(
        vocabulary.outcome_definitions.len(),
        vocabulary.outcomes.len()
    );
    // ...and the version rows are stamped with is a number they can be compared against.
    assert!(vocabulary.taxonomy_version > 0);
}

/// A category or outcome outside the taxonomy fails the item instead of widening it.
#[test]
fn an_out_of_vocabulary_value_fails_the_item() {
    for (field, value) in [
        // A plausible synonym of a real member, which is exactly what an open vocabulary
        // would fragment into...
        ("category", "refactoring"),
        // ...and an outcome the taxonomy does not carry.
        ("outcome", "succeeded"),
    ] {
        let failure = validate(&payload(json!({ field: value })))
            .expect_err("an out-of-vocabulary value is refused");
        assert_eq!(failure.kind, FailureKind::InvalidOutput, "{field}");
        // ...and the raised text names the field but not what the model said, since anything
        // the model wrote may be quoted from a private transcript.
        assert!(failure.to_string().contains(field));
        assert!(!failure.to_string().contains(value), "{field}");
    }
}

/// A description carrying a credential shape fails, and the failure never repeats it.
///
/// This screen is the one control between a credential sitting in a transcript and a
/// description pasted into a committed report.
#[test]
fn a_secret_shape_fails_the_item_without_repeating_it() {
    for secret in [
        // Invented credentials — obviously fake, in the shapes the screen knows.
        "sk-ant-api03-0000000000000000000000000000000000000000000000",
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----",
        "ghp_0000000000000000000000000000000000",
    ] {
        let description = format!("Rotated the key {secret} and re-deployed.");
        let failure = validate(&payload(json!({ "description": description })))
            .expect_err("a credential shape is refused");
        assert_eq!(failure.kind, FailureKind::SecretShape, "{secret}");
        assert!(!failure.to_string().contains(secret));
    }
}

/// The screen covers every string the model wrote, not just the description.
#[test]
fn a_secret_in_the_friction_line_fails_too() {
    let failure = validate(&payload(
        json!({ "friction": "Retried after AKIAIOSFODNN7EXAMPLE was rejected." }),
    ))
    .expect_err("a credential in the friction line is refused");
    assert_eq!(failure.kind, FailureKind::SecretShape);
}

/// A model that reports no friction produces no friction, rather than an empty string.
#[test]
fn a_well_formed_output_validates_with_null_friction() {
    // If the model reports no friction — as null, and as the empty string it sometimes
    // sends instead...
    for absent in [Value::Null, Value::from("  ")] {
        assert_eq!(
            validate(&payload(json!({ "friction": absent }))).expect("a well-formed answer"),
            Enrichment {
                description: "Fixed a failing parser test and re-ran the suite.".to_owned(),
                category: "fix_bug".to_owned(),
                outcome: "completed".to_owned(),
                // ...then friction stays absent, so `friction IS NULL` means what it says.
                friction: None,
            }
        );
    }
}

/// Output that does not fit the schema fails the item rather than writing a partial row.
#[test]
fn a_malformed_output_fails_the_item() {
    let mut missing_category = payload(json!({}));
    missing_category.remove("category");
    for broken in [
        // A field the schema requires, missing...
        missing_category,
        // ...a description of the wrong type...
        payload(json!({ "description": 12 })),
        // ...and one that is present but says nothing.
        payload(json!({ "description": "   " })),
    ] {
        let failure = validate(&broken).expect_err("malformed output is refused");
        assert_eq!(failure.kind, FailureKind::InvalidOutput);
    }
}
