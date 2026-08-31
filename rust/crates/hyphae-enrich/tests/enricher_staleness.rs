//! What makes an item stale, how a new description travels up, and what a failure takes with it.
//!
//! Ported from the staleness and failure half of `tests/enrich/test_enricher.py`; what a pass
//! sends and in what order is in `enricher.rs`. The store is the whole clean fixture corpus, so
//! every count here was taken against it rather than lifted from the Python file's three
//! sessions.
//!
//! Python monkeypatches `PROMPT_VERSION` and `TAXONOMY_VERSION` to move a version. Rust has no
//! twin for that, so these leaves move the *stored* version instead and check both halves at
//! once: the level is re-sent, and every row comes back stamped with the version the code
//! holds. A pass that read its stamp from anywhere else would leave 99 in the table.

mod common;

use hyphae_enrich::client::Answer;
use hyphae_enrich::enricher::{EnrichReport, PassError, enrich};
use hyphae_enrich::{EnrichmentStore, FailureKind, Item, Level};
use hyphae_testsupport::fake_cli::{MODEL, OTHER_MODEL};
use hyphae_testsupport::passes::{
    FAKE_CATEGORY, FAKE_SECRET, FakeClient, level_keys, rename_a_leaf_tool, run_key, session_key,
    succeeding, turn_key,
};
use serde_json::json;

use common::{SPINE, SPINE_LEAF, SPINE_RUN};

/// The `spine/` main turn that spawned [`SPINE_RUN`] — the one the cascade has to reach.
const SPAWNING_TURN: &str = "818588ad";

/// Changing the instructions the hash cannot see re-enriches everything they cover.
#[test]
fn a_prompt_version_bump_re_enriches_the_level() {
    let (_scratch, store) = common::open_copy();
    enrich(&store, &FakeClient::new(), None, None).expect("the first pass runs");
    // If the turn level's rows were written under another prompt version — an instruction or
    // output-schema edit since...
    bump(&store, Level::Turn, "prompt_version");
    let client = FakeClient::new();
    enrich(&store, &client, None, None).expect("the second pass runs");
    // ...then every turn is re-sent, and nothing else is: the answers are the same words, so
    // no session's rendered input moved...
    assert_eq!(client.keys(), level_keys(&store, Level::Turn));
    // ...and every row records the version the code holds, not the one it found.
    assert_eq!(
        versions(&store, Level::Turn, "prompt_version"),
        vec![Level::Turn.prompt_version()]
    );
}

/// A taxonomy revision makes existing rows stale without invalidating them.
#[test]
fn a_taxonomy_bump_re_enriches() {
    let (_scratch, store) = common::open_copy();
    enrich(&store, &FakeClient::new(), None, None).expect("the first pass runs");
    // If every level's rows were written under an older vocabulary...
    for level in Level::ALL {
        bump(&store, level, "taxonomy_version");
    }
    let client = FakeClient::new();
    enrich(&store, &client, None, None).expect("the second pass runs");
    // ...then everything is described again, and stamped with today's vocabulary.
    assert_everything_asked(&client, &store);
    let today = hyphae_testsupport::metadata::enrichment().taxonomy_version;
    for level in Level::ALL {
        assert_eq!(versions(&store, level, "taxonomy_version"), vec![today]);
    }
}

/// `--model` re-enriches automatically: a description is an answer from one model.
#[test]
fn a_model_switch_re_enriches() {
    let (_scratch, store) = common::open_copy();
    enrich(&store, &FakeClient::new(), None, None).expect("the first pass runs");
    let client = FakeClient::as_model(OTHER_MODEL);
    enrich(&store, &client, None, None).expect("the second pass runs");
    assert_everything_asked(&client, &store);
    for level in Level::ALL {
        assert_eq!(models(&store, level), vec![OTHER_MODEL.to_owned()]);
    }
}

