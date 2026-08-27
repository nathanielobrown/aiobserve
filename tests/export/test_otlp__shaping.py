"""What each recorded row becomes: tool calls, subagent runs, compactions and PR events.

Recorded traces in, spans out — the whole-trace invariants and the id contract are
`test_otlp.py`. Every planted value here is labeled where it sits, because a shape the
corpus never recorded is a hypothesis, not evidence.
"""

import datetime as dt
from dataclasses import replace

import pytest

from hyphae.export.otlp import (
    CompactionBeforeRunError,
    SpanKey,
    TimelessRunError,
    copied_compaction,
    session_spans,
)
from tests.conftest import (
    BYREF_FORK,
    COMPACTED,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    FORK_RUN,
    MAIN,
    NO_PROJECT_SESSION,
    RESUME,
    SERVER_TOOLS,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
    TEAMMATE,
    TEAMMATE_RUN,
    TraceFactory,
)
from tests.export.conftest import any_value, attributes, digest, nanos, one

# `spine/`'s `Agent` call that spawned `SPINE_RUN`, and the model call that issued it.
SPINE_SPAWN = "toolu_015dP3eMe5GZn7BzFipupZwS"
SPINE_SPAWN_CALL = "msg_011CdmToQdxciYnDo9M2d7HN"
# A different main-thread call, read for its whole attribute set.
SPINE_CALL = "msg_011CdmMjFXDofyYSMxYtXa5n"

MILLISECOND = dt.timedelta(milliseconds=1)


def climb(spans: list, span: bytes) -> list[bytes]:  # type: ignore[type-arg]
    """Every span id between one span and the root, the root last."""
    parents = {candidate.span_id: candidate.parent_span_id for candidate in spans}
    walked = []
    while span:
        walked.append(span)
        span = parents[span]
    return walked


def test_a_live_call_under_a_replayed_turn_hangs_off_its_run(fixture_trace: TraceFactory) -> None:
    """A fork's own model calls hang off the fork's run when the turn above them is a copy."""
    # If the fork fixture is shaped — two live model calls sitting under a turn the fork
    # replayed from the run it continues, so that turn emits no span at all...
    trace = fixture_trace("fork_origin", FORK_ORIGIN)
    spans = session_spans(trace)
    live = [call for call in trace.api_calls if call.source == FORK_RUN and not call.replayed]
    assert len(live) == 2, "the fixture stopped carrying the live calls this leaf reads"
    # ...then each hangs off its own run's span, which the call can name from the `source` it
    # already carries — no join, and no dangling parent where the turn would have been.
    for call in live:
        span = one(spans, digest(FORK_ORIGIN, SpanKey.api_call, call.source, call.id))
        assert span.parent_span_id == digest(FORK_ORIGIN, SpanKey.agent_run, "", FORK_RUN)


def test_a_call_that_opens_mid_conversation_hangs_off_its_source(
    fixture_trace: TraceFactory,
) -> None:
    """A model call with no turn behind it hangs off its run inside a subagent, off the root
    on the main thread."""
    # If a resume — whose whole first stretch was copied in before the user typed anything —
    # is shaped...
    trace = fixture_trace("resume_pair", RESUME)
    spans = session_spans(trace)
    turnless = [call for call in trace.api_calls if call.turn_id is None]
    assert len(turnless) == 5, "the fixture stopped carrying the turnless calls this leaf reads"
    # ...then its five turnless calls hang off the root, since `main` has no run above it...
    for call in turnless:
        span = one(spans, digest(RESUME, SpanKey.api_call, call.source, call.id))
        assert span.parent_span_id == digest(RESUME, SpanKey.session, "", RESUME)
    # ...and when a by-reference fork opens mid-conversation the same way, its calls hang off
    # the fork's run instead. Its session records no timestamps of its own, so the root is
    # given the fork's clock — planted, because the mapper refuses a timeless session and the
    # source filter never hands it one.
    byref = fixture_trace("fork_byref", NO_PROJECT_SESSION)
    opened = min(call.started_at for call in byref.api_calls)
    closed = max(call.ended_at for call in byref.api_calls)
    byref = replace(byref, session=replace(byref.session, started_at=opened, ended_at=closed))
    spans = session_spans(byref)
    calls = [call for call in byref.api_calls if call.turn_id is None]
    assert len(calls) == 2
    for call in calls:
        span = one(spans, digest(NO_PROJECT_SESSION, SpanKey.api_call, call.source, call.id))
        assert span.parent_span_id == digest(NO_PROJECT_SESSION, SpanKey.agent_run, "", BYREF_FORK)


