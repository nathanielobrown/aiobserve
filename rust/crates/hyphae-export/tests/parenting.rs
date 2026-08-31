//! Where each row hangs: a model call's parent span, and a subagent run's.
//!
//! Half the port of `tests/export/test_otlp__shaping.py`; what a span carries is `shaping.rs`.
//! Every planted value is labeled where it sits, because a shape the corpus never recorded is
//! a hypothesis, not evidence.

use std::collections::BTreeMap;

use chrono::TimeDelta;
use hyphae_export::otlp::{METADATA_ONLY, ShapeError, session_spans};
use hyphae_testsupport::corpus;
use hyphae_testsupport::landmarks::{
    BYREF_FORK, FORK_ORIGIN, FORK_ORIGIN_RUN, FORK_RUN, MAIN, NO_PROJECT_SESSION, RESUME, SPINE,
    SPINE_LEAF, SPINE_RUN, TEAMMATE, TEAMMATE_RUN,
};
use hyphae_testsupport::otlp::{Value, attributes, digest, emitted, nanos, one};
use opentelemetry_proto::tonic::trace::v1::Span;

/// `spine/`'s `Agent` call that spawned `SPINE_RUN`, and the model call that issued it.
const SPINE_SPAWN: &str = "toolu_015dP3eMe5GZn7BzFipupZwS";
const SPINE_SPAWN_CALL: &str = "msg_011CdmToQdxciYnDo9M2d7HN";

/// Every span id between one span and the root, the root last.
fn climb(spans: &[Span], span: &[u8]) -> Vec<Vec<u8>> {
    let parents: BTreeMap<&[u8], &[u8]> = spans
        .iter()
        .map(|candidate| {
            (
                candidate.span_id.as_slice(),
                candidate.parent_span_id.as_slice(),
            )
        })
        .collect();
    let mut walked = Vec::new();
    let mut span = span;
    while !span.is_empty() {
        walked.push(span.to_vec());
        span = parents[span];
    }
    walked
}

/// A fork's own model calls hang off the fork's run when the turn above them is a copy.
#[test]
fn a_live_call_under_a_replayed_turn_hangs_off_its_run() {
    // If the fork fixture is shaped — two live model calls sitting under a turn the fork
    // replayed from the run it continues, so that turn emits no span at all...
    let trace = corpus::trace("fork_origin", FORK_ORIGIN);
    let spans = emitted(&trace);
    let live: Vec<&hyphae_model::ApiCall> = trace
        .api_calls
        .iter()
        .filter(|call| call.source == FORK_RUN && !call.replayed)
        .collect();
    assert_eq!(
        live.len(),
        2,
        "the fixture stopped carrying the live calls this leaf reads"
    );
    // ...then each hangs off its own run's span, which the call can name from the `source` it
    // already carries — no join, and no dangling parent where the turn would have been.
    for call in live {
        let span = one(
            &spans,
            &digest(FORK_ORIGIN, "api_call", &call.source, &call.id),
        );
        assert_eq!(
            span.parent_span_id,
            digest(FORK_ORIGIN, "agent_run", "", FORK_RUN)
        );
    }
}

/// A model call with no turn behind it hangs off its run inside a subagent, off the root on
/// the main thread.
#[test]
fn a_call_that_opens_mid_conversation_hangs_off_its_source() {
    // If a resume — whose whole first stretch was copied in before the user typed anything —
    // is shaped...
    let trace = corpus::trace("resume_pair", RESUME);
    let spans = emitted(&trace);
    let turnless: Vec<&hyphae_model::ApiCall> = trace
        .api_calls
        .iter()
        .filter(|call| call.turn_id.is_none())
        .collect();
    assert_eq!(
        turnless.len(),
        5,
        "the fixture stopped carrying the turnless calls this leaf reads"
    );
    // ...then its five turnless calls hang off the root, since `main` has no run above it...
    for call in turnless {
        let span = one(&spans, &digest(RESUME, "api_call", &call.source, &call.id));
        assert_eq!(span.parent_span_id, digest(RESUME, "session", "", RESUME));
    }
    // ...and when a by-reference fork opens mid-conversation the same way, its calls hang off
    // the fork's run instead. Its session records no timestamps of its own, so the root is
    // given the fork's clock — planted, because the mapper refuses a timeless session and the
    // source filter never hands it one.
    let mut byref = corpus::trace("fork_byref", NO_PROJECT_SESSION);
    byref.session.started_at = byref.api_calls.iter().map(|call| call.started_at).min();
    byref.session.ended_at = byref.api_calls.iter().map(|call| call.ended_at).max();
    let reshaped = emitted(&byref);
    let calls: Vec<&hyphae_model::ApiCall> = byref
        .api_calls
        .iter()
        .filter(|call| call.turn_id.is_none())
        .collect();
    assert_eq!(calls.len(), 2);
    for call in calls {
        let span = one(
            &reshaped,
            &digest(NO_PROJECT_SESSION, "api_call", &call.source, &call.id),
        );
        assert_eq!(
            span.parent_span_id,
            digest(NO_PROJECT_SESSION, "agent_run", "", BYREF_FORK)
        );
    }
}

