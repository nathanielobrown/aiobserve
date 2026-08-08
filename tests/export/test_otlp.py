"""Span shaping: what a recorded session becomes, and the ids that make a re-send a re-send.

No store and no HTTP here — recorded traces in, spans out. Delivery is
`test_otlp__delivery.py`; this tier is the only one that can drive a session the source
filter excludes.
"""

import hashlib
from pathlib import Path

import pytest

from aiobserve.export.duckdb import open_trace_store
from aiobserve.export.otlp import (
    CLIENT,
    INTERNAL,
    AmbiguousKeyError,
    SpanKey,
    TimelessSessionError,
    session_spans,
    span_id,
)
from aiobserve.extract.store import StoreSource
from aiobserve.model import SessionTrace
from aiobserve.pipeline import SessionSource
from tests.conftest import (
    FIXTURES,
    NO_PROJECT_SESSION,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
    TraceFactory,
    build_store,
    corpus_transcripts,
    exportable_transcripts,
)

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
    """Every span id this trace can name, mapped to a label a failure can be read from.

    Includes the `invoke_agent` ids slice 2 emits: a turn recorded inside a subagent run
    hangs off its run, so the parent this tier asserts on has to be nameable before the
    span itself exists.
    """
    session_id = trace.session.id
    named = {digest(session_id, SpanKey.session, "", session_id): "root"}
    for run in trace.agent_runs:
        named[digest(session_id, SpanKey.agent_run, "", run.id)] = f"run {run.id}"
    for turn in trace.turns:
        named[digest(session_id, SpanKey.turn, turn.source, turn.id)] = (
            f"turn {turn.source}#{turn.index}"
        )
    return named


def digest(session_id: str, kind: str, source: str, natural_id: str) -> bytes:
    """The span id the design specifies, recomputed here rather than imported.

    Digest **bytes** sliced to 8 — `hexdigest()[:8]` is also 8 bytes and would pass any
    length-only assertion while giving 32-bit ids.
    """
    return hashlib.sha256(f"{session_id}/{kind}/{source}/{natural_id}".encode()).digest()[:8]


def shape(trace: SessionTrace) -> list[tuple[str, int, str]]:
    """Each emitted span as its name, its kind, and the label of its parent."""
    named = labels(trace)
    return [
        (span.name, span.kind, named.get(span.parent_span_id, "none"))
        for span in session_spans(trace)
    ]


def test_the_spine_becomes_a_root_a_turn_and_a_chat_span_each(fixture_trace: TraceFactory) -> None:
    """A recorded session's turns and model calls become spans with the design's names,
    kinds and parents."""
    # If the deepest recorded session is shaped — four main turns and one turn inside each
    # of its two subagent runs, seven model calls between them...
    trace = fixture_trace("spine", SPINE)
    # ...then the spans are the root, one per turn and one per call, and each one hangs off
    # the row that drove it: a main-thread turn off the root, a subagent's turn off its
    # run's span, and every call off its turn.
    assert shape(trace) == [
        ("claude_code.session", INTERNAL, "none"),
        ("claude_code.turn", INTERNAL, "root"),
        ("claude_code.turn", INTERNAL, "root"),
        ("claude_code.turn", INTERNAL, "root"),
        ("claude_code.turn", INTERNAL, "root"),
        ("claude_code.turn", INTERNAL, f"run {SPINE_RUN}"),
        ("claude_code.turn", INTERNAL, f"run {SPINE_LEAF}"),
        ("chat claude-fable-5", CLIENT, "turn main#1"),
        ("chat claude-fable-5", CLIENT, "turn main#2"),
        ("chat claude-fable-5", CLIENT, "turn main#2"),
        # The placeholder reply Claude Code wrote itself keeps its recorded model name.
        ("chat <synthetic>", CLIENT, "turn main#3"),
        ("chat claude-opus-5", CLIENT, f"turn {SPINE_RUN}#0"),
        ("chat claude-opus-5", CLIENT, f"turn {SPINE_RUN}#0"),
        ("chat claude-opus-5", CLIENT, f"turn {SPINE_LEAF}#0"),
    ]


def test_ids_are_digest_bytes_not_hex_characters(fixture_trace: TraceFactory) -> None:
    """Every id is the sha256 digest of its key, sliced to the width the OTLP spec gives it."""
    # If a recorded session is shaped...
    trace = fixture_trace("spine", SPINE)
    session_id = trace.session.id
    spans = session_spans(trace)
    # ...then one trace id covers it, 16 bytes of digest — not the 16 hex *characters* of
    # `hexdigest()[:16]`, which is the same length and half the entropy...
    assert {span.trace_id for span in spans} == {hashlib.sha256(session_id.encode()).digest()[:16]}
    # ...and each span id is its own key's digest, 8 bytes, recomputed from the rows.
    expected = {digest(session_id, SpanKey.session, "", session_id)}
    expected |= {digest(session_id, SpanKey.turn, row.source, row.id) for row in trace.turns}
    expected |= {
        digest(session_id, SpanKey.api_call, row.source, row.id) for row in trace.api_calls
    }
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
    connection = open_trace_store(path, read_only=True)
    rebuilt = StoreSource(connection).extract(SessionSource(id=SPINE, files=(), fingerprint="x"))
    connection.close()
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
