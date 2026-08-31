//! What a written enrichment row does: replacing, going stale, and being swept.
//!
//! Ported from the writing half of `tests/enrich/test_store.py`; [`common`] holds the fixture
//! keys and the scratch store, and `read.rs` holds the leaves that only assemble items.

use std::collections::BTreeSet;

use duckdb::params;
use hyphae_enrich::{EnrichError, EnrichmentStore, Item, Level, SessionItem, Stamp};
use hyphae_store::StoreError;
use hyphae_testsupport::{cache, corpus};
use tempfile::TempDir;

mod common;

use common::{
    DESCRIBABLE, DUP_UUID, FORK_BYREF, INVENTED_EMPTY, MODEL_ONLY, RESUME, SPINE, STOP_REASON_TURN,
    column, enrichment, open_copy, spine_runs, spine_turns, stamp,
};

#[test]
fn the_tables_survive_a_re_export() {
    let (_scratch, store) = open_copy();
    // If a turn of `spine/` is enriched...
    store
        .upsert(
            &spine_turns(&store)[0],
            &enrichment("Read two files."),
            &stamp("hash-1"),
        )
        .expect("the row writes");
    let before = column(
        &store,
        "SELECT turn_id FROM turn_enrichments ORDER BY turn_id",
    );
    // ...and the pipeline then replaces every row that session owns...
    let source = corpus::fixture_source("spine", SPINE);
    let trace = corpus::extractor()
        .extract(&source)
        .expect("spine extracts");
    store
        .store()
        .export(&trace, &source.fingerprint)
        .expect("spine re-exports");
    // ...then the enrichment row is untouched: the per-session replace never reaches these
    // tables.
    assert_eq!(
        column(
            &store,
            "SELECT turn_id FROM turn_enrichments ORDER BY turn_id"
        ),
        before
    );
    assert_eq!(before.len(), 1);
}

#[test]
fn a_second_upsert_replaces_the_row() {
    let (_scratch, store) = open_copy();
    let item = spine_turns(&store).remove(0);
    store
        .upsert(&item, &enrichment("The first answer."), &stamp("hash-1"))
        .expect("the first row writes");
    store
        .upsert(&item, &enrichment("The second answer."), &stamp("hash-2"))
        .expect("the second row writes");
    // Enriching the same item twice leaves one row, holding the second answer.
    assert_eq!(
        column(&store, "SELECT description FROM turn_enrichments"),
        [Some("The second answer.".to_owned())]
    );
    assert_eq!(
        column(&store, "SELECT input_hash FROM turn_enrichments"),
        [Some("hash-2".to_owned())]
    );
}

#[test]
fn enriched_turns_left_joins() {
    // If two of `spine/`'s four main turns are enriched...
    let (_scratch, store) = open_copy();
    for item in spine_turns(&store).iter().take(2) {
        store
            .upsert(item, &enrichment("Read two files."), &stamp("hash-1"))
            .expect("the row writes");
    }
    // ...then the view returns all four, and says plainly which two carry no description.
    assert_eq!(
        column(
            &store,
            &format!(
                "SELECT description FROM enriched_turns
                 WHERE session_id = '{SPINE}' AND source = 'main' ORDER BY \"index\""
            ),
        ),
        [
            Some("Read two files.".to_owned()),
            Some("Read two files.".to_owned()),
            None,
            None
        ]
    );
}

/// A row is stale when any of the four staleness fields differs from today's value.
fn assert_staleness_moves(column: &str, value: &str) {
    let (_scratch, store) = open_copy();
    let items = spine_turns(&store);
    let planned: Vec<(String, Stamp)> = items
        .iter()
        .map(|item| (item.key(), stamp("hash-1")))
        .collect();
    // If every turn is enriched under the same stamp, nothing is stale...
    for item in &items {
        store
            .upsert(item, &enrichment("Read two files."), &stamp("hash-1"))
            .expect("the row writes");
    }
    assert_eq!(
        store
            .stale_keys(Level::Turn, &planned)
            .expect("staleness reads"),
        Vec::<String>::new()
    );
    // ...and if one stored row's stamp is moved off today's value...
    let target = &items[1];
    store
        .connection()
        .execute(
            &format!("UPDATE turn_enrichments SET {column} = ? WHERE turn_id = ?"),
            params![value, target.turn_id],
        )
        .expect("the stamp moves");
    // ...then that row, and only that row, comes back stale.
    assert_eq!(
        store
            .stale_keys(Level::Turn, &planned)
            .expect("staleness reads"),
        [target.key()]
    );
}

// Each of the four fields of the staleness key, changed one at a time on a stored row: the
// re-render that produced a different prompt...
#[test]
fn a_moved_input_hash_is_stale() {
    assert_staleness_moves("input_hash", "a-different-hash");
}