def test_a_matched_tool_call_becomes_the_run_it_spawned(fixture_trace: TraceFactory) -> None:
    """The `Agent` call that started a subagent ships as that subagent's span, on the
    subagent's clock rather than the launch acknowledgement's."""
    # If the session holding a recorded `Agent` call and the run that answered it is shaped...
    trace = fixture_trace("spine", SPINE)
    spans = session_spans(trace)
    tool = next(call for call in trace.tool_calls if call.id == SPINE_SPAWN)
    run = next(row for row in trace.agent_runs if row.id == SPINE_RUN)
    assert run.started_at is not None and run.ended_at is not None and tool.ended_at is not None
    # ...then the tool call itself ships no span — one event, not two...
    key = digest(SPINE, SpanKey.tool_call, tool.source, tool.id)
    assert all(span.span_id != key for span in spans)
    # ...and the run's span carries the agent it ran, timed to the run's own work rather than
    # to the 11 ms Claude Code took to acknowledge the launch.
    span = one(spans, digest(SPINE, SpanKey.agent_run, "", SPINE_RUN))
    assert span.name == "invoke_agent claude"
    assert tool.ended_at - tool.started_at == dt.timedelta(milliseconds=11)
    assert run.ended_at - run.started_at == dt.timedelta(minutes=4, seconds=50, milliseconds=609)
    assert span.start_time_unix_nano == nanos(run.started_at)
    assert span.end_time_unix_nano == nanos(run.ended_at)


def test_a_runs_work_nests_under_its_invoke_agent_span(fixture_trace: TraceFactory) -> None:
    """Everything a subagent recorded climbs to that subagent's span, and a subagent's
    subagent nests inside its caller."""
    # If the deepest recorded run tree is shaped...
    trace = fixture_trace("spine", SPINE)
    spans = session_spans(trace)
    # ...then every row a run's transcript wrote reaches that run's span...
    for run in trace.agent_runs:
        own = digest(SPINE, SpanKey.agent_run, "", run.id)
        rows = [
            (SpanKey.turn, row.source, row.id) for row in trace.turns if row.source == run.id
        ] + [
            (SpanKey.api_call, row.source, row.id)
            for row in trace.api_calls
            if row.source == run.id
        ]
        assert rows, f"run {run.id} recorded nothing for this leaf to place"
        for key in rows:
            assert own in climb(spans, digest(SPINE, *key)), f"{key} never reaches run {run.id}"
    # ...and the run the outer run spawned sits inside it, rather than beside it under the root.
    assert digest(SPINE, SpanKey.agent_run, "", SPINE_RUN) in climb(
        spans, digest(SPINE, SpanKey.agent_run, "", SPINE_LEAF)
    )


