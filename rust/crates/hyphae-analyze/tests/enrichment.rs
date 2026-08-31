//! The enrichment slice of the library: what is described, what one session says, what to check.
//!
//! The twin of `tests/analyze/test_enrichment.py`. Enrichment writes a model's words beside a
//! run, a turn and a session, and nothing in the store says whether those words are true. So
//! the leaves here are about the three things a reader needs before trusting them: a coverage
//! row that counts the items a pass could have described rather than every row of a level, a
//! per-session sheet that pairs an item's description with the timeline that shows what the
//! item did, and a draw that puts every category in front of a validation reader instead of
//! the common ones.
//!
//! The rows come from the enriched store, planted through the real writer: the keys are a
//! pass's own, the four model-written fields are invented, and the last item of each level is
//! left undescribed so coverage has a gap to report.

use std::collections::BTreeSet;
use std::path::Path;

use hyphae_analyze::{QueryError, Request};
use hyphae_store::{Param, Row};
use hyphae_testsupport::planting::PLANTED_MODELS;
use hyphae_testsupport::{cache, landmarks, windows};

mod common;

use common::{attempt, corpus, keyed, of_period, probe};

/// The level names the three queries share, as `enrich/prompts.py` spells them.
const TURN: &str = "turn";
const AGENT_RUN: &str = "agent_run";
const SESSION: &str = "session";

/// One level's coverage rows over the whole corpus, the window period dropped.
fn coverage(db: &Path, level: &str) -> Vec<Row> {
    of_period(
        &corpus(db, "enrichment_coverage", windows::AS_OF_WHOLE, &[]),
        "corpus",
    )
    .into_iter()
    .filter(|row| row.str("level").expect("a level") == level)
    .collect()
}

/// One `select_enrichments` draw over the whole corpus.
fn draw(db: &Path, level: &str, params: &[(&str, &str)]) -> Vec<Row> {
    let mut bound = vec![("level", level)];
    bound.extend_from_slice(params);
    corpus(db, "select_enrichments", windows::AS_OF_WHOLE, &bound).rows
}

/// One session's enrichment sheet.
fn digest(db: &Path, session_id: &str, params: &[(&str, &str)]) -> Vec<Row> {
    let mut bound = vec![("session_id", session_id)];
    bound.extend_from_slice(params);
    keyed(db, "enrichment_digest", &bound).rows
}

fn count(row: &Row, column: &str) -> i64 {
    row.i64(column).expect("a count")
}

/// A text column that is empty when no pass has written it — the gap a reader has to see.
fn said<'a>(row: &'a Row, column: &str) -> Option<&'a str> {
    row.opt_str(column).expect("a text column")
}

// ---------------------------------------------------------------------------
// Coverage

#[test]
fn coverage_counts_the_items_a_pass_could_have_described() {
    let db = cache::enriched_store();
    // If the corpus holds agent runs, all of which a pass would describe...
    let runs = count(
        &probe(
            &db,
            "SELECT count(*) AS n FROM corpus_agent_runs a
             JOIN sessions s ON s.id = a.session_id WHERE starts_with(s.project_dir, $project)",
            &[("project", landmarks::MYCELIA.into())],
        ),
        "n",
    );
    let described = count(
        &probe(
            &db,
            "SELECT count(*) AS n FROM agent_run_enrichments e
             JOIN corpus_agent_runs a ON a.session_id = e.session_id AND a.id = e.agent_run_id
             JOIN sessions s ON s.id = a.session_id WHERE starts_with(s.project_dir, $project)",
            &[("project", landmarks::MYCELIA.into())],
        ),
        "n",
    );
    assert!(0 < described && described < runs);

    let rows = coverage(&db, AGENT_RUN);
    // ...then every row of the level carries the same denominator, which is that count...
    let denominators: BTreeSet<i64> = rows.iter().map(|row| count(row, "level_items")).collect();
    assert_eq!(denominators, [runs].into_iter().collect());
    // ...the described rows add up to the rows a pass wrote...
    let counted: i64 = rows
        .iter()
        .filter(|row| said(row, "category").is_some())
        .map(|row| count(row, "items"))
        .sum();
    assert_eq!(counted, described);
    // ...and the ones it has not reached are one row with no category, so a reader sees the
    // gap without subtracting anything.
    let gap: Vec<i64> = rows
        .iter()
        .filter(|row| said(row, "category").is_none())
        .map(|row| count(row, "items"))
        .collect();
    assert_eq!(gap, vec![runs - described]);
}

