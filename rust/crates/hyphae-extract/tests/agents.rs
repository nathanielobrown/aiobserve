//! Subagent transcripts: the work a session delegated, parsed like the work it did itself.
//!
//! The port of `tests/extract/test_claude_code__agents.py`. A subagent writes its own file
//! under the session's `subagents/`, in the same record shapes the main transcript uses.
//! Every row it produces carries the agent's id as its `source`, so one session's rows stay
//! separable by who did the work, and an `AgentRun` beside them says who asked for the work.
//!
//! One leaf of the Python file is not here: a teammate run is announced on stderr, which no
//! in-process leaf can read, so `hp::cli` asserts the warning over a spawned binary.

use hyphae_testsupport::corpus::{self, at};
use hyphae_testsupport::landmarks::{
    DEEP_RESEARCH_SESSION, SPINE, SPINE_LEAF, SPINE_RUN, TEAMMATE, TEAMMATE_RUN, WORKFLOW_AGENT,
    WORKFLOW_RUN,
};

use hyphae_model::{AgentRun, MAIN_SOURCE};

/// The delegated work parses like any other, under the agent's id rather than "main".
#[test]
fn a_subagents_rows_are_sourced_by_its_agent_id() {
    // If a session spawned a subagent that ran a tool and delegated further...
    let extracted = corpus::trace("spine", SPINE);
    let turns: Vec<_> = extracted
        .turns
        .iter()
        .filter(|turn| turn.source == SPINE_RUN)
        .collect();
    let calls: Vec<_> = extracted
        .api_calls
        .iter()
        .filter(|call| call.source == SPINE_RUN)
        .collect();
    let tools: Vec<_> = extracted
        .tool_calls
        .iter()
        .filter(|tool| tool.source == SPINE_RUN)
        .collect();

    // ...then its transcript yields the same three kinds of row the main one does...
    assert_eq!((turns.len(), calls.len(), tools.len()), (1, 2, 3));
    // ...its two assistant chunks merge into the one call they shared a message id for...
    assert_eq!(calls[0].id, "msg_011CdmTpsWWekY9vCQNRhnDj");
    assert_eq!(calls[0].turn_id.as_deref(), Some(turns[0].id.as_str()));
    assert_eq!(
        tools
            .iter()
            .map(|tool| tool.name.as_str())
            .collect::<Vec<_>>(),
        ["Bash", "Agent", "Agent"]
    );
    // ...and the rows are scoped to the session that spawned it, not a session of its own.
    for owner in [
        &turns[0].session_id,
        &calls[0].session_id,
        &tools[0].session_id,
    ] {
        assert_eq!(owner, SPINE);
    }
    // The main transcript's own rows are untouched by the extra source.
    assert_eq!(
        extracted
            .turns
            .iter()
            .filter(|turn| turn.source == MAIN_SOURCE)
            .map(|turn| turn.index)
            .collect::<Vec<i32>>(),
        [0, 1, 2, 3]
    );
}

/// Inside a subagent transcript the `isSidechain` exclusion does not apply.
///
/// Every record of a subagent file is `isSidechain: true` — the flag marks delegated work in
/// the *main* transcript, where the subagent's own records would double-count it. Read as an
/// exclusion here, it would leave every subagent turnless.
#[test]
fn a_delegated_prompt_opens_a_turn() {
    // If the delegating prompt is the subagent transcript's first record...
    let extracted = corpus::trace("spine", SPINE);
    let turn = extracted
        .turns
        .iter()
        .find(|turn| turn.source == SPINE_RUN)
        .expect("the subagent's thread holds a turn");

    // ...then it opens the agent's only turn, which runs to the end of its work.
    assert_eq!(turn.index, 0);
    assert_eq!(turn.prompt, "[redacted]");
    assert_eq!(turn.started_at, at("2026-08-06T12:04:25.042"));
    assert_eq!(turn.ended_at, at("2026-08-06T12:09:15.651"));
}