/// Failed items crash the run at the end, classified by kind and named by key alone.
///
/// Nothing the model wrote reaches the summary — the natural implementation, formatting the
/// failed response into the message, is the one that leaks a credential out of a transcript.
#[test]
fn a_round_of_mixed_failures_crashes_naming_keys_and_kinds() {
    // If one round fails three ways at once — an item the breaker abandoned unsent, an answer
    // outside the taxonomy, and an answer carrying something shaped like a credential...
    let (_scratch, store) = common::open_copy();
    let turns = common::spine_turns(&store);
    let (abandoned, invalid, refused, kept) = (
        turns[0].key(),
        turns[1].key(),
        turns[2].key(),
        turns[3].key(),
    );
    let client = FakeClient::answering(
        MODEL,
        [
            // The abandoned item carries no sentinel because a failure has no field to put one
            // in: a failure record cannot repeat model output it never received.
            Answer::Failed {
                key: abandoned.clone(),
                kind: FailureKind::Aborted,
            },
            succeeding(
                &invalid,
                json!({ "category": format!("refactoring-{FAKE_CATEGORY}") }),
            ),
            succeeding(
                &refused,
                json!({ "description": format!("Rotated {FAKE_SECRET} and re-ran.") }),
            ),
        ],
    );
    // ...then the run crashes, because a silent failure here is a hole in the coverage the hash
    // would then call current forever...
    let failure =
        enrich(&store, &client, None, None).expect_err("a round with failures does not report");
    let PassError::Items(failed) = &failure else {
        panic!("{failure}");
    };
    assert_eq!(failed.len(), 3);
    let summary = failure.to_string();
    // ...the summary names each item and how it failed...
    for named in [&abandoned, &invalid, &refused] {
        assert!(summary.contains(named), "{summary}");
    }
    for kind in [
        FailureKind::Aborted,
        FailureKind::InvalidOutput,
        FailureKind::SecretShape,
    ] {
        assert!(summary.contains(kind.word()), "{summary}");
    }
    // ...and carries nothing either answer said...
    assert!(!summary.contains(FAKE_SECRET) && !summary.contains(FAKE_CATEGORY));
    // ...the three failed turns hold no row, so rerunning is the retry...
    let written = common::described(&store);
    for turn in [&turns[0], &turns[1], &turns[2]] {
        assert!(!written.contains_key(&turn.turn_id), "{}", turn.turn_id);
    }
    // ...and the sibling that succeeded in the same round was kept.
    assert_eq!(written[&turns[3].turn_id], format!("Described {kept}."));
}

/// An item the CLI could not answer writes nothing, and the next run picks it up again.
///
/// Staleness is the whole resume mechanism: there is no state to keep, so a crashed run leaves
/// nothing behind to clean up or to go stale itself. `timeout` is the kind that makes the point
/// — the client already retried this item and gave up, so the only retry left is the rerun.
#[test]
fn a_failed_request_leaves_its_item_stale() {
    let (scratch, store) = common::open_copy();
    let dropped = common::spine_turns(&store)[0].key();
    let first = FakeClient::answering(
        MODEL,
        [Answer::Failed {
            key: dropped.clone(),
            kind: FailureKind::Timeout,
        }],
    );
    enrich(&store, &first, None, None).expect_err("a timed-out item fails the pass");
    // If the next run is the retry, it asks about exactly the item that failed — and about the
    // session it belongs to, which the first run refused to describe from a hole...
    let client = FakeClient::new();
    assert_eq!(
        enrich(&store, &client, None, None).expect("the retry runs"),
        EnrichReport {
            swept: 0,
            enriched: 2
        }
    );
    assert_eq!(client.keys(), vec![dropped, session_key(SPINE)]);
    // ...and the crash wrote no resume file to find it by: the store and DuckDB's own
    // write-ahead log are everything on disk.
    let left: Vec<String> = std::fs::read_dir(scratch.path())
        .expect("the scratch directory reads")
        .map(|entry| {
            entry
                .expect("an entry")
                .file_name()
                .to_string_lossy()
                .into()
        })
        .collect();
    for name in &left {
        assert!(
            ["traces.duckdb", "traces.duckdb.wal"].contains(&name.as_str()),
            "{name}"
        );
    }
}

/// A description that changes re-describes everything above it, in the same invocation.
///
/// The stale set has to be recomputed after each round's upserts. Computing it once up front
/// passes every other check here while silently never cascading.
#[test]
fn a_childs_new_description_makes_its_ancestors_stale() {
    // If the corpus is fully enriched, and then the leaf run alone is made stale — by renaming
    // a tool call only that run's prompt renders...
    let (_scratch, store) = common::open_copy();
    enrich(&store, &FakeClient::new(), None, None).expect("the first pass runs");
    let before = common::hashes(&store);
    rename_a_leaf_tool(&store);
    // ...and the model answers with new text each time it is asked again, as a re-read of
    // changed work would...
    let (leaf, run, turn) = (
        run_key(&store, SPINE_LEAF),
        run_key(&store, SPINE_RUN),
        turn_key(&store, SPINE, SPAWNING_TURN),
    );
    let client = FakeClient::answering(
        MODEL,
        [&leaf, &run, &turn]
            .into_iter()
            .map(|key| succeeding(key, json!({ "description": format!("Rewrote {key}.") }))),
    );
    enrich(&store, &client, None, None).expect("the second pass runs");
    // ...then the run goes up the tree: the leaf, then the run whose prompt embeds its
    // description, then the main turn whose prompt embeds *that*, and last the session whose
    // prompt embeds the turn — none of which was stale when the round started.
    assert_eq!(
        client.keys(),
        vec![leaf, run, turn.clone(), session_key(SPINE)]
    );
    // ...and each of their stored inputs moved, and nothing else's did.
    let after = common::hashes(&store);
    let moved: Vec<&String> = after
        .keys()
        .filter(|id| before[*id] != after[*id])
        .collect();
    assert_eq!(
        moved,
        vec![
            &SPINE.to_owned(),
            &turn
                .rsplit('|')
                .next()
                .expect("a turn key ends in its id")
                .to_owned(),
            &SPINE_LEAF.to_owned(),
            &SPINE_RUN.to_owned(),
        ]
        .into_iter()
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>()
    );
}

