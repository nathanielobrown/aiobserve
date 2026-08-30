//! The transcript walk over the recorded corpus: what each fixture's ids and parents are.
//!
//! Every leaf here reads a redacted recording under `tests/fixtures/`, except the one that
//! needs a schema violation — no recorded session carries one, so it appends an invented
//! line to a copy in a tempdir and says so.

mod common;

use hyphae_extract::sessions::SessionFiles;
use hyphae_extract::{ExtractError, SessionSource};
use hyphae_model::{MAIN_SOURCE, SessionTrace};

/// The four fixtures the Python generator dumps, in its order.
/// `tests/snapshot_from_python.py:FIXTURES` is the authority; these two lists disagreeing is
/// a snapshot mismatch, not a silent pass.
const SNAPSHOT_FIXTURES: &[(&str, &str)] = &[
    ("spine", "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b"),
    ("parallel_tools", "5f4b59fb-a9a8-4ca1-af62-a64b9d0ce515"),
    ("teammate", "10d0349d-0705-4e23-aa64-5b1b97698b2e"),
    ("compaction", "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9"),
];

/// The ids and parents of one trace, one entity per line, in extraction order.
///
/// The twin of `tests/snapshot_from_python.py:dump`. Both write the same lines from the same
/// fields, so a port that attaches a call to the wrong turn — or emits entities in another
/// order — shows up as a diff rather than as equal counts.
fn dump(trace: &SessionTrace) -> Vec<String> {
    let mut lines = vec![format!("session {}", trace.session.id)];
    for turn in &trace.turns {
        lines.push(format!(
            "  turn {} {} {} replayed={}",
            turn.index, turn.source, turn.id, turn.replayed
        ));
    }
    for call in &trace.api_calls {
        lines.push(format!(
            "  call {} {} {} turn={} replayed={}",
            call.index,
            call.source,
            call.id,
            optional(call.turn_id.as_deref()),
            call.replayed
        ));
    }
    for tool in &trace.tool_calls {
        lines.push(format!(
            "  tool {} {} {} call={} replayed={}",
            tool.index, tool.source, tool.id, tool.api_call_id, tool.replayed
        ));
    }
    for run in &trace.agent_runs {
        lines.push(format!(
            "  run {} parent={} tool={} depth={}",
            run.id,
            optional(run.parent_agent_id.as_deref()),
            optional(run.tool_use_id.as_deref()),
            run.spawn_depth
                .map_or_else(|| "None".to_owned(), |depth| depth.to_string())
        ));
    }
    for compaction in &trace.compactions {
        lines.push(format!(
            "  compaction {} {}",
            compaction.source, compaction.id
        ));
    }
    lines
}

/// A missing value printed the way Python prints `None`, since Python writes the other side.
fn optional(value: Option<&str>) -> &str {
    value.unwrap_or("None")
}

/// The parity leaf. The snapshot is not written by Rust: `snapshot_from_python.py` generated
/// it from `hyphae.extract` over these same four transcripts, so a mismatch means the two
/// extractors read one recording differently.
#[test]
fn ids_and_parents_match_the_python_extractor() {
    let dumped: Vec<String> = SNAPSHOT_FIXTURES
        .iter()
        .flat_map(|(directory, stem)| dump(&common::trace(directory, stem)))
        .collect();
    insta::assert_snapshot!("ids_and_parents", dumped.join("\n"));
}

