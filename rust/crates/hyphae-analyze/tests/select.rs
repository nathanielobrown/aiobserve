//! Which sessions and runs an iteration reads, drawn at fixture-sized quotas.
//!
//! The twin of `tests/analyze/test_select.py`. Selection is the one part of the process that
//! decides what nobody will look at, so the leaves here pin the mechanics a report's realized
//! composition is built from: strata fill in order, each walks down past what an earlier
//! stratum took, a stratum whose metric runs out stops short rather than padding, and the
//! slots nobody used fall through to discovery.
//!
//! Every quota is bound small — the fixture pool is twelve sessions — except the last leaf,
//! which pins the production defaults a committed report cites.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use hyphae_store::Store;
use hyphae_testsupport::{corpus as fixtures, landmarks, windows};
use indexmap::IndexMap;
use tempfile::TempDir;

mod common;

use common::corpus;

/// A selected session as the report reads it: the stratum that took it, and which session.
type Pick = (String, String);

const COST: &str = "cost";
const ERRORS: &str = "tool-errors";
const COMPACTIONS: &str = "compactions";
const DISCOVERY: &str = "discovery";
const GRILL_ME: &str = "skill:grill-me";

/// Quotas small enough that a twelve-session pool can show a stratum running out. Each leaf
/// overrides the ones it is about; anything it leaves alone stays off, so the set it asserts
/// on is only the mechanism it names.
const OFF: &[(&str, &str)] = &[
    ("cost_quota", "0"),
    ("error_quota", "0"),
    ("compaction_quota", "0"),
    ("discovery_quota", "0"),
    // Above every fixture skill's user count, so no skill qualifies unless a leaf lowers it.
    ("skill_threshold", "99"),
    // Below every fixture session's api-call count, so discovery's substance floor admits the
    // whole pool: the fixture corpus's busiest session made 7 calls, the production floor is 10.
    ("min_discovery_api_calls", "0"),
];

/// What `select_sessions` declares, and what a committed report therefore cites.
const PRODUCTION_QUOTAS: &[(&str, &str)] = &[
    ("cost_quota", "8"),
    ("error_quota", "5"),
    ("compaction_quota", "4"),
    ("discovery_quota", "8"),
    ("skill_threshold", "5"),
    ("seed", "\"hyphae\""),
    ("min_api_calls", "1"),
    ("min_discovery_api_calls", "10"),
];
/// The run draw rides the same pin: its floor is what keeps a corpus of one-off agent names
/// from turning a ~20-run reading budget into one run per name.
const PRODUCTION_RUN_QUOTAS: &[(&str, &str)] = &[("runs_per_stratum", "1"), ("min_runs", "5")];

/// `select_sessions` at fixture-sized quotas, as the (stratum, session) list it returns.
fn select(db: &Path, bindings: &[(&str, &str)], as_of: &str) -> Vec<Pick> {
    let mut bound: IndexMap<&str, &str> = OFF.iter().copied().collect();
    bound.extend(bindings.iter().copied());
    let params: Vec<(&str, &str)> = bound.into_iter().collect();
    corpus(db, "select_sessions", as_of, &params)
        .rows
        .iter()
        .map(|row| {
            (
                row.str("stratum").expect("a stratum").to_owned(),
                row.str("session_id").expect("a session").to_owned(),
            )
        })
        .collect()
}

/// The same over the whole-corpus window, which every leaf but one draws from.
fn whole(db: &Path, bindings: &[(&str, &str)]) -> Vec<Pick> {
    select(db, bindings, windows::AS_OF_WHOLE)
}

/// One expected pick, written the way the assertions read.
fn pick(stratum: &str, session: &str) -> Pick {
    (stratum.to_owned(), session.to_owned())
}

fn strata(picks: &[Pick]) -> Vec<&str> {
    picks.iter().map(|(stratum, _)| stratum.as_str()).collect()
}

fn sessions(picks: &[Pick]) -> BTreeSet<&str> {
    picks.iter().map(|(_, session)| session.as_str()).collect()
}

fn taken_by<'a>(picks: &'a [Pick], stratum: &str) -> Vec<&'a Pick> {
    picks.iter().filter(|(taken, _)| taken == stratum).collect()
}

// ---------------------------------------------------------------------------
// The draw is one list, whatever produced it

