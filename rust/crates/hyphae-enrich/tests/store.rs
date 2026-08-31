//! The enrichment tables: what they hold, what makes a row stale, and what sweeps it away.
//!
//! Ported from `tests/enrich/test_store.py`. The base rows come from the cached fixture
//! corpus, so the natural keys under test are the ones the pipeline really writes.
//!
//! Every leaf takes a writable copy: enrichment opens a store for writing, and a write lock
//! on the shared cache would refuse every read-only open running beside it
//! (`hyphae_testsupport::cache`). The counts differ from the Python file's — that suite picks
//! 11 fixture directories and this corpus is the whole clean 18 — so each one below was
//! recounted against this store rather than lifted.

use std::collections::{BTreeSet, HashMap};

use duckdb::params;
use hyphae_enrich::{
    AgentRunItem, EnrichError, Enrichment, EnrichmentStore, Item, Level, SessionItem, Stamp,
    TurnItem,
};
use hyphae_store::StoreError;
use hyphae_testsupport::{cache, corpus, metadata};
use tempfile::TempDir;

// The recorded sessions this file names, and the runs inside them. `tests/fixtures/*/README.md`
// names the session behind each fixture directory.
const SPINE: &str = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b";
const SPINE_RUN: &str = "ac461ef46b4bb8e32";
const SPINE_LEAF: &str = "af6473ae437c9608d";
/// `spine/`'s `/model` turn: the one whose stdout record the archive read exercises.
const SPINE_MODEL_TURN: &str = "5b848af7-f86e-4950-b474-cd98125fad24";
/// The line that record sits on, so a planted one can be ordered against it.
const SPINE_MODEL_LINE: i64 = 8;
/// `model_only/`: three turns the CLI answered by itself and no api call under any of them.
const MODEL_ONLY: &str = "bec99999-cbb7-4d11-9a58-3ad3d0e1c8cf";
/// `resume_pair/`'s ancestor and the plain turn its stdout record hangs off.
const RESUME_ANCESTOR: &str = "2352492b-1437-4427-ad51-70f35c75f663";
const RESUME_PLAIN_TURN: &str = "55309e59-0fae-4ef1-9251-877e27487bda";
/// The resume itself: every api call it holds sits under a turn its ancestor ran.
const RESUME: &str = "0a76f771-5f5b-447e-852a-664fc972ea7c";
/// The session that records no main turn and no agent run.
const DUP_UUID: &str = "8ee00a94-b01a-4394-b447-b065f74b11af";
/// The invented fixture with neither: the third session with nothing to describe, and the
/// one the Python suite's corpus leaves out.
const INVENTED_EMPTY: &str = "invented-no-cache-creation";
/// A fork whose only work is a subagent's — no turn of its own, and every call under the run.
const FORK_BYREF: &str = "07a769d7-828c-4edb-b3ce-af51e2712aa3";
/// The turn whose three recorded calls stopped `end_turn`, `tool_use` and nothing.
const STOP_REASON_TURN: &str = "9ae45aaa-d992-4089-a78d-f65d2f237080";
/// The two sessions the project filter re-homes, and the project every fixture was recorded
/// under.
const WORKTREE_SESSION: &str = "0b34d1b8-ebd3-40a6-bd89-f1881e1de2ba";
const NEIGHBOUR_SESSION: &str = "10d0349d-0705-4e23-aa64-5b1b97698b2e";
const MYCELIA: &str = "/Users/nob/repos/mycelia";

/// The sessions this corpus hands out to describe: 18 recorded, three with nothing in them.
const DESCRIBABLE: usize = 13;

/// A private copy of the cached corpus, open for enrichment.
///
/// The `TempDir` comes back with the store: dropping it deletes the file the store is on.
fn open_copy() -> (TempDir, EnrichmentStore) {
    let (scratch, path) = cache::writable_copy(&cache::corpus_store());
    let store = EnrichmentStore::open(&path).expect("the copy opens for enrichment");
    (scratch, store)
}

/// What a planted row says. Invented, as any model answer in a test must be.
fn enrichment(description: &str) -> Enrichment {
    let vocabulary = metadata::enrichment();
    Enrichment {
        description: description.to_owned(),
        // From the bridged vocabulary rather than a third hand copy of the taxonomy.
        category: vocabulary.categories[0].clone(),
        outcome: vocabulary.outcomes[0].clone(),
        friction: None,
    }
}

