//! A whole enrichment run, driven by a fake client: what gets sent, in what order, and written.
//!
//! Ported from the ordering half of `tests/enrich/test_enricher.py`; what makes an item stale
//! and how a failure travels is in `enricher_staleness.rs`. The store is real — the whole clean
//! fixture corpus, which is a superset of the three sessions the Python file picks for its
//! forest: `spine/` nests a run under a run under a main turn, `fork_origin/` nests a fork
//! under an auditor with no main turn above either, and `teammate/` holds a run nothing
//! spawned. Only the model is fake (`hyphae_testsupport::passes`). Every count here was taken
//! against this corpus rather than lifted from the Python file's smaller one.

mod common;

use std::collections::BTreeSet;

use hyphae_enrich::client::{Answer, BatchClient, EnrichRequest, RoundError};
use hyphae_enrich::enricher::{EnrichReport, PassError, ROUND_ORDER, enrich};
use hyphae_enrich::prompts::{TURN_BUDGETS, input_hash, render_turn};
use hyphae_enrich::{Item, Level, SessionItem, level_of};
use hyphae_testsupport::fake_cli::MODEL;
use hyphae_testsupport::passes::{FakeClient, answer, level_keys, run_key, session_key};

use common::{FORK_ORIGIN_RUN, FORK_RUN, MODEL_ONLY, SPINE, SPINE_LEAF, SPINE_RUN, TEAMMATE_RUN};

/// What one pass over the whole corpus describes: 11 agent runs, 17 main turns, 13 sessions.
const RUNS: usize = 11;
const TURNS: usize = 17;
const SESSIONS: usize = 13;

#[test]
fn every_level_the_store_can_write_gets_a_round() {
    // A pass describes all three levels — a level with no round would be described by nothing.
    // The rounds are ordered bottom-up and the store's levels are a closed set, so the two are
    // the same members read for different reasons; only their equality is checked here.
    assert_eq!(
        ROUND_ORDER.into_iter().collect::<BTreeSet<_>>(),
        Level::ALL.into_iter().collect::<BTreeSet<_>>()
    );
}

/// One pass describes every enrichable item and records what it was described under.
#[test]
fn a_run_writes_a_row_for_every_stale_item() {
    // If a run enriches the whole corpus...
    let (_scratch, store) = common::open_copy();
    let client = FakeClient::new();
    let report = enrich(&store, &client, None, None).expect("the pass runs");
    // ...then it reports what it did, having swept nothing — there are no orphans yet...
    assert_eq!(
        report,
        EnrichReport {
            swept: 0,
            enriched: RUNS + TURNS + SESSIONS,
        }
    );
    // ...the client was asked about every item exactly once...
    let asked = client.keys();
    let once: BTreeSet<&String> = asked.iter().collect();
    assert_eq!(once.len(), asked.len());
    assert_eq!(
        once.into_iter().cloned().collect::<BTreeSet<String>>(),
        ROUND_ORDER
            .into_iter()
            .flat_map(|level| level_keys(&store, level))
            .collect::<BTreeSet<String>>()
    );
    // ...every agent run before every main turn and every main turn before every session,
    // because each of those prompts embeds the descriptions below it...
    assert_eq!(
        asked.iter().map(|key| level_of(key)).collect::<Vec<_>>(),
        [
            vec![Some(Level::AgentRun); RUNS],
            vec![Some(Level::Turn); TURNS],
            vec![Some(Level::Session); SESSIONS],
        ]
        .concat()
    );
    // ...and each turn row holds the answer that came back, keyed by the turn it describes and
    // stamped with everything that decides whether it is still current. The hashes are taken
    // now, not before the run: a turn that spawned a subagent renders differently once that
    // subagent has a description.
    let mut items = store.turn_items(None).expect("the turns read");
    items.sort_by(|left, right| left.turn_id.cmp(&right.turn_id));
    assert_eq!(
        stored_turns(&store),
        items
            .iter()
            .map(|item| StoredTurn {
                session_id: item.session_id.clone(),
                source: item.source.clone(),
                turn_id: item.turn_id.clone(),
                description: format!("Described {}.", item.key()),
                category: "test".to_owned(),
                outcome: "completed".to_owned(),
                friction: None,
                input_hash: input_hash(&render_turn(item, &TURN_BUDGETS)),
                prompt_version: Level::Turn.prompt_version(),
                taxonomy_version: hyphae_testsupport::metadata::enrichment().taxonomy_version,
                model: MODEL.to_owned(),
            })
            .collect::<Vec<_>>()
    );
}

