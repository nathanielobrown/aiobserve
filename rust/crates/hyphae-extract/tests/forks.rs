//! Forks: agents that continue a conversation someone else started.
//!
//! The port of `tests/extract/test_claude_code__forks.py`. Claude Code writes a fork two
//! ways. A **copied-history** fork replays its parent's records into its own file, verbatim
//! uuids and all, then carries on; a **by-reference** fork copies nothing and opens
//! mid-conversation, naming the context it inherited. Both wreck a count that assumes a uuid
//! belongs to one transcript, so the extractor decides who owns a record and flags every
//! later copy as `replayed`.

use std::collections::{HashMap, HashSet};

use hyphae_testsupport::corpus::{self, at};
use hyphae_testsupport::landmarks::{
    BYREF_FORK, DENSE_CALL as COPIED_MESSAGE, DENSE_CALL_TURN as REPEATED_RECORD, FORK_ORIGIN,
    FORK_ORIGIN_RUN as AUDITOR, FORK_RUN, NO_PROJECT_SESSION as BYREF,
};

use hyphae_extract::ExtractError;
use hyphae_model::MAIN_SOURCE;

/// A fork's replay of its parent's work is marked as a replay, and the parent keeps it.
///
/// Flagging both sides instead would zero-count work that really happened — the copied
/// message here is the auditor's, and it must stay countable somewhere.
#[test]
fn a_copied_record_belongs_to_the_transcript_that_ran_it() {
    // If a fork replayed the transcript it was spawned from and then carried on...
    let extracted = corpus::trace("fork_origin", FORK_ORIGIN);
    let calls: HashMap<(&str, &str), bool> = extracted
        .api_calls
        .iter()
        .filter(|call| call.source == AUDITOR || call.source == FORK_RUN)
        .map(|call| ((call.source.as_str(), call.id.as_str()), call.replayed))
        .collect();

    // ...then the copied message counts under the transcript that ran it first...
    assert_eq!(
        calls,
        HashMap::from([
            ((AUDITOR, COPIED_MESSAGE), false),
            // ...and as a replay under the fork that inherited it...
            ((FORK_RUN, COPIED_MESSAGE), true),
            // ...while everything the fork went on to do is its own.
            ((FORK_RUN, "msg_011CdFxjoNbXw31ASkCpKqdz"), false),
            ((FORK_RUN, "msg_011CdFxq21kNYhF6hTn6oE95"), false),
        ])
    );
    // The same holds for the turn both files open on, and for the tools the copy repeats.
    let turns: HashSet<(&str, bool)> = extracted
        .turns
        .iter()
        .filter(|turn| turn.source != MAIN_SOURCE)
        .map(|turn| (turn.source.as_str(), turn.replayed))
        .collect();
    assert_eq!(turns, HashSet::from([(AUDITOR, false), (FORK_RUN, true)]));
    let replayed: HashSet<&str> = extracted
        .tool_calls
        .iter()
        .filter(|tool| tool.source == FORK_RUN && tool.replayed)
        .map(|tool| tool.id.as_str())
        .collect();
    let original: HashSet<&str> = extracted
        .tool_calls
        .iter()
        .filter(|tool| tool.source == AUDITOR)
        .map(|tool| tool.id.as_str())
        .collect();
    assert_eq!(replayed, original);
    // No row is flagged on both sides, which is what would make the work vanish.
    assert!(
        !extracted
            .api_calls
            .iter()
            .any(|call| call.source == AUDITOR && call.replayed)
    );
}

/// A fork's run begins at its first fresh record, not at the copied history's start.
#[test]
fn a_forks_run_starts_when_its_own_work_does() {
    // If a fork's file opens with 18 seconds of someone else's conversation...
    let extracted = corpus::trace("fork_origin", FORK_ORIGIN);
    let runs: HashMap<&str, _> = extracted
        .agent_runs
        .iter()
        .map(|run| (run.id.as_str(), run))
        .collect();

    // ...then its run starts where the copying stops...
    assert_eq!(
        runs[FORK_RUN].started_at,
        Some(at("2026-07-21T22:05:03.221"))
    );
    assert_eq!(runs[FORK_RUN].ended_at, Some(at("2026-07-21T22:08:02.177")));
    // ...which is after the transcript it copied from even began...
    assert_eq!(
        runs[AUDITOR].started_at,
        Some(at("2026-07-21T22:04:45.578"))
    );
    // ...and the meta says which of the two is the fork. Nothing was inherited by
    // reference, so there is no context to point at.
    assert!(runs[FORK_RUN].is_fork);
    assert_eq!(runs[FORK_RUN].fork_context_uuid, None);
    assert!(!runs[AUDITOR].is_fork);
    assert_eq!(runs[AUDITOR].fork_context_uuid, None);
}