#[test]
fn coverage_leaves_out_the_sessions_enrichment_never_describes() {
    let db = cache::enriched_store();
    // If the corpus holds sessions that did no work of their own, and sessions whose turns
    // drove no model response — both of which a pass skips...
    let counts = probe(
        &db,
        "SELECT count(*) AS total,
                count(*) FILTER ((r.turns > 0 OR r.agent_runs > 0) AND r.api_calls > 0)
                    AS enrichable,
                count(*) FILTER (r.turns > 0 AND r.api_calls = 0) AS silent
         FROM corpus_rollups r WHERE starts_with(r.project_dir, $project)",
        &[("project", landmarks::MYCELIA.into())],
    );
    let (total, enrichable, silent) = (
        count(&counts, "total"),
        count(&counts, "enrichable"),
        count(&counts, "silent"),
    );
    assert!(enrichable < total);
    // ...the second kind being what makes this leaf discriminate at all: without one in scope
    // the denominator reads the same whether the api-call filter is there or not.
    assert!(silent > 0);
    // ...then the session level counts the ones it would describe and no others.
    let denominators: BTreeSet<i64> = coverage(&db, SESSION)
        .iter()
        .map(|row| count(row, "level_items"))
        .collect();
    assert_eq!(denominators, [enrichable].into_iter().collect());
}

#[test]
fn coverage_splits_a_level_by_the_stamp_its_rows_were_written_under() {
    let db = cache::enriched_store();
    // If a level's rows were written by two models, and some under an older prompt...
    let rows: Vec<Row> = coverage(&db, TURN)
        .into_iter()
        .filter(|row| said(row, "category").is_some())
        .collect();
    let models: BTreeSet<&str> = rows
        .iter()
        .map(|row| row.str("enrichment_model").expect("a model"))
        .collect();
    assert_eq!(models, PLANTED_MODELS.into_iter().collect());
    let versions: BTreeSet<i64> = rows
        .iter()
        .map(|row| count(row, "prompt_version"))
        .collect();
    assert_eq!(versions.len(), 2);

    // ...then each model-and-version pair is counted on its own, matching the store...
    for model in PLANTED_MODELS {
        for version in &versions {
            let stamped: i64 = rows
                .iter()
                .filter(|row| {
                    row.str("enrichment_model").expect("a model") == model
                        && count(row, "prompt_version") == *version
                })
                .map(|row| count(row, "items"))
                .sum();
            let held = count(
                &probe(
                    &db,
                    "SELECT count(*) AS n FROM turn_enrichments e
                     JOIN corpus_turns t ON t.session_id = e.session_id
                        AND t.source = e.source AND t.id = e.turn_id
                     JOIN sessions s ON s.id = t.session_id
                     WHERE starts_with(s.project_dir, $project)
                       AND e.model = $model AND e.prompt_version = $version",
                    &[
                        ("project", landmarks::MYCELIA.into()),
                        ("model", model.into()),
                        ("version", Param::Int(*version)),
                    ],
                ),
                "n",
            );
            assert_eq!(stamped, held, "{model} at prompt version {version}");
        }
    }

    // ...and the pairs are not the same split as the categories, which would make either
    // column unreadable from the other.
    let pairs: BTreeSet<(&str, i64)> = rows
        .iter()
        .map(|row| {
            (
                row.str("enrichment_model").expect("a model"),
                count(row, "prompt_version"),
            )
        })
        .collect();
    let categories: BTreeSet<&str> = rows
        .iter()
        .map(|row| said(row, "category").expect("a category"))
        .collect();
    assert!(pairs.len() > 1 && categories.len() > 1);
}

// ---------------------------------------------------------------------------
// One session's sheet

