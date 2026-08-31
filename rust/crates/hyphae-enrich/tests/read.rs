//! What the enrichment store hands out as an item, and what it refuses to.
//!
//! Ported from the reading half of `tests/enrich/test_store.py`. The base rows come from the
//! cached fixture corpus, so the natural keys under test are the ones the pipeline really
//! writes; [`common`] holds those keys, and `store.rs` holds the leaves that write a row.

use std::collections::{BTreeSet, HashMap};

use duckdb::params;
use hyphae_enrich::{Item, Level, TurnItem};

mod common;

use common::{
    MODEL_ONLY, MYCELIA, NEIGHBOUR_SESSION, RESUME_ANCESTOR, RESUME_PLAIN_TURN, SPINE, SPINE_LEAF,
    SPINE_MODEL_LINE, SPINE_MODEL_TURN, SPINE_RUN, WORKTREE_SESSION, open_copy, plant_record,
    spine_turns,
};

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