/// One store and one set of bindings give one selection — sessions, tags, and order.
#[test]
fn the_same_bindings_select_the_same_sessions_however_the_store_was_built() {
    let db = fixtures_store();
    let (_scratch, reversed) = reversed_store();
    // If the same bindings are drawn twice from one store, and once from a store built by
    // extracting the same transcripts in the opposite order...
    let bindings = [
        ("cost_quota", "2"),
        ("error_quota", "1"),
        ("compaction_quota", "1"),
        ("discovery_quota", "2"),
        ("skill_threshold", "2"),
    ];
    let first = whole(&db, &bindings);
    let second = whole(&db, &bindings);
    let reversed_build = whole(&reversed, &bindings);
    // ...then all three are the same list: reproducibility is the whole claim the selection
    // makes, and a draw that depended on insertion order would break here and nowhere else.
    assert_eq!(first, second);
    assert_eq!(first, reversed_build);
    // ...and the ranked strata took what their rankings say, with `grill-me`'s only two pool
    // users already taken — one by cost, one by errors — so its slot fell through to discovery.
    assert_eq!(
        first[..4],
        [
            pick(COST, landmarks::SPINE),
            pick(COST, landmarks::PARALLEL),
            pick(ERRORS, landmarks::SERVER_TOOLS),
            pick(COMPACTIONS, landmarks::COMPACTED),
        ]
    );
    assert_eq!(strata(&first[4..]), [DISCOVERY; 3]);
}

/// A session an earlier stratum took does not spend a later stratum's quota.
#[test]
fn a_later_stratum_walks_past_what_an_earlier_one_took() {
    let db = fixtures_store();
    // If the cost stratum's five take `COMPACTED`, the session that compacted most...
    let picks = whole(&db, &[("cost_quota", "5"), ("compaction_quota", "1")]);
    assert!(picks.contains(&pick(COST, landmarks::COMPACTED)));
    // ...then the compaction stratum walks down to the next session that compacted and still
    // meets its quota, rather than reporting one the cost stratum already accounted for.
    assert_eq!(
        taken_by(&picks, COMPACTIONS),
        [&pick(COMPACTIONS, landmarks::ANCESTOR)]
    );
}

/// A stratum whose metric runs out returns fewer sessions instead of padding to quota.
#[test]
fn a_ranked_stratum_takes_only_nonzero_sessions_and_stops_short() {
    let db = fixtures_store();
    // If only two pool sessions hold an error tool call and the quota asks for three...
    let picks = whole(&db, &[("error_quota", "3")]);
    // ...then the stratum stops at two, and the error-free rest of the pool carries no
    // `tool-errors` tag: the tag is what a report's realized composition is counted from, and
    // a stratum that padded to quota would make every one of those counts a lie.
    assert_eq!(
        taken_by(&picks, ERRORS),
        [
            &pick(ERRORS, landmarks::SERVER_TOOLS),
            &pick(ERRORS, landmarks::FORK_ORIGIN),
        ]
    );
    // ...while the slot it could not fill is spent, not lost — the next leaf is about where.
    assert_eq!(strata(&picks), [ERRORS, ERRORS, DISCOVERY]);
}

/// Slots a ranked stratum could not fill are drawn at random rather than lost.
#[test]
fn an_unused_ranked_slot_falls_through_to_discovery() {
    let db = fixtures_store();
    // If the error stratum leaves one of its three slots unused and discovery asks for two...
    let picks = whole(&db, &[("error_quota", "3"), ("discovery_quota", "2")]);
    // ...then discovery draws three, and the set is the quota sum it was given...
    assert_eq!(taken_by(&picks, DISCOVERY).len(), 3);
    assert_eq!(picks.len(), 3 + 2);
    // ...while a discovery quota larger than the pool stops at the pool, not in a loop.
    let exhausted = whole(&db, &[("error_quota", "3"), ("discovery_quota", "20")]);
    assert_eq!(exhausted.len(), windows::POOL_AT_WHOLE);
}

/// Sessions tied on a stratum's metric are ordered by session id, so the draw is fixed.
#[test]
fn a_stratum_ranks_by_its_metric_then_by_session_id() {
    let db = fixtures_store();
    // If three pool sessions compacted — `COMPACTED` three times, the other two once each,
    // so the metric decides the first slot and a tie decides the second...
    let picks = whole(&db, &[("compaction_quota", "2")]);
    // ...then the lower session id takes the tied slot. Without the tiebreak that draw is
    // whatever the storage layer felt like returning that day.
    assert_eq!(
        picks,
        [
            pick(COMPACTIONS, landmarks::COMPACTED),
            pick(COMPACTIONS, landmarks::ANCESTOR),
        ]
    );
    assert!(landmarks::ANCESTOR < landmarks::REGISTRY_ZOO);
}

