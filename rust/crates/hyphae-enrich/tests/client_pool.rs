//! Many items at once: the breaker, the interrupt, the child env, and preflight.
//!
//! Ported from `tests/enrich/test_client__pool.py`. Driven over the same faked seam as
//! `client.rs` (`hyphae_testsupport::fake_cli`), but every leaf here is about the round rather
//! than the answer — what a pool spends before it stops, what each child process is handed, and
//! what a run refuses at the door. The live smoke lives with the CLI that runs it.

use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, OnceLock};

use hyphae_enrich::client::{
    CLAUDE, DEFAULT_MODEL, Interrupt, NarrowPool, RoundError, build_env, preflight,
};
use hyphae_enrich::prompts::OUTPUT_SCHEMA;
use hyphae_enrich::runner::CallError;
use hyphae_enrich::validation::FailureKind;
use hyphae_enrich::{BatchClient, CliClient};
use hyphae_testsupport::fake_cli::{
    AUTH_CALL, Chain, FakeCli, INSTRUCTIONS, MODEL, OTHER_MODEL, Reply, content_of, errors, kinds,
    mutated, recorded, recorded_usage, requests_for, succeeds,
};
use serde_json::{Value, json};

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

/// `item-0`..`item-{count}`, as both the keys and the requests a round sends.
fn items(count: usize) -> Vec<String> {
    (0..count).map(|index| format!("item-{index}")).collect()
}

fn named(keys: &[String]) -> Vec<&str> {
    keys.iter().map(String::as_str).collect()
}

/// A round that gives up still hands back every answer it already paid for.
#[test]
fn a_tripped_breaker_returns_the_paid_work() {
    // If three items answer and then five in a row fail...
    let keys = items(10);
    let replies: Vec<(&str, Reply)> = named(&keys)
        .into_iter()
        .enumerate()
        .map(|(index, key)| {
            (
                key,
                if (3..8).contains(&index) {
                    errors()
                } else {
                    succeeds()
                },
            )
        })
        .collect();
    let fake = FakeCli::new(script(&replies));
    // ...serially, so the trip point is exactly where the fifth failure lands...
    let answers = client(MODEL, 1, &fake)
        .submit(&requests_for(&named(&keys)))
        .expect("the round finished");
    // ...then the answers survive, the failures name their own kind, and the two items that
    // were never sent are `aborted` — not the kind that tripped the breaker.
    let mut expected = BTreeMap::new();
    for (index, key) in keys.iter().enumerate() {
        expected.insert(
            key.clone(),
            match index {
                3..=7 => Some(FailureKind::ApiError),
                8..=9 => Some(FailureKind::Aborted),
                _ => None,
            },
        );
    }
    assert_eq!(kinds(&answers), expected);
}

/// Scattered failures never end a round, however many of them there are.
#[test]
fn a_success_resets_the_breaker() {
    // If failures alternate with answers past the breaker's bound...
    let keys = items(12);
    let replies: Vec<(&str, Reply)> = named(&keys)
        .into_iter()
        .enumerate()
        .map(|(index, key)| (key, if index % 2 == 0 { succeeds() } else { errors() }))
        .collect();
    let fake = FakeCli::new(script(&replies));
    let answers = client(MODEL, 1, &fake)
        .submit(&requests_for(&named(&keys)))
        .expect("the round finished");
    // ...then nothing is aborted: six failures, none of them consecutive.
    assert_eq!(kinds(&answers).len(), keys.len());
    assert!(
        !answers
            .iter()
            .any(|answer| answer.failure() == Some(FailureKind::Aborted))
    );
}

/// The completion order the chain below forces, as a comment reads it: the pool holds four
/// items, and one new item starts for every completion the client records — so waiting for a
/// start is how a fake waits for a record. `pool-0` answers, and is held until four failures
/// have landed, which is what makes the trip depend on completion order rather than submission
/// order. Submitted in order, `pool-0`'s answer would reset the counter at the second item and
/// the round would end six items later, aborting six; counted per worker, four workers sharing
/// thirteen failures would never reach five and nothing would abort at all.
const COMPLETION_CHAIN: [(&str, &str); 12] = [
    ("pool-2", "pool-4"),
    ("pool-1", "pool-5"),
    ("pool-4", "pool-6"),
    ("pool-0", "pool-7"),
    ("pool-5", "pool-8"),
    ("pool-6", "pool-9"),
    ("pool-7", "pool-10"),
    ("pool-8", "pool-11"),
    ("pool-9", "pool-12"),
    ("pool-10", "pool-12"),
    ("pool-11", "pool-12"),
    ("pool-12", "pool-12"),
];