/// Two tools issued in one message attach to that message and keep the transcript's order.
#[test]
fn parallel_tool_calls_attach_to_their_own_api_call() {
    let trace = common::trace("parallel_tools", "5f4b59fb-a9a8-4ca1-af62-a64b9d0ce515");
    // The fixture's point: one api call issuing more than one tool.
    let batched = trace
        .api_calls
        .iter()
        .find(|call| {
            trace
                .tool_calls
                .iter()
                .filter(|tool| tool.api_call_id == call.id)
                .count()
                > 1
        })
        .expect("parallel_tools records an api call with several tools");
    let owned: Vec<&str> = trace
        .tool_calls
        .iter()
        .filter(|tool| tool.api_call_id == batched.id)
        .map(|tool| tool.name.as_str())
        .collect();
    assert!(owned.len() > 1, "the batch holds {owned:?}");
    // Index is per transcript and assigned in walk order, so the batch's own indices ascend
    // with no gap: nothing else was recorded between them.
    let indices: Vec<i32> = trace
        .tool_calls
        .iter()
        .filter(|tool| tool.api_call_id == batched.id)
        .map(|tool| tool.index)
        .collect();
    let first = indices[0];
    assert_eq!(
        indices,
        (first..first + indices.len() as i32).collect::<Vec<_>>()
    );
}

/// A subagent's transcript is its own thread, keyed by the run id rather than by `main`.
#[test]
fn a_subagent_transcript_becomes_its_own_thread() {
    let trace = common::trace("spine", "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b");
    let run = trace
        .agent_runs
        .iter()
        .find(|run| run.tool_use_id.is_some())
        .expect("spine's runs were spawned by tool calls");
    // The run's own work carries its id as `source`, not the session's `main`.
    assert!(
        trace.turns.iter().any(|turn| turn.source == run.id),
        "the run's thread holds turns"
    );
    assert!(trace.api_calls.iter().any(|call| call.source == run.id));
    // And the tool call that asked for it is on the spawning thread, pointing back.
    let spawner = trace
        .tool_calls
        .iter()
        .find(|tool| Some(&tool.id) == run.tool_use_id.as_ref())
        .expect("the spawning tool call is in the same trace");
    assert_ne!(spawner.source, run.id);
}

/// A teammate has no spawning tool call and still gets its thread — the orphan case.
#[test]
fn a_teammate_thread_exists_without_a_spawning_tool_call() {
    let trace = common::trace("teammate", "10d0349d-0705-4e23-aa64-5b1b97698b2e");
    let orphan = trace
        .agent_runs
        .iter()
        .find(|run| run.tool_use_id.is_none())
        .expect("teammate records a run the team mechanism started");
    assert!(trace.turns.iter().any(|turn| turn.source == orphan.id));
}

/// A compaction is recorded against the thread whose context filled.
#[test]
fn a_compaction_is_recorded_on_its_own_thread() {
    let trace = common::trace("compaction", "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9");
    let compaction = trace
        .compactions
        .first()
        .expect("the fixture records a compaction");
    assert_eq!(compaction.source, MAIN_SOURCE);
    assert!(
        compaction.pre_tokens > 0,
        "the boundary reports the context it summarised"
    );
    // "auto" or "manual" — a third value would be a schema change worth noticing.
    assert!(matches!(compaction.trigger.as_str(), "auto" | "manual"));
}

/// A fork replays its origin's records. They stay as rows, flagged, rather than vanishing.
#[test]
fn replayed_rows_are_marked_rather_than_dropped() {
    let trace = common::trace("fork_origin", "5a88789c-1da7-4f32-b631-40a7e243334b");
    let fork = trace
        .agent_runs
        .iter()
        .find(|run| run.is_fork)
        .expect("fork_origin records a forked run");
    let replayed: Vec<&str> = trace
        .api_calls
        .iter()
        .filter(|call| call.source == fork.id && call.replayed)
        .map(|call| call.id.as_str())
        .collect();
    assert!(
        !replayed.is_empty(),
        "the fork's copied calls are still rows"
    );
    // The origin's own copy is not the replay: the first transcript to hold a record keeps it.
    for id in replayed {
        assert!(
            trace
                .api_calls
                .iter()
                .any(|call| call.id == id && !call.replayed),
            "{id} is replayed on the fork and original somewhere else"
        );
    }
}