// ---------------------------------------------------------------------------
// One slot per major skill

/// A skill qualifies on how many sessions used it, not how many calls it made.
#[test]
fn a_major_skill_is_one_used_across_sessions_not_one_used_often() {
    let db = fixtures_store();
    // If `pr-and-document` made four calls inside one pool session while `grill-me` made two
    // across two, and the threshold is two sessions...
    let picks = whole(&db, &[("skill_threshold", "2")]);
    // ...then only `grill-me` earns a slot, and it takes its most recent user. A call-counting
    // implementation ranks `pr-and-document` first and reads the wrong session.
    assert_eq!(picks, [pick(GRILL_ME, landmarks::SPINE)]);
}

/// Every major skill gets a reader, walking down its own users past what is taken.
#[test]
fn skills_are_iterated_in_name_order_each_taking_its_most_recent_user() {
    let db = fixtures_store();
    // If the cost stratum takes `SPINE` — the most recent user of both `grill-me` and
    // `night-run` — and every fixture skill qualifies...
    let picks = whole(&db, &[("cost_quota", "1"), ("skill_threshold", "1")]);
    let skills: Vec<&Pick> = picks
        .iter()
        .filter(|(stratum, _)| stratum.starts_with("skill:"))
        .collect();
    // ...then the skills are walked in name order, `grill-me` falls to its other user, and
    // `night-run`, whose only user is already selected, contributes nothing.
    assert_eq!(
        skills,
        [
            &pick("skill:deep-research", landmarks::DEEP_RESEARCH_SESSION),
            &pick(GRILL_ME, landmarks::SERVER_TOOLS),
            &pick("skill:manager", landmarks::COMPACTED),
            &pick("skill:pr-and-document", landmarks::ANCESTOR),
        ]
    );
}

/// A skill with nothing left to offer costs the iteration a slot, not a session.
#[test]
fn a_skill_whose_users_are_all_selected_gives_its_slot_to_discovery() {
    let db = fixtures_store();
    // If the cost stratum's four take both of `grill-me`'s pool users...
    let picks = whole(
        &db,
        &[
            ("cost_quota", "4"),
            ("discovery_quota", "1"),
            ("skill_threshold", "2"),
        ],
    );
    // ...then no skill row appears, and the skill's slot turns up in discovery: the budget
    // the citation reports is still the budget that was read.
    assert!(!strata(&picks).iter().any(|it| it.starts_with("skill:")));
    assert_eq!(
        strata(&picks),
        [COST, COST, COST, COST, DISCOVERY, DISCOVERY]
    );
}

// ---------------------------------------------------------------------------
// Discovery, and the pool it draws from

/// The random stratum is reproducible from its seed and disjoint from the ranked draw.
#[test]
fn discovery_is_a_function_of_its_seed_and_never_re_picks() {
    let db = fixtures_store();
    // If the same seed is drawn twice and a different one once...
    let seeded = |seed: &str| {
        whole(
            &db,
            &[
                ("cost_quota", "2"),
                ("error_quota", "1"),
                ("discovery_quota", "3"),
                ("seed", seed),
            ],
        )
    };
    let (first, again, other) = (seeded("a"), seeded("a"), seeded("b"));
    // ...then the seed alone decides the draw...
    assert_eq!(first, again);
    assert_ne!(first, other);
    // ...and discovery draws from what the ranked strata left, so no session is read twice.
    for picks in [&first, &other] {
        let side = |discovery: bool| -> BTreeSet<&str> {
            picks
                .iter()
                .filter(|(stratum, _)| (stratum == DISCOVERY) == discovery)
                .map(|(_, session)| session.as_str())
                .collect()
        };
        let (ranked, discovered) = (side(false), side(true));
        assert_eq!(discovered.len(), 3);
        assert!(ranked.is_disjoint(&discovered));
    }
}