#[test]
fn a_digest_lists_one_session_at_every_level_and_says_what_is_undescribed() {
    let db = cache::enriched_store();
    let rows = digest(&db, landmarks::SPINE, &[]);
    // If the session's sheet holds a row per main turn, per agent run, and one for itself...
    let levels: BTreeSet<&str> = rows
        .iter()
        .map(|row| row.str("level").expect("a level"))
        .collect();
    assert_eq!(levels, [TURN, AGENT_RUN, SESSION].into_iter().collect());
    for (level, sql) in [
        (
            TURN,
            "SELECT list(id) AS ids FROM live_turns
             WHERE session_id = $session AND source = 'main'",
        ),
        (
            AGENT_RUN,
            "SELECT list(id) AS ids FROM live_agent_runs WHERE session_id = $session",
        ),
    ] {
        let listed: BTreeSet<&str> = rows
            .iter()
            .filter(|row| row.str("level").expect("a level") == level)
            .map(|row| row.str("item_id").expect("an item"))
            .collect();
        let stored = probe(&db, sql, &[("session", landmarks::SPINE.into())]);
        let held: BTreeSet<&str> = stored
            .strings("ids")
            .expect("a list of ids")
            .into_iter()
            .collect();
        assert_eq!(listed, held, "the {level} rows");
    }
    // ...then the described ones carry the words and the stamp a reader is checking...
    let described: Vec<&Row> = rows
        .iter()
        .filter(|row| said(row, "description").is_some())
        .collect();
    assert!(!described.is_empty());
    for row in described {
        for column in [
            "category",
            "outcome",
            "enrichment_model",
            "taxonomy_version",
            "prompt_version",
        ] {
            assert!(
                !row.is_null(column).expect("a stamp column"),
                "a described row carries {column}"
            );
        }
    }
}

#[test]
fn an_item_no_pass_has_described_keeps_its_row_on_the_sheet() {
    let db = cache::enriched_store();
    // If a session the pass would describe has no enrichment row yet...
    let undescribed = probe(
        &db,
        "SELECT list(r.session_id) AS ids FROM session_rollups r
         LEFT JOIN session_enrichments e USING (session_id)
         WHERE (r.turns > 0 OR r.agent_runs > 0) AND e.session_id IS NULL",
        &[],
    );
    let undescribed: Vec<&str> = undescribed.strings("ids").expect("a list of ids");
    assert!(!undescribed.is_empty());
    // ...then its sheet still holds the session row, with the model's columns empty — which
    // is what tells a reader the pass has not reached it rather than that it does not exist.
    let rows = digest(&db, undescribed[0], &[("level", SESSION)]);
    for column in ["description", "category", "enrichment_model"] {
        let written: Vec<Option<&str>> = rows.iter().map(|row| said(row, column)).collect();
        assert_eq!(written, vec![None], "{column} on an undescribed session");
    }
}

#[test]
fn a_digest_narrows_to_one_level() {
    let db = cache::enriched_store();
    // Bind `$level` and the sheet is that level's rows — the rest is the same sheet.
    let every = digest(&db, landmarks::SPINE, &[]);
    let runs = digest(&db, landmarks::SPINE, &[("level", AGENT_RUN)]);
    let narrowed: Vec<&[duckdb::types::Value]> = every
        .iter()
        .filter(|row| row.str("level").expect("a level") == AGENT_RUN)
        .map(Row::values)
        .collect();
    assert_eq!(runs.iter().map(Row::values).collect::<Vec<_>>(), narrowed);
    assert!(!runs.is_empty() && runs.len() < every.len());
}

// ---------------------------------------------------------------------------
// The draw

#[test]
fn the_draw_takes_the_same_rows_every_time_it_runs() {
    let db = cache::enriched_store();
    // If the same bindings run twice, they return the same rows in the same order...
    let first = draw(&db, TURN, &[("per_category", "1")]);
    let again = draw(&db, TURN, &[("per_category", "1")]);
    let ids = |rows: &[Row]| -> Vec<String> {
        rows.iter()
            .map(|row| row.str("item_id").expect("an item").to_owned())
            .collect()
    };
    assert_eq!(ids(&first), ids(&again));
    // ...then the seed is what a reader rotates to see other items. The turn level, because
    // a stratum has to hold more items than the draw takes for a rotation to have anywhere
    // to go — the corpus's agent runs are two to a category.
    let rotated = draw(&db, TURN, &[("per_category", "1"), ("seed", "rotated")]);
    let set = |rows: &[Row]| -> BTreeSet<String> { ids(rows).into_iter().collect() };
    assert_ne!(set(&rotated), set(&first));
}

