//! Span shaping: what a recorded session becomes, and the ids that make a re-send a re-send.
//!
//! The port of `tests/export/test_otlp.py`. No store and no HTTP here — recorded traces in,
//! spans out. The per-entity arms are `parenting.rs` and `shaping.rs`; this file holds
//! identity and the whole-trace invariants.

use std::collections::{HashMap, HashSet};

use hyphae_export::otlp::{
    CLIENT, INTERNAL, METADATA_ONLY, ShapeError, SpanKey, session_spans, span_id,
};
use hyphae_extract::SessionSource;
use hyphae_model::SessionTrace;
use hyphae_store::Store;
use hyphae_store::source::StoreSource;
use hyphae_testsupport::landmarks::{
    DEEP_RESEARCH_SESSION, FORK_ORIGIN, NO_PROJECT_SESSION, SPINE, SPINE_LEAF, SPINE_RUN, TEAMMATE,
};
use hyphae_testsupport::otlp::{digest, emitted, hex, nanos, one};
use hyphae_testsupport::{cache, corpus};
use opentelemetry_proto::tonic::trace::v1::Span;
use sha2::Digest as _;

/// One fixture, extracted the way every leaf here reads it.
fn trace(directory: &str, stem: &str) -> SessionTrace {
    corpus::trace(directory, stem)
}

/// Every span id this trace can name, mapped to a label a failure can be read from.
fn labels(trace: &SessionTrace) -> HashMap<Vec<u8>, String> {
    let session_id = &trace.session.id;
    let mut named = HashMap::new();
    named.insert(
        digest(session_id, "session", "", session_id),
        "root".to_owned(),
    );
    for run in &trace.agent_runs {
        named.insert(
            digest(session_id, "agent_run", "", &run.id),
            format!("run {}", run.id),
        );
    }
    for turn in &trace.turns {
        named.insert(
            digest(session_id, "turn", &turn.source, &turn.id),
            format!("turn {}#{}", turn.source, turn.index),
        );
    }
    for call in &trace.api_calls {
        named.insert(
            digest(session_id, "api_call", &call.source, &call.id),
            format!("chat {}#{}", call.source, call.index),
        );
    }
    named
}

/// Each emitted span as its name, its kind, and the label of its parent.
fn shape(trace: &SessionTrace) -> Vec<(String, i32, String)> {
    let named = labels(trace);
    emitted(trace)
        .iter()
        .map(|span| {
            let parent = named
                .get(&span.parent_span_id)
                .cloned()
                .unwrap_or_else(|| "none".to_owned());
            (span.name.clone(), span.kind, parent)
        })
        .collect()
}

