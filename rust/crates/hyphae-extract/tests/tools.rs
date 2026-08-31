//! Tool calls: pairing a `tool_use` block with the result that answered it.
//!
//! The port of `tests/extract/test_claude_code__tools.py`. The fixtures are the same redacted
//! mycelia sessions the rest of the extractor tests read; `tests/fixtures/*/README.md` names
//! each source session and its Claude Code version.

use std::collections::HashMap;

use hyphae_testsupport::corpus::{self, at};
use hyphae_testsupport::landmarks::{CONFIG_ONLY, MYCELIA, OFFLOAD_FILE, OFFLOAD_TOOL, PARALLEL};
use hyphae_testsupport::landmarks::{SERVER_TOOLS, SERVER_TOOLS_RUN, SPINE};

use hyphae_model::{MAIN_SOURCE, ToolCall};

/// The one call in `spine/` whose result record survived the trim, and the message that
/// issued it alongside two others.
const ANSWERED: &str = "toolu_01GzkcnijJv7xLcXGBsKivfz";
const BATCH: &str = "msg_011CdmMjFXDofyYSMxYtXa5n";
/// A message that issued a single call. The recorded session answered it; `spine/` ends at
/// the call, standing in for the sessions that really do end mid-call.
const LONE: &str = "toolu_01B6iTUMs3YrNvULzgRkwuar";
// `server_tools/`'s three `advisor` calls: one the service refused, one whose answer came
// back encrypted, and one issued alongside two local calls and never answered. See
// `tests/fixtures/server_tools/README.md`.
const REFUSED: &str = "srvtoolu_01KUMaS97sNkE7Z12UW4HMEp";
const ENCRYPTED: &str = "srvtoolu_01TK5pPoxEdDu3g975oMijMg";
const UNANSWERED: &str = "srvtoolu_01FHMDigqBGzPfr9CkXyA91v";
/// `parallel_tools/`'s two batches: one written as a single record, one spread over two.
const ONE_RECORD: &str = "msg_011Cd6RyHnMi8h4ZAceminTf";
const MANY_RECORDS: &str = "msg_011Cd6SbrBGHDLxr2oKBJZCf";

/// One fixture's tool calls, keyed by the `tool_use` id — the twin of the Python `calls`.
fn calls(directory: &str, stem: &str) -> HashMap<String, ToolCall> {
    corpus::trace(directory, stem)
        .tool_calls
        .into_iter()
        .map(|call| (call.id.clone(), call))
        .collect()
}