// ...an instruction or output-schema change...
#[test]
fn a_moved_prompt_version_is_stale() {
    assert_staleness_moves("prompt_version", "99");
}

// ...a taxonomy revision...
#[test]
fn a_moved_taxonomy_version_is_stale() {
    assert_staleness_moves("taxonomy_version", "99");
}

// ...and a `--model` switch.
#[test]
fn a_moved_model_is_stale() {
    assert_staleness_moves("model", "claude-sonnet-4-5");
}

#[test]
fn an_item_with_no_row_is_stale() {
    // A turn nothing has enriched yet is stale, which is how a first pass finds work.
    let (_scratch, store) = open_copy();
    let planned: Vec<(String, Stamp)> = spine_turns(&store)
        .iter()
        .map(|item| (item.key(), stamp("hash-1")))
        .collect();
    assert_eq!(
        store
            .stale_keys(Level::Turn, &planned)
            .expect("staleness reads"),
        planned
            .iter()
            .map(|(key, _)| key.clone())
            .collect::<Vec<_>>()
    );
}

#[test]
fn a_zombie_enrichment_is_swept() {
    // If every main turn of `spine/` is enriched...
    let (_scratch, store) = open_copy();
    let items = spine_turns(&store);
    for item in &items {
        store
            .upsert(item, &enrichment("Read two files."), &stamp("hash-1"))
            .expect("the row writes");
    }
    // ...and one of those turns then vanishes — an extractor bump redrawing turn boundaries
    // is the real case...
    let gone = &items[1];
    store
        .connection()
        .execute("DELETE FROM turns WHERE id = ?", params![gone.turn_id])
        .expect("the turn is deleted");
    // ...then the sweep reports and removes its enrichment, and leaves the rest alone.
    assert_eq!(store.sweep_zombies().expect("the sweep runs"), 1);
    let mut survivors: Vec<String> = items
        .iter()
        .filter(|item| item.turn_id != gone.turn_id)
        .map(|item| item.turn_id.clone())
        .collect();
    survivors.sort();
    assert_eq!(
        column(
            &store,
            "SELECT turn_id FROM turn_enrichments ORDER BY turn_id"
        ),
        survivors.into_iter().map(Some).collect::<Vec<_>>()
    );
}

#[test]
fn a_session_with_no_turn_and_no_run_is_never_enriched() {
    // 102 of 575 recorded sessions are in this state — compactions and duplicate-uuid records
    // with no work of their own.
    let (_scratch, store) = open_copy();
    let described: BTreeSet<String> = store
        .session_items(None)
        .expect("the sessions read")
        .into_iter()
        .map(|item| item.session_id)
        .collect();
    // If a session in the store recorded no main turn and no agent run...
    let empty: BTreeSet<String> = column(
        &store,
        "SELECT session_id FROM session_rollups WHERE turns = 0 AND agent_runs = 0",
    )
    .into_iter()
    .flatten()
    .collect();
    // ...then it is not an item, so nothing ever sends it or writes a row for it, while every
    // other session of the fixture corpus is. `resume_pair/`'s resume is one of the three: its
    // api calls all sit under a turn its ancestor ran, so it opened none of its own.
    assert_eq!(
        empty,
        BTreeSet::from([
            DUP_UUID.to_owned(),
            RESUME.to_owned(),
            INVENTED_EMPTY.to_owned()
        ])
    );
    assert_eq!(described.intersection(&empty).count(), 0);
    assert_eq!(described.len(), DESCRIBABLE);
}

#[test]
fn an_api_call_carries_the_stop_reason_as_recorded() {
    // A null is a recorded state, not a missing row — 26 of the 69 stop reasons in the
    // fixtures are null — so the render can say "not recorded" rather than say nothing.
    let (_scratch, store) = open_copy();
    // If a turn's three recorded calls stopped `end_turn`, `tool_use` and nothing...
    let item = store
        .turn_items(None)
        .expect("the turns read")
        .into_iter()
        .find(|item| item.turn_id == STOP_REASON_TURN)
        .expect("the recorded turn is in the corpus");
    // ...then the items carry all three values in the order they were recorded, so no render
    // of the three states rests on an invented row.
    assert_eq!(
        item.api_calls
            .iter()
            .map(|call| call.stop_reason.as_deref())
            .collect::<Vec<_>>(),
        [Some("end_turn"), Some("tool_use"), None]
    );
}