/// A session with no model response is not described, and the row it had is swept away.
///
/// The whole of the gate as an operator meets it: a row the corpus already holds goes, the
/// count reaches the console through `EnrichReport.swept`, and nothing is billed to replace it.
/// Neither half is visible from the store alone.
#[test]
fn a_pass_never_sends_a_gated_session_and_reports_the_row_it_deleted() {
    let (_scratch, store) = common::open_copy();
    // If a store holds a session whose turns drove no api call, described by an earlier pass
    // that had no gate...
    store
        .upsert(
            &SessionItem::bare(MODEL_ONLY),
            &common::enrichment("described before the gate existed"),
            &common::stamp("stale"),
        )
        .expect("the ungated row plants");
    let client = FakeClient::new();
    let report = enrich(&store, &client, None, None).expect("the pass runs");
    // ...then the run sweeps that row and says so — the one place a reader learns it went...
    assert_eq!(report.swept, 1);
    let described = common::stored_ids(&store, Level::Session);
    assert_eq!(described.len(), SESSIONS);
    assert!(!described.contains(&MODEL_ONLY.to_owned()));
    // ...and the gated session was never sent, so nothing is billed to describe it again...
    assert!(!client.keys().contains(&session_key(MODEL_ONLY)));
    // ...while its `/model` turns were, since turns are not gated.
    assert!(
        client
            .keys()
            .iter()
            .any(|key| key.starts_with(&format!("turn|{MODEL_ONLY}|")))
    );
}

/// Running again with nothing changed submits nothing and rewrites nothing.
///
/// This is what makes `enrich` safe to run beside `extract` on a schedule. `fork_origin/`'s
/// fork replayed its own spawning call into its transcript: a render that let that call carry
/// a description would embed the fork's description in the fork's own prompt, so the hash would
/// never settle and the run would be re-described — and re-billed — every night.
#[test]
fn a_second_run_over_an_unchanged_store_sends_nothing() {
    // If a store is enriched, and then enriched again with nothing changed...
    let (_scratch, store) = common::open_copy();
    enrich(&store, &FakeClient::new(), None, None).expect("the first pass runs");
    let before = common::written_at(&store);
    let second = FakeClient::new();
    let report = enrich(&store, &second, None, None).expect("the second pass runs");
    // ...then the second run sends no round at all — not an empty one...
    assert!(second.rounds().is_empty());
    assert_eq!(
        report,
        EnrichReport {
            swept: 0,
            enriched: 0
        }
    );
    // ...and every row of all three levels is untouched, down to when it was written.
    assert_eq!(common::written_at(&store), before);
}

/// Every run is described after the runs it spawned, and every main turn after both.
///
/// A parent's prompt embeds its children's descriptions, so a parent sent first would be
/// described from a hole — and the hash would then call that description current forever.
#[test]
fn rounds_send_children_before_parents() {
    // If the corpus is enriched — a run under a run under a turn in `spine/`, a fork under an
    // auditor under no turn at all in `fork_origin/`, and a run nothing spawned in `teammate/`...
    let (_scratch, store) = common::open_copy();
    let client = FakeClient::new();
    enrich(&store, &client, None, None).expect("the pass runs");
    let rounds = client.round_keys();
    // ...then the rounds are the levels of the forest, deepest first: every leaf run, then the
    // two runs that spawned one...
    assert_eq!(rounds.len(), 4);
    assert_eq!(
        rounds[1],
        BTreeSet::from([run_key(&store, SPINE_RUN), run_key(&store, FORK_ORIGIN_RUN)])
    );
    for leaf in [SPINE_LEAF, FORK_RUN, TEAMMATE_RUN] {
        assert!(rounds[0].contains(&run_key(&store, leaf)), "{leaf}");
    }
    // ...between them every agent run, once...
    assert_eq!(
        rounds[0]
            .union(&rounds[1])
            .cloned()
            .collect::<BTreeSet<_>>(),
        level_keys(&store, Level::AgentRun)
            .into_iter()
            .collect::<BTreeSet<_>>()
    );
    assert_eq!(rounds[0].len() + rounds[1].len(), RUNS);
    // ...then the main turns, because a turn embeds the runs it spawned...
    assert_eq!(
        rounds[2],
        level_keys(&store, Level::Turn).into_iter().collect()
    );
    // ...and the sessions last of all, each embedding its own turns and the runs nothing else
    // in it embeds.
    assert_eq!(
        rounds[3],
        level_keys(&store, Level::Session).into_iter().collect()
    );
}

/// A run no tool call spawned is a leaf of nobody's tree, and goes out in the first round.
///
/// Recorded runs that carry no spawning call are mostly teammates, which the team mechanism
/// starts rather than an agent. Waiting for a parent they do not have would strand them.
#[test]
fn a_rootless_run_is_a_root() {
    let (_scratch, store) = common::open_copy();
    let client = FakeClient::new();
    enrich(&store, &client, None, None).expect("the pass runs");
    let first = &client.round_keys()[0];
    // The teammate run, which names neither a spawning call nor a parent agent, goes in the
    // first round; a run that does name a parent waits for it.
    assert!(first.contains(&run_key(&store, TEAMMATE_RUN)));
    assert!(!first.contains(&run_key(&store, SPINE_RUN)));
}

