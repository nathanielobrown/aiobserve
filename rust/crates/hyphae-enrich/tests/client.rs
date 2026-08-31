//! One item's round trip: what a recorded envelope becomes, and what is retried or refused.
//!
//! Ported from `tests/enrich/test_client.py`. The fake seam and every envelope are in
//! `hyphae_testsupport::fake_cli`; what happens when many items run at once is in
//! `client_pool.rs`. Here a client answers one item at a time, so each leaf reads a single
//! reply against a single answer.

use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use hyphae_enrich::client::{ATTEMPTS, BREAKER_BOUND, BatchClient, CliClient, ITEM_TIMEOUT};
use hyphae_enrich::validation::{Enrichment, FailureKind, validate};
use hyphae_enrich::{Answer, RoundError};
use hyphae_testsupport::fake_cli::{
    FakeCli, MODEL, OTHER_MODEL, Reply, content_of, errors, hangs, kinds, mutated, recorded_output,
    recorded_usage, requests_for, succeeds, without,
};
use serde_json::json;

/// The replies a fake answers, keyed the way the client sends: one render per item key.
fn script(replies: &[(&str, Reply)]) -> HashMap<String, Reply> {
    replies
        .iter()
        .map(|(key, reply)| (content_of(key), reply.clone()))
        .collect()
}

/// A client of the given width, spending through the fake rather than a process.
fn client(model: &str, concurrency: usize, fake: &Arc<FakeCli>) -> CliClient {
    CliClient::new(model, concurrency, Box::new(fake.clone())).expect("a pool one item wide")
}

/// One reply, sent to one item, over a client only that item passes through.
fn one_item(reply: Reply) -> (Arc<FakeCli>, Result<Vec<Answer>, RoundError>) {
    let fake = FakeCli::new(script(&[("item-0", reply)]));
    let answers = client(MODEL, 1, &fake).submit(&requests_for(&["item-0"]));
    (fake, answers)
}

/// A real CLI envelope becomes the answer `validation::validate` then accepts.
#[test]
fn a_recorded_envelope_becomes_a_validated_answer() {
    // If the CLI answers as it really answered on 2026-08-13...
    let (_, answers) = one_item(succeeds());
    let answers = answers.expect("the round finished");
    // ...then the envelope's `structured_output` is the output, handed over unchanged...
    assert_eq!(
        answers,
        vec![Answer::Succeeded {
            key: "item-0".to_owned(),
            output: recorded_output(),
        }]
    );
    // ...and validation accepts it, which is the next thing the enricher does with it.
    let Answer::Succeeded { output, .. } = &answers[0] else {
        panic!("the recorded envelope answered");
    };
    assert_eq!(
        validate(output).expect("the recorded answer validates"),
        Enrichment {
            description: recorded_output()["description"]
                .as_str()
                .expect("the recorded description is text")
                .to_owned(),
            category: "implement".to_owned(),
            outcome: "completed".to_owned(),
            friction: None,
        }
    );
}

/// An `is_error` answer fails its own item and nothing else.
#[test]
fn an_errored_envelope_fails_its_item() {
    // Derived from the recorded success: `is_error` flipped, the exit code left at zero, so
    // the flag alone decides.
    let (_, answers) = one_item(Reply::printing(
        &mutated(&[("is_error", json!(true))]).to_string(),
    ));
    assert_eq!(
        answers.expect("the round finished"),
        vec![Answer::Failed {
            key: "item-0".to_owned(),
            kind: FailureKind::ApiError,
        }]
    );
}

/// A CLI that exits nonzero is asked again before its item is given up on.
#[test]
fn a_nonzero_exit_is_retried_once() {
    for reply in [
        // The recorded logged-out call: exit 1 with a full envelope behind it...
        errors(),
        // ...and, invented because no crash was recorded, a CLI that dies before printing
        // anything. The exit code decides alone: nothing is parsed once it is nonzero, so
        // empty stdout never reaches the envelope reader.
        Reply::refusing(""),
    ] {
        let (fake, answers) = one_item(reply);
        // Whatever it printed, a nonzero exit is sent twice and fails once.
        assert_eq!(fake.calls().len(), ATTEMPTS);
        assert_eq!(ATTEMPTS, 2);
        assert_eq!(
            answers.expect("the round finished"),
            vec![Answer::Failed {
                key: "item-0".to_owned(),
                kind: FailureKind::ApiError,
            }]
        );
    }
}

