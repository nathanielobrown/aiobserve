"""What a session becomes on the wire: the span shaping, and the census that counts it.

One trace per session, ids derived from the store's own composite keys, so re-sending a
session lands on the spans it landed on last time. That is the whole delivery promise:
at-least-once with stable ids. An append-only backend never dedupes, so a failed run
re-sends the session whole rather than diffing what got through
(`plans/otlp-export/design.md`).

Only structure ships by default. Transcript text is untrusted and POSTing it to a third
party publishes it, so prompts, model text, tool arguments and results stay home.
"""

import datetime as dt
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from hyphae.model import (
    MAIN_SOURCE,
    AgentRun,
    ApiCall,
    Compaction,
    PrLink,
    Session,
    SessionTrace,
    ToolCall,
    Turn,
)
from hyphae.pipeline import Extractor, SessionSource

# The span-shaping version. A row in `otlp_delivery` recorded under an older one is treated
# as undelivered, so a shaping change re-sends the corpus the way an extractor upgrade
# re-extracts it. Bump it whenever what a session becomes changes.
MAPPER_VERSION = "1"

# The two OTLP span kinds slice 1 emits. Typed, so a bare `int` cannot reach a proto field.
SpanKind = trace_pb2.Span.SpanKind.ValueType
INTERNAL: SpanKind = trace_pb2.Span.SpanKind.SPAN_KIND_INTERNAL
CLIENT: SpanKind = trace_pb2.Span.SpanKind.SPAN_KIND_CLIENT

# The span name every compaction gets, which is also how a census recognizes one.
COMPACTION_SPAN = "claude_code.compaction"

# The delimiter the id keys join on, and the invariant every component must hold to.
DELIMITER = "/"

# A span with no positive duration renders as an invisible sliver, and the store holds
# plenty (a turn whose only record is its prompt). One millisecond is the floor.
MINIMUM_DURATION = dt.timedelta(milliseconds=1)

# How much of an opted-in text field ships. Attributes are an ingest and a context cost, and
# a whole tool result can be megabytes. Truncation is not redaction — a credential fits in
# 200 characters — which is why text is opt-in rather than truncated-by-default.
DEFAULT_MAX_CHARS = 500

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


class SpanKey(StrEnum):
    """The `kind` slot of a span id key — the store table the span came from.

    Its values are part of every span id, so renaming one re-ids every span of that kind
    and re-sends the corpus as a second copy. Change one only with `MAPPER_VERSION`.
    """

    session = "session"
    turn = "turn"
    api_call = "api_call"
    tool_call = "tool_call"
    agent_run = "agent_run"
    compaction = "compaction"


class AmbiguousKeyError(Exception):
    """An id-key component holds the delimiter, so two different keys would hash as one."""


def trace_id(session_id: str) -> bytes:
    """The 16-byte trace id of a session. Digest bytes, never hex characters."""
    return hashlib.sha256(session_id.encode()).digest()[:16]


def span_id(session_id: str, kind: SpanKey, source: str, natural_id: str) -> bytes:
    """The 8-byte span id of one row, from the composite key the store already holds.

    `source` is empty for rows keyed without one — an agent run, or the session itself.
    Crashes when a component holds `/`: no shipped row across the canonical store does, and
    absorbing one would silently collapse two rows into one span.
    """
    components = (session_id, str(kind), source, natural_id)
    for component in components:
        if DELIMITER in component:
            raise AmbiguousKeyError(
                f"{component!r} holds {DELIMITER!r}, which the span-id key joins on. "
                f"An id that carries the delimiter is schema drift we need to see."
            )
    return hashlib.sha256(DELIMITER.join(components).encode()).digest()[:8]


@dataclass(frozen=True)
class TextPolicy:
    """Whether transcript-derived text ships, and how much of each field.

    Text is untrusted and POSTing it to a third party publishes it, so the default policy
    sends none of it. `--include-text` swaps in an including one.
    """

    include: bool
    # Characters kept per field, applied only when `include` is set.
    max_chars: int


METADATA_ONLY = TextPolicy(include=False, max_chars=DEFAULT_MAX_CHARS)