def test_the_invoke_agent_id_survives_a_matched_to_orphan_flip(
    fixture_trace: TraceFactory,
) -> None:
    """A run's span keeps its id when the tool call that spawned it stops being found."""
    # If a recorded matched run is shaped, and then shaped again with its `tool_use_id`
    # cleared — a planted single-field edit, since no recorded run flips...
    trace = fixture_trace("spine", SPINE)
    matched = one(session_spans(trace), digest(SPINE, SpanKey.agent_run, "", SPINE_RUN))
    flipped = replace(
        trace,
        agent_runs=[
            replace(run, tool_use_id=None) if run.id == SPINE_RUN else run
            for run in trace.agent_runs
        ],
    )
    orphaned = one(session_spans(flipped), digest(SPINE, SpanKey.agent_run, "", SPINE_RUN))
    # ...then the span id is byte-identical, because it comes from the run's own key and no
    # part of the tool call enters the hash — a key that moved would land the same subagent
    # twice on a backend that never dedupes...
    assert orphaned.span_id == matched.span_id
    assert matched.span_id != digest(SPINE, SpanKey.tool_call, MAIN, SPINE_SPAWN)
    # ...and what does move is where it hangs and the flag that says why.
    assert matched.parent_span_id == digest(SPINE, SpanKey.api_call, MAIN, SPINE_SPAWN_CALL)
    assert orphaned.parent_span_id == digest(SPINE, SpanKey.session, "", SPINE)
    assert attributes(orphaned)["hyphae.orphan"] is True
    assert "hyphae.orphan" not in attributes(matched)


def test_an_orphan_run_hangs_off_the_root(fixture_trace: TraceFactory) -> None:
    """A teammate the team mechanism started, with no tool call behind it, is still a span."""
    # If the recorded orphan is shaped...
    trace = fixture_trace("teammate", TEAMMATE)
    span = one(session_spans(trace), digest(TEAMMATE, SpanKey.agent_run, "", TEAMMATE_RUN))
    # ...then it hangs off the root and says so, rather than dangling under a call that never
    # existed.
    assert span.name == "invoke_agent architect"
    assert span.parent_span_id == digest(TEAMMATE, SpanKey.session, "", TEAMMATE)
    assert attributes(span)["hyphae.orphan"] is True


def test_a_run_naming_a_call_this_trace_never_held_hangs_off_the_root(
    fixture_trace: TraceFactory,
) -> None:
    """A run whose spawning tool call is in no transcript of this session still lands in the
    tree, carrying the id that failed to place it."""
    # If the fork fixture's auditor run is shaped — it names the `Agent` call that started it,
    # and no transcript in this session recorded that call...
    trace = fixture_trace("fork_origin", FORK_ORIGIN)
    run = next(row for row in trace.agent_runs if row.id == FORK_ORIGIN_RUN)
    assert run.tool_use_id is not None
    assert all(call.id != run.tool_use_id for call in trace.tool_calls)
    # ...then it hangs off the root like an orphan, and ships the unplaceable id so the two
    # are told apart in the data rather than by a second flag.
    span = one(session_spans(trace), digest(FORK_ORIGIN, SpanKey.agent_run, "", FORK_ORIGIN_RUN))
    assert span.parent_span_id == digest(FORK_ORIGIN, SpanKey.session, "", FORK_ORIGIN)
    assert attributes(span)["hyphae.orphan"] is True
    assert attributes(span)["claude_code.agent_run.tool_use_id"] == run.tool_use_id


def test_a_fork_spawned_inside_its_own_transcript_hangs_off_the_run_it_continues(
    fixture_trace: TraceFactory,
) -> None:
    """A fork whose spawning call its own transcript recorded nests under the run it forked
    from, not under a model call that already sits inside it."""
    # If the recorded fork is shaped — the `Agent` call that created it was written into the
    # fork's own transcript, so hanging the fork off that call's span would make the fork its
    # own ancestor...
    trace = fixture_trace("fork_origin", FORK_ORIGIN)
    run = next(row for row in trace.agent_runs if row.id == FORK_RUN)
    spawn = next(call for call in trace.tool_calls if call.id == run.tool_use_id)
    assert spawn.source == FORK_RUN
    # ...then it hangs off the run it continues instead, which is the one place above it that
    # cannot be inside it.
    span = one(session_spans(trace), digest(FORK_ORIGIN, SpanKey.agent_run, "", FORK_RUN))
    assert span.parent_span_id == digest(FORK_ORIGIN, SpanKey.agent_run, "", FORK_ORIGIN_RUN)


