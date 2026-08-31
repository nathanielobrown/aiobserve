//! What a span carries: a tool call's clock, a compaction, a PR event, and the gen_ai keys.
//!
//! Half the port of `tests/export/test_otlp__shaping.py`; where a row hangs is `parenting.rs`.
//! Every planted value is labeled where it sits, because a shape the corpus never recorded is
//! a hypothesis, not evidence.

use std::collections::BTreeMap;

use chrono::TimeDelta;
use hyphae_export::otlp::{METADATA_ONLY, ShapeError, copied_compaction, session_spans};
use hyphae_testsupport::corpus;
use hyphae_testsupport::landmarks::{
    COMPACTED, FORK_ORIGIN, FORK_RUN, MAIN, SERVER_TOOLS, SPINE, SPINE_RUN,
};
use hyphae_testsupport::otlp::{Value, attributes, digest, emitted, nanos, one};

/// A different main-thread call of `spine/`, read for its whole attribute set.
const SPINE_CALL: &str = "msg_011CdmMjFXDofyYSMxYtXa5n";

const MILLISECOND: TimeDelta = TimeDelta::milliseconds(1);

/// A tool the session never saw finish ends at its start, flagged, rather than running to the
/// end of the session.
#[test]
fn an_incomplete_tool_call_ends_where_it_started() {
    // If the session holding three interrupted tool calls is shaped...
    let trace = corpus::trace("spine", SPINE);
    let shaped = emitted(&trace);
    let incomplete: Vec<&hyphae_model::ToolCall> = trace
        .tool_calls
        .iter()
        .filter(|call| call.ended_at.is_none())
        .collect();
    assert_eq!(
        incomplete.len(),
        3,
        "the fixture stopped carrying the interrupted calls"
    );
    // ...then each spans the floor from its recorded start and says it is incomplete.
    for call in incomplete {
        let span = one(&shaped, &digest(SPINE, "tool_call", &call.source, &call.id));
        assert_eq!(span.start_time_unix_nano, nanos(call.started_at));
        assert_eq!(
            span.end_time_unix_nano - span.start_time_unix_nano,
            1_000_000
        );
        assert_eq!(
            attributes(span).get("hyphae.incomplete"),
            Some(&Value::Bool(true))
        );
    }
}

/// A shared batch start and a server-side run are attributes on the times as recorded, never
/// invented ones.
#[test]
fn flagged_tool_times_ride_the_recorded_clock() {
    // If a tool call whose start was shared with the rest of its batch is shaped...
    let trace = corpus::trace("spine", SPINE);
    let shared = trace
        .tool_calls
        .iter()
        .find(|call| call.duration_synthetic && call.ended_at.is_some() && !call.replayed)
        .expect("a recorded batch start");
    let ended_at = shared.ended_at.expect("it completed");
    let shaped = emitted(&trace);
    let span = one(
        &shaped,
        &digest(SPINE, "tool_call", &shared.source, &shared.id),
    );
    // ...then the span keeps both recorded times and flags the one that was not measured...
    assert_eq!(span.start_time_unix_nano, nanos(shared.started_at));
    assert_eq!(span.end_time_unix_nano, nanos(ended_at));
    assert_eq!(
        attributes(span).get("claude_code.tool_call.duration_synthetic"),
        Some(&Value::Bool(true))
    );
    // ...and a tool Anthropic ran server-side ships under its own name with the same treatment.
    let served = corpus::trace("server_tools", SERVER_TOOLS);
    let call = served
        .tool_calls
        .iter()
        .find(|row| row.server_side && row.ended_at.is_some())
        .expect("a recorded server-side call");
    let shaped = emitted(&served);
    let span = one(
        &shaped,
        &digest(SERVER_TOOLS, "tool_call", &call.source, &call.id),
    );
    assert_eq!(span.name, "execute_tool advisor");
    assert_eq!(
        attributes(span).get("claude_code.tool_call.server_side"),
        Some(&Value::Bool(true))
    );
}

/// A reply Claude Code wrote itself ships under its recorded model name, marked.
#[test]
fn a_placeholder_reply_is_flagged_as_synthetic() {
    let trace = corpus::trace("spine", SPINE);
    let call = trace
        .api_calls
        .iter()
        .find(|row| row.synthetic)
        .expect("the recorded placeholder");
    let shaped = emitted(&trace);
    let span = one(&shaped, &digest(SPINE, "api_call", &call.source, &call.id));
    // It reports no tokens and costs nothing, so counting it as a model call inflates the call
    // count of every aggregation that does not filter it out.
    assert_eq!(span.name, "chat <synthetic>");
    assert_eq!(
        attributes(span).get("hyphae.synthetic"),
        Some(&Value::Bool(true))
    );
}