/// What a planted row was written under, at a version nothing reads as drift.
fn stamp(input_hash: &str) -> Stamp {
    Stamp {
        input_hash: input_hash.to_owned(),
        prompt_version: 1,
        taxonomy_version: metadata::enrichment().taxonomy_version,
        model: "claude-haiku-4-5-20251001".to_owned(),
    }
}

fn spine_turns(store: &EnrichmentStore) -> Vec<TurnItem> {
    store
        .turn_items(None)
        .expect("the turns read")
        .into_iter()
        .filter(|item| item.session_id == SPINE)
        .collect()
}

fn spine_runs(store: &EnrichmentStore) -> Vec<AgentRunItem> {
    store
        .run_items(None)
        .expect("the runs read")
        .into_iter()
        .filter(|item| item.session_id == SPINE)
        .collect()
}

/// Add one raw transcript record to a session's archive, at a line of its own.
fn plant_record(store: &EnrichmentStore, session_id: &str, line_no: i64, record: &str) {
    store
        .connection()
        .execute(
            "INSERT INTO raw_records (session_id, source, line_no, uuid, timestamp, type, raw)
             VALUES (?, 'main', ?, ?, now(), 'user', ?)",
            params![session_id, line_no, format!("planted-{line_no}"), record],
        )
        .expect("the record plants");
}

/// One column of a table, as strings, in the order the query names.
fn column(store: &EnrichmentStore, sql: &str) -> Vec<Option<String>> {
    store
        .store()
        .fetch(sql, &[])
        .expect("the query runs")
        .iter()
        .map(|row| {
            row.opt_str(row.columns()[0].as_str())
                .expect("the column reads")
                .map(str::to_owned)
        })
        .collect()
}

#[test]
fn the_enrichable_turns_are_the_sessions_own_main_turns() {
    let (_scratch, store) = open_copy();
    let items = spine_turns(&store);
    // If `spine/` recorded four main turns, two of them slash commands...
    assert_eq!(
        items
            .iter()
            .map(|item| &item.turn_id[..8])
            .collect::<Vec<_>>(),
        ["5b848af7", "30aad8e5", "818588ad", "8cdceb31"]
    );
    assert_eq!(
        items
            .iter()
            .map(|item| item.command_name.as_deref())
            .collect::<Vec<_>>(),
        [Some("/model"), Some("/night-run"), None, None]
    );
    // ...then what the CLI printed for each comes with it, read out of the archive: the
    // `/model` turn's stdout record names it as its `parentUuid`, and nothing archived an
    // answer for the other three, which is None rather than an empty string.
    assert_eq!(
        items
            .iter()
            .map(|item| item.command_result.as_deref())
            .collect::<Vec<_>>(),
        [Some("[redacted]"), None, None, None]
    );
    // ...then each turn carries the api calls it drove, and each call its tool calls —
    // turn 818588ad drove four calls, one of which asked for two tools at once.
    let third = &items[2];
    assert_eq!(
        third
            .api_calls
            .iter()
            .map(|call| call.tool_calls.len())
            .collect::<Vec<_>>(),
        [1, 2, 1, 1]
    );
    assert_eq!(
        third
            .api_calls
            .iter()
            .map(|call| call.tool_calls[0].name.as_str())
            .collect::<Vec<_>>(),
        ["Agent", "Bash", "PushNotification", "Read"]
    );
    // ...and the item names itself with its own primary key, which is what a request and a
    // failure record carry.
    assert_eq!(third.level(), Level::Turn);
    assert_eq!(third.key(), format!("turn|{SPINE}|main|{}", third.turn_id));
}

#[test]
fn the_second_carrier_and_the_empty_body_both_arrive() {
    // The two states nothing else tells apart: `None` is a turn no record answered, and `""`
    // is a record that answered with nothing. Collapsing them puts the model back to
    // inferring.
    let (_scratch, store) = open_copy();
    let items: HashMap<String, TurnItem> = store
        .turn_items(None)
        .expect("the turns read")
        .into_iter()
        .filter(|item| item.session_id == MODEL_ONLY)
        .map(|item| (item.command_name.clone().unwrap_or_default(), item))
        .collect();
    // If `model_only/`'s `/reload-skills` turn was answered by a `system`/`local_command`
    // record, which carries its output at `$.content` rather than at `$.message.content` —
    // 37 recorded instances hang on that second read...
    assert_eq!(
        items["/reload-skills"].command_result.as_deref(),
        Some("[redacted]")
    );
    // ...and its `/clear` turn was answered by a record that printed nothing at all — every
    // one of the 21 recorded `/clear` bodies is empty — then the empty body arrives as the
    // empty string, which is not the same value as no record.
    assert_eq!(items["/clear"].command_result.as_deref(), Some(""));
}