def test_null_agent_run_times_crash(fixture_trace: TraceFactory) -> None:
    """A run with no recorded clock crashes rather than being given an invented one."""
    # If a recorded run has its start cleared — planted, since not one of the canonical
    # store's 2,487 runs records a null time...
    trace = fixture_trace("spine", SPINE)
    planted = replace(
        trace,
        agent_runs=[
            replace(run, started_at=None) if run.id == SPINE_RUN else run
            for run in trace.agent_runs
        ],
    )
    # ...then the mapper names the run and the session it found it in, because a run that
    # cannot be timed is a shape we need to see rather than a span to guess at.
    with pytest.raises(TimelessRunError) as raised:
        session_spans(planted)
    assert SPINE_RUN in str(raised.value)
    assert SPINE in str(raised.value)


def test_an_incomplete_tool_call_ends_where_it_started(fixture_trace: TraceFactory) -> None:
    """A tool the session never saw finish ends at its start, flagged, rather than running to
    the end of the session."""
    # If the session holding three interrupted tool calls is shaped...
    trace = fixture_trace("spine", SPINE)
    spans = session_spans(trace)
    incomplete = [call for call in trace.tool_calls if call.ended_at is None]
    assert len(incomplete) == 3, "the fixture stopped carrying the interrupted calls"
    # ...then each spans the floor from its recorded start and says it is incomplete.
    for call in incomplete:
        span = one(spans, digest(SPINE, SpanKey.tool_call, call.source, call.id))
        assert span.start_time_unix_nano == nanos(call.started_at)
        assert span.end_time_unix_nano - span.start_time_unix_nano == 1_000_000
        assert attributes(span)["hyphae.incomplete"] is True


def test_flagged_tool_times_ride_the_recorded_clock(fixture_trace: TraceFactory) -> None:
    """A shared batch start and a server-side run are attributes on the times as recorded,
    never invented ones."""
    # If a tool call whose start was shared with the rest of its batch is shaped...
    trace = fixture_trace("spine", SPINE)
    shared = next(
        call
        for call in trace.tool_calls
        if call.duration_synthetic and call.ended_at is not None and not call.replayed
    )
    assert shared.ended_at is not None
    span = one(session_spans(trace), digest(SPINE, SpanKey.tool_call, shared.source, shared.id))
    # ...then the span keeps both recorded times and flags the one that was not measured...
    assert span.start_time_unix_nano == nanos(shared.started_at)
    assert span.end_time_unix_nano == nanos(shared.ended_at)
    assert attributes(span)["claude_code.tool_call.duration_synthetic"] is True
    # ...and a tool Anthropic ran server-side ships under its own name with the same treatment.
    served = fixture_trace("server_tools", SERVER_TOOLS)
    call = next(row for row in served.tool_calls if row.server_side and row.ended_at is not None)
    span = one(session_spans(served), digest(SERVER_TOOLS, SpanKey.tool_call, call.source, call.id))
    assert span.name == "execute_tool advisor"
    assert attributes(span)["claude_code.tool_call.server_side"] is True


def test_a_placeholder_reply_is_flagged_as_synthetic(fixture_trace: TraceFactory) -> None:
    """A reply Claude Code wrote itself ships under its recorded model name, marked."""
    trace = fixture_trace("spine", SPINE)
    call = next(row for row in trace.api_calls if row.synthetic)
    span = one(session_spans(trace), digest(SPINE, SpanKey.api_call, call.source, call.id))
    # It reports no tokens and costs nothing, so counting it as a model call inflates the
    # call count of every aggregation that does not filter it out.
    assert span.name == "chat <synthetic>"
    assert attributes(span)["hyphae.synthetic"] is True