/// A by-reference fork points at the conversation it continues instead of copying it.
#[test]
fn a_by_reference_fork_names_the_record_it_picked_up_from() {
    let trace = common::trace("fork_byref", "07a769d7-828c-4edb-b3ce-af51e2712aa3");
    let fork = trace
        .agent_runs
        .iter()
        .find(|run| run.fork_context_uuid.is_some())
        .expect("fork_byref records a by-reference fork");
    assert!(fork.is_fork);
}

/// An unregistered record type stops the run, naming the type and the line.
///
/// The one invented input in this file, and unavoidably so: no recorded session carries a
/// schema violation. Only the appended line is invented — the transcript under it is the
/// recording.
#[test]
fn an_unknown_record_type_stops_the_run() {
    let directory = tempfile::tempdir().expect("a tempdir");
    let stem = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b";
    let copied = directory.path().join(format!("{stem}.jsonl"));
    let mut text = std::fs::read_to_string(
        common::fixtures()
            .join("spine")
            .join(format!("{stem}.jsonl")),
    )
    .expect("the fixture is readable");
    let appended_at = text.lines().count() + 1;
    text.push_str(
        r#"{"type": "telepathy", "uuid": "00000000-0000-0000-0000-000000000000", "timestamp": "2026-01-01T00:00:00.000Z"}"#,
    );
    text.push('\n');
    std::fs::write(&copied, text).expect("the copy is writable");

    let error = common::extractor()
        .extract(&common::from_transcript(&copied))
        .expect_err("an unregistered type is refused");
    let ExtractError::Schema(message) = &error else {
        panic!("expected a schema error, got {error:?}");
    };
    assert!(
        message.contains("telepathy"),
        "the message names the type: {message}"
    );
    // The line number, so a reader can open the file at the offending record.
    assert!(
        message.contains(&format!("line {appended_at}")),
        "the message names the line: {message}"
    );
}

/// Tool output Claude Code wrote to a file becomes an offload row the tool call points at.
#[test]
fn an_offloaded_tool_result_becomes_its_own_row() {
    let trace = common::trace("offload", "7e37bb35-4dcb-4e16-85be-55ac510c168e");
    let offload = trace
        .offload_files
        .first()
        .expect("the fixture records an offload file");
    let owner = trace
        .tool_calls
        .iter()
        .find(|tool| tool.offload_file.as_deref() == Some(offload.name.as_str()))
        .expect("a tool call names the offload file");
    assert!(!offload.content.is_empty());
    assert_eq!(offload.size_bytes, offload.content.len() as i64);
    // The transcript keeps a short preview; the file holds the whole thing.
    assert!(owner.result.is_some());
}

/// Two records sharing a uuid stay two rows: the archive keeps both, the walk keeps both.
#[test]
fn a_duplicated_record_uuid_does_not_collapse_two_rows() {
    let trace = common::trace("dup_uuid", "8ee00a94-b01a-4394-b447-b065f74b11af");
    // Every line of the file is archived, duplicates included.
    let uuids: Vec<&str> = trace
        .raw_records
        .iter()
        .filter_map(|row| row.uuid.as_deref())
        .collect();
    let mut unique = uuids.clone();
    unique.sort_unstable();
    unique.dedup();
    assert!(
        unique.len() < uuids.len(),
        "the fixture records a duplicated uuid"
    );
}

/// Discovery under a projects root, the path `hp extract` takes.
#[test]
fn discovery_finds_a_projects_sessions_with_fingerprints() {
    // The fixture directories are not encoded project paths, so this points the extractor at
    // one directly: what is under test is `files()` and the digest, not the path encoding.
    let transcript = common::fixtures()
        .join("spine")
        .join("4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b.jsonl");
    let session = SessionFiles {
        id: "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b".to_owned(),
        transcript: transcript.clone(),
    };
    let files = session.files().expect("the session's files are readable");
    // The transcript first, then everything under its sibling directory.
    assert_eq!(files[0], transcript);
    assert!(
        files.len() > 1,
        "spine records subagent transcripts beside it"
    );
    let source: SessionSource = common::from_transcript(&transcript);
    assert_eq!(source.files, files);
}