/// A hung `claude` fails its item on a deadline every call carries.
#[test]
fn a_hung_call_times_out_and_is_retried_once() {
    let (fake, answers) = one_item(hangs());
    assert_eq!(
        answers.expect("the round finished"),
        vec![Answer::Failed {
            key: "item-0".to_owned(),
            kind: FailureKind::Timeout,
        }]
    );
    // Both attempts carried the same 300s ceiling — ~19x the worst wall time probed.
    let deadlines: Vec<u64> = fake
        .calls()
        .iter()
        .map(|call| call.timeout.as_secs())
        .collect();
    assert_eq!(deadlines, vec![300, 300]);
    assert_eq!(ITEM_TIMEOUT.as_secs(), 300);
}

/// An answer carrying no usable structured output is not worth resending.
#[test]
fn an_unusable_answer_fails_without_a_retry() {
    for envelope in [
        // Derived: the CLI omits `structured_output` when the model produced nothing
        // conforming, which is what the recorded logged-out envelope shows it doing...
        without(&["structured_output"]),
        // ...and, invented, an answer that is present but is not an object. `validate` reads
        // it by key, so a list would fail there — one item later, having already been stored.
        mutated(&[(
            "structured_output",
            json!([{ "description": "not an object" }]),
        )]),
    ] {
        let (fake, answers) = one_item(Reply::printing(&envelope.to_string()));
        assert_eq!(
            answers.expect("the round finished"),
            vec![Answer::Failed {
                key: "item-0".to_owned(),
                kind: FailureKind::InvalidOutput,
            }]
        );
        // A second identical send cannot improve a bad answer.
        assert_eq!(fake.calls().len(), 1);
    }
}

/// An answer cut off at the output cap is a bad answer, not a transport failure.
#[test]
fn a_truncated_answer_is_an_invalid_output() {
    // Derived: `stop_reason` swapped for the truncation value.
    let (_, answers) = one_item(Reply::printing(
        &mutated(&[("stop_reason", json!("max_tokens"))]).to_string(),
    ));
    assert_eq!(
        answers.expect("the round finished"),
        vec![Answer::Failed {
            key: "item-0".to_owned(),
            kind: FailureKind::InvalidOutput,
        }]
    );
}

/// Only a failure the transport might not repeat is worth a second call.
#[test]
fn only_transport_failures_are_retried() {
    for (reply, kind, calls) in [
        (
            Reply::printing(&mutated(&[("is_error", json!(true))]).to_string()),
            FailureKind::ApiError,
            2,
        ),
        (hangs(), FailureKind::Timeout, 2),
        (
            Reply::printing(&without(&["structured_output"]).to_string()),
            FailureKind::InvalidOutput,
            1,
        ),
        (
            Reply::printing(&without(&["modelUsage"]).to_string()),
            FailureKind::Drift,
            1,
        ),
    ] {
        // With a canary already answered, so every shape below is judged after the canary...
        let fake = FakeCli::new(script(&[("canary", succeeds()), ("item-0", reply)]));
        let answers = client(MODEL, 1, &fake)
            .submit(&requests_for(&["canary", "item-0"]))
            .expect("the round finished");
        // ...each shape fails as its own kind, and only the transport ones are sent twice.
        assert_eq!(
            kinds(&answers),
            BTreeMap::from([
                ("canary".to_owned(), None),
                ("item-0".to_owned(), Some(kind)),
            ])
        );
        assert_eq!(fake.calls().len(), 1 + calls);
    }
}

/// A CLI that stops writing any contracted field crashes the run on the first item.
///
/// Written out rather than read from `CONTRACT_FIELDS`, so a field dropped from that array
/// fails here instead of silently shrinking this leaf to the fields that are left.
#[test]
fn a_first_item_missing_a_contract_field_ends_the_run() {
    for field in ["is_error", "stop_reason", "modelUsage"] {
        // Derived: one field removed. One item's spend is the whole price of the crash — and
        // an unread field would otherwise surface mid-round, forfeiting the paid work.
        let (_, answers) = one_item(Reply::printing(&without(&[field]).to_string()));
        let RoundError::Drift(said) = answers.expect_err("the canary ends the run") else {
            panic!("a missing contract field is drift");
        };
        assert!(said.contains(field), "{said}");
    }
}

/// A first call that printed something other than JSON crashes the run.
#[test]
fn stdout_that_is_not_json_ends_the_run_on_the_canary() {
    // Invented, because no such call was recorded: `--output-format json` promises one JSON
    // document, so a *zero* exit that printed anything else is the flag no longer meaning what
    // it means — and every later item in the round would be unreadable the same way.
    let (_, answers) = one_item(Reply::printing("Usage: claude [options] [command]\n"));
    let RoundError::Drift(said) = answers.expect_err("the canary ends the run") else {
        panic!("unreadable stdout is drift");
    };
    assert!(said.contains("not JSON"), "{said}");
}