def session_spans(trace: SessionTrace, text: TextPolicy = METADATA_ONLY) -> list[trace_pb2.Span]:
    """Every span one session becomes, root first.

    Replayed rows emit nothing: a fork's copy of its parent's transcript would double-count
    in every backend aggregation. A tool call that started a subagent becomes that subagent's
    span rather than a span of its own.
    """
    session = trace.session
    if session.started_at is None or session.ended_at is None:
        raise TimelessSessionError(
            f"Session {session.id} records no timestamps but reached the mapper. The source "
            f"filter excludes the sessions that hold none, so this is schema drift."
        )
    live = trace.live()
    # Every turn the trace holds, copies included: a call under a replayed turn needs to see
    # the copy to tell it from a turn its session never recorded, which crashes.
    turns = {(turn.source, turn.id): turn for turn in trace.turns}
    runs = {run.id: run for run in live.agent_runs}
    # The live tool call each run named as its launch, if this trace holds one. Matching a
    # fork's copy would collapse a span that never ships.
    launched = {run.tool_use_id for run in live.agent_runs if run.tool_use_id is not None}
    spawns = {call.id: call for call in live.tool_calls if call.id in launched}
    children = [
        *(_turn_span(session, turn, text) for turn in live.turns),
        *(_chat_span(session, call, turns, text) for call in live.api_calls),
        *(_tool_span(session, call, text) for call in live.tool_calls if call.id not in spawns),
        *(
            _run_span(session, run, spawns.get(run.tool_use_id or ""), runs, text)
            for run in live.agent_runs
        ),
        *(_compaction_span(session, compaction) for compaction in live.compactions),
    ]
    return [_root_span(trace, children, text), *children]


def session_resource(session: Session, service_name: str | None) -> resource_pb2.Resource:
    """What every span of a session is attributed to.

    `service.name` is the project directory's name, which is what routes a Honeycomb dataset
    per project; `--service-name` overrides it for a one-off run.
    """
    if service_name is None and session.project_dir is None:
        raise PlacelessSessionError(
            f"Session {session.id} records no project_dir, so it has no service name. "
            f"The source filter excludes those, so this is schema drift."
        )
    service = service_name or Path(str(session.project_dir)).name
    return resource_pb2.Resource(
        attributes=_attributes(
            {
                "service.name": service,
                "hyphae.exporter.version": MAPPER_VERSION,
                # These spans were shipped from the store after the fact, not emitted live by
                # the agent — a distinction a backend query cannot recover.
                "hyphae.telemetry.source": "store-export",
            }
        )
    )


@dataclass(frozen=True)
class Census:
    """What a run would ship, counted by shaping every session and sending nothing."""

    sessions: int
    spans: int
    # Compactions shipped: what `live_compactions` holds, since the mapper and the view
    # both read the extractor's `replayed` flag.
    compactions: int


def census(traces: Iterable[SessionTrace], text: TextPolicy = METADATA_ONLY) -> Census:
    """Count what a send would put on the wire, without sending it.

    Shapes each session exactly as `export()` does, so the total is the mapper's own answer
    rather than a SQL approximation of it.
    """
    sessions = spans = compactions = 0
    for trace in traces:
        shaped = session_spans(trace, text)
        sessions += 1
        spans += len(shaped)
        compactions += sum(1 for span in shaped if span.name == COMPACTION_SPAN)
    return Census(sessions=sessions, spans=spans, compactions=compactions)


def census_project[SourceT: SessionSource](
    project: Path, *, extractor: Extractor[SourceT], text: TextPolicy = METADATA_ONLY
) -> Census:
    """Count what a run against `project` would ship, shaping every session and sending none.

    The dry run's half of `pipeline.refresh`: the same extractor, driven the same way, with
    no fingerprint diff in front of it — a census counts the whole selection, not the part
    that moved since the last send.
    """
    return census((extractor.extract(source) for source in extractor.sessions(project)), text)


class TimelessSessionError(Exception):
    """A session with no recorded times reached the mapper, which cannot time its root."""


class PlacelessSessionError(Exception):
    """A session with no project directory reached the exporter, which cannot name a service."""


