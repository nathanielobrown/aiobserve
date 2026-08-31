//! One span per store row: the root, and each kind of work under it.
//!
//! The shapes live here; the ids, the envelope and the attribute encoding are the parent
//! module's. Ported from `src/hyphae/export/otlp.py`, which stays the authority.

use super::*;

/// The session's root span, stretched to cover work that outlived the main transcript.
///
/// `Session.ended_at` reads the main transcript only, and a subagent can run past it — a
/// waterfall whose root ends before its children renders broken. The recorded value stays in
/// the attributes.
pub(super) fn root_span(
    trace: &SessionTrace,
    started_at: DateTime<Utc>,
    recorded_end: DateTime<Utc>,
    children: &[Span],
    text: &TextPolicy,
) -> Result<Span> {
    let session = &trace.session;
    let ended_at = children
        .iter()
        .map(|child| from_nanos(child.end_time_unix_nano))
        .fold(recorded_end, DateTime::max);
    build_span(
        &session.id,
        span_id(&session.id, SpanKey::Session, "", &session.id)?,
        Vec::new(),
        "claude_code.session",
        INTERNAL,
        started_at,
        ended_at,
        vec![
            ("gen_ai.conversation.id", str_attr(Some(&session.id))),
            ("claude_code.session.id", str_attr(Some(&session.id))),
            (
                "claude_code.session.version",
                str_attr(session.version.as_deref()),
            ),
            (
                "claude_code.session.entrypoint",
                str_attr(session.entrypoint.as_deref()),
            ),
            (
                "claude_code.session.project_dir",
                str_attr(session.project_dir.as_deref()),
            ),
            (
                "claude_code.session.git_branch",
                str_attr(session.git_branch.as_deref()),
            ),
            // What Claude Code reported working, well below the span's own wall time.
            (
                "claude_code.session.active_ms",
                Some(Attr::Int(session.active_ms)),
            ),
            // The recorded end, which the span's own end may have stretched past.
            (
                "claude_code.session.ended_at",
                Some(Attr::Str(iso(recorded_end))),
            ),
            ("hyphae.extractor", str_attr(Some(&trace.extractor))),
            (
                "hyphae.extractor.version",
                str_attr(Some(&trace.extractor_version)),
            ),
            // Model-written from the conversation, so it counts as transcript text.
            (
                "claude_code.session.title",
                text_attr(text, session.title.as_deref()),
            ),
            (
                "claude_code.session.agent_name",
                text_attr(text, session.agent_name.as_deref()),
            ),
            // Ids, never the title: `ai-title` is model-written from the conversation.
            (
                "logfire.msg",
                Some(Attr::Str(format!("session {}", session.id))),
            ),
        ],
        trace
            .pr_links
            .iter()
            .map(|link| pr_event(link, text))
            .collect(),
    )
}

/// One pull request the session touched — an instant on the root, not a span.
fn pr_event(link: &PrLink, text: &TextPolicy) -> span::Event {
    span::Event {
        time_unix_nano: nanos(link.timestamp),
        name: "claude_code.pr_link".to_owned(),
        attributes: attributes(vec![
            (
                "claude_code.pr_link.number",
                Some(Attr::Int(i64::from(link.pr_number))),
            ),
            // Both name a repository that may be private, so they stay home by default.
            (
                "claude_code.pr_link.url",
                text_attr(text, Some(&link.pr_url)),
            ),
            (
                "claude_code.pr_link.repository",
                text_attr(text, Some(&link.pr_repository)),
            ),
        ]),
        ..Default::default()
    }
}

/// One prompt and the work it drove. Under the root on `main`, under its run otherwise.
pub(super) fn turn_span(session: &Session, turn: &Turn, text: &TextPolicy) -> Result<Span> {
    build_span(
        &session.id,
        span_id(&session.id, SpanKey::Turn, &turn.source, &turn.id)?,
        source_parent(&session.id, &turn.source)?,
        "claude_code.turn",
        INTERNAL,
        turn.started_at,
        turn.ended_at,
        vec![
            ("claude_code.turn.id", str_attr(Some(&turn.id))),
            (
                "claude_code.turn.index",
                Some(Attr::Int(i64::from(turn.index))),
            ),
            ("claude_code.source", str_attr(Some(&turn.source))),
            // The command's name only — its arguments are user-typed text.
            (
                "claude_code.turn.command_name",
                str_attr(turn.command_name.as_deref()),
            ),
            (
                "claude_code.turn.prompt",
                text_attr(text, Some(&turn.prompt)),
            ),
            (
                "claude_code.turn.command_args",
                text_attr(text, turn.command_args.as_deref()),
            ),
            ("logfire.msg", Some(Attr::Str(format!("turn {}", turn.id)))),
        ],
        Vec::new(),
    )
}