/// A session that barely did anything cannot take a discovery slot, but is still ranked.
#[test]
fn discovery_passes_over_a_session_with_almost_nothing_in_it() {
    let db = fixtures_store();
    // If discovery is asked for the whole pool with the substance floor at four api calls...
    let picks = whole(
        &db,
        &[("discovery_quota", "20"), ("min_discovery_api_calls", "4")],
    );
    // ...then it draws the five pool sessions that made at least that many, and passes over
    // the rest, which made one to three. On mycelia's 2026-08-13 window, four of the eight
    // discovery draws had gone to sessions of 1, 4, 4 and 9 api calls.
    assert_eq!(
        sessions(&picks),
        BTreeSet::from([
            landmarks::SERVER_TOOLS,
            landmarks::SPINE,
            landmarks::ANCESTOR,
            landmarks::COMPACTED,
            landmarks::PARALLEL,
        ])
    );
    assert_eq!(
        strata(&picks).into_iter().collect::<BTreeSet<&str>>(),
        BTreeSet::from([DISCOVERY])
    );
    // ...while the floor is discovery's alone: a ranked stratum still reaches the thinnest
    // session in the corpus, because what it ranks on is the reason to read it.
    let ranked = whole(
        &db,
        &[("compaction_quota", "3"), ("min_discovery_api_calls", "4")],
    );
    assert!(ranked.contains(&pick(COMPACTIONS, landmarks::REGISTRY_ZOO)));
}

/// Sessions with no turns and no agent runs are unreadable, so no stratum reaches them.
#[test]
fn a_session_that_did_no_work_of_its_own_is_outside_the_pool() {
    let db = fixtures_store();
    // If discovery is asked for more sessions than the pool holds, every stratum runs dry...
    let picks = whole(
        &db,
        &[
            ("cost_quota", "4"),
            ("compaction_quota", "2"),
            ("discovery_quota", "20"),
        ],
    );
    // ...and what comes back is the pool itself — which excludes the two sessions whose
    // work belongs to another session, one of them despite having compacted.
    assert_eq!(picks.len(), windows::POOL_AT_WHOLE);
    let idle: BTreeSet<&str> = landmarks::NO_WORK_SESSIONS.iter().copied().collect();
    assert!(sessions(&picks).is_disjoint(&idle));
}

/// A session that only set an option has nothing to read, so discovery cannot draw it.
#[test]
fn a_session_whose_turns_made_no_api_call_is_outside_the_pool() {
    let (_scratch, db) = config_only_store();
    // If a session holds a turn but no api call — the shape a `/model` or `/effort` session
    // has, carried here twice: `MODEL_ONLY` recorded it, and `CONFIG_ONLY` is a second one
    // planted by stripping a real session's calls — then the whole-pool draw comes back
    // without either, one session shorter than a pool that never held `MODEL_ONLY` anyway...
    let quiet = [landmarks::CONFIG_ONLY, landmarks::MODEL_ONLY];
    let picks = whole(&db, &[("discovery_quota", "20")]);
    assert_eq!(picks.len(), windows::POOL_AT_WHOLE - 1);
    assert!(sessions(&picks).is_disjoint(&quiet.into_iter().collect()));
    // ...and it is the api-call floor that excluded them, not the missing rows: bind the floor
    // to zero and both are back in the pool. Iteration 1 spent three of eight discovery
    // slots on sessions like this, and the binding is in the citation so a report says which
    // pool it drew from.
    let admitted = whole(&db, &[("discovery_quota", "20"), ("min_api_calls", "0")]);
    assert_eq!(admitted.len(), windows::POOL_AT_WHOLE + 1);
    let drawn = sessions(&admitted);
    assert!(quiet.iter().all(|session| drawn.contains(session)));
}

/// Moving the as-of date moves the pool the draw is made from, and nothing else.
#[test]
fn the_selection_window_rides_as_of() {
    let db = fixtures_store();
    // If the same bindings are drawn against a window covering the whole corpus and then one
    // opening mid-corpus...
    let bindings = [("cost_quota", "3"), ("discovery_quota", "20")];
    let all = select(&db, &bindings, windows::AS_OF_WHOLE);
    let partial = select(&db, &bindings, windows::AS_OF_PARTIAL);
    // ...then the second draw is made entirely from the smaller pool, and stops at its size.
    assert_eq!(all.len(), windows::POOL_AT_WHOLE);
    assert_eq!(partial.len(), windows::POOL_AT_PARTIAL);
    let (all, partial) = (sessions(&all), sessions(&partial));
    assert!(partial.is_subset(&all) && partial != all);
}

