//! The corpus descriptions: what kind of session each one was, and what each thing cost.
//!
//! The twin of `tests/analyze/test_shapes.py`. These six queries answer "describe this
//! corpus" rather than "count this event", and a description is only worth citing if the
//! reader can see the rule behind it — so the leaves here are about the rules: which sessions
//! a bound threshold moves between shapes, whose rows a per-run number counts, and what a
//! share of spend is a share of.
//!
//! Two of the rules cannot bite on the recorded corpus at all — it holds no `Edit`, `Write` or
//! `NotebookEdit` call and no `Skill` call — so those leaves plant one onto real rows and say
//! so. Both absences are asserted before the plant, because an unreachable arm is worth
//! knowing about.

use std::collections::BTreeMap;
use std::path::Path;

use hyphae_store::{Param, Row};
use hyphae_testsupport::{cache, landmarks, windows};
use tempfile::TempDir;

mod common;

use common::{corpus, key, of_period, probe, probe_all};

/// How many sessions each named shape holds — the form every expectation here takes.
type Shapes = &'static [(&'static str, i64)];

/// One bound threshold, the value it is moved to, and the corpus that binding describes.
type Rebinding = (&'static str, &'static str, Shapes);

/// How the recorded corpus classifies under the manifest's own thresholds: four of the seven
/// shapes, over all [`windows::MYCELIA_SESSIONS`] mycelia sessions. The rebindings below are
/// read against this. Re-take it by running `session_shapes`, not by editing a red number.
const RECORDED_SHAPES: Shapes = &[
    ("conversational", 7),
    ("no-work", 2),
    ("read-only-analysis", 4),
    ("skill-orchestrated", 2),
];

/// The two sessions `corpus_rollups` credits with no turns and no agent runs, and what makes
/// them worth a shape of their own: one compacted, so they are sessions that did work for a
/// thread elsewhere rather than sessions where nothing happened.
const NO_WORK_SESSIONS: i64 = 2;
const NO_WORK_COMPACTIONS: i64 = 1;

/// Each bound threshold, and the corpus it describes once it is moved. Three cut points, three
/// re-readings of the same 15 sessions.
const REBINDINGS: &[Rebinding] = &[
    // Three of the four read-only-analysis sessions ran 2 agent runs each, so lowering the bar
    // for delegation takes them and leaves the fourth...
    (
        "delegating_runs",
        "2",
        &[
            ("conversational", 7),
            ("no-work", 2),
            ("delegation-heavy", 3),
            ("skill-orchestrated", 2),
            ("read-only-analysis", 1),
        ],
    ),
    // ...a share no session can reach empties the skill shape, and its two sessions fall
    // through to whatever the arms below say they are...
    (
        "skill_share_pct",
        "101",
        &[
            ("conversational", 8),
            ("read-only-analysis", 5),
            ("no-work", 2),
        ],
    ),
    // ...and raising what counts as busy moves sessions the other way, out of analysis and
    // into conversation, because the same threshold decides both arms.
    (
        "busy_tool_calls",
        "8",
        &[
            ("conversational", 10),
            ("no-work", 2),
            ("skill-orchestrated", 2),
            ("read-only-analysis", 1),
        ],
    ),
];

/// The session the editing plant lands on, and what the plant is worth: `FORK_ORIGIN` holds 8
/// recorded `Read` calls, of which the corpus views keep 4 — its fork replays the other half
/// under a second source, and a replayed call is not the corpus's to count twice.
const PLANTED_EDIT_CALLS: i64 = 4;
/// Where the plant puts that session as `$editing_calls` moves across it. At 5 the ladder runs
/// off the end of its named shapes, which no binding over the recorded corpus can reach: a
/// session with edit calls is neither read-only nor, at 7 tool calls, conversational.
const EDITING_AT_OR_BELOW: &str = "solo-editing";
const EDITING_ABOVE: &str = "mixed";

/// Every agent definition the corpus spawned a run under.
const AGENT_TYPES: usize = 7;