/// A fork that inherits context without copying it names the record it continues from.
///
/// Its transcript starts with an answer to a prompt that lives in another file, so the work
/// before its first local prompt belongs to no turn of its own — reading a turn in would
/// attribute the whole fork to a prompt it never received.
#[test]
fn a_by_reference_fork_opens_mid_conversation() {
    // If a fork opened by reference...
    let extracted = corpus::trace("fork_byref", BYREF);
    let [run] = extracted.agent_runs.as_slice() else {
        panic!("fork_byref records one run");
    };

    // ...then it names the conversation and the record it picked up from...
    assert!(run.is_fork);
    assert_eq!(
        run.fork_context_uuid.as_deref(),
        Some("97e2004c-f9f6-48ac-add8-0eef6026d3f9")
    );
    // ...nothing is a replay, because nothing was copied...
    assert!(!extracted.api_calls.iter().any(|call| call.replayed));
    // ...and its calls hang off no turn, the transcript holding no prompt to open one.
    assert_eq!(
        extracted
            .api_calls
            .iter()
            .filter(|call| call.source == BYREF_FORK)
            .map(|call| call.turn_id.clone())
            .collect::<Vec<Option<String>>>(),
        [None, None]
    );
    assert!(!extracted.turns.iter().any(|turn| turn.source == BYREF_FORK));
}

/// A transcript repeating another's records without being a fork stops the run.
///
/// Copying is a fork's doing; anywhere else it means the ordering rule put the wrong
/// transcript first, and every count downstream would be attributed to the wrong agent. The
/// `isFork` flag is dropped from a recorded fork's meta here — a planted change, since all 51
/// overlapping pairs on this machine have a fork on one side (scanned 2026-08-07).
#[test]
fn a_transcript_that_replays_another_must_be_a_fork() {
    // If the transcript that replays another does not admit to being a fork...
    let recorded = corpus::fixtures()
        .join("fork_origin")
        .join(FORK_ORIGIN)
        .join("subagents");
    let read = |name: String| std::fs::read(recorded.join(name)).expect("the fixture is readable");
    let mut meta: serde_json::Value =
        serde_json::from_slice(&read(format!("agent-{FORK_RUN}.meta.json")))
            .expect("the fork's meta is JSON");
    let object = meta.as_object_mut().expect("a meta is an object");
    object.remove("isFork");
    object.insert("agentType".to_owned(), "auditor".into());
    let disowned = serde_json::to_vec(&meta).expect("the edited meta serializes");
    let (auditor, auditor_meta) = (
        read(format!("agent-{AUDITOR}.jsonl")),
        read(format!("agent-{AUDITOR}.meta.json")),
    );
    let fork = read(format!("agent-{FORK_RUN}.jsonl"));
    let planted = corpus::planted(
        "fork_origin",
        FORK_ORIGIN,
        &[
            (&format!("subagents/agent-{AUDITOR}.jsonl"), &auditor),
            (
                &format!("subagents/agent-{AUDITOR}.meta.json"),
                &auditor_meta,
            ),
            (&format!("subagents/agent-{FORK_RUN}.jsonl"), &fork),
            (&format!("subagents/agent-{FORK_RUN}.meta.json"), &disowned),
        ],
    );

    // ...then extraction stops, naming the transcript and one of the records it repeated.
    let error = corpus::extractor()
        .extract(&planted.source)
        .expect_err("an unadmitted replay is refused");
    let ExtractError::Schema(message) = &error else {
        panic!("expected a schema error, got {error:?}");
    };
    assert!(message.contains(FORK_RUN), "{message}");
    assert!(message.contains(REPEATED_RECORD), "{message}");
}