/// The `Agent` call that started a subagent ships as that subagent's span, on the subagent's
/// clock rather than the launch acknowledgement's.
#[test]
fn a_matched_tool_call_becomes_the_run_it_spawned() {
    // If the session holding a recorded `Agent` call and the run that answered it is shaped...
    let trace = corpus::trace("spine", SPINE);
    let spans = emitted(&trace);
    let tool = trace
        .tool_calls
        .iter()
        .find(|call| call.id == SPINE_SPAWN)
        .expect("the recorded spawn");
    let run = trace
        .agent_runs
        .iter()
        .find(|row| row.id == SPINE_RUN)
        .expect("the run it started");
    let (started_at, ended_at) = (
        run.started_at.expect("a start"),
        run.ended_at.expect("an end"),
    );
    let tool_ended = tool.ended_at.expect("the spawn completed");
    // ...then the tool call itself ships no span — one event, not two...
    let key = digest(SPINE, "tool_call", &tool.source, &tool.id);
    assert!(spans.iter().all(|span| span.span_id != key));
    // ...and the run's span carries the agent it ran, timed to the run's own work rather than
    // to the 11 ms Claude Code took to acknowledge the launch.
    let span = one(&spans, &digest(SPINE, "agent_run", "", SPINE_RUN));
    assert_eq!(span.name, "invoke_agent claude");
    assert_eq!(tool_ended - tool.started_at, TimeDelta::milliseconds(11));
    assert_eq!(
        ended_at - started_at,
        TimeDelta::minutes(4) + TimeDelta::seconds(50) + TimeDelta::milliseconds(609)
    );
    assert_eq!(span.start_time_unix_nano, nanos(started_at));
    assert_eq!(span.end_time_unix_nano, nanos(ended_at));
}

/// Everything a subagent recorded climbs to that subagent's span, and a subagent's subagent
/// nests inside its caller.
#[test]
fn a_runs_work_nests_under_its_invoke_agent_span() {
    // If the deepest recorded run tree is shaped...
    let trace = corpus::trace("spine", SPINE);
    let spans = emitted(&trace);
    // ...then every row a run's transcript wrote reaches that run's span...
    for run in &trace.agent_runs {
        let own = digest(SPINE, "agent_run", "", &run.id);
        let mut rows: Vec<Vec<u8>> = trace
            .turns
            .iter()
            .filter(|row| row.source == run.id)
            .map(|row| digest(SPINE, "turn", &row.source, &row.id))
            .collect();
        rows.extend(
            trace
                .api_calls
                .iter()
                .filter(|row| row.source == run.id)
                .map(|row| digest(SPINE, "api_call", &row.source, &row.id)),
        );
        assert!(
            !rows.is_empty(),
            "run {} recorded nothing for this leaf to place",
            run.id
        );
        for key in rows {
            assert!(
                climb(&spans, &key).contains(&own),
                "a row of run {} never reaches it",
                run.id
            );
        }
    }
    // ...and the run the outer run spawned sits inside it, rather than beside it under the
    // root.
    assert!(
        climb(&spans, &digest(SPINE, "agent_run", "", SPINE_LEAF)).contains(&digest(
            SPINE,
            "agent_run",
            "",
            SPINE_RUN
        ))
    );
}

/// A run's span keeps its id when the tool call that spawned it stops being found.
#[test]
fn the_invoke_agent_id_survives_a_matched_to_orphan_flip() {
    // If a recorded matched run is shaped, and then shaped again with its `tool_use_id`
    // cleared — a planted single-field edit, since no recorded run flips...
    let trace = corpus::trace("spine", SPINE);
    let shaped = emitted(&trace);
    let matched = one(&shaped, &digest(SPINE, "agent_run", "", SPINE_RUN));
    let mut flipped = trace.clone();
    for run in &mut flipped.agent_runs {
        if run.id == SPINE_RUN {
            run.tool_use_id = None;
        }
    }
    let reshaped = emitted(&flipped);
    let orphaned = one(&reshaped, &digest(SPINE, "agent_run", "", SPINE_RUN));
    // ...then the span id is byte-identical, because it comes from the run's own key and no
    // part of the tool call enters the hash — a key that moved would land the same subagent
    // twice on a backend that never dedupes...
    assert_eq!(orphaned.span_id, matched.span_id);
    assert_ne!(
        matched.span_id,
        digest(SPINE, "tool_call", MAIN, SPINE_SPAWN)
    );
    // ...and what does move is where it hangs and the flag that says why.
    assert_eq!(
        matched.parent_span_id,
        digest(SPINE, "api_call", MAIN, SPINE_SPAWN_CALL)
    );
    assert_eq!(orphaned.parent_span_id, digest(SPINE, "session", "", SPINE));
    assert_eq!(
        attributes(orphaned).get("hyphae.orphan"),
        Some(&Value::Bool(true))
    );
    assert!(!attributes(matched).contains_key("hyphae.orphan"));
}