/// The skills the recorded corpus attributes api calls to, as `(api_calls, sessions)`. No
/// fixture records a `Skill` tool call, so every one of these is invoked zero times — the
/// halves of this query are independent, which is the reason it joins them rather than reading
/// either alone.
const RECORDED_SKILLS: &[(&str, (i64, i64))] = &[
    ("pr-and-document", (4, 1)),
    ("grill-me", (2, 2)),
    ("deep-research", (2, 1)),
    ("manager", (1, 1)),
    ("night-run", (1, 1)),
];
/// The skills the plant invokes: one the corpus already attributes calls to, so the two halves
/// meet in one row; one it does not, so the invoked half stands alone at zero api calls.
const INVOKED_ATTRIBUTED: &str = "grill-me";
const INVOKED_ALONE: &str = "commit";
/// A `Skill` call whose input is not JSON, which the query keeps under a NULL skill rather than
/// filtering away — a shape change should arrive as a row a reader can see.
const UNREADABLE_INPUT: &str = "not json at all";

/// The command turn the corpus records with any spend of its own: one turn of `SPINE`, holding
/// one api call and the three tool calls under it.
const BILLED_COMMAND: &str = "/night-run";
const BILLED_API_CALLS: i64 = 1;
const BILLED_TOOL_CALLS: i64 = 3;

/// What the costliest tenth of 15 sessions is: `percent_rank` puts two of them at or above 0.9,
/// and their share of the corpus bill is what a mean would hide.
const TOP_DECILE_SESSIONS: usize = 2;

/// One query over the fixture project, as the rows of the whole-corpus period.
fn rows_of(db: &Path, name: &str, params: &[(&str, &str)]) -> Vec<Row> {
    of_period(&corpus(db, name, windows::AS_OF_WHOLE, params), "corpus")
}

/// `session_shapes` over the whole corpus, as how many sessions each shape holds.
fn shapes(db: &Path, params: &[(&str, &str)]) -> BTreeMap<String, i64> {
    rows_of(db, "session_shapes", params)
        .iter()
        .map(|row| {
            (
                row.str("shape").expect("a shape").to_owned(),
                row.i64("sessions").expect("a count"),
            )
        })
        .collect()
}

/// A table of expected counts as the map a query's rows key into.
fn expected(pairs: &[(&str, i64)]) -> BTreeMap<String, i64> {
    pairs
        .iter()
        .map(|(name, count)| ((*name).to_owned(), *count))
        .collect()
}

/// One count a leaf reads off the store itself rather than off the query under test.
fn counted(db: &Path, sql: &str, params: &[(&str, Param)]) -> i64 {
    probe(db, sql, params).i64("n").expect("a count")
}

/// Four decimal places, as every money column of these queries is rounded to.
fn to_4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}

// ---------------------------------------------------------------------------
// The shape ladder

#[test]
fn every_corpus_session_lands_in_exactly_one_shape() {
    let db = cache::corpus_store();
    // Under the thresholds the manifest ships, the recorded corpus falls into four shapes...
    assert_eq!(shapes(&db, &[]), expected(RECORDED_SHAPES));
    // ...which between them account for every session the window holds, so a reader comparing
    // two shapes' costs is comparing parts of one whole rather than two samples.
    let total: i64 = RECORDED_SHAPES.iter().map(|(_, count)| count).sum();
    assert_eq!(
        usize::try_from(total).expect("a count"),
        windows::MYCELIA_SESSIONS
    );
}

/// A session with nothing of its own is called that, whatever the thresholds are bound to.
///
/// The ladder is ordered and first match wins, which only matters at the top: `no-work` is a
/// statement about the session, and the shapes under it are statements about a threshold.
#[test]
fn a_session_that_did_no_work_is_shaped_before_any_threshold_is_read() {
    let db = cache::corpus_store();
    // The no-work sessions are not empty recordings — one compacted, and their spend is
    // counted — so a ladder that read the metrics first would have something to say about them.
    let rows = key(&rows_of(&db, "session_shapes", &[]), "shape");
    let no_work = &rows["no-work"];
    assert_eq!(no_work.i64("sessions").expect("a count"), NO_WORK_SESSIONS);
    assert_eq!(
        no_work.i64("compactions").expect("a count"),
        NO_WORK_COMPACTIONS
    );
    assert!(no_work.f64("cost_usd").expect("a cost") > 0.0);
    // With `$skill_share_pct` bound at 0 every session that made an api call while a skill was
    // loaded matches the arm below, so the whole corpus would be skill-orchestrated if the
    // first arm did not win. The two stay where they are.
    assert_eq!(
        shapes(&db, &[("skill_share_pct", "0")])["no-work"],
        NO_WORK_SESSIONS
    );
}