def test_a_main_thread_compaction_is_a_span_under_the_root(fixture_trace: TraceFactory) -> None:
    """A compaction spans the time it took to summarise, under the thread that compacted."""
    # If the session holding two recorded main-thread compactions is shaped...
    trace = fixture_trace("compaction", COMPACTED)
    spans = session_spans(trace)
    main_thread = [row for row in trace.compactions if row.source == MAIN]
    assert len(main_thread) == 2
    # ...then each is a span under the root, as long as the summarising took, carrying the
    # context sizes either side — the point where the session's account of itself gets lossy.
    for compaction in main_thread:
        span = one(spans, digest(COMPACTED, SpanKey.compaction, compaction.source, compaction.id))
        assert span.name == "claude_code.compaction"
        assert span.parent_span_id == digest(COMPACTED, SpanKey.session, "", COMPACTED)
        assert span.start_time_unix_nano == nanos(compaction.timestamp)
        width = span.end_time_unix_nano - span.start_time_unix_nano
        assert width == compaction.duration_ms * 1_000_000
        assert attributes(span)["claude_code.compaction.trigger"] == compaction.trigger
        assert attributes(span)["claude_code.compaction.pre_tokens"] == compaction.pre_tokens


def test_the_copied_prefix_rule_decides_a_compactions_replay(
    fixture_trace: TraceFactory,
) -> None:
    """A compaction a fork copied in with its prefix is a replay, and one landing exactly on
    the fork's first own record is a copy too."""
    # If a recorded compaction is planted onto the fork's source at each timestamp around the
    # fork's own first record — labeled, because all six recorded compactions are main-thread,
    # and `compactions` carries no `replayed` column for the mapper to read...
    recorded = fixture_trace("compaction", COMPACTED).compactions[0]
    fork = next(
        run for run in fixture_trace("fork_origin", FORK_ORIGIN).agent_runs if run.id == FORK_RUN
    )
    assert fork.is_fork and fork.started_at is not None
    planted = replace(recorded, session_id=FORK_ORIGIN, source=FORK_RUN)
    # ...then everything at or before that instant is a copy of the parent's compaction: a
    # fork cannot compact at the instant of its own first record, and when the copied prefix
    # ends at the compaction the two share a millisecond...
    assert copied_compaction(replace(planted, timestamp=fork.started_at - MILLISECOND), fork)
    assert copied_compaction(replace(planted, timestamp=fork.started_at), fork)
    # ...only what came afterwards is the fork's own work...
    assert not copied_compaction(replace(planted, timestamp=fork.started_at + MILLISECOND), fork)
    # ...a fork that copied everything it holds records no start at all, so nothing in it can
    # be its own (planted: no recorded fork run has a null start)...
    copied_whole = replace(fork, started_at=None)
    assert copied_compaction(replace(planted, timestamp=fork.started_at), copied_whole)
    # ...and a main-thread compaction, which comes first in the ordering and so can hold no
    # copies, always ships.
    assert not copied_compaction(replace(recorded, source=MAIN), None)


def test_the_copied_prefix_rule_is_wired_into_the_mapper(fixture_trace: TraceFactory) -> None:
    """A compaction the rule calls a copy reaches no span, and one it calls live hangs off the
    run that made it."""
    # If the same planted compaction is put through the whole mapper, once on the tie and once
    # a millisecond later...
    trace = fixture_trace("fork_origin", FORK_ORIGIN)
    recorded = fixture_trace("compaction", COMPACTED).compactions[0]
    fork = next(run for run in trace.agent_runs if run.id == FORK_RUN)
    assert fork.started_at is not None
    tie = replace(recorded, session_id=FORK_ORIGIN, source=FORK_RUN, timestamp=fork.started_at)
    own = replace(tie, timestamp=fork.started_at + MILLISECOND)
    key = digest(FORK_ORIGIN, SpanKey.compaction, FORK_RUN, recorded.id)
    # ...then the tie ships nothing...
    assert all(span.span_id != key for span in session_spans(replace(trace, compactions=[tie])))
    # ...and the later one ships under the run whose transcript recorded it.
    span = one(session_spans(replace(trace, compactions=[own])), key)
    assert span.parent_span_id == digest(FORK_ORIGIN, SpanKey.agent_run, "", FORK_RUN)