/// A teammate the team mechanism started, with no tool call behind it, is still a span.
#[test]
fn an_orphan_run_hangs_off_the_root() {
    // If the recorded orphan is shaped...
    let trace = corpus::trace("teammate", TEAMMATE);
    let shaped = emitted(&trace);
    let span = one(&shaped, &digest(TEAMMATE, "agent_run", "", TEAMMATE_RUN));
    // ...then it hangs off the root and says so, rather than dangling under a call that never
    // existed.
    assert_eq!(span.name, "invoke_agent architect");
    assert_eq!(
        span.parent_span_id,
        digest(TEAMMATE, "session", "", TEAMMATE)
    );
    assert_eq!(
        attributes(span).get("hyphae.orphan"),
        Some(&Value::Bool(true))
    );
}

/// A run whose spawning tool call is in no transcript of this session still lands in the tree,
/// carrying the id that failed to place it.
#[test]
fn a_run_naming_a_call_this_trace_never_held_hangs_off_the_root() {
    // If the fork fixture's auditor run is shaped — it names the `Agent` call that started it,
    // and no transcript in this session recorded that call...
    let trace = corpus::trace("fork_origin", FORK_ORIGIN);
    let run = trace
        .agent_runs
        .iter()
        .find(|row| row.id == FORK_ORIGIN_RUN)
        .expect("the auditor run");
    let named = run.tool_use_id.clone().expect("it names a call");
    assert!(trace.tool_calls.iter().all(|call| call.id != named));
    // ...then it hangs off the root like an orphan, and ships the unplaceable id so the two
    // are told apart in the data rather than by a second flag.
    let shaped = emitted(&trace);
    let span = one(
        &shaped,
        &digest(FORK_ORIGIN, "agent_run", "", FORK_ORIGIN_RUN),
    );
    assert_eq!(
        span.parent_span_id,
        digest(FORK_ORIGIN, "session", "", FORK_ORIGIN)
    );
    let shipped = attributes(span);
    assert_eq!(shipped.get("hyphae.orphan"), Some(&Value::Bool(true)));
    assert_eq!(
        shipped.get("claude_code.agent_run.tool_use_id"),
        Some(&Value::Str(named))
    );
}

/// A fork whose spawning call its own transcript recorded nests under the run it forked from,
/// not under a model call that already sits inside it.
#[test]
fn a_fork_spawned_inside_its_own_transcript_hangs_off_the_run_it_continues() {
    // If the recorded fork is shaped — the `Agent` call that created it was written into the
    // fork's own transcript, so hanging the fork off that call's span would make the fork its
    // own ancestor...
    let trace = corpus::trace("fork_origin", FORK_ORIGIN);
    let run = trace
        .agent_runs
        .iter()
        .find(|row| row.id == FORK_RUN)
        .expect("the fork");
    let spawn = trace
        .tool_calls
        .iter()
        .find(|call| Some(&call.id) == run.tool_use_id.as_ref())
        .expect("the call that created it");
    assert_eq!(spawn.source, FORK_RUN);
    // ...then it hangs off the run it continues instead, which is the one place above it that
    // cannot be inside it.
    let shaped = emitted(&trace);
    let span = one(&shaped, &digest(FORK_ORIGIN, "agent_run", "", FORK_RUN));
    assert_eq!(
        span.parent_span_id,
        digest(FORK_ORIGIN, "agent_run", "", FORK_ORIGIN_RUN)
    );
}

/// A run with no recorded clock crashes rather than being given an invented one.
#[test]
fn null_agent_run_times_crash() {
    // If a recorded run has its start cleared — planted, since not one of the canonical
    // store's 2,487 runs records a null time...
    let mut planted = corpus::trace("spine", SPINE);
    for run in &mut planted.agent_runs {
        if run.id == SPINE_RUN {
            run.started_at = None;
        }
    }
    // ...then the mapper names the run and the session it found it in, because a run that
    // cannot be timed is a shape we need to see rather than a span to guess at.
    let refused = session_spans(&planted, &METADATA_ONLY).expect_err("a timeless run refuses");
    assert!(matches!(refused, ShapeError::TimelessRun { .. }));
    let message = refused.to_string();
    assert!(message.contains(SPINE_RUN), "{message}");
    assert!(message.contains(SPINE), "{message}");
}