#[test]
fn a_session_whose_turns_drove_no_api_call_is_never_enriched() {
    // 45 of the 473 sessions with work in them are in this state — `/model` and `/effort`
    // turns that the CLI answered by itself — and every description written for one was
    // invented.
    let (_scratch, store) = open_copy();
    let described: BTreeSet<String> = store
        .session_items(None)
        .expect("the sessions read")
        .into_iter()
        .map(|item| item.session_id)
        .collect();
    // If the recorded `/model` session drove no api call under any of its three turns —
    // `/model`, `/clear` and `/reload-skills`, all answered by the CLI itself...
    let rollup = store
        .store()
        .fetch(
            "SELECT turns, agent_runs, api_calls FROM session_rollups WHERE session_id = $id",
            &[("id", MODEL_ONLY.into())],
        )
        .expect("the rollup reads");
    assert_eq!(
        (
            rollup[0].i64("turns").expect("turns read"),
            rollup[0].i64("agent_runs").expect("runs read"),
            rollup[0].i64("api_calls").expect("calls read")
        ),
        (3, 0, 0)
    );
    // ...then it is not an item, while a session whose only work is a subagent's — no turn of
    // its own, and its api calls all under the run — still is: the gate counts calls across
    // every source, not just the main transcript.
    assert!(!described.contains(MODEL_ONLY));
    assert!(described.contains(FORK_BYREF));
    // ...its `/model` turn is still an item of its own, since turns are deliberately not
    // gated and the `configure` census reads `turns.command_name` off exactly these...
    assert!(
        store
            .turn_items(None)
            .expect("the turns read")
            .iter()
            .any(|item| item.session_id == MODEL_ONLY)
    );
    // ...and the sessions view still carries it, with nothing described — which is what makes
    // the viewer render no enrichment block, as a never-described session does.
    assert_eq!(
        column(
            &store,
            &format!("SELECT description FROM enriched_sessions WHERE session_id = '{MODEL_ONLY}'"),
        ),
        [None]
    );
}

#[test]
fn a_row_already_written_for_a_gated_session_is_swept() {
    let (_scratch, store) = open_copy();
    // If a row was written for a gated session before the gate existed — as 45 were...
    store
        .upsert(
            &SessionItem::bare(MODEL_ONLY),
            &enrichment("Read two files."),
            &stamp("hash-1"),
        )
        .expect("the row writes");
    // ...then the sweep takes it, because a row no pass will ever refresh is a zombie by the
    // same definition a row whose session was deleted is. Skipping the item alone would leave
    // it on disk and rendered as current forever.
    assert_eq!(store.sweep_zombies().expect("the sweep runs"), 1);
    assert_eq!(
        column(&store, "SELECT session_id FROM session_enrichments"),
        []
    );
}

#[test]
fn the_gate_and_the_sweep_read_one_population() {
    // Two names for the population would bill a row every night: the pass describes a session
    // and the next sweep deletes it, forever, and no coverage number would ever show it.
    let (_scratch, store) = open_copy();
    // If every session in the store is enriched, gated or not...
    for session_id in column(&store, "SELECT id FROM sessions")
        .into_iter()
        .flatten()
    {
        store
            .upsert(
                &SessionItem::bare(&session_id),
                &enrichment("Read two files."),
                &stamp("hash-1"),
            )
            .expect("the row writes");
    }
    store.sweep_zombies().expect("the sweep runs");
    // ...then what survives the sweep is precisely what the store hands out to describe.
    assert_eq!(
        column(&store, "SELECT session_id FROM session_enrichments")
            .into_iter()
            .flatten()
            .collect::<BTreeSet<_>>(),
        store
            .session_items(None)
            .expect("the sessions read")
            .into_iter()
            .map(|item| item.session_id)
            .collect::<BTreeSet<_>>()
    );
}

#[test]
fn the_run_and_session_views_left_join_too() {
    // If one of `spine/`'s two agent runs is enriched, and no session is...
    let (_scratch, store) = open_copy();
    let runs = spine_runs(&store);
    store
        .upsert(&runs[0], &enrichment("Read one file."), &stamp("hash-1"))
        .expect("the row writes");
    // ...then the runs view returns both, saying which carries no description...
    assert_eq!(
        column(
            &store,
            &format!(
                "SELECT description FROM enriched_agent_runs
                 WHERE session_id = '{SPINE}' ORDER BY id"
            ),
        ),
        [Some("Read one file.".to_owned()), None]
    );
    // ...it keeps the run's own recorded model under a name that says whose it is, and the
    // run's own brief needs none, so `description` means the enrichment's in all three
    // views...
    let columns: BTreeSet<String> = column(
        &store,
        "SELECT column_name FROM duckdb_columns() WHERE table_name = 'enriched_agent_runs'",
    )
    .into_iter()
    .flatten()
    .collect();
    for name in ["brief", "agent_model", "description", "enrichment_model"] {
        assert!(columns.contains(name), "the view carries `{name}`");
    }
    // ...and the sessions view reads coverage honestly for a corpus nothing has described.
    let coverage = store
        .store()
        .fetch(
            "SELECT count(*) AS rows, count(description) AS described FROM enriched_sessions",
            &[],
        )
        .expect("the coverage reads");
    assert_eq!(
        (
            coverage[0].i64("rows").expect("the count reads"),
            coverage[0].i64("described").expect("the count reads")
        ),
        (18, 0)
    );
}