/// A bare selection run draws the budget the committed reports quote.
#[test]
fn the_production_quotas_are_the_designed_reading_budget() {
    // Every other leaf here binds fixture-sized values, so this is the only thing standing
    // between an edited quota and a report citing a number nobody ran. The defaults cross the
    // generation bridge as JSON, so a seed reads back quoted and a quota bare.
    for (query, declared) in [
        ("select_sessions", PRODUCTION_QUOTAS),
        ("select_runs", PRODUCTION_RUN_QUOTAS),
    ] {
        let defaults: Vec<(&str, String)> = hyphae_store::manifest::entry(query)
            .params
            .iter()
            .map(|(name, spec)| {
                (
                    name.as_str(),
                    spec.default
                        .as_ref()
                        .unwrap_or_else(|| panic!("{query}.{name} has a default"))
                        .to_string(),
                )
            })
            .collect();
        let expected: Vec<(&str, String)> = declared
            .iter()
            .map(|(name, value)| (*name, (*value).to_owned()))
            .collect();
        assert_eq!(defaults, expected, "{query}");
    }
}

/// A commonly used agent definition is read every iteration, through its furthest runs.
#[test]
fn every_agent_type_gives_up_its_worst_and_its_costliest_run() {
    let db = fixtures_store();
    // If the corpus holds eleven runs across seven distinct agent types, one of which hit a
    // tool error, and the threshold is set low enough to admit all seven...
    let drawn = corpus(
        &db,
        "select_runs",
        windows::AS_OF_WHOLE,
        &[("min_runs", "1")],
    );
    let mut rows: Vec<Pick> = drawn
        .rows
        .iter()
        .map(|row| {
            (
                row.str("stratum").expect("a stratum").to_owned(),
                row.str("agent_type").expect("a type").to_owned(),
            )
        })
        .collect();
    // ...then every type contributes exactly one run, tagged with the stratum that took it:
    // the errored run by its errors, and the rest by what they spent.
    assert_eq!(rows.len(), windows::AGENT_TYPES);
    let distinct: BTreeSet<&str> = rows.iter().map(|(_, kind)| kind.as_str()).collect();
    assert_eq!(distinct.len(), rows.len());
    rows.sort();
    let mut expected: Vec<Pick> = [
        "Explore",
        "architect",
        "auditor",
        "claude",
        "general-purpose",
        "workflow-subagent",
    ]
    .iter()
    .map(|kind| pick("run-cost", kind))
    .collect();
    expected.push(pick("run-errors", "fork"));
    expected.sort();
    assert_eq!(rows, expected);
    // ...but `agent_type` is an open set — a session names its own subagents, and a name used
    // once is not a definition worth a reading slot every iteration. Raise the threshold above
    // every fixture type's run count and the same draw over the same runs comes back empty.
    let quiet = corpus(
        &db,
        "select_runs",
        windows::AS_OF_WHOLE,
        &[("min_runs", "4")],
    );
    assert!(quiet.rows.is_empty());
}

// ---------------------------------------------------------------------------
// The stores a leaf draws from

/// The shared corpus, read-only — what every leaf but the two below draws from.
fn fixtures_store() -> PathBuf {
    hyphae_testsupport::cache::corpus_store()
}

/// The same corpus, extracted in the opposite order — a different insertion order.
///
/// Built rather than cached: it exists to be a store nothing else shares, and one leaf pays
/// for it. Keep the `TempDir`: dropping it deletes the store the returned path names.
fn reversed_store() -> (TempDir, PathBuf) {
    let scratch = TempDir::new().expect("a tempdir for the reversed build");
    let path = scratch.path().join("traces.duckdb");
    let extractor = fixtures::extractor();
    let store = Store::create(&path).expect("a fresh store");
    for source in fixtures::corpus_sources().into_iter().rev() {
        let trace = extractor
            .extract(&source)
            .unwrap_or_else(|error| panic!("{} extracts: {error}", source.id));
        store
            .export(&trace, &source.fingerprint)
            .unwrap_or_else(|error| panic!("{} exports: {error}", source.id));
    }
    store
        .connection()
        .execute_batch("FORCE CHECKPOINT")
        .expect("the store checkpoints before it closes");
    drop(store);
    (scratch, path)
}

/// The corpus with one real session's api calls stripped, leaving it a turn and nothing.
///
/// Planted: every recorded fixture session answered its turns, so nothing in the corpus has
/// the shape of a session that only set an option. The turn is the recorded one.
fn config_only_store() -> (TempDir, PathBuf) {
    common::planted(|store| {
        // The tool calls go with them: a tool call with no api call behind it is a shape no
        // transcript holds.
        for table in ["tool_calls", "api_calls"] {
            store
                .connection()
                .execute(
                    &format!("DELETE FROM {table} WHERE session_id = ?"),
                    duckdb::params![landmarks::CONFIG_ONLY],
                )
                .expect("the copy gives up the stripped rows");
        }
    })
}