def _root_span(
    trace: SessionTrace, children: list[trace_pb2.Span], text: TextPolicy
) -> trace_pb2.Span:
    """The session's root span, stretched to cover work that outlived the main transcript.

    `Session.ended_at` reads the main transcript only, and a subagent can run past it — a
    waterfall whose root ends before its children renders broken. The recorded value stays
    in the attributes.
    """
    session = trace.session
    # Narrowing for the type checker: an extracted session carries both ends.
    assert session.started_at is not None and session.ended_at is not None  # noqa: S101
    ended_at = max(
        [session.ended_at, *(_from_nanos(child.end_time_unix_nano) for child in children)]
    )
    return _span(
        session_id=session.id,
        span=span_id(session.id, SpanKey.session, "", session.id),
        parent=b"",
        name="claude_code.session",
        kind=INTERNAL,
        started_at=session.started_at,
        ended_at=ended_at,
        attributes={
            "gen_ai.conversation.id": session.id,
            "claude_code.session.id": session.id,
            "claude_code.session.version": session.version,
            "claude_code.session.entrypoint": session.entrypoint,
            "claude_code.session.project_dir": session.project_dir,
            "claude_code.session.git_branch": session.git_branch,
            # What Claude Code reported working, well below the span's own wall time.
            "claude_code.session.active_ms": session.active_ms,
            # The recorded end, which the span's own end may have stretched past.
            "claude_code.session.ended_at": session.ended_at.isoformat(),
            "hyphae.extractor": trace.extractor,
            "hyphae.extractor.version": trace.extractor_version,
            # Model-written from the conversation, so it counts as transcript text.
            "claude_code.session.title": _text(text, session.title),
            "claude_code.session.agent_name": _text(text, session.agent_name),
            # Ids, never the title: `ai-title` is model-written from the conversation.
            "logfire.msg": f"session {session.id}",
        },
        events=[_pr_event(link, text) for link in trace.pr_links],
    )


def _pr_event(link: PrLink, text: TextPolicy) -> trace_pb2.Span.Event:
    """One pull request the session touched — an instant on the root, not a span."""
    return trace_pb2.Span.Event(
        time_unix_nano=_nanos(link.timestamp),
        name="claude_code.pr_link",
        attributes=_attributes(
            {
                "claude_code.pr_link.number": link.pr_number,
                # Both name a repository that may be private, so they stay home by default.
                "claude_code.pr_link.url": _text(text, link.pr_url),
                "claude_code.pr_link.repository": _text(text, link.pr_repository),
            }
        ),
    )


def _turn_span(session: Session, turn: Turn, text: TextPolicy) -> trace_pb2.Span:
    """One prompt and the work it drove. Under the root on `main`, under its run otherwise."""
    return _span(
        session_id=session.id,
        span=span_id(session.id, SpanKey.turn, turn.source, turn.id),
        parent=_source_parent(session.id, turn.source),
        name="claude_code.turn",
        kind=INTERNAL,
        started_at=turn.started_at,
        ended_at=turn.ended_at,
        attributes={
            "claude_code.turn.id": turn.id,
            "claude_code.turn.index": turn.index,
            "claude_code.source": turn.source,
            # The command's name only — its arguments are user-typed text.
            "claude_code.turn.command_name": turn.command_name,
            "claude_code.turn.prompt": _text(text, turn.prompt),
            "claude_code.turn.command_args": _text(text, turn.command_args),
            "logfire.msg": f"turn {turn.id}",
        },
    )


def _chat_span(
    session: Session, call: ApiCall, turns: dict[tuple[str, str], Turn], text: TextPolicy
) -> trace_pb2.Span:
    """One model response, under the turn that drove it."""
    return _span(
        session_id=session.id,
        span=span_id(session.id, SpanKey.api_call, call.source, call.id),
        parent=_chat_parent(session.id, call, turns),
        name=f"chat {call.model}",
        kind=CLIENT,
        started_at=call.started_at,
        ended_at=call.ended_at,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": call.model,
            "gen_ai.conversation.id": session.id,
            "gen_ai.usage.input_tokens": call.input_tokens,
            "gen_ai.usage.output_tokens": call.output_tokens,
            "gen_ai.response.finish_reasons": call.stop_reason,
            "claude_code.api_call.id": call.id,
            "claude_code.source": call.source,
            "claude_code.api_call.cache_read_tokens": call.cache_read_tokens,
            "claude_code.api_call.cache_creation_tokens": call.cache_creation_tokens,
            "claude_code.api_call.effort": call.effort,
            # The model asked for first, when the request was retried on another.
            "claude_code.api_call.fallback_from": call.fallback_from,
            "claude_code.api_call.attribution_skill": call.attribution_skill,
            "claude_code.api_call.request_id": call.request_id,
            # From our own price table, not the transcript; absent when it prices no model.
            "claude_code.api_call.cost_usd": call.cost_usd,
            # A placeholder reply Claude Code wrote itself: no tokens, no cost, not a call.
            "hyphae.synthetic": call.synthetic or None,
            "claude_code.api_call.text": _text(text, call.text),
            "claude_code.api_call.thinking": _text(text, call.thinking),
            "logfire.msg": f"chat {call.model}",
        },
    )