/// Each bound threshold is a cut point a reader can move, and moving one re-describes the
/// corpus.
///
/// The shapes are a starting vocabulary rather than a finding, so what has to hold is that the
/// binding in a citation is the whole classifier: re-run it rebound and the sessions move.
#[test]
fn a_rebound_threshold_moves_sessions_between_shapes() {
    let db = cache::corpus_store();
    assert!(!REBINDINGS.is_empty(), "nothing was rebound");
    for (param, value, moved) in REBINDINGS {
        assert_eq!(shapes(&db, &[(param, value)]), expected(moved), "{param}");
        // However the sessions move, they are the same sessions: a rebinding re-describes the
        // corpus rather than sampling part of it.
        let total: i64 = moved.iter().map(|(_, count)| count).sum();
        assert_eq!(
            usize::try_from(total).expect("a count"),
            windows::MYCELIA_SESSIONS,
            "{param}"
        );
    }
}

/// A session that edits is shaped by how much it edited, and the ladder's last arm exists.
///
/// Bounding the absence first: the recorded corpus holds no edit call at all, so `solo-editing`
/// and `mixed` are arms nothing can reach and the plant is what reaches them.
#[test]
fn the_editing_shapes_need_edit_calls_no_fixture_recorded() {
    assert_eq!(
        counted(
            &cache::corpus_store(),
            "SELECT count(*) AS n FROM corpus_tool_calls
             WHERE name IN ('Edit', 'Write', 'NotebookEdit')",
            &[],
        ),
        0
    );

    let (_scratch, db) = planted_edits();
    // With one session's reads rewritten as edits, a threshold at or under its 4 edit calls
    // calls it solo editing — the shape that reads "this session did its own work"...
    let at_four: Vec<(i64, i64)> = rows_of(&db, "session_shapes", &[("editing_calls", "4")])
        .iter()
        .filter(|row| row.str("shape").expect("a shape") == EDITING_AT_OR_BELOW)
        .map(|row| {
            (
                row.i64("sessions").expect("a count"),
                row.i64("edit_calls").expect("a count"),
            )
        })
        .collect();
    assert_eq!(at_four, vec![(1, PLANTED_EDIT_CALLS)]);
    // ...and a threshold above them drops the session past every named shape into the ladder's
    // last arm: it edited, so it is not read-only, and it is busy, so it is not conversational.
    let above = shapes(
        &db,
        &[("editing_calls", &(PLANTED_EDIT_CALLS + 1).to_string())],
    );
    assert_eq!(above[EDITING_ABOVE], 1);
    assert!(!above.contains_key(EDITING_AT_OR_BELOW));
}

// ---------------------------------------------------------------------------
// What each thing cost

/// A run's counts are the rows written under its own agent id, not its children's.
///
/// `SPINE` is the fixture with a run that spawned a run. Sum a subtree instead and the parent
/// definition's per-run average silently double-counts the work its children did.
#[test]
fn an_agent_types_numbers_are_the_runs_own_thread_not_its_subtree() {
    let db = cache::corpus_store();
    let rows = key(&rows_of(&db, "agent_types", &[]), "agent_type");
    // Every recorded run is counted once, under the definition that ran it, so `runs` cannot
    // hide a fan-out...
    assert_eq!(rows.len(), AGENT_TYPES);
    let runs: i64 = rows
        .values()
        .map(|row| row.i64("runs").expect("a count"))
        .sum();
    assert_eq!(
        runs,
        counted(
            &db,
            "SELECT count(*) AS n FROM corpus_agent_runs a JOIN sessions s ON s.id = a.session_id
             WHERE s.project_dir = $project",
            &[("project", Param::Text(landmarks::MYCELIA.into()))],
        )
    );
    // ...and the parent's tool calls are its own thread's, with the child's counted only under
    // the child. Read from the store by source, which is what the query claims to do.
    let by_source = |source: &str| {
        counted(
            &db,
            "SELECT count(*) AS n FROM corpus_tool_calls
             WHERE session_id = $session AND source = $source",
            &[
                ("session", Param::Text(landmarks::SPINE.into())),
                ("source", Param::Text(source.into())),
            ],
        )
    };
    let (parent, child) = (
        by_source(landmarks::SPINE_RUN),
        by_source(landmarks::SPINE_LEAF),
    );
    assert!(
        child > 0,
        "the child run recorded no tool calls: it no longer proves the case"
    );
    assert_eq!(rows["claude"].i64("tool_calls").expect("a count"), parent);
    assert_eq!(rows["Explore"].i64("tool_calls").expect("a count"), child);
    // And a run that forked its parent's thread is flagged as one, which is what stops a fork's
    // replayed opening being read as a definition that gets spawned twice as often as it is.
    assert_eq!(rows["fork"].i64("forks").expect("a count"), 1);
}