/// One model response, under the turn that drove it.
pub(super) fn chat_span(
    session: &Session,
    call: &ApiCall,
    turns: &HashMap<(&str, &str), &Turn>,
    text: &TextPolicy,
) -> Result<Span> {
    build_span(
        &session.id,
        span_id(&session.id, SpanKey::ApiCall, &call.source, &call.id)?,
        chat_parent(&session.id, call, turns)?,
        &format!("chat {}", call.model),
        CLIENT,
        call.started_at,
        call.ended_at,
        vec![
            ("gen_ai.operation.name", str_attr(Some("chat"))),
            ("gen_ai.request.model", str_attr(Some(&call.model))),
            ("gen_ai.conversation.id", str_attr(Some(&session.id))),
            (
                "gen_ai.usage.input_tokens",
                Some(Attr::Int(call.input_tokens)),
            ),
            (
                "gen_ai.usage.output_tokens",
                Some(Attr::Int(call.output_tokens)),
            ),
            (
                "gen_ai.response.finish_reasons",
                str_attr(call.stop_reason.as_deref()),
            ),
            ("claude_code.api_call.id", str_attr(Some(&call.id))),
            ("claude_code.source", str_attr(Some(&call.source))),
            (
                "claude_code.api_call.cache_read_tokens",
                Some(Attr::Int(call.cache_read_tokens)),
            ),
            (
                "claude_code.api_call.cache_creation_tokens",
                Some(Attr::Int(call.cache_creation_tokens)),
            ),
            (
                "claude_code.api_call.effort",
                str_attr(call.effort.as_deref()),
            ),
            // The model asked for first, when the request was retried on another.
            (
                "claude_code.api_call.fallback_from",
                str_attr(call.fallback_from.as_deref()),
            ),
            (
                "claude_code.api_call.attribution_skill",
                str_attr(call.attribution_skill.as_deref()),
            ),
            (
                "claude_code.api_call.request_id",
                str_attr(call.request_id.as_deref()),
            ),
            // From our own price table, not the transcript; absent when it prices no model.
            (
                "claude_code.api_call.cost_usd",
                call.cost_usd.map(Attr::Double),
            ),
            // A placeholder reply Claude Code wrote itself: no tokens, no cost, not a call.
            ("hyphae.synthetic", flag(call.synthetic)),
            (
                "claude_code.api_call.text",
                text_attr(text, Some(&call.text)),
            ),
            (
                "claude_code.api_call.thinking",
                text_attr(text, Some(&call.thinking)),
            ),
            (
                "logfire.msg",
                Some(Attr::Str(format!("chat {}", call.model))),
            ),
        ],
        Vec::new(),
    )
}

/// One tool the model asked for, under the model call that asked.
pub(super) fn tool_span(session: &Session, call: &ToolCall, text: &TextPolicy) -> Result<Span> {
    build_span(
        &session.id,
        span_id(&session.id, SpanKey::ToolCall, &call.source, &call.id)?,
        span_id(
            &session.id,
            SpanKey::ApiCall,
            &call.source,
            &call.api_call_id,
        )?,
        &format!("execute_tool {}", call.name),
        INTERNAL,
        call.started_at,
        // A call the session never saw finish ends where it started rather than running to
        // the end of the transcript; `build_span` floors it to the minimum.
        call.ended_at.unwrap_or(call.started_at),
        vec![
            ("gen_ai.operation.name", str_attr(Some("execute_tool"))),
            ("gen_ai.tool.name", str_attr(Some(&call.name))),
            ("gen_ai.conversation.id", str_attr(Some(&session.id))),
            ("claude_code.tool_call.id", str_attr(Some(&call.id))),
            ("claude_code.source", str_attr(Some(&call.source))),
            (
                "claude_code.tool_call.index",
                Some(Attr::Int(i64::from(call.index))),
            ),
            ("claude_code.api_call.id", str_attr(Some(&call.api_call_id))),
            // Anthropic ran it; no local transcript records the work it did.
            ("claude_code.tool_call.server_side", flag(call.server_side)),
            // The start is the batch's, not this call's — flagged, never invented away.
            (
                "claude_code.tool_call.duration_synthetic",
                flag(call.duration_synthetic),
            ),
            ("claude_code.tool_call.is_error", flag(call.is_error)),
            // The archived file the output went to, which stays local.
            (
                "claude_code.tool_call.offload_file",
                str_attr(call.offload_file.as_deref()),
            ),
            ("hyphae.incomplete", flag(call.ended_at.is_none())),
            (
                "claude_code.tool_call.input",
                text_attr(text, Some(&call.input)),
            ),
            (
                "claude_code.tool_call.result",
                text_attr(text, call.result.as_deref()),
            ),
            (
                "logfire.msg",
                Some(Attr::Str(format!("execute_tool {}", call.name))),
            ),
        ],
        Vec::new(),
    )
}