#[test]
fn zombies_are_swept_at_all_three_levels() {
    // If a turn, a run and a session are each enriched...
    let (_scratch, store) = open_copy();
    let turn = spine_turns(&store).remove(0);
    let run = spine_runs(&store).remove(0);
    let session = store
        .session_items(None)
        .expect("the sessions read")
        .into_iter()
        .find(|item| item.session_id == SPINE)
        .expect("spine is describable");
    for item in [&turn as &dyn Item, &run as &dyn Item, &session as &dyn Item] {
        store
            .upsert(item, &enrichment("Read two files."), &stamp("hash-1"))
            .expect("the row writes");
    }
    // ...and every base row they hang off is then deleted, as an extractor bump that redraws
    // a session's boundaries would...
    for (sql, key) in [
        ("DELETE FROM turns WHERE id = ?", turn.turn_id.as_str()),
        (
            "DELETE FROM agent_runs WHERE id = ?",
            run.agent_run_id.as_str(),
        ),
        ("DELETE FROM sessions WHERE id = ?", SPINE),
    ] {
        store
            .connection()
            .execute(sql, params![key])
            .expect("the row is deleted");
    }
    // ...then all three enrichments go, because the LEFT-joined views would otherwise hide
    // them completely.
    assert_eq!(store.sweep_zombies().expect("the sweep runs"), 3);
    for level in Level::ALL {
        assert_eq!(
            column(&store, &format!("SELECT session_id FROM {}", level.table())),
            []
        );
    }
}

#[test]
fn opening_a_store_creates_every_enrichment_table() {
    // The enrichment schema is created on open, whatever the store held before.
    let (_scratch, store) = open_copy();
    let names: BTreeSet<String> = column(&store, "SELECT table_name FROM duckdb_tables()")
        .into_iter()
        .flatten()
        .collect();
    let views: BTreeSet<String> = column(&store, "SELECT view_name FROM duckdb_views()")
        .into_iter()
        .flatten()
        .collect();
    for level in Level::ALL {
        assert!(names.contains(level.table()), "the table is created");
    }
    assert!(views.contains("enriched_turns"));
}

#[test]
fn a_store_written_by_another_schema_is_refused() {
    // Enrichment refuses a store whose base tables this build cannot read.
    let (_scratch, path) = cache::writable_copy(&cache::corpus_store());
    let connection = duckdb::Connection::open(&path).expect("the copy opens");
    connection
        .execute("UPDATE meta SET schema_version = 1", [])
        .expect("the version is planted");
    drop(connection);
    // The refusal is the store's, which never opened the file for enrichment at all — this
    // build runs no migration, so a store of another vintage goes back to the Python `hp`.
    let refusal = EnrichmentStore::open(&path).expect_err("the open refuses");
    assert!(
        matches!(
            refusal,
            EnrichError::Store(StoreError::SchemaVersion { .. })
        ),
        "the refusal names the schema version"
    );
}

#[test]
fn a_path_with_no_store_behind_it_creates_nothing() {
    // If a `--db` names nothing — one character off the store an operator meant...
    let scratch = TempDir::new().expect("a tempdir");
    let path = scratch.path().join("tarces.duckdb");
    // ...then enrichment says so...
    let refusal = EnrichmentStore::open(&path).expect_err("the open refuses");
    assert!(matches!(
        refusal,
        EnrichError::Store(StoreError::NoStore(_))
    ));
    // ...and nothing was created at the typo: opening read-write would leave an empty DuckDB
    // behind, and the next run would read it as a store with nothing to enrich.
    assert!(!path.exists());
}

#[test]
fn stamp_is_the_four_field_staleness_key() {
    // The staleness key is exactly the design's four fields, so a fifth cannot creep in.
    // Rust has no reflection over a struct's fields, so the declaration is read as text — the
    // same shape the constant ratchets in `hyphae-view/tests/bounds.rs` use.
    let source = include_str!("../src/store.rs");
    let body = source
        .split_once("pub struct Stamp {")
        .expect("`Stamp` is declared in `store.rs`")
        .1
        .split_once('}')
        .expect("the declaration closes")
        .0;
    let fields: Vec<&str> = body
        .lines()
        .filter_map(|line| line.trim().strip_prefix("pub "))
        .filter_map(|line| line.split_once(':'))
        .map(|(name, _)| name)
        .collect();
    assert_eq!(
        fields,
        ["input_hash", "prompt_version", "taxonomy_version", "model"]
    );
}