/// The distribution says what the costliest tenth of sessions is worth, not just the mean.
///
/// Checked against the costliest sessions read off the store by cost order — a different
/// mechanism from the query's `percent_rank`, so the two agreeing is evidence.
#[test]
fn the_top_decile_share_is_what_the_costliest_sessions_carry() {
    let db = cache::corpus_store();
    let rows = rows_of(&db, "cost_distribution", &[]);
    let [row] = &rows[..] else {
        panic!("one row per period");
    };
    let costs: Vec<f64> = probe_all(
        &db,
        "SELECT r.cost_usd FROM corpus_rollups r JOIN sessions s ON s.id = r.session_id
         WHERE s.project_dir = $project ORDER BY r.cost_usd DESC",
        &[("project", Param::Text(landmarks::MYCELIA.into()))],
    )
    .iter()
    .map(|row| row.f64("cost_usd").expect("a cost"))
    .collect();
    let bill: f64 = costs.iter().sum();
    assert_eq!(costs.len(), windows::MYCELIA_SESSIONS);
    assert_eq!(
        usize::try_from(row.i64("sessions").expect("a count")).expect("a count"),
        costs.len()
    );
    // The whole bill, its mean and its maximum are the corpus's own...
    assert_eq!(row.f64("cost_usd").expect("a cost"), to_4(bill));
    assert_eq!(
        row.f64("mean_cost_usd").expect("a cost"),
        to_4(bill / costs.len() as f64)
    );
    assert_eq!(row.f64("max_cost_usd").expect("a cost"), to_4(costs[0]));
    // ...and the share is the top two sessions' — over 15 sessions, the two `percent_rank`
    // puts at or above 0.9 — which is a third of the spend from an eighth of the sessions.
    let top: f64 = costs[..TOP_DECILE_SESSIONS].iter().sum();
    assert_eq!(
        row.f64("top_decile_share").expect("a share"),
        to_4(top / bill)
    );
}

// ---------------------------------------------------------------------------
// Skills, commands and failures

/// Every skill the corpus attributes calls to is listed with its own spread of sessions.
#[test]
fn skill_activity_counts_the_calls_made_while_a_skill_was_loaded() {
    let db = cache::corpus_store();
    let rows = rows_of(&db, "skill_activity", &[]);
    let measured: BTreeMap<String, (i64, i64)> = rows
        .iter()
        .map(|row| {
            (
                row.str("skill").expect("a skill").to_owned(),
                (
                    row.i64("api_calls").expect("a count"),
                    row.i64("sessions").expect("a count"),
                ),
            )
        })
        .collect();
    let declared: BTreeMap<String, (i64, i64)> = RECORDED_SKILLS
        .iter()
        .map(|(skill, pair)| ((*skill).to_owned(), *pair))
        .collect();
    assert_eq!(measured, declared);
    // None of them was invoked: no fixture records a `Skill` tool call, so the recorded corpus
    // is evidence about attribution alone. The planted leaf below covers the other half.
    let invocations: Vec<i64> = rows
        .iter()
        .map(|row| row.i64("invocations").expect("a count"))
        .collect();
    assert_eq!(invocations, vec![0; rows.len()]);
}