def test_a_compaction_before_a_non_fork_runs_start_crashes(fixture_trace: TraceFactory) -> None:
    """A compaction older than the run that recorded it, where no copying can explain it,
    stops the run."""
    # If a compaction is planted just before a recorded non-fork run's first record — labeled,
    # since none of the canonical store's 847 non-fork-run compactions is one...
    trace = fixture_trace("spine", SPINE)
    recorded = fixture_trace("compaction", COMPACTED).compactions[0]
    run = next(row for row in trace.agent_runs if row.id == SPINE_RUN)
    assert not run.is_fork and run.started_at is not None
    planted = replace(
        recorded,
        session_id=SPINE,
        source=SPINE_RUN,
        timestamp=run.started_at - MILLISECOND,
    )
    # ...then the mapper crashes naming both clocks, because the replay rule's whole safety is
    # that only a fork can hold a copy — dropping this one silently would lose a live event.
    with pytest.raises(CompactionBeforeRunError) as raised:
        session_spans(replace(trace, compactions=[planted]))
    message = str(raised.value)
    assert SPINE in message and SPINE_RUN in message
    assert planted.timestamp.isoformat() in message
    assert run.started_at.isoformat() in message


def test_pr_links_are_events_on_the_root_carrying_only_the_number(
    fixture_trace: TraceFactory,
) -> None:
    """Each PR the session touched is an event on the root holding the bare number."""
    # If the session that linked the same pull request twice is shaped...
    trace = fixture_trace("spine", SPINE)
    assert len(trace.pr_links) == 2
    root = session_spans(trace)[0]
    # ...then both links are events rather than spans — they mark an instant, not a duration —
    # and neither carries the URL or the repository name, which name a private repo.
    assert [
        (event.name, {key.key: any_value(key.value) for key in event.attributes})
        for event in root.events
    ] == [
        ("claude_code.pr_link", {"claude_code.pr_link.number": 656}),
        ("claude_code.pr_link", {"claude_code.pr_link.number": 656}),
    ]
    assert [event.time_unix_nano for event in root.events] == [
        nanos(link.timestamp) for link in trace.pr_links
    ]


def test_gen_ai_attributes_ride_every_chat_span(fixture_trace: TraceFactory) -> None:
    """A model call ships the GenAI semconv attributes a backend groups on, and nothing else."""
    # If a recorded model call is shaped...
    trace = fixture_trace("spine", SPINE)
    call = next(row for row in trace.api_calls if row.id == SPINE_CALL)
    span = one(session_spans(trace), digest(SPINE, SpanKey.api_call, MAIN, SPINE_CALL))
    # ...then its whole attribute set is the semconv names plus our own mirrors — no prompt,
    # no reply, no thinking, and no column that held nothing.
    assert attributes(span) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "claude-fable-5",
        "gen_ai.conversation.id": SPINE,
        "gen_ai.usage.input_tokens": 2,
        "gen_ai.usage.output_tokens": 415,
        "gen_ai.response.finish_reasons": "tool_use",
        "claude_code.api_call.id": SPINE_CALL,
        "claude_code.source": MAIN,
        "claude_code.api_call.cache_read_tokens": 9768,
        "claude_code.api_call.cache_creation_tokens": 20257,
        "claude_code.api_call.effort": "high",
        # The skill that was driving when the call was made.
        "claude_code.api_call.attribution_skill": "night-run",
        "claude_code.api_call.request_id": "req_011CdmMjDTCU8h7qzXd5Chuj",
        # From our own price table, not the transcript, which records no cost.
        "claude_code.api_call.cost_usd": call.cost_usd,
        "logfire.msg": "chat claude-fable-5",
    }