/// A re-described child whose text did not change leaves its ancestors alone.
///
/// The other half of the hash contract, and the reason a dry run's count is an upper bound.
#[test]
fn a_child_re_described_identically_stops_the_cascade() {
    // If the same leaf run is made stale, and the model answers it with the same description as
    // before...
    let (_scratch, store) = common::open_copy();
    enrich(&store, &FakeClient::new(), None, None).expect("the first pass runs");
    let before = common::written_at(&store);
    rename_a_leaf_tool(&store);
    let client = FakeClient::new();
    enrich(&store, &client, None, None).expect("the second pass runs");
    // ...then the leaf is the only item sent: its parent's prompt reads the same as it did, so
    // nothing above it is stale...
    assert_eq!(client.keys(), vec![run_key(&store, SPINE_LEAF)]);
    // ...and no other row was rewritten, down to when it was written.
    let after = common::written_at(&store);
    let moved: Vec<&String> = after
        .keys()
        .filter(|id| before[*id] != after[*id])
        .collect();
    assert_eq!(moved, vec![SPINE_LEAF]);
}

/// When a child fails, the items whose prompts embed it write nothing at all.
///
/// Writing a parent whose child failed bakes a hole into a description that the hash then calls
/// current forever — the one failure mode a rerun cannot heal.
#[test]
fn a_failed_childs_parents_are_skipped() {
    // If the leaf run fails and everything else answers normally...
    let (_scratch, store) = common::open_copy();
    let leaf = run_key(&store, SPINE_LEAF);
    let client = FakeClient::answering(
        MODEL,
        [Answer::Failed {
            key: leaf.clone(),
            kind: FailureKind::ApiError,
        }],
    );
    let failure = enrich(&store, &client, None, None).expect_err("a failed child fails the pass");
    assert!(failure.to_string().contains(FailureKind::ApiError.word()));
    // ...then nothing above it was sent — not the run that spawned it, not the main turn that
    // spawned *that*, and not the session, whose prompt embeds the turn...
    let asked = client.keys();
    let skipped = [
        run_key(&store, SPINE_RUN),
        turn_key(&store, SPINE, SPAWNING_TURN),
        session_key(SPINE),
    ];
    for key in &skipped {
        assert!(!asked.contains(key), "{key}");
    }
    // ...and none of the four wrote a row...
    let written = common::described(&store);
    for id in [SPINE_LEAF, SPINE_RUN, SPINE] {
        assert!(!written.contains_key(id), "{id}");
    }
    assert!(!written.keys().any(|id| id.starts_with(SPAWNING_TURN)));
    // ...while `spine/`'s three other main turns were enriched as usual: a skip is not a
    // failure, and it takes only the ancestors with it.
    let described = common::spine_turns(&store)
        .into_iter()
        .filter(|item| written.contains_key(&item.turn_id))
        .count();
    assert_eq!(described, 3);
    // ...and every item the failure did not stand above kept its round: the leaf itself was
    // asked, and only its three ancestors were held back.
    assert_eq!(asked.len(), every_item(&store).len() - skipped.len());
}

/// Rewrite one level's stored version, standing for rows written under an older release.
fn bump(store: &EnrichmentStore, level: Level, column: &str) {
    store
        .connection()
        .execute(&format!("UPDATE {} SET {column} = 99", level.table()), [])
        .expect("the stored version moves");
}

/// The client was asked about every enrichable item, once each.
///
/// Sorted: within the agent-run level a pass sends by round, which is not the order the store
/// reads them out in.
fn assert_everything_asked(client: &FakeClient, store: &EnrichmentStore) {
    let (mut asked, mut held) = (client.keys(), every_item(store));
    asked.sort();
    held.sort();
    assert_eq!(asked, held);
}

/// Every item key of every level.
fn every_item(store: &EnrichmentStore) -> Vec<String> {
    [Level::AgentRun, Level::Turn, Level::Session]
        .into_iter()
        .flat_map(|level| level_keys(store, level))
        .collect()
}

/// The distinct values one level's rows carry in a version column.
fn versions(store: &EnrichmentStore, level: Level, column: &str) -> Vec<i64> {
    store
        .store()
        .fetch(
            &format!("SELECT DISTINCT {column} AS held FROM {}", level.table()),
            &[],
        )
        .expect("the versions read")
        .iter()
        .map(|row| row.i64("held").expect("a version reads"))
        .collect()
}

/// The distinct models one level's rows were written by.
fn models(store: &EnrichmentStore, level: Level) -> Vec<String> {
    common::column(
        store,
        &format!("SELECT DISTINCT model FROM {}", level.table()),
    )
    .into_iter()
    .map(|held| held.expect("a model reads"))
    .collect()
}