/// A skill is listed whether it was invoked, attributed calls, or both.
///
/// Bounding the absence first: no fixture records a `Skill` call, so the invoked half of this
/// query and both outer arms of its join are unreachable without the plant.
#[test]
fn a_skill_invocation_joins_its_attributed_calls_or_stands_alone() {
    assert_eq!(
        counted(
            &cache::corpus_store(),
            "SELECT count(*) AS n FROM corpus_tool_calls WHERE name = 'Skill'",
            &[],
        ),
        0
    );

    let (_scratch, db) = planted_skills();
    let rows = rows_of(&db, "skill_activity", &[]);
    // A NULL skill is read as one rather than keyed by the empty string a CSV writer would
    // print for it: the query means "the parser could not name this one".
    let named = |skill: &str| -> &Row {
        rows.iter()
            .find(|row| row.opt_str("skill").expect("a skill column") == Some(skill))
            .unwrap_or_else(|| panic!("no row for `{skill}`"))
    };
    // A skill invoked in a session that also attributed calls to it is one row, not two: the
    // two halves are the same skill seen from either end...
    let merged = named(INVOKED_ATTRIBUTED);
    assert_eq!(
        (
            merged.i64("invocations").expect("a count"),
            merged.i64("invoking_sessions").expect("a count")
        ),
        (1, 1)
    );
    let attributed = RECORDED_SKILLS
        .iter()
        .find(|(skill, _)| *skill == INVOKED_ATTRIBUTED)
        .expect("the corpus attributes calls to it")
        .1;
    assert_eq!(
        (
            merged.i64("api_calls").expect("a count"),
            merged.i64("sessions").expect("a count")
        ),
        attributed
    );
    // ...a skill invoked and never attributed a call still gets a row, at zero — the shape of
    // a skill whose work runs as plain turns...
    let alone = named(INVOKED_ALONE);
    assert_eq!(
        (
            alone.i64("invocations").expect("a count"),
            alone.i64("api_calls").expect("a count")
        ),
        (1, 0)
    );
    // ...and a call whose input the parser could not read lands under a nameless skill instead
    // of vanishing, so a schema change shows up as a row rather than a smaller count.
    let nameless: Vec<&Row> = rows
        .iter()
        .filter(|row| row.opt_str("skill").expect("a skill column").is_none())
        .collect();
    assert_eq!(nameless.len(), 1);
    assert_eq!(nameless[0].i64("invocations").expect("a count"), 1);
}

/// A slash command is billed for the turn it started — not for the rest of that thread, and
/// not for what an agent run did.
///
/// The corpus's one billed command sits in a session that holds other turns and two agent
/// runs. The runs write under their own sources, so a bill counted per thread would swallow
/// them; counted per turn it takes neither them nor the neighbouring turns.
#[test]
fn a_commands_bill_is_its_own_turns_calls_and_nothing_elses() {
    let db = cache::corpus_store();
    let rows = key(&rows_of(&db, "slash_commands", &[]), "command");
    let billed = &rows[BILLED_COMMAND];
    assert_eq!(
        (
            billed.i64("api_calls").expect("a count"),
            billed.i64("tool_calls").expect("a count")
        ),
        (BILLED_API_CALLS, BILLED_TOOL_CALLS)
    );
    // The session's main thread holds more tool calls than the command's turn is billed for,
    // and the session holds more again once the agent runs are counted. Both gaps are real, so
    // a bill widened to either would be a bigger number than this one.
    let spine = [("session", Param::Text(landmarks::SPINE.into()))];
    let main_thread = counted(
        &db,
        "SELECT count(*) AS n FROM corpus_tool_calls
         WHERE session_id = $session AND source = 'main'",
        &spine,
    );
    let whole_session = counted(
        &db,
        "SELECT count(*) AS n FROM corpus_tool_calls WHERE session_id = $session",
        &spine,
    );
    assert!(BILLED_TOOL_CALLS < main_thread && main_thread < whole_session);
}