def _tool_span(session: Session, call: ToolCall, text: TextPolicy) -> trace_pb2.Span:
    """One tool the model asked for, under the model call that asked."""
    return _span(
        session_id=session.id,
        span=span_id(session.id, SpanKey.tool_call, call.source, call.id),
        parent=span_id(session.id, SpanKey.api_call, call.source, call.api_call_id),
        name=f"execute_tool {call.name}",
        kind=INTERNAL,
        started_at=call.started_at,
        # A call the session never saw finish ends where it started rather than running to
        # the end of the transcript; `_span` floors it to the minimum.
        ended_at=call.ended_at if call.ended_at is not None else call.started_at,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": call.name,
            "gen_ai.conversation.id": session.id,
            "claude_code.tool_call.id": call.id,
            "claude_code.source": call.source,
            "claude_code.tool_call.index": call.index,
            "claude_code.api_call.id": call.api_call_id,
            # Anthropic ran it; no local transcript records the work it did.
            "claude_code.tool_call.server_side": call.server_side or None,
            # The start is the batch's, not this call's — flagged, never invented away.
            "claude_code.tool_call.duration_synthetic": call.duration_synthetic or None,
            "claude_code.tool_call.is_error": call.is_error or None,
            # The archived file the output went to, which stays local.
            "claude_code.tool_call.offload_file": call.offload_file,
            "hyphae.incomplete": call.ended_at is None or None,
            "claude_code.tool_call.input": _text(text, call.input),
            "claude_code.tool_call.result": _text(text, call.result),
            "logfire.msg": f"execute_tool {call.name}",
        },
    )


def _run_span(
    session: Session,
    run: AgentRun,
    spawn: ToolCall | None,
    runs: dict[str, AgentRun],
    text: TextPolicy,
) -> trace_pb2.Span:
    """One subagent, timed to its own work rather than to the launch acknowledgement.

    The id comes from the run's own key, never the tool call's: children in the run's
    transcript know only their `source`, and a run that flips between matched and orphan
    across extracts must keep the span id it already shipped under.
    """
    if run.started_at is None or run.ended_at is None:
        raise TimelessRunError(
            f"Agent run {run.id} of session {session.id} records no timestamps, so its span "
            f"cannot be timed. The model permits it and no recorded run does it."
        )
    parent, orphan = _run_parent(session.id, run, spawn, runs)
    return _span(
        session_id=session.id,
        span=span_id(session.id, SpanKey.agent_run, "", run.id),
        parent=parent,
        name=f"invoke_agent {run.agent_type}",
        kind=INTERNAL,
        started_at=run.started_at,
        ended_at=run.ended_at,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": run.agent_type,
            "gen_ai.conversation.id": session.id,
            "claude_code.agent_run.id": run.id,
            "claude_code.agent_run.parent_agent_id": run.parent_agent_id,
            # Kept even when it placed nothing, so an orphan that named a call and one that
            # named none are told apart in the data rather than by a second flag.
            "claude_code.agent_run.tool_use_id": run.tool_use_id,
            "claude_code.agent_run.model": run.model,
            "claude_code.agent_run.workflow_id": run.workflow_id,
            "claude_code.agent_run.spawn_depth": run.spawn_depth,
            # A continuation of another run, carrying a copy of its transcript's prefix.
            "claude_code.agent_run.is_fork": run.is_fork or None,
            "claude_code.agent_run.brief": _text(text, run.brief),
            # No tool call in this trace placed it, so it hangs off the root.
            "hyphae.orphan": orphan or None,
            "logfire.msg": f"invoke_agent {run.agent_type}",
        },
    )


class TimelessRunError(Exception):
    """A subagent run with no recorded times, whose span therefore cannot be placed in time."""


def _run_parent(
    session_id: str, run: AgentRun, spawn: ToolCall | None, runs: dict[str, AgentRun]
) -> tuple[bytes, bool]:
    """Where a run's span hangs, and whether it is an orphan.

    A fork's spawning call is copied into the fork's own transcript, so hanging the run off
    that call's span would make the run its own ancestor. Those fall back to the lineage the
    run already records, which is the one place above it that cannot be inside it.
    """
    if spawn is None:
        return span_id(session_id, SpanKey.session, "", session_id), True
    if _inside(run.id, spawn.source, runs):
        return _source_parent(session_id, run.parent_agent_id or MAIN_SOURCE), False
    return span_id(session_id, SpanKey.api_call, spawn.source, spawn.api_call_id), False


def _inside(run_id: str, source: str, runs: dict[str, AgentRun]) -> bool:
    """Whether a source is a run itself or something that run spawned."""
    walked: set[str] = set()
    while source != MAIN_SOURCE and source not in walked:
        if source == run_id:
            return True
        walked.add(source)
        run = runs.get(source)
        if run is None:
            return False
        source = run.parent_agent_id or MAIN_SOURCE
    return False