/// One subagent, timed to its own work rather than to the launch acknowledgement.
///
/// The id comes from the run's own key, never the tool call's: children in the run's
/// transcript know only their `source`, and a run that flips between matched and orphan across
/// extracts must keep the span id it already shipped under.
pub(super) fn run_span(
    session: &Session,
    run: &AgentRun,
    spawn: Option<&ToolCall>,
    runs: &HashMap<&str, &AgentRun>,
    text: &TextPolicy,
) -> Result<Span> {
    let (Some(started_at), Some(ended_at)) = (run.started_at, run.ended_at) else {
        return Err(ShapeError::TimelessRun {
            run_id: run.id.clone(),
            session_id: session.id.clone(),
        });
    };
    let (parent, orphan) = run_parent(&session.id, run, spawn, runs)?;
    build_span(
        &session.id,
        span_id(&session.id, SpanKey::AgentRun, "", &run.id)?,
        parent,
        &format!("invoke_agent {}", run.agent_type),
        INTERNAL,
        started_at,
        ended_at,
        vec![
            ("gen_ai.operation.name", str_attr(Some("invoke_agent"))),
            ("gen_ai.agent.name", str_attr(Some(&run.agent_type))),
            ("gen_ai.conversation.id", str_attr(Some(&session.id))),
            ("claude_code.agent_run.id", str_attr(Some(&run.id))),
            (
                "claude_code.agent_run.parent_agent_id",
                str_attr(run.parent_agent_id.as_deref()),
            ),
            // Kept even when it placed nothing, so an orphan that named a call and one that
            // named none are told apart in the data rather than by a second flag.
            (
                "claude_code.agent_run.tool_use_id",
                str_attr(run.tool_use_id.as_deref()),
            ),
            (
                "claude_code.agent_run.model",
                str_attr(run.model.as_deref()),
            ),
            (
                "claude_code.agent_run.workflow_id",
                str_attr(run.workflow_id.as_deref()),
            ),
            (
                "claude_code.agent_run.spawn_depth",
                run.spawn_depth.map(|depth| Attr::Int(i64::from(depth))),
            ),
            // A continuation of another run, carrying a copy of its transcript's prefix.
            ("claude_code.agent_run.is_fork", flag(run.is_fork)),
            (
                "claude_code.agent_run.brief",
                text_attr(text, run.brief.as_deref()),
            ),
            // No tool call in this trace placed it, so it hangs off the root.
            ("hyphae.orphan", flag(orphan)),
            (
                "logfire.msg",
                Some(Attr::Str(format!("invoke_agent {}", run.agent_type))),
            ),
        ],
        Vec::new(),
    )
}

/// Where a run's span hangs, and whether it is an orphan.
///
/// A fork's spawning call is copied into the fork's own transcript, so hanging the run off
/// that call's span would make the run its own ancestor. Those fall back to the lineage the
/// run already records, which is the one place above it that cannot be inside it.
fn run_parent(
    session_id: &str,
    run: &AgentRun,
    spawn: Option<&ToolCall>,
    runs: &HashMap<&str, &AgentRun>,
) -> Result<(Vec<u8>, bool)> {
    let Some(spawn) = spawn else {
        return Ok((span_id(session_id, SpanKey::Session, "", session_id)?, true));
    };
    if inside(&run.id, &spawn.source, runs) {
        let lineage = run.parent_agent_id.as_deref().unwrap_or(MAIN_SOURCE);
        return Ok((source_parent(session_id, lineage)?, false));
    }
    Ok((
        span_id(
            session_id,
            SpanKey::ApiCall,
            &spawn.source,
            &spawn.api_call_id,
        )?,
        false,
    ))
}