/// A session's turns, model calls, tools and subagent runs become spans with the design's
/// names, kinds and parents.
#[test]
fn the_spine_becomes_a_span_per_live_row() {
    // If the deepest recorded session is shaped — four main turns and one turn inside each of
    // its two subagent runs, nine model calls between them, twelve tool calls of which two
    // spawned the runs...
    let trace = trace("spine", SPINE);
    // ...then the spans are the root and one per live row, each hanging off the row that drove
    // it: a main-thread turn off the root, a subagent's turn off its run, every model call off
    // its turn, every tool off the call that asked for it...
    let expected: Vec<(&str, i32, String)> = vec![
        ("claude_code.session", INTERNAL, "none".to_owned()),
        ("claude_code.turn", INTERNAL, "root".to_owned()),
        ("claude_code.turn", INTERNAL, "root".to_owned()),
        ("claude_code.turn", INTERNAL, "root".to_owned()),
        ("claude_code.turn", INTERNAL, "root".to_owned()),
        ("claude_code.turn", INTERNAL, format!("run {SPINE_RUN}")),
        ("claude_code.turn", INTERNAL, format!("run {SPINE_LEAF}")),
        ("chat claude-fable-5", CLIENT, "turn main#1".to_owned()),
        ("chat claude-fable-5", CLIENT, "turn main#2".to_owned()),
        ("chat claude-fable-5", CLIENT, "turn main#2".to_owned()),
        ("chat claude-fable-5", CLIENT, "turn main#2".to_owned()),
        ("chat claude-fable-5", CLIENT, "turn main#2".to_owned()),
        // The placeholder reply Claude Code wrote itself keeps its recorded model name.
        ("chat <synthetic>", CLIENT, "turn main#3".to_owned()),
        ("chat claude-opus-5", CLIENT, format!("turn {SPINE_RUN}#0")),
        ("chat claude-opus-5", CLIENT, format!("turn {SPINE_RUN}#0")),
        ("chat claude-opus-5", CLIENT, format!("turn {SPINE_LEAF}#0")),
        ("execute_tool Bash", INTERNAL, "chat main#0".to_owned()),
        ("execute_tool Read", INTERNAL, "chat main#0".to_owned()),
        ("execute_tool Read", INTERNAL, "chat main#0".to_owned()),
        // One reply asked for two tools at once, so both hang off the same call...
        ("execute_tool Bash", INTERNAL, "chat main#2".to_owned()),
        (
            "execute_tool ToolSearch",
            INTERNAL,
            "chat main#2".to_owned(),
        ),
        (
            "execute_tool PushNotification",
            INTERNAL,
            "chat main#3".to_owned(),
        ),
        ("execute_tool Read", INTERNAL, "chat main#4".to_owned()),
        ("execute_tool Bash", INTERNAL, format!("chat {SPINE_RUN}#0")),
        // A third `Agent` call that no recorded run answers stays a plain tool call.
        (
            "execute_tool Agent",
            INTERNAL,
            format!("chat {SPINE_RUN}#1"),
        ),
        (
            "execute_tool Read",
            INTERNAL,
            format!("chat {SPINE_LEAF}#0"),
        ),
        // ...and the two `Agent` calls a run *did* answer become the runs themselves, each off
        // the model call that asked for it — which is what makes the two runs nest.
        ("invoke_agent claude", INTERNAL, "chat main#1".to_owned()),
        (
            "invoke_agent Explore",
            INTERNAL,
            format!("chat {SPINE_RUN}#1"),
        ),
    ];
    assert_eq!(
        shape(&trace),
        expected
            .into_iter()
            .map(|(name, kind, parent)| (name.to_owned(), kind, parent))
            .collect::<Vec<_>>()
    );
}

/// Every span hangs off a span the same trace holds, and every chain ends at one root.
#[test]
fn every_span_climbs_to_the_one_root() {
    // If each recorded session the source filter would ship is shaped...
    for transcript in corpus::exportable_transcripts() {
        let stem = transcript.file_stem().expect("a stem").to_string_lossy();
        let directory = transcript
            .parent()
            .and_then(|at| at.file_name())
            .expect("a fixture directory")
            .to_string_lossy();
        let trace = trace(&directory, &stem);
        let spans = emitted(&trace);
        let parents: HashMap<&[u8], &[u8]> = spans
            .iter()
            .map(|span| (span.span_id.as_slice(), span.parent_span_id.as_slice()))
            .collect();
        let named = labels(&trace);
        // ...then exactly one span is parentless...
        let roots: Vec<&Span> = spans
            .iter()
            .filter(|span| span.parent_span_id.is_empty())
            .collect();
        assert_eq!(
            roots
                .iter()
                .map(|span| span.name.as_str())
                .collect::<Vec<_>>(),
            vec!["claude_code.session"],
            "{stem}"
        );
        // ...and walking any span's parents reaches it without meeting a parent the trace
        // never emitted, and without ever coming back around to a span the walk already
        // passed.
        for span in &spans {
            let mut walked: Vec<&[u8]> = Vec::new();
            let mut current: &[u8] = &span.span_id;
            while !current.is_empty() {
                let label = named
                    .get(current)
                    .map(String::as_str)
                    .unwrap_or("an unnamed span");
                assert!(
                    parents.contains_key(current),
                    "{} climbs to {label}, which was not emitted",
                    span.name
                );
                assert!(
                    !walked.contains(&current),
                    "{} climbs through {label} twice",
                    span.name
                );
                walked.push(current);
                current = parents[current];
            }
            assert_eq!(walked.last().copied(), Some(roots[0].span_id.as_slice()));
        }
    }
}