def _compaction_span(session: Session, compaction: Compaction) -> trace_pb2.Span:
    """One point where Claude Code summarised the conversation, as long as that took."""
    return _span(
        session_id=session.id,
        span=span_id(session.id, SpanKey.compaction, compaction.source, compaction.id),
        parent=_source_parent(session.id, compaction.source),
        name=COMPACTION_SPAN,
        kind=INTERNAL,
        started_at=compaction.timestamp,
        ended_at=compaction.timestamp + dt.timedelta(milliseconds=compaction.duration_ms),
        attributes={
            "claude_code.compaction.id": compaction.id,
            "claude_code.source": compaction.source,
            "claude_code.compaction.trigger": compaction.trigger,
            # Either side of the summary: where the session's account of itself gets lossy.
            "claude_code.compaction.pre_tokens": compaction.pre_tokens,
            "claude_code.compaction.post_tokens": compaction.post_tokens,
            "logfire.msg": f"compaction {compaction.trigger}",
        },
    )


def _text(policy: TextPolicy, value: str | None) -> str | None:
    """One transcript-derived string, truncated — or nothing at all, which is the default."""
    if not policy.include or value is None:
        return None
    return value[: policy.max_chars]


def _source_parent(session_id: str, source: str) -> bytes:
    """What a row's `source` hangs off: the root on the main thread, its run inside one."""
    if source == MAIN_SOURCE:
        return span_id(session_id, SpanKey.session, "", session_id)
    return span_id(session_id, SpanKey.agent_run, "", source)


def _chat_parent(session_id: str, call: ApiCall, turns: dict[tuple[str, str], Turn]) -> bytes:
    """The span a model call hangs off.

    Its turn, except where that turn emits no span: a by-reference fork opens mid-conversation
    with no turn at all, and a fork that replayed its parent's turn holds a live call under a
    turn this trace never ships. Both fall back to the call's own source, which the call knows
    without a join.
    """
    if call.turn_id is None:
        return _source_parent(session_id, call.source)
    turn = turns.get((call.source, call.turn_id))
    if turn is None:
        raise UnparentedCallError(
            f"Api call {call.id} of session {session_id} names turn {call.turn_id} in source "
            f"{call.source}, which the trace does not hold."
        )
    if turn.replayed:
        return _source_parent(session_id, call.source)
    return span_id(session_id, SpanKey.turn, turn.source, turn.id)


class UnparentedCallError(Exception):
    """A model call names a turn its own trace does not hold — a shape we need to see."""


def _span(
    *,
    session_id: str,
    span: bytes,
    parent: bytes,
    name: str,
    kind: SpanKind,
    started_at: dt.datetime,
    ended_at: dt.datetime,
    attributes: dict[str, Any],
    events: list[trace_pb2.Span.Event] | None = None,
) -> trace_pb2.Span:
    """One span, with its duration floored and its empty attributes dropped."""
    return trace_pb2.Span(
        trace_id=trace_id(session_id),
        span_id=span,
        parent_span_id=parent,
        name=name,
        kind=kind,
        start_time_unix_nano=_nanos(started_at),
        end_time_unix_nano=_nanos(max(ended_at, started_at + MINIMUM_DURATION)),
        attributes=_attributes(attributes),
        events=events or [],
    )


def _attributes(values: dict[str, Any]) -> list[common_pb2.KeyValue]:
    """The non-empty entries as OTLP attributes.

    None is dropped rather than sent: OTLP has no null, and an absent attribute is how the
    wire says a column held nothing.
    """
    return [
        common_pb2.KeyValue(key=key, value=_any_value(value))
        for key, value in values.items()
        if value is not None
    ]


def _any_value(value: Any) -> common_pb2.AnyValue:
    """One attribute value, typed. An unmapped Python type is a mapper bug, so it crashes."""
    match value:
        case bool():
            return common_pb2.AnyValue(bool_value=value)
        case int():
            return common_pb2.AnyValue(int_value=value)
        case float():
            return common_pb2.AnyValue(double_value=value)
        case str():
            return common_pb2.AnyValue(string_value=value)
        case _:
            raise TypeError(f"{type(value).__name__} is not an OTLP attribute type")


def _nanos(value: dt.datetime) -> int:
    """Nanoseconds since the epoch, computed in integers — a float loses the microseconds."""
    delta = value - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _from_nanos(value: int) -> dt.datetime:
    return _EPOCH + dt.timedelta(microseconds=value // 1_000)