/// A run any other model had a hand in crashes rather than mislabels its rows.
#[test]
fn a_usage_map_naming_another_model_ends_the_run_on_the_canary() {
    for usage in [
        // The usage map rekeyed to another model, as a silent substitution would leave it...
        json!({ OTHER_MODEL: recorded_usage() }),
        // ...and one naming the model asked for *and* another, which is the shape a mid-call
        // fallback really takes: the asked-for model is present, and still not what answered.
        json!({ MODEL: recorded_usage(), OTHER_MODEL: recorded_usage() }),
    ] {
        let (_, answers) = one_item(Reply::printing(
            &mutated(&[("modelUsage", usage)]).to_string(),
        ));
        let RoundError::Drift(said) = answers.expect_err("the canary ends the run") else {
            panic!("a substituted model is drift");
        };
        assert!(said.contains(OTHER_MODEL), "{said}");
    }
}

/// Once the round is spending, drift fails one item instead of forfeiting the paid ones.
#[test]
fn drift_after_the_canary_fails_its_item() {
    // If the canary answered and a later item drifts...
    let fake = FakeCli::new(script(&[
        ("canary", succeeds()),
        (
            "item-0",
            Reply::printing(&without(&["modelUsage"]).to_string()),
        ),
    ]));
    // ...then the round returns rather than erroring — the property the enricher rests on.
    let answers = client(MODEL, 1, &fake)
        .submit(&requests_for(&["canary", "item-0"]))
        .expect("the round finished");
    assert_eq!(
        kinds(&answers),
        BTreeMap::from([
            ("canary".to_owned(), None),
            ("item-0".to_owned(), Some(FailureKind::Drift)),
        ])
    );
}

/// A canary that never saw an envelope is retried alone, not answered by opening the pool.
#[test]
fn an_inconclusive_canary_recanaries_before_the_pool_opens() {
    // If the first two items error — which validates no envelope at all...
    let inconclusive = Reply::printing(&mutated(&[("is_error", json!(true))]).to_string());
    let mut replies = script(&[
        ("item-0", inconclusive.clone()),
        ("item-1", inconclusive.clone()),
    ]);
    replies.extend(script(&[
        ("item-2", succeeds()),
        ("item-3", succeeds()),
        ("item-4", succeeds()),
        ("item-5", succeeds()),
    ]));
    let fake = FakeCli::new(replies);
    let keys: Vec<String> = (0..6).map(|index| format!("item-{index}")).collect();
    let requests: Vec<&str> = keys.iter().map(String::as_str).collect();
    let answers = client(MODEL, 4, &fake)
        .submit(&requests_for(&requests))
        .expect("the round finished");
    // ...then no two calls ran at once until one of them came back with an envelope...
    assert_eq!(fake.peak_before_an_answer(), 1);
    // ...and the pool only then took the rest.
    assert_eq!(
        kinds(&answers),
        BTreeMap::from([
            ("item-0".to_owned(), Some(FailureKind::ApiError)),
            ("item-1".to_owned(), Some(FailureKind::ApiError)),
            ("item-2".to_owned(), None),
            ("item-3".to_owned(), None),
            ("item-4".to_owned(), None),
            ("item-5".to_owned(), None),
        ])
    );
}

/// Re-canarying is bounded by the breaker: five silent items end the round, unsent.
#[test]
fn a_canary_that_never_answers_ends_the_round() {
    // If every call errors, the serial canary never opens the pool...
    let keys: Vec<String> = (0..8).map(|index| format!("item-{index}")).collect();
    let requests: Vec<&str> = keys.iter().map(String::as_str).collect();
    let replies: Vec<(&str, Reply)> = requests.iter().map(|key| (*key, errors())).collect();
    let fake = FakeCli::new(script(&replies));
    let answers = client(MODEL, 4, &fake)
        .submit(&requests_for(&requests))
        .expect("the round finished");
    // ...and the breaker stops it after five items rather than walking the whole round.
    let failed: Vec<Option<FailureKind>> = answers.iter().map(Answer::failure).collect();
    let mut expected = vec![Some(FailureKind::ApiError); BREAKER_BOUND];
    expected.extend(vec![Some(FailureKind::Aborted); 3]);
    assert_eq!(failed, expected);
    assert_eq!(fake.calls().len(), BREAKER_BOUND * ATTEMPTS);
}