/// One counter, advanced as answers land — not one per worker, and not by send order.
#[test]
fn the_breaker_counts_completions_across_workers() {
    // If a pool of four runs fourteen items whose completion order is not their send order...
    let keys: Vec<String> = (0..14).map(|index| format!("pool-{index}")).collect();
    let mut replies = script(&[("canary", succeeds()), ("pool-0", succeeds())]);
    replies.extend(script(
        &named(&keys)[1..]
            .iter()
            .map(|key| (*key, errors()))
            .collect::<Vec<_>>(),
    ));
    let chain = Chain::new(
        COMPLETION_CHAIN
            .into_iter()
            .map(|(key, awaited)| (content_of(key), content_of(awaited)))
            .collect(),
    );
    let fake = FakeCli::gated(replies, move |key| chain.gate(key));
    let mut requests = vec!["canary".to_owned()];
    requests.extend(keys.clone());
    let answers = client(MODEL, 4, &fake)
        .submit(&requests_for(&named(&requests)))
        .expect("the round finished");
    // ...then the fifth consecutive failure *to land* ends the round, which leaves exactly one
    // item never sent.
    let aborted: Vec<String> = kinds(&answers)
        .into_iter()
        .filter(|(_, kind)| *kind == Some(FailureKind::Aborted))
        .map(|(key, _)| key)
        .collect();
    assert_eq!(aborted, vec!["pool-13".to_owned()]);
}

/// A `claude` that never launched costs its own item, not the answers around it.
#[test]
fn a_process_the_machine_could_not_start_fails_one_item() {
    // Invented, because no such run was recorded: the runner fails before the child exists —
    // no file descriptor left at concurrency 4, no memory to fork with, the binary gone
    // mid-round. Three items are already paid for when it happens...
    let keys = items(6);
    let mut replies: Vec<(&str, Reply)> = named(&keys)
        .into_iter()
        .map(|key| (key, succeeds()))
        .collect();
    replies[3] = (
        "item-3",
        Reply::failing(CallError::Os("Too many open files".to_owned())),
    );
    let fake = FakeCli::new(script(&replies));
    let answers = client(MODEL, 1, &fake)
        .submit(&requests_for(&named(&keys)))
        .expect("the round finished");
    // ...and they all come back, with the refused item one classified failure among them
    // rather than an error that forfeits the round. Five of these in a row would trip the
    // breaker, which is the shape a machine that cannot start processes takes here.
    let failed: BTreeMap<String, Option<FailureKind>> = kinds(&answers);
    assert_eq!(failed["item-3"], Some(FailureKind::ApiError));
    assert!(failed.iter().filter(|(_, kind)| kind.is_none()).count() == 5);
    // It is a transport failure, so it was sent twice before it was given up on.
    assert_eq!(fake.calls().len(), keys.len() + 1);
}

/// An interrupt hands back everything the round already paid for, and stops the run after it.
#[test]
fn an_interrupt_ends_the_round_without_forfeiting_it() {
    // If the operator stops the run while two pool items are in flight — held there by a chain
    // so the stop lands with both of them running, and not between them...
    let held = Chain::new(HashMap::from([(
        content_of("item-0"),
        content_of("item-1"),
    )]));
    let stopping: Arc<OnceLock<Interrupt>> = Arc::new(OnceLock::new());
    let asked = stopping.clone();
    let keys = ["canary", "warm-0", "warm-1", "item-0", "item-1", "item-2"];
    let fake = FakeCli::gated(
        script(&keys.map(|key| (key, succeeds()))),
        move |key: &str| {
            held.gate(key);
            if key == content_of("item-1") {
                asked.get().expect("the client was built").stop();
            }
        },
    );
    // The two items ahead of them warm the pool, so the collector is waiting on answers when
    // the stop arrives rather than starting a worker.
    let client = client(MODEL, 2, &fake);
    stopping.set(client.interrupt()).expect("one client");
    let answers = client
        .submit(&requests_for(&keys))
        .expect("the round returns rather than erroring");
    // ...then every answer already bought is there for the enricher to write — an error would
    // have thrown away the whole round, up to ~1,900 items in the deepest one. The item never
    // sent is `aborted`: one answer per key either way...
    assert_eq!(
        kinds(&answers),
        BTreeMap::from([
            ("canary".to_owned(), None),
            ("warm-0".to_owned(), None),
            ("warm-1".to_owned(), None),
            ("item-0".to_owned(), None),
            ("item-1".to_owned(), None),
            ("item-2".to_owned(), Some(FailureKind::Aborted)),
        ])
    );
    assert!(!fake.started().contains(&content_of("item-2")));
    // ...and the stop is delivered at the next round, which is the first moment the paid work
    // is written and the first moment stopping costs nothing.
    assert_eq!(
        client.submit(&requests_for(&["next-round"])),
        Err(RoundError::Interrupted)
    );
    assert!(!fake.started().contains(&content_of("next-round")));
}