/// An instruction from another agent drives work exactly as a user's prompt does.
///
/// Only subagent transcripts hold these — a census of main transcripts never sees the tag,
/// and the parser crashes on an unregistered one, so an unteamed corpus hides it until a
/// session uses teams.
#[test]
fn a_teammate_message_opens_a_turn() {
    // If a teammate agent's transcript opens on its team lead's instruction...
    let extracted = corpus::trace("teammate", TEAMMATE);
    let turns: Vec<_> = extracted
        .turns
        .iter()
        .filter(|turn| turn.source != MAIN_SOURCE)
        .collect();

    // ...then each instruction opens a turn, carried as recorded — attributes on the tag and
    // all, which is how these differ from every other registered tag. The lead came back with
    // a second one, so the run is a conversation rather than a single task.
    assert_eq!(
        turns.iter().map(|turn| turn.index).collect::<Vec<i32>>(),
        [0, 1]
    );
    for turn in turns {
        assert!(
            turn.prompt
                .starts_with(r#"<teammate-message teammate_id="team-lead""#),
            "the tag is carried whole"
        );
        assert_eq!(turn.command_name, None);
        assert_eq!(turn.command_args, None);
    }
}

/// A subagent's `meta.json` is the link from the delegated work back to the ask.
#[test]
fn a_subagent_run_names_the_call_that_spawned_it() {
    // If a session delegated work with the `Agent` tool...
    let extracted = corpus::trace("spine", SPINE);

    // ...then the run it started is a row of its own, keyed within the session...
    assert_eq!(
        extracted
            .agent_runs
            .iter()
            .find(|run| run.id == SPINE_RUN)
            .expect("spine records the run"),
        &AgentRun {
            id: SPINE_RUN.to_owned(),
            session_id: SPINE.to_owned(),
            parent_agent_id: None,
            // ...naming the `Agent` tool call that asked for it, which the session's own tool
            // calls hold under the same id...
            tool_use_id: Some("toolu_015dP3eMe5GZn7BzFipupZwS".to_owned()),
            agent_type: "claude".to_owned(),
            brief: Some("[redacted]".to_owned()),
            model: Some("opus".to_owned()),
            workflow_id: None,
            spawn_depth: Some(1),
            // ...continuing no one else's conversation...
            is_fork: false,
            fork_context_uuid: None,
            // ...and spanning its own transcript, which starts a beat after the call.
            started_at: Some(at("2026-08-06T12:04:25.042")),
            ended_at: Some(at("2026-08-06T12:09:15.651")),
        }
    );
    let spawning = extracted
        .tool_calls
        .iter()
        .find(|call| call.id == "toolu_015dP3eMe5GZn7BzFipupZwS")
        .expect("the spawning call is in the same trace");
    assert_eq!(spawning.name, "Agent");
    assert_eq!(spawning.source, MAIN_SOURCE);
}

/// A subagent's own subagent records the agent above it, not just the session.
#[test]
fn a_nested_run_names_its_parent() {
    // If a subagent delegated in turn...
    let extracted = corpus::trace("spine", SPINE);
    let run = extracted
        .agent_runs
        .iter()
        .find(|run| run.id == SPINE_LEAF)
        .expect("spine records the nested run");

    // ...then its run hangs off the agent that spawned it, one level deeper...
    assert_eq!(run.parent_agent_id.as_deref(), Some(SPINE_RUN));
    assert_eq!(run.spawn_depth, Some(2));
    // ...and the spawning call is one of that agent's, not the session's.
    assert_eq!(
        run.tool_use_id.as_deref(),
        Some("toolu_01SpzLooq2oJd72pCg4Jmq6v")
    );
    // `model` is absent from this run's meta — the caller named none — and absence is a
    // state, not a parse failure.
    assert_eq!(run.model, None);
}

/// A fan-out's agents link to the `Workflow` call through the run id it reported.
///
/// Their own metas carry no `toolUseId` — nothing spawned them one by one. What names them is
/// the `wf_<id>` directory they sit in, which the launching call's result reports as its
/// `runId`.
#[test]
fn a_workflow_agent_joins_by_its_run_directory() {
    // If a session launched a workflow that ran an agent...
    let extracted = corpus::trace("workflow", DEEP_RESEARCH_SESSION);
    let run = extracted
        .agent_runs
        .iter()
        .find(|run| run.id == WORKFLOW_AGENT)
        .expect("the fan-out records its agent");

    // ...then the run carries the fan-out it belonged to...
    assert_eq!(run.workflow_id.as_deref(), Some(WORKFLOW_RUN));
    assert_eq!(run.agent_type, "workflow-subagent");
    assert_eq!(run.spawn_depth, Some(1));
    // ...and the `Workflow` call that launched the fan-out stands as its spawning call.
    let launch = extracted
        .tool_calls
        .iter()
        .find(|call| Some(&call.id) == run.tool_use_id.as_ref())
        .expect("the launching call is in the same trace");
    assert_eq!(launch.name, "Workflow");
    assert_eq!(launch.source, MAIN_SOURCE);
}

/// An agent of a parallel fan-out is a subagent that sits one directory deeper.
#[test]
fn a_workflow_agent_parses_like_any_other() {
    // If a workflow ran an agent...
    let extracted = corpus::trace("workflow", DEEP_RESEARCH_SESSION);
    let calls: Vec<_> = extracted
        .api_calls
        .iter()
        .filter(|call| call.source == WORKFLOW_AGENT)
        .collect();

    // ...its records parse under its own agent id, the extra directory notwithstanding...
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].id, "msg_011CcxQueXXAiVxM6LKvY7Ya");
    // ...and the journal beside it stays an archive, contributing no calls of its own.
    let mut sources: Vec<&str> = extracted
        .api_calls
        .iter()
        .map(|call| call.source.as_str())
        .collect();
    sources.sort_unstable();
    sources.dedup();
    assert_eq!(sources, [WORKFLOW_AGENT, MAIN_SOURCE]);
}