#[test]
fn output_archived_against_a_plain_turn_belongs_to_no_turn() {
    // 183 recorded records are in this shape — a resume replays its ancestor's stdout records
    // against plain turns — so the read has to drop them, and the shape guard has to let them
    // go without a word.
    let (_scratch, store) = open_copy();
    let items: Vec<TurnItem> = store
        .turn_items(None)
        .expect("the turns read")
        .into_iter()
        .filter(|item| item.session_id == RESUME_ANCESTOR)
        .collect();
    // If the ancestor's one main turn ran no command, then the stdout record naming it as
    // `parentUuid` is not its prompt's to carry.
    assert_eq!(
        items
            .iter()
            .map(|item| (item.turn_id.as_str(), item.command_name.as_deref()))
            .collect::<Vec<_>>(),
        [(RESUME_PLAIN_TURN, None)]
    );
    assert_eq!(items[0].command_result, None);
}

#[test]
fn output_archived_over_several_records_reads_in_line_order() {
    // Five recorded turns hold two stdout records; the bodies here are invented and planted,
    // because every redacted fixture body is the same ten characters and could not show an
    // order at all.
    let (_scratch, store) = open_copy();
    // If two more records are archived against `spine/`'s `/model` turn, whose own recorded
    // answer sits at line 8 — inserted later line first, so a read that trusted the row order
    // DuckDB returns would put them back to front...
    for (line_no, body) in [(900, "second"), (700, "first")] {
        plant_record(
            &store,
            SPINE,
            line_no,
            &format!(
                r#"{{"parentUuid": "{SPINE_MODEL_TURN}", "type": "system",
                    "content": "<local-command-stdout>{body}</local-command-stdout>"}}"#
            ),
        );
    }
    let item = spine_turns(&store).remove(0);
    // ...then the turn carries all three, in the order the transcript wrote them.
    assert_eq!(
        item.command_result.as_deref(),
        Some("[redacted]\nfirst\nsecond")
    );
}

// A record the archive filter catches whose body no carrier holds. Both are invented, and
// have to be: a shape we have seen is a shape the reader handles, so the only way to exercise
// the guard is to write down one we have not. `spine/`'s `/model` turn is the parent, so each
// row reaches the classification rather than being dropped for hanging off nothing.

/// A record archiving a command's output in an unknown shape stops the pass, naming it.
///
/// Claude Code owns these shapes and changes them without notice. Neither silent state is
/// tolerable: a dropped record loses the one fact the prompt gained, and a body that reads as
/// empty tells the model the command printed nothing, which is the absence the fix removes.
fn assert_unreadable(record: &str) {
    let (_scratch, store) = open_copy();
    plant_record(&store, SPINE, 900, record);
    let refusal = store
        .turn_items(None)
        .expect_err("the read refuses")
        .to_string();
    // The error names where to look: the session, and the line of the transcript.
    assert!(
        refusal.contains(SPINE) && refusal.contains("line 900"),
        "the refusal names the session and the line"
    );
}

#[test]
fn a_command_output_with_no_carrier_field_crashes() {
    // The tag is in the record but in neither field a carrier has ever used: the `coalesce`
    // yields NULL, which the aggregation would have skipped without a word.
    assert_unreadable(&format!(
        r#"{{"parentUuid": "{SPINE_MODEL_TURN}", "type": "user",
            "toolUseResult": "<local-command-stdout>printed</local-command-stdout>"}}"#
    ));
}

#[test]
fn a_command_output_whose_carrier_holds_no_tag_crashes() {
    // A carrier that holds no tag: the extract yields '', which is the empty-body state — an
    // unread record would render as "the command printed nothing".
    assert_unreadable(&format!(
        r#"{{"parentUuid": "{SPINE_MODEL_TURN}", "type": "system",
            "content": "printed, in a shape with no tag around it",
            "toolUseResult": "<local-command-stdout>printed</local-command-stdout>"}}"#
    ));
}