/// Rows a fork replayed from the transcript it continues are shipped by neither of them.
#[test]
fn a_forks_copies_never_become_spans() {
    // If the fork fixture is shaped — one replayed turn, one replayed model call and four
    // replayed tool calls, all copies of rows the auditor run beneath it already holds...
    let trace = trace("fork_origin", FORK_ORIGIN);
    let mut replayed: Vec<Vec<u8>> = Vec::new();
    replayed.extend(
        trace
            .turns
            .iter()
            .filter(|row| row.replayed)
            .map(|row| digest(FORK_ORIGIN, "turn", &row.source, &row.id)),
    );
    replayed.extend(
        trace
            .api_calls
            .iter()
            .filter(|row| row.replayed)
            .map(|row| digest(FORK_ORIGIN, "api_call", &row.source, &row.id)),
    );
    replayed.extend(
        trace
            .tool_calls
            .iter()
            .filter(|row| row.replayed)
            .map(|row| digest(FORK_ORIGIN, "tool_call", &row.source, &row.id)),
    );
    assert_eq!(
        replayed.len(),
        6,
        "the fixture stopped carrying the copies this leaf reads"
    );
    // ...then no span carries a copy's key, because shipping one double-counts the same event
    // in every backend aggregation — the whole reason the exclusion exists...
    let shipped: HashSet<Vec<u8>> = emitted(&trace)
        .iter()
        .map(|span| span.span_id.clone())
        .collect();
    for key in &replayed {
        assert!(
            !shipped.contains(key),
            "a replayed row shipped as {}",
            hex(key)
        );
    }
    // ...and what is left is the root, one span per live row, and one per run — less the one
    // live `Agent` call that spawned the fork, which became its run's span instead.
    let live = trace.turns.iter().filter(|row| !row.replayed).count()
        + trace.api_calls.iter().filter(|row| !row.replayed).count()
        + trace.tool_calls.iter().filter(|row| !row.replayed).count();
    assert_eq!(shipped.len(), 1 + trace.agent_runs.len() + live - 1);
}

/// The root ends when its last child does, while its attributes keep the end the session
/// actually recorded.
#[test]
fn the_root_covers_work_that_outlived_the_main_transcript() {
    // If one of the three recorded sessions whose subagents ran on past the main transcript's
    // last record is shaped...
    for (directory, session_id) in [
        ("fork_origin", FORK_ORIGIN),
        ("workflow", DEEP_RESEARCH_SESSION),
        ("teammate", TEAMMATE),
    ] {
        let trace = trace(directory, session_id);
        let recorded = trace.session.ended_at.expect("a recorded end");
        let spans = emitted(&trace);
        let (root, children) = spans.split_first().expect("a root and its children");
        // ...then the root stretches to cover them, because a waterfall whose root ends before
        // its children renders broken...
        let last = children
            .iter()
            .map(|child| child.end_time_unix_nano)
            .max()
            .expect("a child");
        assert_eq!(root.end_time_unix_nano, last, "{session_id}");
        assert!(root.end_time_unix_nano > nanos(recorded), "{session_id}");
        // ...and the recorded end survives as an attribute, which is what keeps the stretch
        // from becoming a lie. Read back as an instant rather than as a string, so the leaf
        // pins the moment and not the mapper's own spelling of it.
        let shipped = hyphae_testsupport::otlp::attributes(root);
        let Some(hyphae_testsupport::otlp::Value::Str(written)) =
            shipped.get("claude_code.session.ended_at")
        else {
            panic!("{session_id} shipped no recorded end");
        };
        let parsed = chrono::DateTime::parse_from_rfc3339(written)
            .unwrap_or_else(|error| panic!("{written} is not an instant: {error}"));
        assert_eq!(parsed.to_utc(), recorded, "{session_id}");
    }
}

