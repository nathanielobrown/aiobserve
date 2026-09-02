"""Span shaping: what a recorded session becomes, and the ids that make a re-send a re-send.

No store and no HTTP here — recorded traces in, spans out. Delivery is
`test_otlp__delivery.py`; this tier is the only one that can drive a session the source
filter excludes. The per-entity arms — tool calls, runs, compactions, PR events — are
`test_otlp__shaping.py`; this file holds identity and the whole-trace invariants.
"""

import hashlib
from pathlib import Path

import pytest

from hyphae.export.duckdb import open_trace_store
from hyphae.export.otlp import (
    CLIENT,
    INTERNAL,
    AmbiguousKeyError,
    SpanKey,
    TimelessSessionError,
    session_spans,
    span_id,
)
from hyphae.extract.store import StoreSource
from hyphae.model import SessionTrace
from hyphae.pipeline import SessionSource
from tests.conftest import (
    FIXTURES,
    FORK_ORIGIN,
    NO_PROJECT_SESSION,
    NO_WAIT,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
    TraceFactory,
    build_store,
    corpus_transcripts,
    exportable_transcripts,
)
from tests.export.conftest import digest, nanos, one

# The id-key components of every table the mapper ships, as `(kind, source, natural_id)`.
# Read off the trace rather than listed, so the slash sweep covers a session's whole corpus.
SHIPPED_KEYS = {
    SpanKey.turn: lambda trace: [(row.source, row.id) for row in trace.turns],
    SpanKey.api_call: lambda trace: [(row.source, row.id) for row in trace.api_calls],
    SpanKey.tool_call: lambda trace: [(row.source, row.id) for row in trace.tool_calls],
    SpanKey.agent_run: lambda trace: [("", row.id) for row in trace.agent_runs],
    SpanKey.compaction: lambda trace: [(row.source, row.id) for row in trace.compactions],
}


def labels(trace: SessionTrace) -> dict[bytes, str]:
    """Every span id this trace can name, mapped to a label a failure can be read from."""
    session_id = trace.session.id
    named = {digest(session_id, SpanKey.session, "", session_id): "root"}
    for run in trace.agent_runs:
        named[digest(session_id, SpanKey.agent_run, "", run.id)] = f"run {run.id}"
    for turn in trace.turns:
        named[digest(session_id, SpanKey.turn, turn.source, turn.id)] = (
            f"turn {turn.source}#{turn.index}"
        )
    for call in trace.api_calls:
        named[digest(session_id, SpanKey.api_call, call.source, call.id)] = (
            f"chat {call.source}#{call.index}"
        )
    return named


def shape(trace: SessionTrace) -> list[tuple[str, int, str]]:
    """Each emitted span as its name, its kind, and the label of its parent."""
    named = labels(trace)
    return [
        (span.name, span.kind, named.get(span.parent_span_id, "none"))
        for span in session_spans(trace)
    ]