/// A run whose meta omits `spawnDepth` has no depth, not depth zero.
///
/// One meta of the 2764 on this machine leaves the key out (scanned 2026-08-07):
/// `-Users-nob-repos-mac-settings/c31ecec9-.../subagents/agent-a20276f6d8a4e5309.meta.json`,
/// **Claude Code 2.1.186**. Its shape is planted here rather than added as a fixture tree —
/// the missing key is the whole record. `description` is redacted as every fixture's is.
#[test]
fn a_meta_that_names_no_depth_says_so_rather_than_guessing() {
    // If a session holds a subagent whose meta names its type and its spawning call, and
    // nothing else...
    let recorded = std::fs::read(
        corpus::fixtures()
            .join("spine")
            .join(SPINE)
            .join("subagents")
            .join(format!("agent-{SPINE_LEAF}.jsonl")),
    )
    .expect("the recorded subagent transcript is readable");
    let transcript = format!("subagents/agent-{SPINE_LEAF}.jsonl");
    let meta = format!("subagents/agent-{SPINE_LEAF}.meta.json");
    let planted = corpus::planted(
        "spine",
        SPINE,
        &[
            (transcript.as_str(), recorded.as_slice()),
            (
                meta.as_str(),
                concat!(
                    r#"{"agentType": "claude-code-guide", "description": "[redacted]", "#,
                    r#""toolUseId": "toolu_01Wibfj3Q3njBXyH76pSf1hk"}"#
                )
                .as_bytes(),
            ),
        ],
    );

    // ...then the run reports the depth as unknown, rather than reading absence as the top.
    let extracted = corpus::extractor()
        .extract(&planted.source)
        .expect("the planted session parses");
    let [run] = extracted.agent_runs.as_slice() else {
        panic!("the planted session holds one run");
    };
    assert_eq!(run.id, SPINE_LEAF);
    assert_eq!(run.agent_type, "claude-code-guide");
    assert_eq!(run.spawn_depth, None);
}

/// A run with no spawning call is exported anyway.
///
/// A teammate is started by the team mechanism rather than by a tool call, so its meta
/// carries no `toolUseId` and nothing in the transcript points at it. Dropping such a run
/// would hide a whole delegated workload: the prior importer reported 100% direct tool calls
/// that way. The announcement it makes on the way is asserted in `hp::cli`.
#[test]
fn a_teammate_run_is_an_orphan_and_says_so() {
    // If a session ran a long-lived teammate...
    let extracted = corpus::trace("teammate", TEAMMATE);
    let run = extracted
        .agent_runs
        .first()
        .expect("the teammate session records a run");

    // ...then the run is recorded with no call behind it, at the depth teams start at.
    assert_eq!(run.id, TEAMMATE_RUN);
    assert_eq!(run.tool_use_id, None);
    assert_eq!(run.spawn_depth, Some(0));
    assert_eq!(run.agent_type, "architect");
    assert_eq!(run.model.as_deref(), Some("fable"));
}