/// A child whose parent run is not in the store crashes the run, naming the child.
///
/// Planted, not recorded: no run of the corpus names a parent the store lacks. Ordering cannot
/// be right for a tree with a gap in it, and guessing a root would send the child before a
/// parent that may yet arrive.
#[test]
fn a_run_naming_a_missing_parent_crashes() {
    let (_scratch, store) = common::open_copy();
    // If the run that spawned `spine/`'s leaf is deleted, standing for a store missing an agent
    // that some other agent named as its parent...
    store
        .connection()
        .execute("DELETE FROM agent_runs WHERE id = ?", [SPINE_RUN])
        .expect("the parent run deletes");
    // ...then the run refuses to order anything, and says which child it could not place.
    let failure = enrich(&store, &FakeClient::new(), None, None)
        .expect_err("a gap in the forest is not orderable");
    assert!(matches!(failure, PassError::Store(_)));
    let said = failure.to_string();
    assert!(
        said.contains(SPINE_LEAF) && said.contains(SPINE_RUN),
        "{said}"
    );
    assert!(said.contains(SPINE), "{said}");
}

/// A client that loses track of the batch it was given crashes the pass.
///
/// Not a port — `test_enricher.py` has no leaf here. This crash is what stands between a client
/// bug and a row written against some other item's key, which no later pass would ever notice:
/// the row would be stamped current for an item that was never described.
#[test]
fn a_client_that_loses_track_of_the_batch_crashes() {
    for mislaid in [Mislay::Extra, Mislay::Duplicate, Mislay::Dropped] {
        let (_scratch, store) = common::open_copy();
        let failure = enrich(&store, &Mislaying(mislaid), None, None)
            .expect_err("a mislaid batch is not a pass");
        match mislaid {
            // An answer to a key the round did not send, and one sent back twice, are the same
            // fault: the answers no longer line up with the requests.
            Mislay::Extra | Mislay::Duplicate => {
                assert!(matches!(failure, PassError::Unasked(_)), "{failure}");
            }
            Mislay::Dropped => assert!(matches!(failure, PassError::Unanswered(1)), "{failure}"),
        }
    }
}

/// How a round's answers stop matching its requests.
#[derive(Debug, Clone, Copy)]
enum Mislay {
    /// An answer to something the round never sent.
    Extra,
    /// One request answered twice.
    Duplicate,
    /// One request never answered.
    Dropped,
}

/// A client that answers every request well-formedly, then mislays the batch one way.
struct Mislaying(Mislay);

impl BatchClient for Mislaying {
    fn model(&self) -> &str {
        MODEL
    }

    fn submit(&self, requests: &[EnrichRequest]) -> Result<Vec<Answer>, RoundError> {
        let mut answers: Vec<Answer> = requests
            .iter()
            .map(|request| Answer::Succeeded {
                key: request.key.clone(),
                output: answer(&request.key),
            })
            .collect();
        match self.0 {
            Mislay::Extra => answers.push(Answer::Succeeded {
                key: "agent_run|no-such-session|no-such-run".to_owned(),
                output: answer("no-such-run"),
            }),
            Mislay::Duplicate => answers.push(answers[0].clone()),
            Mislay::Dropped => {
                answers.pop();
            }
        }
        Ok(answers)
    }
}

/// One turn enrichment row, in the columns that decide whether it is still current.
#[derive(Debug, PartialEq, Eq)]
struct StoredTurn {
    session_id: String,
    source: String,
    turn_id: String,
    description: String,
    category: String,
    outcome: String,
    friction: Option<String>,
    input_hash: String,
    prompt_version: i64,
    taxonomy_version: i64,
    model: String,
}

fn stored_turns(store: &hyphae_enrich::EnrichmentStore) -> Vec<StoredTurn> {
    store
        .store()
        .fetch(
            "SELECT session_id, source, turn_id, description, category, outcome, friction,
                    input_hash, prompt_version, taxonomy_version, model
             FROM turn_enrichments ORDER BY turn_id",
            &[],
        )
        .expect("the turn rows read")
        .iter()
        .map(|row| StoredTurn {
            session_id: row.str("session_id").expect("a session id").to_owned(),
            source: row.str("source").expect("a source").to_owned(),
            turn_id: row.str("turn_id").expect("a turn id").to_owned(),
            description: row.str("description").expect("a description").to_owned(),
            category: row.str("category").expect("a category").to_owned(),
            outcome: row.str("outcome").expect("an outcome").to_owned(),
            friction: row
                .opt_str("friction")
                .expect("a friction line")
                .map(str::to_owned),
            input_hash: row.str("input_hash").expect("an input hash").to_owned(),
            prompt_version: row.i64("prompt_version").expect("a prompt version"),
            taxonomy_version: row.i64("taxonomy_version").expect("a taxonomy version"),
            model: row.str("model").expect("a model").to_owned(),
        })
        .collect()
}