def test_the_spine_becomes_a_span_per_live_row(fixture_trace: TraceFactory) -> None:
    """A session's turns, model calls, tools and subagent runs become spans with the design's
    names, kinds and parents."""
    # If the deepest recorded session is shaped — four main turns and one turn inside each of
    # its two subagent runs, ten model calls between them, twelve tool calls of which two
    # spawned the runs...
    trace = fixture_trace("spine", SPINE)
    # ...then the spans are the root and one per live row, each hanging off the row that drove
    # it: a main-thread turn off the root, a subagent's turn off its run, every model call off
    # its turn, every tool off the call that asked for it...
    assert shape(trace) == [
        ("claude_code.session", INTERNAL, "none"),
        ("claude_code.turn", INTERNAL, "root"),
        ("claude_code.turn", INTERNAL, "root"),
        ("claude_code.turn", INTERNAL, "root"),
        ("claude_code.turn", INTERNAL, "root"),
        ("claude_code.turn", INTERNAL, f"run {SPINE_RUN}"),
        ("claude_code.turn", INTERNAL, f"run {SPINE_LEAF}"),
        ("chat claude-fable-5", CLIENT, "turn main#1"),
        ("chat claude-fable-5", CLIENT, "turn main#1"),
        ("chat claude-fable-5", CLIENT, "turn main#2"),
        ("chat claude-fable-5", CLIENT, "turn main#2"),
        ("chat claude-fable-5", CLIENT, "turn main#2"),
        ("chat claude-fable-5", CLIENT, "turn main#2"),
        # The placeholder reply Claude Code wrote itself keeps its recorded model name.
        ("chat <synthetic>", CLIENT, "turn main#3"),
        ("chat claude-opus-5", CLIENT, f"turn {SPINE_RUN}#0"),
        ("chat claude-opus-5", CLIENT, f"turn {SPINE_RUN}#0"),
        ("chat claude-opus-5", CLIENT, f"turn {SPINE_LEAF}#0"),
        ("execute_tool Bash", INTERNAL, "chat main#0"),
        ("execute_tool Read", INTERNAL, "chat main#0"),
        ("execute_tool Read", INTERNAL, "chat main#0"),
        # One reply asked for two tools at once, so both hang off the same call...
        ("execute_tool Bash", INTERNAL, "chat main#3"),
        ("execute_tool ToolSearch", INTERNAL, "chat main#3"),
        ("execute_tool PushNotification", INTERNAL, "chat main#4"),
        ("execute_tool Read", INTERNAL, "chat main#5"),
        ("execute_tool Bash", INTERNAL, f"chat {SPINE_RUN}#0"),
        # A third `Agent` call that no recorded run answers stays a plain tool call.
        ("execute_tool Agent", INTERNAL, f"chat {SPINE_RUN}#1"),
        ("execute_tool Read", INTERNAL, f"chat {SPINE_LEAF}#0"),
        # ...and the two `Agent` calls a run *did* answer become the runs themselves, each off
        # the model call that asked for it — which is what makes the two runs nest.
        ("invoke_agent claude", INTERNAL, "chat main#2"),
        ("invoke_agent Explore", INTERNAL, f"chat {SPINE_RUN}#1"),
    ]


@pytest.mark.parametrize(
    "transcript", exportable_transcripts(), ids=lambda transcript: str(transcript.stem)
)
def test_every_span_climbs_to_the_one_root(fixture_trace: TraceFactory, transcript: Path) -> None:
    """Every span hangs off a span the same trace holds, and every chain ends at one root."""
    # If each recorded session the source filter would ship is shaped...
    trace = fixture_trace(transcript.parent.name, transcript.stem)
    spans = session_spans(trace)
    parents = {span.span_id: span.parent_span_id for span in spans}
    named = labels(trace)
    # ...then exactly one span is parentless...
    roots = [span for span in spans if not span.parent_span_id]
    assert [span.name for span in roots] == ["claude_code.session"]
    # ...and walking any span's parents reaches it without meeting a parent the trace never
    # emitted, and without ever coming back around to a span the walk already passed.
    for span in spans:
        walked: list[bytes] = []
        current = span.span_id
        while current:
            label = named.get(current, "an unnamed span")
            assert current in parents, f"{span.name} climbs to {label}, which was not emitted"
            assert current not in walked, f"{span.name} climbs through {label} twice"
            walked.append(current)
            current = parents[current]
        assert walked[-1] == roots[0].span_id


def test_a_forks_copies_never_become_spans(fixture_trace: TraceFactory) -> None:
    """Rows a fork replayed from the transcript it continues are shipped by neither of them."""
    # If the fork fixture is shaped — one replayed turn, one replayed model call, four
    # replayed tool calls and one replayed compaction, all copies of rows the auditor run
    # beneath it already holds...
    trace = fixture_trace("fork_origin", FORK_ORIGIN)
    replayed = (
        [(SpanKey.turn, row.source, row.id) for row in trace.turns if row.replayed]
        + [(SpanKey.api_call, row.source, row.id) for row in trace.api_calls if row.replayed]
        + [(SpanKey.tool_call, row.source, row.id) for row in trace.tool_calls if row.replayed]
        + [(SpanKey.compaction, row.source, row.id) for row in trace.compactions if row.replayed]
    )
    assert len(replayed) == 7, "the fixture stopped carrying the copies this leaf reads"
    # ...then no span carries a copy's key, because shipping one double-counts the same event
    # in every backend aggregation — the whole reason the exclusion exists...
    shipped = {span.span_id for span in session_spans(trace)}
    assert shipped.isdisjoint({digest(FORK_ORIGIN, *key) for key in replayed})
    # ...and what is left is the root, one span per live row, and one per run — less the one
    # live `Agent` call that spawned the fork, which became its run's span instead.
    live = sum(
        1
        for rows in (trace.turns, trace.api_calls, trace.tool_calls, trace.compactions)
        for row in rows
        if not row.replayed
    )
    assert len(shipped) == 1 + len(trace.agent_runs) + live - 1