/// A compaction spans the time it took to summarise, under the thread that compacted.
#[test]
fn a_main_thread_compaction_is_a_span_under_the_root() {
    // If the session holding two recorded main-thread compactions is shaped...
    let trace = corpus::trace("compaction", COMPACTED);
    let shaped = emitted(&trace);
    let main_thread: Vec<&hyphae_model::Compaction> = trace
        .compactions
        .iter()
        .filter(|row| row.source == MAIN)
        .collect();
    assert_eq!(main_thread.len(), 2);
    // ...then each is a span under the root, as long as the summarising took, carrying the
    // context sizes either side — the point where the session's account of itself gets lossy.
    for compaction in main_thread {
        let span = one(
            &shaped,
            &digest(COMPACTED, "compaction", &compaction.source, &compaction.id),
        );
        assert_eq!(span.name, "claude_code.compaction");
        assert_eq!(
            span.parent_span_id,
            digest(COMPACTED, "session", "", COMPACTED)
        );
        assert_eq!(span.start_time_unix_nano, nanos(compaction.timestamp));
        let width = span.end_time_unix_nano - span.start_time_unix_nano;
        assert_eq!(width, (compaction.duration_ms * 1_000_000) as u64);
        let shipped = attributes(span);
        assert_eq!(
            shipped.get("claude_code.compaction.trigger"),
            Some(&Value::Str(compaction.trigger.clone()))
        );
        assert_eq!(
            shipped.get("claude_code.compaction.pre_tokens"),
            Some(&Value::Int(compaction.pre_tokens))
        );
    }
}

/// A compaction a fork copied in with its prefix is a replay, and one landing exactly on the
/// fork's first own record is a copy too.
#[test]
fn the_copied_prefix_rule_decides_a_compactions_replay() {
    // If a recorded compaction is planted onto the fork's source at each timestamp around the
    // fork's own first record — labeled, because all six recorded compactions are main-thread,
    // and `compactions` carries no `replayed` column for the mapper to read...
    let recorded = corpus::trace("compaction", COMPACTED).compactions.remove(0);
    let origin = corpus::trace("fork_origin", FORK_ORIGIN);
    let fork = origin
        .agent_runs
        .iter()
        .find(|run| run.id == FORK_RUN)
        .expect("the fork");
    assert!(fork.is_fork);
    let opened = fork.started_at.expect("the fork records its own start");
    let mut planted = recorded.clone();
    planted.session_id = FORK_ORIGIN.to_owned();
    planted.source = FORK_RUN.to_owned();
    let at = |moment| {
        let mut copy = planted.clone();
        copy.timestamp = moment;
        copy
    };
    // ...then everything at or before that instant is a copy of the parent's compaction: a
    // fork cannot compact at the instant of its own first record, and when the copied prefix
    // ends at the compaction the two share a millisecond...
    assert!(copied_compaction(&at(opened - MILLISECOND), Some(fork)).expect("the rule reads"));
    assert!(copied_compaction(&at(opened), Some(fork)).expect("the rule reads"));
    // ...only what came afterwards is the fork's own work...
    assert!(!copied_compaction(&at(opened + MILLISECOND), Some(fork)).expect("the rule reads"));
    // ...a fork that copied everything it holds records no start at all, so nothing in it can
    // be its own (planted: no recorded fork run has a null start)...
    let mut copied_whole = fork.clone();
    copied_whole.started_at = None;
    assert!(copied_compaction(&at(opened), Some(&copied_whole)).expect("the rule reads"));
    // ...and a main-thread compaction, which comes first in the ordering and so can hold no
    // copies, always ships.
    let mut on_main = recorded.clone();
    on_main.source = MAIN.to_owned();
    assert!(!copied_compaction(&on_main, None).expect("the rule reads"));
}

/// A compaction the rule calls a copy reaches no span, and one it calls live hangs off the run
/// that made it.
#[test]
fn the_copied_prefix_rule_is_wired_into_the_mapper() {
    // If the same planted compaction is put through the whole mapper, once on the tie and once
    // a millisecond later...
    let trace = corpus::trace("fork_origin", FORK_ORIGIN);
    let recorded = corpus::trace("compaction", COMPACTED).compactions.remove(0);
    let fork = trace
        .agent_runs
        .iter()
        .find(|run| run.id == FORK_RUN)
        .expect("the fork");
    let opened = fork.started_at.expect("the fork records its own start");
    let mut tie = recorded.clone();
    tie.session_id = FORK_ORIGIN.to_owned();
    tie.source = FORK_RUN.to_owned();
    tie.timestamp = opened;
    let mut own = tie.clone();
    own.timestamp = opened + MILLISECOND;
    let key = digest(FORK_ORIGIN, "compaction", FORK_RUN, &recorded.id);
    // ...then the tie ships nothing...
    let mut tied = trace.clone();
    tied.compactions = vec![tie];
    assert!(emitted(&tied).iter().all(|span| span.span_id != key));
    // ...and the later one ships under the run whose transcript recorded it.
    let mut lived = trace.clone();
    lived.compactions = vec![own];
    let shaped = emitted(&lived);
    let span = one(&shaped, &key);
    assert_eq!(
        span.parent_span_id,
        digest(FORK_ORIGIN, "agent_run", "", FORK_RUN)
    );
}