/// Every tool the corpus called is listed with its errors, its calls and its spread.
#[test]
fn tool_failures_reports_each_error_beside_the_calls_it_is_a_rate_over() {
    let db = cache::corpus_store();
    let rows = rows_of(&db, "tool_failures", &[]);
    // Every call the corpus holds is counted once, under the tool that made it...
    let calls: i64 = rows
        .iter()
        .map(|row| row.i64("calls").expect("a count"))
        .sum();
    assert_eq!(
        calls,
        counted(
            &db,
            "SELECT count(*) AS n FROM corpus_tool_calls t JOIN sessions s ON s.id = t.session_id
             WHERE s.project_dir = $project",
            &[("project", Param::Text(landmarks::MYCELIA.into()))],
        )
    );
    // ...and the two recorded failures come back as rates over very different denominators,
    // which is the pair this query exists to keep together: one `Agent` call in ten failed,
    // in one of the five sessions that called it, against one server-side `advisor` call in
    // three, in the only session that called it at all.
    let failing: BTreeMap<String, &Row> = rows
        .iter()
        .filter(|row| row.i64("errors").expect("a count") > 0)
        .map(|row| (row.str("tool").expect("a tool").to_owned(), row))
        .collect();
    let read = |row: &Row| {
        (
            row.i64("errors").expect("a count"),
            row.i64("calls").expect("a count"),
            row.f64("error_rate").expect("a rate"),
        )
    };
    assert_eq!(
        failing
            .iter()
            .map(|(tool, row)| (tool.clone(), read(row)))
            .collect::<BTreeMap<String, (i64, i64, f64)>>(),
        BTreeMap::from([
            ("Agent".to_owned(), (1, 10, 0.1)),
            ("advisor".to_owned(), (1, 3, 0.3333)),
        ])
    );
    assert_eq!(
        (
            failing["Agent"].i64("sessions").expect("a count"),
            failing["Agent"].i64("erring_sessions").expect("a count")
        ),
        (5, 1)
    );
}

// ---------------------------------------------------------------------------
// The plants

/// The corpus with one session's `Read` calls rewritten as `Edit` calls.
///
/// Invented, and it has to be: no recorded fixture edits a file, so the shapes that read
/// `edit_calls` are arms nothing exercises. What is real is the rows — their session, its
/// period and its other counts — and the fork among them, which is why the corpus views keep
/// four of the eight rewritten calls.
fn planted_edits() -> (TempDir, std::path::PathBuf) {
    common::planted(|store| {
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET name = 'Edit' WHERE name = 'Read' AND session_id = ?",
                duckdb::params![landmarks::FORK_ORIGIN],
            )
            .expect("the copy takes the planted edits");
    })
}

/// The corpus with three of `SPINE`'s reads rewritten as `Skill` invocations.
///
/// Invented inputs — fixture redaction replaces every tool input, and no fixture calls `Skill`
/// at all — but the shape is the one `docs/schema.md` records: the skill's name sits at
/// `$.skill` of the call's input. One invokes a skill the corpus attributes calls to, one a
/// skill it does not, and one carries an input no parser can read.
fn planted_skills() -> (TempDir, std::path::PathBuf) {
    common::planted(|store| {
        let ids: Vec<String> = {
            let mut statement = store
                .connection()
                .prepare(
                    "SELECT id FROM tool_calls
                     WHERE name = 'Read' AND session_id = ? AND source = 'main' ORDER BY id",
                )
                .expect("the copy answers");
            let found = statement
                .query_map(duckdb::params![landmarks::SPINE], |row| {
                    row.get::<_, String>(0)
                })
                .expect("the reads are readable");
            found.map(|id| id.expect("an id")).collect()
        };
        let inputs = [
            serde_json::json!({ "skill": INVOKED_ATTRIBUTED }).to_string(),
            serde_json::json!({ "skill": INVOKED_ALONE }).to_string(),
            UNREADABLE_INPUT.to_owned(),
        ];
        assert!(
            ids.len() >= inputs.len(),
            "SPINE's main thread lost reads: re-pick the rows"
        );
        for (call_id, value) in ids.iter().zip(inputs.iter()) {
            store
                .connection()
                .execute(
                    "UPDATE tool_calls SET name = 'Skill', input = ?
                     WHERE id = ? AND session_id = ?",
                    duckdb::params![value, call_id, landmarks::SPINE],
                )
                .expect("the copy takes the planted invocations");
        }
    })
}