/// A row whose recorded start and end are the same instant still spans a millisecond.
#[test]
fn a_row_with_no_duration_floors_to_a_millisecond() {
    // If the fixtures are searched for model calls that started and ended at the same instant
    // — a lookup rather than a list, so the leaf survives a fixture change...
    let mut found = 0;
    for transcript in corpus::exportable_transcripts() {
        let stem = transcript.file_stem().expect("a stem").to_string_lossy();
        let directory = transcript
            .parent()
            .and_then(|at| at.file_name())
            .expect("a fixture directory")
            .to_string_lossy();
        let trace = trace(&directory, &stem);
        let instant: Vec<&hyphae_model::ApiCall> = trace
            .api_calls
            .iter()
            .filter(|call| !call.replayed && call.ended_at <= call.started_at)
            .collect();
        if instant.is_empty() {
            continue;
        }
        let spans = emitted(&trace);
        // ...then each one's span still has a positive width, because a zero-width span
        // renders as an invisible sliver no waterfall can show.
        for call in instant {
            let key = digest(&trace.session.id, "api_call", &call.source, &call.id);
            let span = one(&spans, &key);
            assert_eq!(
                span.end_time_unix_nano - span.start_time_unix_nano,
                1_000_000,
                "{stem}"
            );
            found += 1;
        }
    }
    assert!(
        found > 0,
        "no recorded row has a zero-length duration to floor"
    );
}

/// Every id is the sha256 digest of its key, sliced to the width the OTLP spec gives it.
#[test]
fn ids_are_digest_bytes_not_hex_characters() {
    // If a recorded session is shaped...
    let trace = trace("spine", SPINE);
    let session_id = &trace.session.id;
    let spans = emitted(&trace);
    // ...then one trace id covers it, 16 bytes of digest — not the 16 hex *characters* of
    // a hex digest sliced the same way, which is the same length and half the entropy...
    let traces: HashSet<Vec<u8>> = spans.iter().map(|span| span.trace_id.clone()).collect();
    let expected_trace = sha2::Sha256::digest(session_id.as_bytes())[..16].to_vec();
    assert_eq!(traces, HashSet::from([expected_trace]));
    // ...and each span id is its own key's digest, 8 bytes, recomputed from the rows. The two
    // tool calls a run answered are keyed as runs, never as the tool call they replace.
    let matched: HashSet<&str> = trace
        .agent_runs
        .iter()
        .filter_map(|run| run.tool_use_id.as_deref())
        .collect();
    let mut expected: HashSet<Vec<u8>> =
        HashSet::from([digest(session_id, "session", "", session_id)]);
    expected.extend(
        trace
            .turns
            .iter()
            .map(|row| digest(session_id, "turn", &row.source, &row.id)),
    );
    expected.extend(
        trace
            .api_calls
            .iter()
            .map(|row| digest(session_id, "api_call", &row.source, &row.id)),
    );
    expected.extend(
        trace
            .tool_calls
            .iter()
            .filter(|row| !matched.contains(row.id.as_str()))
            .map(|row| digest(session_id, "tool_call", &row.source, &row.id)),
    );
    expected.extend(
        trace
            .agent_runs
            .iter()
            .map(|row| digest(session_id, "agent_run", "", &row.id)),
    );
    assert_eq!(
        spans
            .iter()
            .map(|span| span.span_id.clone())
            .collect::<HashSet<_>>(),
        expected
    );
    assert_eq!(
        spans
            .iter()
            .map(|span| span.span_id.len())
            .collect::<HashSet<_>>(),
        HashSet::from([8])
    );
}

/// Shaping the same session again — even from a store built and read back — gives the same
/// ids.
#[test]
fn ids_hold_still_across_a_re_export() {
    // If a recorded session is shaped twice from the transcript, and once more from rows
    // written into a store and read back...
    let first: HashSet<Vec<u8>> = emitted(&trace("spine", SPINE))
        .iter()
        .map(|span| span.span_id.clone())
        .collect();
    let again: HashSet<Vec<u8>> = emitted(&trace("spine", SPINE))
        .iter()
        .map(|span| span.span_id.clone())
        .collect();
    let store = Store::open_read_only(&cache::corpus_store()).expect("the built store opens");
    let rebuilt = StoreSource::new(&store)
        .extract(&SessionSource {
            id: SPINE.to_owned(),
            files: Vec::new(),
            fingerprint: "x".to_owned(),
        })
        .expect("the stored session rebuilds");
    let stored: HashSet<Vec<u8>> = emitted(&rebuilt)
        .iter()
        .map(|span| span.span_id.clone())
        .collect();
    // ...then all three passes name the same spans: at-least-once delivery is only a re-send
    // while the ids stay put, and an id that moves lands a second unrelated trace.
    assert_eq!(first, again);
    assert_eq!(first, stored);
}