@pytest.mark.parametrize(
    ("directory", "session_id"),
    [
        ("fork_origin", FORK_ORIGIN),
        ("workflow", "8d930c77-9e60-4784-9885-6d4c226280f7"),
        ("teammate", "10d0349d-0705-4e23-aa64-5b1b97698b2e"),
    ],
)
def test_the_root_covers_work_that_outlived_the_main_transcript(
    fixture_trace: TraceFactory, directory: str, session_id: str
) -> None:
    """The root ends when its last child does, while its attributes keep the end the session
    actually recorded."""
    # If one of the three recorded sessions whose subagents ran on past the main transcript's
    # last record is shaped...
    trace = fixture_trace(directory, session_id)
    assert trace.session.ended_at is not None
    spans = session_spans(trace)
    root, children = spans[0], spans[1:]
    # ...then the root stretches to cover them, because a waterfall whose root ends before its
    # children renders broken...
    assert root.end_time_unix_nano == max(child.end_time_unix_nano for child in children)
    assert root.end_time_unix_nano > nanos(trace.session.ended_at)
    # ...and the recorded end survives as an attribute, which is what keeps the stretch from
    # becoming a lie.
    recorded = [
        attribute.value.string_value
        for attribute in root.attributes
        if attribute.key == "claude_code.session.ended_at"
    ]
    assert recorded == [trace.session.ended_at.isoformat()]


def test_a_row_with_no_duration_floors_to_a_millisecond(fixture_trace: TraceFactory) -> None:
    """A row whose recorded start and end are the same instant still spans a millisecond."""
    # If the fixtures are searched for model calls that started and ended at the same instant
    # — a lookup rather than a list, so the leaf survives a fixture change...
    found = 0
    for transcript in exportable_transcripts():
        trace = fixture_trace(transcript.parent.name, transcript.stem)
        instant = [
            call
            for call in trace.api_calls
            if not call.replayed and call.ended_at <= call.started_at
        ]
        if not instant:
            continue
        spans = session_spans(trace)
        # ...then each one's span still has a positive width, because a zero-width span
        # renders as an invisible sliver no waterfall can show.
        for call in instant:
            key = digest(trace.session.id, SpanKey.api_call, call.source, call.id)
            span = one(spans, key)
            assert span.end_time_unix_nano - span.start_time_unix_nano == 1_000_000
            found += 1
    assert found, "no recorded row has a zero-length duration to floor"


def test_ids_are_digest_bytes_not_hex_characters(fixture_trace: TraceFactory) -> None:
    """Every id is the sha256 digest of its key, sliced to the width the OTLP spec gives it."""
    # If a recorded session is shaped...
    trace = fixture_trace("spine", SPINE)
    session_id = trace.session.id
    spans = session_spans(trace)
    # ...then one trace id covers it, 16 bytes of digest — not the 16 hex *characters* of
    # `hexdigest()[:16]`, which is the same length and half the entropy...
    assert {span.trace_id for span in spans} == {hashlib.sha256(session_id.encode()).digest()[:16]}
    # ...and each span id is its own key's digest, 8 bytes, recomputed from the rows. The two
    # tool calls a run answered are keyed as runs, never as the tool call they replace.
    matched = {run.tool_use_id for run in trace.agent_runs}
    expected = {digest(session_id, SpanKey.session, "", session_id)}
    expected |= {digest(session_id, SpanKey.turn, row.source, row.id) for row in trace.turns}
    expected |= {
        digest(session_id, SpanKey.api_call, row.source, row.id) for row in trace.api_calls
    }
    expected |= {
        digest(session_id, SpanKey.tool_call, row.source, row.id)
        for row in trace.tool_calls
        if row.id not in matched
    }
    expected |= {digest(session_id, SpanKey.agent_run, "", row.id) for row in trace.agent_runs}
    assert {span.span_id for span in spans} == expected
    assert {len(span.span_id) for span in spans} == {8}