/// Whether a source is a run itself or something that run spawned.
fn inside(run_id: &str, source: &str, runs: &HashMap<&str, &AgentRun>) -> bool {
    let mut walked: HashSet<&str> = HashSet::new();
    let mut source = source;
    while source != MAIN_SOURCE && !walked.contains(source) {
        if source == run_id {
            return true;
        }
        walked.insert(source);
        let Some(run) = runs.get(source) else {
            return false;
        };
        source = run.parent_agent_id.as_deref().unwrap_or(MAIN_SOURCE);
    }
    false
}

/// One point where Claude Code summarised the conversation, as long as that took.
pub(super) fn compaction_span(session: &Session, compaction: &Compaction) -> Result<Span> {
    build_span(
        &session.id,
        span_id(
            &session.id,
            SpanKey::Compaction,
            &compaction.source,
            &compaction.id,
        )?,
        source_parent(&session.id, &compaction.source)?,
        COMPACTION_SPAN,
        INTERNAL,
        compaction.timestamp,
        compaction.timestamp + TimeDelta::milliseconds(compaction.duration_ms),
        vec![
            ("claude_code.compaction.id", str_attr(Some(&compaction.id))),
            ("claude_code.source", str_attr(Some(&compaction.source))),
            (
                "claude_code.compaction.trigger",
                str_attr(Some(&compaction.trigger)),
            ),
            // Either side of the summary: where the session's account of itself gets lossy.
            (
                "claude_code.compaction.pre_tokens",
                Some(Attr::Int(compaction.pre_tokens)),
            ),
            (
                "claude_code.compaction.post_tokens",
                Some(Attr::Int(compaction.post_tokens)),
            ),
            (
                "logfire.msg",
                Some(Attr::Str(format!("compaction {}", compaction.trigger))),
            ),
        ],
        Vec::new(),
    )
}

/// Whether a compaction is one a fork copied in with its prefix, and so ships no span.
///
/// `compactions` carries no `replayed` column, so the rule reads the same prefix shape the
/// extractor's flags read: [`AgentRun::started_at`] is by contract the first record no earlier
/// transcript already held, so anything in a fork at or before it came from the parent. A tie
/// is a copy — a fork cannot compact at the instant of its own first record, and when the
/// copied prefix ends at the compaction the two share a millisecond.
///
/// `run` is the run a compaction's `source` names, or `None` on the main thread, which comes
/// first in the extractor's ordering and can hold no copies.
pub fn copied_compaction(compaction: &Compaction, run: Option<&AgentRun>) -> Result<bool> {
    let Some(run) = run else { return Ok(false) };
    if !run.is_fork {
        if let Some(started_at) = run.started_at
            && compaction.timestamp < started_at
        {
            return Err(ShapeError::CompactionBeforeRun {
                compaction_id: compaction.id.clone(),
                session_id: compaction.session_id.clone(),
                timestamp: iso(compaction.timestamp),
                thread: compaction.source.clone(),
                started_at: iso(started_at),
            });
        }
        return Ok(false);
    }
    // It copied everything it holds, so nothing in it is its own.
    match run.started_at {
        None => Ok(true),
        Some(started_at) => Ok(compaction.timestamp <= started_at),
    }
}

/// What a row's `source` hangs off: the root on the main thread, its run inside one.
fn source_parent(session_id: &str, source: &str) -> Result<Vec<u8>> {
    if source == MAIN_SOURCE {
        return span_id(session_id, SpanKey::Session, "", session_id);
    }
    span_id(session_id, SpanKey::AgentRun, "", source)
}

/// The span a model call hangs off.
///
/// Its turn, except where that turn emits no span: a by-reference fork opens mid-conversation
/// with no turn at all, and a fork that replayed its parent's turn holds a live call under a
/// turn this trace never ships. Both fall back to the call's own source, which the call knows
/// without a join.
fn chat_parent(
    session_id: &str,
    call: &ApiCall,
    turns: &HashMap<(&str, &str), &Turn>,
) -> Result<Vec<u8>> {
    let Some(turn_id) = call.turn_id.as_deref() else {
        return source_parent(session_id, &call.source);
    };
    let Some(turn) = turns.get(&(call.source.as_str(), turn_id)) else {
        return Err(ShapeError::UnparentedCall {
            call_id: call.id.clone(),
            session_id: session_id.to_owned(),
            turn_id: turn_id.to_owned(),
            thread: call.source.clone(),
        });
    };
    if turn.replayed {
        return source_parent(session_id, &call.source);
    }
    span_id(session_id, SpanKey::Turn, &turn.source, &turn.id)
}