/// A `tool_use` block and the `tool_result` record answering it become one row.
#[test]
fn a_tool_call_carries_its_result() {
    // If a session issued a tool call and recorded its result...
    let call = calls("spine", SPINE)
        .remove(ANSWERED)
        .expect("spine's call");

    // ...then the two halves meet in one row, keyed by the tool_use id...
    assert_eq!(
        call,
        ToolCall {
            id: ANSWERED.to_owned(),
            session_id: SPINE.to_owned(),
            source: MAIN_SOURCE.to_owned(),
            // ...pointing back at the message that issued it...
            api_call_id: BATCH.to_owned(),
            index: 1,
            name: "Read".to_owned(),
            // ...run locally, as all but the `advisor` tool are...
            server_side: false,
            // ...carrying the arguments as recorded, JSON and all — this fixture keeps the
            // five input fields the viewer titles a call from, and redacts everything else
            // under `input` (`tests/fixtures/spine/README.md`)...
            input: format!(r#"{{"file_path": "{MYCELIA}/docs/handoffs.md"}}"#),
            // ...the flattened result text, which this fixture's redaction replaced...
            result: Some("[redacted]".to_owned()),
            offload_file: None,
            is_error: false,
            incomplete: false,
            // ...starting when its batch was issued and ending when the result landed.
            started_at: at("2026-08-06T10:44:33.136"),
            ended_at: Some(at("2026-08-06T10:44:33.589")),
            duration_synthetic: true,
            replayed: false,
        }
    );
}

/// Calls a message issued a record apart report a shared, synthetic start.
///
/// Claude Code usually writes each `tool_use` block as its own record, in the order it got
/// round to running them, so per-record timestamps rank a parallel batch by execution order
/// rather than by issue time. The flag is what stops an analysis ranking on that noise.
#[test]
fn parallel_calls_share_a_start_and_say_so() {
    let spine = calls("spine", SPINE);
    // If one assistant message issued three calls...
    let batch: Vec<&ToolCall> = spine
        .values()
        .filter(|call| call.api_call_id == BATCH)
        .collect();

    // ...then all three share one start and are flagged as measuring from it...
    assert_eq!(batch.len(), 3);
    assert!(
        batch
            .iter()
            .all(|call| call.started_at == at("2026-08-06T10:44:33.136")),
    );
    assert!(batch.iter().all(|call| call.duration_synthetic));
    // Index is per transcript and assigned in walk order, so the batch's own indices ascend
    // with no gap: nothing else was recorded between them.
    let mut indices: Vec<i32> = batch.iter().map(|call| call.index).collect();
    indices.sort_unstable();
    let first = indices[0];
    assert_eq!(
        indices,
        (first..first + indices.len() as i32).collect::<Vec<_>>()
    );

    // ...while a message that issued a single call reports its own, real start.
    let lone = &spine[LONE];
    assert!(!lone.duration_synthetic);
    assert_eq!(lone.started_at, at("2026-08-06T18:41:14.084"));
}

/// Several `tool_use` blocks in one record are one issue moment, not a queue.
///
/// The other shape a parallel batch takes: 23 records of the mycelia corpus hold two or more
/// `tool_use` blocks (`docs/schema.md`), and one record timestamps all of its calls at once —
/// so nothing was ranked by execution order and nothing is synthetic.
#[test]
fn calls_issued_in_one_record_keep_their_measured_start() {
    // If one record issued two calls...
    let parallel = calls("parallel_tools", PARALLEL);
    let mut together: Vec<&ToolCall> = parallel
        .values()
        .filter(|call| call.api_call_id == ONE_RECORD)
        .collect();
    together.sort_by_key(|call| call.index);

    // ...then both blocks became calls, the first of them whole...
    assert_eq!(
        together[0],
        &ToolCall {
            id: "toolu_01KZDHBh9UU4G5BkzFyTgSQR".to_owned(),
            session_id: PARALLEL.to_owned(),
            source: MAIN_SOURCE.to_owned(),
            api_call_id: ONE_RECORD.to_owned(),
            index: 0,
            name: "SendMessage".to_owned(),
            server_side: false,
            // ...arguments and answer as recorded. Redaction replaced every value an agent
            // wrote; `to` survives because it is an id, and the run it addresses is in the
            // fixture, so the viewer has something to resolve it against...
            input: concat!(
                r#"{"to": "a43bfe9fc86734ff1", "summary": "[redacted]", "message": "[redacted]", "#,
                r#""type": "[redacted]", "recipient": "[redacted]", "content": "[redacted]"}"#
            )
            .to_owned(),
            result: Some("[redacted]".to_owned()),
            offload_file: None,
            is_error: false,
            incomplete: false,
            // ...starting when the record was written, which is when both were issued...
            started_at: at("2026-07-16T21:17:43.798"),
            ended_at: Some(at("2026-07-16T21:17:43.851")),
            duration_synthetic: false,
            replayed: false,
        }
    );
    // ...and its sibling shares that start and that measured flag...
    assert_eq!(together.len(), 2);
    assert!(
        together
            .iter()
            .all(|call| call.started_at == at("2026-07-16T21:17:43.798"))
    );
    assert!(!together.iter().any(|call| call.duration_synthetic));

    // ...while the same session's other batch, spread over two records, is flagged.
    let spread: Vec<&ToolCall> = parallel
        .values()
        .filter(|call| call.api_call_id == MANY_RECORDS)
        .collect();
    assert_eq!(spread.len(), 2);
    assert!(
        spread
            .iter()
            .all(|call| call.started_at == at("2026-07-16T21:25:25.648"))
    );
    assert!(spread.iter().all(|call| call.duration_synthetic));
}

/// A session that ended before its tool returned still exports the call.
#[test]
fn a_call_with_no_result_is_incomplete() {
    // If the transcript holds a `tool_use` with no answering record — here because the
    // fixture stops at the call, which is what a session killed mid-call looks like...
    let call = calls("spine", SPINE)
        .remove(LONE)
        .expect("spine's lone call");

    // ...then the call is there, marked incomplete, with no result and no end.
    assert!(call.incomplete);
    assert_eq!(call.result, None);
    assert_eq!(call.ended_at, None);
    assert_eq!(call.name, "Read");
    assert_eq!(call.api_call_id, "msg_011Cdmz3NQtuzwN3cqYvvkuN");
}

/// A tool Anthropic runs server-side lands in `tool_calls`, flagged as server-side.
///
/// Claude Code records these as `server_tool_use`, answered by a result block inside the
/// same message rather than by a user record. Left unread, an analysis would report that a
/// session used no server tools at all.
#[test]
fn a_server_side_tool_call_is_a_call_like_any_other() {
    let served = calls("server_tools", SERVER_TOOLS);
    // If a session called the server-side `advisor` and the service refused...
    let call = &served[REFUSED];

    // ...then the call is a row like any other, marked as one we did not run ourselves...
    assert_eq!(
        call,
        &ToolCall {
            id: REFUSED.to_owned(),
            session_id: SERVER_TOOLS.to_owned(),
            source: MAIN_SOURCE.to_owned(),
            api_call_id: "msg_01QippSuXCLtCz1UguYEA8tN".to_owned(),
            index: 1,
            name: "advisor".to_owned(),
            server_side: true,
            // ...taking no arguments, as every recorded `advisor` call does...
            input: "{}".to_owned(),
            // ...reporting the refusal by its code, since the block carries no text...
            result: Some("unavailable".to_owned()),
            offload_file: None,
            is_error: true,
            incomplete: false,
            // ...and timed from its own record to the record that answered it.
            started_at: at("2026-07-06T18:19:03.233"),
            ended_at: Some(at("2026-07-06T18:19:12.541")),
            duration_synthetic: false,
            replayed: false,
        }
    );

    // And when the answer came back encrypted, the row says the call succeeded and carries
    // no result: the transcript holds nothing readable to carry.
    let encrypted = &served[ENCRYPTED];
    assert!(!encrypted.is_error);
    assert_eq!(encrypted.result, None);
    assert!(!encrypted.incomplete);
    assert_eq!(encrypted.ended_at, Some(at("2026-07-05T20:43:49.574")));
}

/// A server-side call in a message full of local calls is not timed as part of their batch.
///
/// Local calls in one message are written in execution order, so the batch shares a synthetic
/// start. The server-side call's own record is the request, so it keeps its real start and
/// says so.
#[test]
fn a_server_side_call_keeps_its_own_clock_beside_local_ones() {
    // If one message issued two local calls and a server-side call...
    let served = calls("server_tools", SERVER_TOOLS);
    let batch: Vec<&ToolCall> = served
        .values()
        .filter(|call| call.source == SERVER_TOOLS_RUN)
        .collect();
    let local: Vec<&&ToolCall> = batch.iter().filter(|call| !call.server_side).collect();

    // ...then the two local ones share the batch's synthetic start...
    assert_eq!(local.len(), 2);
    assert!(
        local
            .iter()
            .all(|call| call.started_at == at("2026-07-06T20:22:36.167"))
    );
    assert!(local.iter().all(|call| call.duration_synthetic));

    // ...while the server-side call reports its own, and is incomplete because this message's
    // `advisor` call was never answered — one of the 45 in the corpus is not.
    let server = batch
        .iter()
        .find(|call| call.server_side)
        .expect("the delegation issued a server-side call");
    assert_eq!(server.id, UNANSWERED);
    assert_eq!(server.started_at, at("2026-07-06T20:22:49.761"));
    assert!(!server.duration_synthetic);
    assert!(server.incomplete);
    assert_eq!(server.result, None);
    assert_eq!(server.ended_at, None);
}

/// When a tool's output is too big for the transcript, the call points at the file.
///
/// Claude Code writes the full output to `tool-results/` and leaves a preview in the record,
/// so `result` alone understates what the tool returned.
#[test]
fn an_offloaded_result_names_the_file_holding_it() {
    // If a call's output was moved out of the transcript...
    let trace = corpus::trace("offload", CONFIG_ONLY);
    let call = trace
        .tool_calls
        .iter()
        .find(|call| call.id == OFFLOAD_TOOL)
        .expect("the fixture records the offloaded call");

    // ...then the row keeps the preview and names the file, by name and not by the recording
    // machine's absolute path...
    assert_eq!(call.result.as_deref(), Some("[redacted]"));
    assert_eq!(call.offload_file.as_deref(), Some(OFFLOAD_FILE));
    // ...and the call is otherwise ordinary: complete, timed, and its own batch.
    assert_eq!(call.name, "Bash");
    assert!(!call.is_error);
    assert!(!call.incomplete);
    assert_eq!(call.started_at, at("2026-07-27T14:59:42.004"));
    assert_eq!(call.ended_at, Some(at("2026-07-27T14:59:45.116")));
    assert!(!call.duration_synthetic);

    // ...and the file itself is a row of its own, holding what the transcript did not.
    let offload = trace
        .offload_files
        .iter()
        .find(|file| file.name == OFFLOAD_FILE)
        .expect("the offloaded output is archived");
    assert!(!offload.content.is_empty());
    assert_eq!(offload.size_bytes, offload.content.len() as i64);
}