/// Within one session's trace, every span id is distinct.
#[test]
fn no_two_spans_of_a_session_share_an_id() {
    for transcript in corpus::exportable_transcripts() {
        let stem = transcript.file_stem().expect("a stem").to_string_lossy();
        let directory = transcript
            .parent()
            .and_then(|at| at.file_name())
            .expect("a fixture directory")
            .to_string_lossy();
        let spans = emitted(&trace(&directory, &stem));
        let ids: HashSet<&Vec<u8>> = spans.iter().map(|span| &span.span_id).collect();
        assert_eq!(ids.len(), spans.len(), "{stem}");
    }
}

/// A session the source filter would exclude cannot be shaped: its root has no clock.
#[test]
fn a_session_with_no_recorded_time_crashes() {
    // If the one recorded session holding no timestamps at all is handed to the mapper —
    // which the pipeline never does, since the source filter refuses to place it...
    let trace = trace("fork_byref", NO_PROJECT_SESSION);
    // ...then it crashes rather than inventing a root span's start and end.
    let refused = session_spans(&trace, &METADATA_ONLY).expect_err("a timeless session refuses");
    assert!(matches!(refused, ShapeError::TimelessSession { .. }));
    assert!(refused.to_string().contains(NO_PROJECT_SESSION));
}

/// An id component holding the key's delimiter refuses to hash rather than collide.
#[test]
fn a_slash_in_a_key_component_crashes() {
    // If a recorded agentId is given a slash — invented, since no shipped row across the
    // canonical store holds one, and `raw_records`'s `wf_<id>/journal` sources are one table
    // away...
    let run = trace("spine", SPINE).agent_runs.remove(0);
    let planted = format!("{}/{}", &run.id[..8], &run.id[8..]);
    // ...then the id function crashes naming the component, because `a/b` and `a` + `b` would
    // otherwise hash to one span id and silently become one span.
    let refused =
        span_id(SPINE, SpanKey::AgentRun, "", &planted).expect_err("a slash refuses to hash");
    assert!(matches!(refused, ShapeError::AmbiguousKey { .. }));
    assert!(refused.to_string().contains(&planted));
}

/// No source or natural id in any shipped table contains the `/` the id keys join on.
#[test]
fn no_recorded_key_component_holds_the_delimiter() {
    // The id-key components of every table the mapper ships. Read off the trace rather than
    // listed, so the sweep covers a session's whole corpus.
    for transcript in corpus::corpus_transcripts() {
        let stem = transcript.file_stem().expect("a stem").to_string_lossy();
        let directory = transcript
            .parent()
            .and_then(|at| at.file_name())
            .expect("a fixture directory")
            .to_string_lossy();
        let trace = trace(&directory, &stem);
        let mut components: Vec<&str> = vec![&trace.session.id];
        for turn in &trace.turns {
            components.extend([turn.source.as_str(), turn.id.as_str()]);
        }
        for call in &trace.api_calls {
            components.extend([call.source.as_str(), call.id.as_str()]);
        }
        for call in &trace.tool_calls {
            components.extend([call.source.as_str(), call.id.as_str()]);
        }
        for run in &trace.agent_runs {
            components.push(run.id.as_str());
        }
        for compaction in &trace.compactions {
            components.extend([compaction.source.as_str(), compaction.id.as_str()]);
        }
        let held: Vec<&&str> = components
            .iter()
            .filter(|component| component.contains('/'))
            .collect();
        assert!(held.is_empty(), "{stem} holds {held:?}");
    }
}