#[test]
fn the_draw_gives_every_category_the_same_number_of_slots() {
    let db = cache::enriched_store();
    // If a level's described rows fall into several categories of uneven size...
    let every = draw(&db, TURN, &[("per_category", "99")]);
    let mut sizes: std::collections::BTreeMap<&str, usize> = std::collections::BTreeMap::new();
    for row in &every {
        *sizes
            .entry(row.str("stratum").expect("a stratum"))
            .or_default() += 1;
    }
    let spread: BTreeSet<usize> = sizes.values().copied().collect();
    assert!(sizes.len() > 1 && spread.len() > 1);
    // ...then a draw of one apiece takes one from each, however big the category is.
    let one_each = draw(&db, TURN, &[("per_category", "1")]);
    let mut drawn: Vec<&str> = one_each
        .iter()
        .map(|row| row.str("stratum").expect("a stratum"))
        .collect();
    drawn.sort_unstable();
    assert_eq!(drawn, sizes.keys().copied().collect::<Vec<_>>());
}

#[test]
fn the_draw_carries_what_a_reader_needs_to_pick_and_open_an_item() {
    let db = cache::enriched_store();
    let rows = draw(&db, AGENT_RUN, &[("per_category", "99")]);
    assert!(!rows.is_empty());
    for row in &rows {
        // If a drawn row names the session and the source it sits at...
        let session = row.str("session_id").expect("a session");
        let source = row.str("source").expect("a source");
        assert_eq!(source, row.str("item_id").expect("an item"));
        assert!(said(row, "agent_type").is_some());
        // ...then its size is the run's own thread, as the store counts it — the runs it
        // spawned have sources of their own.
        let held = probe(
            &db,
            "SELECT count(*) AS calls, coalesce(round(sum(cost_usd), 4), 0) AS cost
             FROM corpus_api_calls WHERE session_id = $session AND source = $source",
            &[("session", session.into()), ("source", source.into())],
        );
        assert_eq!(count(row, "api_calls"), count(&held, "calls"));
        let (drawn, stored) = (
            row.f64("cost_usd").expect("a cost"),
            held.f64("cost").expect("a cost"),
        );
        assert!((drawn - stored).abs() < 1e-9, "{drawn} is not {stored}");
    }
}

#[test]
fn the_draw_and_the_digest_agree_on_one_item() {
    let db = cache::enriched_store();
    // If a draw names a run to check...
    let drawn = draw(&db, AGENT_RUN, &[("per_category", "99")]);
    let drawn = drawn.first().expect("a drawn run");
    // ...then opening that session's sheet shows the same description under the same key,
    // which is the pairing a validation read is built on.
    let sheet = digest(
        &db,
        drawn.str("session_id").expect("a session"),
        &[("level", AGENT_RUN)],
    );
    let row = sheet
        .iter()
        .find(|row| row.str("item_id").ok() == drawn.str("item_id").ok())
        .expect("the drawn run is on its session's sheet");
    assert_eq!(said(row, "description"), said(drawn, "description"));
    assert_eq!(said(row, "category"), said(drawn, "stratum"));
    assert_eq!(said(row, "outcome"), said(drawn, "outcome"));
}

// ---------------------------------------------------------------------------
// Against a store no pass has written to

#[test]
fn a_query_that_reads_the_enrichment_tables_says_so_on_a_store_without_them() {
    // Ask the bare corpus for coverage and it fails naming the missing table, not silently.
    let refusal = attempt(
        &cache::corpus_store(),
        "enrichment_coverage",
        Request {
            project: Some(landmarks::MYCELIA.into()),
            since: None,
            as_of: windows::date(windows::AS_OF_WHOLE),
            params: Default::default(),
        },
    )
    .expect_err("a store with no enrichment tables cannot answer");
    assert!(matches!(refusal, QueryError::Store(_)), "{refusal}");
    assert!(refusal.to_string().contains("_enrichments"), "{refusal}");
}