def test_ids_hold_still_across_a_re_export(fixture_trace: TraceFactory, tmp_path: Path) -> None:
    """Shaping the same session again — even from a store rebuilt from scratch — gives the
    same ids."""
    # If a recorded session is shaped twice from the transcript, and once more from rows
    # written into a store and read back...
    trace = fixture_trace("spine", SPINE)
    path = tmp_path / "rebuilt.duckdb"
    build_store(path, [FIXTURES / "spine" / f"{SPINE}.jsonl"])
    with open_trace_store(path, read_only=True, wait=NO_WAIT) as connection:
        rebuilt = StoreSource(connection).extract(SessionSource(id=SPINE, fingerprint="x"))
    # ...then all three passes name the same spans: at-least-once delivery is only a
    # re-send while the ids stay put, and an id that moves lands a second unrelated trace.
    first = {span.span_id for span in session_spans(trace)}
    assert first == {span.span_id for span in session_spans(fixture_trace("spine", SPINE))}
    assert first == {span.span_id for span in session_spans(rebuilt)}


@pytest.mark.parametrize(
    "transcript", exportable_transcripts(), ids=lambda transcript: str(transcript.stem)
)
def test_no_two_spans_of_a_session_share_an_id(
    fixture_trace: TraceFactory, transcript: Path
) -> None:
    """Within one session's trace, every span id is distinct."""
    spans = session_spans(fixture_trace(transcript.parent.name, transcript.stem))
    assert len({span.span_id for span in spans}) == len(spans)


def test_a_session_with_no_recorded_time_crashes(fixture_trace: TraceFactory) -> None:
    """A session the source filter would exclude cannot be shaped: its root has no clock."""
    # If the one recorded session holding no timestamps at all is handed to the mapper —
    # which `refresh()` never does, since the source filter refuses to place it...
    trace = fixture_trace("fork_byref", NO_PROJECT_SESSION)
    # ...then it crashes rather than inventing a root span's start and end.
    with pytest.raises(TimelessSessionError, match=NO_PROJECT_SESSION):
        session_spans(trace)


def test_a_slash_in_a_key_component_crashes(fixture_trace: TraceFactory) -> None:
    """An id component holding the key's delimiter refuses to hash rather than collide."""
    # If a recorded agentId is given a slash — invented, since no shipped row across the
    # canonical store holds one, and `raw_records`'s `wf_<id>/journal` sources are one
    # table away...
    run = fixture_trace("spine", SPINE).agent_runs[0]
    planted = f"{run.id[:8]}/{run.id[8:]}"
    # ...then the id function crashes naming the component, because `a/b` and `a` + `b`
    # would otherwise hash to one span id and silently become one span.
    with pytest.raises(AmbiguousKeyError) as raised:
        span_id(SPINE, SpanKey.agent_run, "", planted)
    assert planted in str(raised.value)


@pytest.mark.parametrize(
    "transcript", corpus_transcripts(), ids=lambda transcript: str(transcript.stem)
)
def test_no_recorded_key_component_holds_the_delimiter(
    fixture_trace: TraceFactory, transcript: Path
) -> None:
    """No source or natural id in any shipped table contains the `/` the id keys join on."""
    trace = fixture_trace(transcript.parent.name, transcript.stem)
    held = {
        component
        for keys in SHIPPED_KEYS.values()
        for key in keys(trace)
        for component in key
        if "/" in component
    }
    assert held == set()
    assert "/" not in trace.session.id