/// A concurrency no pool can honour is refused at construction rather than mid-round.
#[test]
fn a_pool_narrower_than_one_item_is_refused_before_it_spends() {
    // Zero is the only width a pool can be asked for and not honour: a negative one cannot be
    // spelled here, where Python takes any int.
    let fake = FakeCli::new(script(&[("item-0", succeeds())]));
    assert_eq!(
        CliClient::new(MODEL, 0, Box::new(fake.clone())).err(),
        Some(NarrowPool(0))
    );
    // The canary runs before the pool opens, so the same check inside `submit` would have
    // spent an item first and then refused it away.
    assert_eq!(fake.started(), Vec::<String>::new());
}

/// Every key comes back once, whether the round finished or gave up — the enricher needs that.
#[test]
fn every_request_gets_exactly_one_result() {
    let keys = items(6);
    // A clean round...
    let clean = FakeCli::new(script(
        &named(&keys)
            .into_iter()
            .map(|key| (key, succeeds()))
            .collect::<Vec<_>>(),
    ));
    let answered = client(MODEL, 4, &clean)
        .submit(&requests_for(&named(&keys)))
        .expect("the round finished");
    assert_eq!(kinds(&answered).len(), keys.len());
    // ...and a round the breaker ended answer the same requests exactly once each.
    let tripped = FakeCli::new(script(
        &named(&keys)
            .into_iter()
            .map(|key| (key, errors()))
            .collect::<Vec<_>>(),
    ));
    let given_up = client(MODEL, 1, &tripped)
        .submit(&requests_for(&named(&keys)))
        .expect("the round finished");
    assert_eq!(kinds(&given_up).len(), keys.len());
}

/// The auth check runs under the environment the items will spend under, not the shell's.
#[test]
fn preflight_and_items_share_one_env() {
    // If preflight and then an item run through the same fake...
    let mut replies = script(&[("item-0", succeeds())]);
    replies.insert(
        AUTH_CALL.to_owned(),
        Reply::printing(&recorded("auth_status_logged_in").to_string()),
    );
    let fake = FakeCli::new(replies);
    preflight(fake.as_ref()).expect("the recorded subscription passes");
    client(MODEL, 1, &fake)
        .submit(&requests_for(&["item-0"]))
        .expect("the round finished");
    // ...then the auth question and the spend carried the very mapping the one builder
    // returns. A preflight run in the parent env would pass while every item failed.
    let calls = fake.calls();
    assert_eq!(calls[0].env, calls[1].env);
    assert_eq!(calls[0].env, build_env());
}

/// A key or a base url in the parent shell never reaches the child, so auth cannot divert.
#[test]
fn the_child_env_is_constructed_not_inherited() {
    // Set in this process, which nextest gives this leaf to itself.
    unsafe {
        std::env::set_var("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key");
        std::env::set_var("ANTHROPIC_BASE_URL", "https://proxy.invalid");
    }
    let fake = FakeCli::new(script(&[("item-0", succeeds())]));
    client(MODEL, 1, &fake)
        .submit(&requests_for(&["item-0"]))
        .expect("the round finished");
    assert_eq!(
        fake.calls()[0].env,
        BTreeMap::from([
            ("HOME".to_owned(), std::env::var("HOME").expect("HOME")),
            ("PATH".to_owned(), std::env::var("PATH").expect("PATH")),
            ("USER".to_owned(), std::env::var("USER").expect("USER")),
            // The one switch that keeps thinking off, and env rather than settings because
            // `--setting-sources ""` would drop a settings file.
            ("MAX_THINKING_TOKENS".to_owned(), "0".to_owned()),
        ])
    );
}