/// A compaction older than the run that recorded it, where no copying can explain it, stops
/// the run.
#[test]
fn a_compaction_before_a_non_fork_runs_start_crashes() {
    // If a compaction is planted just before a recorded non-fork run's first record — labeled,
    // since none of the canonical store's 847 non-fork-run compactions is one...
    let mut trace = corpus::trace("spine", SPINE);
    let recorded = corpus::trace("compaction", COMPACTED).compactions.remove(0);
    let run = trace
        .agent_runs
        .iter()
        .find(|row| row.id == SPINE_RUN)
        .expect("the run")
        .clone();
    assert!(!run.is_fork);
    let started_at = run.started_at.expect("a start");
    let mut planted = recorded;
    planted.session_id = SPINE.to_owned();
    planted.source = SPINE_RUN.to_owned();
    planted.timestamp = started_at - MILLISECOND;
    trace.compactions = vec![planted.clone()];
    // ...then the mapper crashes naming both clocks, because the replay rule's whole safety is
    // that only a fork can hold a copy — dropping this one silently would lose a live event.
    let refused = session_spans(&trace, &METADATA_ONLY).expect_err("an unexplained copy refuses");
    assert!(matches!(refused, ShapeError::CompactionBeforeRun { .. }));
    let message = refused.to_string();
    assert!(
        message.contains(SPINE) && message.contains(SPINE_RUN),
        "{message}"
    );
    // Both clocks are named, so a reader can see which of the two the drift is in.
    for moment in [planted.timestamp, started_at] {
        let written = moment.format("%Y-%m-%dT%H:%M:%S%.6f+00:00").to_string();
        assert!(message.contains(&written), "{message} names no {written}");
    }
}

/// Each PR the session touched is an event on the root holding the bare number.
#[test]
fn pr_links_are_events_on_the_root_carrying_only_the_number() {
    // If the session that linked the same pull request twice is shaped...
    let trace = corpus::trace("spine", SPINE);
    assert_eq!(trace.pr_links.len(), 2);
    let shaped = emitted(&trace);
    let root = &shaped[0];
    // ...then both links are events rather than spans — they mark an instant, not a duration —
    // and neither carries the URL or the repository name, which name a private repo.
    let read: Vec<(&str, BTreeMap<String, Value>)> = root
        .events
        .iter()
        .map(|event| {
            (
                event.name.as_str(),
                hyphae_testsupport::otlp::read(&event.attributes),
            )
        })
        .collect();
    let number = BTreeMap::from([("claude_code.pr_link.number".to_owned(), Value::Int(656))]);
    assert_eq!(
        read,
        vec![
            ("claude_code.pr_link", number.clone()),
            ("claude_code.pr_link", number)
        ]
    );
    assert_eq!(
        root.events
            .iter()
            .map(|event| event.time_unix_nano)
            .collect::<Vec<_>>(),
        trace
            .pr_links
            .iter()
            .map(|link| nanos(link.timestamp))
            .collect::<Vec<_>>()
    );
}

/// A model call ships the GenAI semconv attributes a backend groups on, and nothing else.
#[test]
fn gen_ai_attributes_ride_every_chat_span() {
    // If a recorded model call is shaped...
    let trace = corpus::trace("spine", SPINE);
    let call = trace
        .api_calls
        .iter()
        .find(|row| row.id == SPINE_CALL)
        .expect("the recorded call");
    let shaped = emitted(&trace);
    let span = one(&shaped, &digest(SPINE, "api_call", MAIN, SPINE_CALL));
    // ...then its whole attribute set is the semconv names plus our own mirrors — no prompt,
    // no reply, no thinking, and no column that held nothing.
    let text = |value: &str| Value::Str(value.to_owned());
    assert_eq!(
        attributes(span),
        BTreeMap::from([
            ("gen_ai.operation.name".to_owned(), text("chat")),
            ("gen_ai.request.model".to_owned(), text("claude-fable-5")),
            ("gen_ai.conversation.id".to_owned(), text(SPINE)),
            ("gen_ai.usage.input_tokens".to_owned(), Value::Int(2)),
            ("gen_ai.usage.output_tokens".to_owned(), Value::Int(415)),
            (
                "gen_ai.response.finish_reasons".to_owned(),
                text("tool_use")
            ),
            ("claude_code.api_call.id".to_owned(), text(SPINE_CALL)),
            ("claude_code.source".to_owned(), text(MAIN)),
            (
                "claude_code.api_call.cache_read_tokens".to_owned(),
                Value::Int(9768)
            ),
            (
                "claude_code.api_call.cache_creation_tokens".to_owned(),
                Value::Int(20257)
            ),
            ("claude_code.api_call.effort".to_owned(), text("high")),
            // The skill that was driving when the call was made.
            (
                "claude_code.api_call.attribution_skill".to_owned(),
                text("night-run")
            ),
            (
                "claude_code.api_call.request_id".to_owned(),
                text("req_011CdmMjDTCU8h7qzXd5Chuj")
            ),
            // From our own price table, not the transcript, which records no cost.
            (
                "claude_code.api_call.cost_usd".to_owned(),
                Value::Double(call.cost_usd.expect("the table prices it"))
            ),
            ("logfire.msg".to_owned(), text("chat claude-fable-5")),
        ])
    );
}