#[test]
fn a_multi_line_command_output_survives_whole() {
    // The body is planted into a recorded record and invented, and it has to be: redaction
    // flattens every string to `[redacted]`, so no fixture body can hold a newline. A reader
    // that stopped at the first line would extract nothing at all and report an empty body.
    let (_scratch, store) = open_copy();
    store
        .connection()
        .execute(
            "UPDATE raw_records SET raw = ? WHERE session_id = ? AND line_no = ?",
            params![
                format!(
                    r#"{{"parentUuid": "{SPINE_MODEL_TURN}", "type": "user",
                        "message": {{"role": "user",
                        "content": "<local-command-stdout>first line\nsecond line</local-command-stdout>"}}}}"#
                ),
                SPINE,
                SPINE_MODEL_LINE
            ],
        )
        .expect("the record is rewritten");
    assert_eq!(
        spine_turns(&store).remove(0).command_result.as_deref(),
        Some("first line\nsecond line")
    );
}

#[test]
fn a_project_filter_narrows_the_items() {
    // The same corpus `hp query --project` and `export-otlp` take, which is what makes a
    // description written under one command citable by the other.
    let (_scratch, store) = open_copy();
    // If a project nothing was recorded under is asked for, it has no items, while the store
    // as a whole has plenty...
    assert_eq!(
        store
            .turn_items(Some("/no/such/repo"))
            .expect("the read runs"),
        []
    );
    assert!(!store.turn_items(None).expect("the read runs").is_empty());
    // ...and since no recorded fixture ran in a worktree, one session's `project_dir` is
    // planted under `<project>/.claude/worktrees/` and another's under a checkout that merely
    // shares the prefix — the two values invented, the sessions under them recorded...
    for (session_id, project_dir) in [
        (
            WORKTREE_SESSION,
            format!("{MYCELIA}/.claude/worktrees/planted"),
        ),
        (NEIGHBOUR_SESSION, format!("{MYCELIA}-old")),
    ] {
        store
            .connection()
            .execute(
                "UPDATE sessions SET project_dir = ? WHERE id = ?",
                params![project_dir, session_id],
            )
            .expect("the project is planted");
    }
    let scoped = store
        .turn_items(Some(MYCELIA))
        .expect("the scoped read runs");
    let whole = store.turn_items(None).expect("the whole read runs");
    let sessions: BTreeSet<&str> = scoped.iter().map(|item| item.session_id.as_str()).collect();
    // ...then the worktree's session is the project's, because a worktree checkout is where
    // the project's own work happens...
    assert!(sessions.contains(WORKTREE_SESSION));
    // ...and the neighbouring checkout's is not: matching the prefix without the `/` would
    // annex every repository whose path begins with this one's.
    assert!(!sessions.contains(NEIGHBOUR_SESSION));
    // ...while the filter only drops items: each one it keeps is the item the unscoped read
    // built, whole. A description is written from the item, so a scoped read that quietly
    // narrowed a field as well as the session set would describe a turn nobody ran.
    assert_eq!(
        scoped,
        whole
            .into_iter()
            .filter(|item| sessions.contains(item.session_id.as_str()))
            .collect::<Vec<_>>()
    );
    // ...which includes the archived command output, read by a query of its own.
    assert!(scoped.iter().any(|item| item.command_result.is_some()));
}

#[test]
fn a_run_naming_no_parent_agent_hangs_off_the_transcript_that_spawned_it() {
    // 112 of 2,459 recorded runs are in this shape. Reading `parent_agent_id` alone calls
    // every one of them a root and sends it before the parent whose prompt embeds its
    // description.
    let (_scratch, store) = open_copy();
    let parents = store.item_parents(None).expect("the parents read");
    // If `spine/`'s leaf run — which names a parent agent *and* was spawned by a call inside
    // that agent's transcript — loses the named parent (planted, and labeled invented: every
    // fixture run naming no parent agent was spawned from the main transcript or from nothing
    // at all, so no fixture carries the recorded shape)...
    store
        .connection()
        .execute(
            "UPDATE agent_runs SET parent_agent_id = NULL WHERE id = ?",
            params![SPINE_LEAF],
        )
        .expect("the parent is cleared");
    // ...then nothing about the forest moves: the transcript holding the spawning call names
    // the parent the deleted column named...
    assert_eq!(store.item_parents(None).expect("the parents read"), parents);
    // ...which is the run that spawned it, not the session and not a turn.
    assert_eq!(
        parents[&format!("agent_run|{SPINE}|{SPINE_LEAF}")],
        format!("agent_run|{SPINE}|{SPINE_RUN}")
    );
}

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