/// Every item runs as the model it was asked for, with no tools, settings, MCP or session.
#[test]
fn every_call_carries_the_isolation_flags() {
    // Built with a model that is not the default, so a client that ignored what it was given
    // would fail here rather than agree with `--model` by coincidence...
    assert_ne!(OTHER_MODEL, DEFAULT_MODEL);
    // ...answered by a usage map naming that model, which is what keeps the canary quiet.
    let fake = FakeCli::new(script(&[(
        "item-0",
        Reply::printing(
            &mutated(&[("modelUsage", json!({ OTHER_MODEL: recorded_usage() }))]).to_string(),
        ),
    )]));
    client(OTHER_MODEL, 1, &fake)
        .submit(&requests_for(&["item-0"]))
        .expect("the round finished");
    let call = fake.calls().remove(0);
    // The schema is read back rather than compared as text, so this leaf pins the contract the
    // CLI is handed and not one spelling of it.
    assert_eq!(
        serde_json::from_str::<Value>(&call.argv[9]).expect("the schema argument is JSON"),
        *OUTPUT_SCHEMA
    );
    let mut argv = call.argv.clone();
    argv[9] = "<the output schema>".to_owned();
    assert_eq!(
        argv,
        [
            CLAUDE,
            "--print",
            "--output-format",
            "json",
            "--model",
            OTHER_MODEL,
            "--system-prompt",
            INSTRUCTIONS,
            "--json-schema",
            "<the output schema>",
            "--tools",
            "",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]
    );
    // No MCP config to go with the strict flag, which is what makes it a closure...
    assert!(!call.argv.iter().any(|argument| argument == "--mcp-config"));
    // ...and the render — untrusted transcript text, and long enough to blow an argv limit —
    // travels over stdin, never on the command line.
    assert_eq!(call.input, Some(content_of("item-0")));
    assert!(
        !call
            .argv
            .iter()
            .any(|argument| argument.contains(&content_of("item-0")))
    );
}

/// Items run outside every extractable project, so a stray session cannot be re-ingested.
#[test]
fn calls_run_in_a_temp_cwd() {
    let fake = FakeCli::new(script(&[("item-0", succeeds())]));
    client(MODEL, 1, &fake)
        .submit(&requests_for(&["item-0"]))
        .expect("the round finished");
    // `sessions.py` keys the projects directory on the cwd, so a temp cwd is the control.
    let cwd = fake.calls()[0].cwd.clone().expect("an item runs somewhere");
    assert!(cwd.starts_with(std::env::temp_dir()), "{}", cwd.display());
    assert_ne!(cwd, std::env::current_dir().expect("a working directory"));
}

/// A run refuses at the door rather than failing every one of thousands of items.
#[test]
fn preflight_refuses_an_unusable_auth() {
    let mut without_subscription = recorded("auth_status_logged_in");
    without_subscription
        .as_object_mut()
        .expect("the blob is an object")
        .remove("subscriptionType");
    for envelope in [
        // Recorded: what `claude auth status` writes with no OAuth in reach.
        recorded("auth_status_logged_out"),
        // Derived from the recorded logged-in blob: an auth with no subscription behind it,
        // which would spend against something other than the allowance this run assumes.
        without_subscription,
    ] {
        let fake = FakeCli::new(HashMap::from([(
            AUTH_CALL.to_owned(),
            Reply::refusing(&envelope.to_string()),
        )]));
        preflight(fake.as_ref()).expect_err("an unusable auth is refused");
    }
}

/// Enrichment runs through the CLI, so a machine without it stops before it reads a store.
#[test]
fn preflight_refuses_a_missing_binary() {
    let fake = FakeCli::new(HashMap::from([(
        AUTH_CALL.to_owned(),
        Reply::failing(CallError::NotFound),
    )]));
    let refusal = preflight(fake.as_ref()).expect_err("a machine with no CLI is refused");
    assert!(refusal.to_string().contains(CLAUDE));
}

/// The refusal names the problem and nothing else — the blob carries an email and an org.
#[test]
fn preflight_never_repeats_the_auth_blob() {
    // Derived from the recorded logged-in blob: logged out, but still carrying the identity
    // fields the real one carries.
    let envelope = mutated_auth(&[("loggedIn", json!(false))]);
    let fake = FakeCli::new(HashMap::from([(
        AUTH_CALL.to_owned(),
        Reply::printing(&envelope.to_string()),
    )]));
    let refusal = preflight(fake.as_ref()).expect_err("a logged-out CLI is refused");
    for held in [
        "REDACTED-EMAIL-9f2c",
        "REDACTED-ORG-ID-9f2c",
        "REDACTED-ORG-NAME-9f2c",
    ] {
        // The recording really carries it, so this leaf cannot pass on an empty search...
        assert!(envelope.to_string().contains(held), "{held}");
        // ...and the refusal does not.
        assert!(!refusal.to_string().contains(held), "{held}");
    }
}

/// A logged-in subscription passes without a word.
#[test]
fn preflight_accepts_a_recorded_subscription() {
    let fake = FakeCli::new(HashMap::from([(
        AUTH_CALL.to_owned(),
        Reply::printing(&recorded("auth_status_logged_in").to_string()),
    )]));
    assert_eq!(preflight(fake.as_ref()), Ok(()));
}

/// The recorded logged-in blob with fields replaced — derived, not a recording.
fn mutated_auth(changes: &[(&str, Value)]) -> Value {
    let mut blob = recorded("auth_status_logged_in");
    let fields = blob.as_object_mut().expect("the blob is an object");
    for (field, value) in changes {
        fields.insert((*field).to_owned(), value.clone());
    }
    blob
}
