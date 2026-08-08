"""Shipping the store's sessions to an OTLP backend: span shaping and checked delivery.

One trace per session, ids derived from the store's own composite keys, so re-sending a
session lands on the spans it landed on last time. That is the whole delivery promise:
at-least-once with stable ids. An append-only backend never dedupes, so a failed run
re-sends the session whole rather than diffing what got through
(`plans/otlp-export/design.md`).

Only structure ships by default. Transcript text is untrusted and POSTing it to a third
party publishes it, so prompts, model text, tool arguments and results stay home.
"""

import datetime as dt
import gzip
import hashlib
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any

import duckdb
import httpx
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from aiobserve.model import (
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

# The span-shaping version. A row in `otlp_delivery` recorded under an older one is treated
# as undelivered, so a shaping change re-sends the corpus the way an extractor upgrade
# re-extracts it. Bump it whenever what a session becomes changes.
MAPPER_VERSION = "1"

# The two OTLP span kinds slice 1 emits. Typed, so a bare `int` cannot reach a proto field.
SpanKind = trace_pb2.Span.SpanKind.ValueType
INTERNAL: SpanKind = trace_pb2.Span.SpanKind.SPAN_KIND_INTERNAL
CLIENT: SpanKind = trace_pb2.Span.SpanKind.SPAN_KIND_CLIENT

# Spans per POST. The biggest canonical session is ~29K spans, so a backfill of it is ~15
# requests. A parameter rather than a constant so tests can bind it down and cross a real
# batch boundary on a recorded session.
DEFAULT_BATCH_SPANS = 2_000

# Spans per second, across a whole run. Prior art measured ~40% silent server-side loss with
# no limiter and none at this rate (issue #6); it puts the full corpus at ~16 minutes, which
# is a backfill's price for not losing two spans in five.
DEFAULT_RATE = 300.0

# Per request. A backend that has not answered by then is down, not slow.
DEFAULT_TIMEOUT = 30.0

# A 429 or a 5xx is the backend asking us to come back; anything else is our bug. Attempts
# include the first, and the wait doubles between them unless `Retry-After` says otherwise.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0

# What the environment holds for the base-case backend: any OTLP/HTTP endpoint.
GENERIC = "generic"
ENDPOINT_ENV = "OTLP_ENDPOINT"
HEADERS_ENV = "OTLP_HEADERS"

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
    in every backend aggregation. Compactions carry no such flag, so `copied_compaction`
    derives one. A tool call that started a subagent becomes that subagent's span rather than
    a span of its own.
    """
    session = trace.session
    if session.started_at is None or session.ended_at is None:
        raise TimelessSessionError(
            f"Session {session.id} records no timestamps but reached the mapper. The source "
            f"filter excludes the sessions that hold none, so this is schema drift."
        )
    turns = {(turn.source, turn.id): turn for turn in trace.turns}
    runs = {run.id: run for run in trace.agent_runs}
    live_tools = [call for call in trace.tool_calls if not call.replayed]
    # The live tool call each run named as its launch, if this trace holds one. Replayed
    # copies are excluded first: matching one would collapse a span that never ships.
    launched = {run.tool_use_id for run in trace.agent_runs if run.tool_use_id is not None}
    spawns = {call.id: call for call in live_tools if call.id in launched}
    children = [
        *(_turn_span(session, turn, text) for turn in trace.turns if not turn.replayed),
        *(_chat_span(session, call, turns, text) for call in trace.api_calls if not call.replayed),
        *(_tool_span(session, call, text) for call in live_tools if call.id not in spawns),
        *(
            _run_span(session, run, spawns.get(run.tool_use_id or ""), runs, text)
            for run in trace.agent_runs
        ),
        *(
            _compaction_span(session, compaction)
            for compaction in trace.compactions
            if not copied_compaction(compaction, runs.get(compaction.source))
        ),
    ]
    return [_root_span(trace, children, text), *children]


class TimelessSessionError(Exception):
    """A session with no recorded times reached the mapper, which cannot time its root."""


def _root_span(
    trace: SessionTrace, children: list[trace_pb2.Span], text: TextPolicy
) -> trace_pb2.Span:
    """The session's root span, stretched to cover work that outlived the main transcript.

    `Session.ended_at` reads the main transcript only, and a subagent can run past it — a
    waterfall whose root ends before its children renders broken. The recorded value stays
    in the attributes.
    """
    session = trace.session
    assert session.started_at is not None and session.ended_at is not None
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
            "aiobserve.extractor": trace.extractor,
            "aiobserve.extractor.version": trace.extractor_version,
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
            "aiobserve.synthetic": call.synthetic or None,
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
            "aiobserve.incomplete": call.ended_at is None or None,
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
            "claude_code.agent_run.description": _text(text, run.description),
            # No tool call in this trace placed it, so it hangs off the root.
            "aiobserve.orphan": orphan or None,
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


def copied_compaction(compaction: Compaction, run: AgentRun | None) -> bool:
    """Whether a compaction is one a fork copied in with its prefix, and so ships no span.

    `compactions` carries no `replayed` column, so the rule reads the same prefix shape the
    extractor's flags read: `AgentRun.started_at` is by contract the first record no earlier
    transcript already held, so anything in a fork at or before it came from the parent. A
    tie is a copy — a fork cannot compact at the instant of its own first record, and when
    the copied prefix ends at the compaction the two share a millisecond.

    `run` is the run a compaction's `source` names, or None on the main thread, which comes
    first in the extractor's ordering and can hold no copies.
    """
    if run is None:
        return False
    if not run.is_fork:
        if run.started_at is not None and compaction.timestamp < run.started_at:
            raise CompactionBeforeRunError(
                f"Compaction {compaction.id} of session {compaction.session_id} is timestamped "
                f"{compaction.timestamp.isoformat()}, before its non-fork run "
                f"{compaction.source} started at {run.started_at.isoformat()}. Only a fork can "
                f"hold a copy, so this is schema drift."
            )
        return False
    # It copied everything it holds, so nothing in it is its own.
    if run.started_at is None:
        return True
    return compaction.timestamp <= run.started_at


class CompactionBeforeRunError(Exception):
    """A compaction predates the run that recorded it, where no copied prefix explains it."""


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


# Lives in the trace store beside the fingerprints it compares against, created on first
# export like the enrichment tables — table existence, no schema-version bump. Deliberately
# outside `TABLES`: swept into the replace transaction it would be erased by every
# re-extract, and every later run would ship the corpus again as duplicates.
_DELIVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS otlp_delivery (
    session_id VARCHAR NOT NULL,
    backend VARCHAR NOT NULL,
    -- The `extract_state` fingerprint that was shipped, and the mapper that shaped it.
    -- Either one moving makes the session undelivered again.
    fingerprint VARCHAR NOT NULL,
    mapper_version VARCHAR NOT NULL,
    -- The local manifest: what a future `--verify` counts against the backend.
    spans_sent BIGINT NOT NULL,
    delivered_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_id, backend)
);
"""


@dataclass(frozen=True)
class Backend:
    """Where spans go, and the name its delivery rows are recorded under.

    `headers` carries the key. It is never logged and never interpolated into an error —
    the crash paths are exactly where one gets published by accident.
    """

    name: str
    endpoint: str
    headers: dict[str, str] = field(default_factory=dict)


class ConfigurationError(Exception):
    """The environment does not say where to ship. Raised before anything is read."""


class DeliveryError(Exception):
    """A batch never landed: the backend refused it, or stopped answering."""


class RejectedSpansError(Exception):
    """The backend took the request and kept only part of it — a mapper bug we need to see."""


@dataclass(frozen=True)
class BackendSpec:
    """A named backend: where it takes spans, and how its key travels.

    Endpoints and header names verified against prior art
    (`/Users/nob/repos/mac_settings/claude-otel/import_transcripts.py:114`).
    """

    endpoint: str
    # The environment variable holding the key, and the header it goes in *bare* — Logfire
    # refuses an `authorization: Bearer …`, and the failure is a 401 an hour into a backfill.
    key_env: str
    header: str


BACKENDS: dict[str, BackendSpec] = {
    "honeycomb": BackendSpec(
        endpoint="https://api.honeycomb.io/v1/traces",
        key_env="HONEYCOMB_API_KEY",
        header="x-honeycomb-team",
    ),
    "logfire": BackendSpec(
        endpoint="https://logfire-us.pydantic.dev/v1/traces",
        key_env="LOGFIRE_API_KEY",
        header="authorization",
    ),
}

# What `--backend` takes. Discovered from the registry, so adding an entry is one edit.
BACKEND_NAMES = (GENERIC, *BACKENDS)


def named_backend(name: str, environ: Mapping[str, str]) -> Backend:
    """Where `--backend <name>` ships, resolved from the environment.

    Validated here rather than at the first POST, so a misconfigured run refuses before it
    reads a session. `OTLP_ENDPOINT` overrides a named backend's endpoint — the way a run
    reaches a collector standing in front of the real thing.
    """
    if name == GENERIC:
        return generic_backend(environ)
    spec = BACKENDS[name]
    key = environ.get(spec.key_env, "").strip()
    if not key:
        raise ConfigurationError(
            f"{spec.key_env} is unset or empty. Put it in .env or the environment"
        )
    return Backend(
        name=name,
        endpoint=environ.get(ENDPOINT_ENV, "").strip() or spec.endpoint,
        headers={spec.header: key},
    )


def generic_backend(environ: Mapping[str, str]) -> Backend:
    """The base-case backend: any OTLP/HTTP endpoint, with optional headers.

    Validated here rather than at the first POST, so a misconfigured run refuses before it
    reads a session. `OTLP_HEADERS` is `name=value` pairs separated by commas.
    """
    endpoint = environ.get(ENDPOINT_ENV, "").strip()
    if not endpoint:
        raise ConfigurationError(
            f"{ENDPOINT_ENV} is unset or empty. Put it in .env or the environment"
        )
    return Backend(name=GENERIC, endpoint=endpoint, headers=_headers(environ.get(HEADERS_ENV, "")))


def _headers(value: str) -> dict[str, str]:
    pairs = [pair.strip() for pair in value.split(",") if pair.strip()]
    if any("=" not in pair for pair in pairs):
        raise ConfigurationError(f"{HEADERS_ENV} takes comma-separated name=value pairs")
    return dict(pair.split("=", 1) for pair in pairs)


class _Pacer:
    """A token bucket over spans, so a run cannot outrun what a backend will really keep.

    Waits through the injected clock rather than `time.sleep`, which is what lets a test
    assert the delay a send *asked* for instead of measuring one.
    """

    def __init__(
        self, rate: float, monotonic: Callable[[], float], sleep: Callable[[float], None]
    ) -> None:
        if rate <= 0:
            raise ConfigurationError(f"a rate of {rate} spans/s would never send anything")
        self.rate = rate
        self.monotonic = monotonic
        self.sleep = sleep
        self.ready = monotonic()

    def wait(self, spans: int) -> None:
        """Block until this many spans may leave, then charge them to the bucket."""
        now = self.monotonic()
        if now < self.ready:
            self.sleep(self.ready - now)
            now = self.ready
        self.ready = now + spans / self.rate


class OtlpExporter:
    """Ships each session's spans to one backend and records what the backend confirmed.

    The promise is at-least-once with stable ids: a session is sent whole or not at all, a
    failure records nothing and re-sends next run, and a backend that ignores span identity
    will hold duplicates. Nothing here diffs what already landed — that machinery was the
    prior importer's largest bug source.

    Takes an open store connection rather than a path because DuckDB admits one writer at a
    time: the `StoreSource` reading beside it has to be holding the same one.
    """

    def __init__(
        self,
        backend: Backend,
        connection: duckdb.DuckDBPyConnection,
        *,
        service_name: str | None = None,
        text: TextPolicy = METADATA_ONLY,
        batch_spans: int = DEFAULT_BATCH_SPANS,
        rate: float = DEFAULT_RATE,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.backend = backend
        self.connection = connection
        # None routes each session to a service named for its project directory.
        self.service_name = service_name
        # Transcript text stays home unless the caller opts it in.
        self.text = text
        self.batch_spans = batch_spans
        # Time is a seam: both the rate bucket and the retry backoff wait through these, so a
        # test asserts the delay a send *asked* for instead of waiting it out.
        self.sleep = sleep
        self.pacer = _Pacer(rate, monotonic, sleep)
        self.client = httpx.Client(timeout=timeout)
        self.connection.execute(_DELIVERY_SCHEMA)

    def __enter__(self) -> "OtlpExporter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Closes the HTTP client. The store connection belongs to the caller."""
        self.client.close()

    def fingerprints(self) -> dict[str, str]:
        """What this backend holds, as far as delivery can tell.

        Rows recorded under an older mapper are left out, which is what makes a shaping
        change re-send the corpus: `refresh()` sees them as sessions it never shipped.
        """
        rows = self.connection.execute(
            "SELECT session_id, fingerprint FROM otlp_delivery"
            " WHERE backend = ? AND mapper_version = ?",
            [self.backend.name, MAPPER_VERSION],
        ).fetchall()
        return dict(rows)

    def export(self, trace: SessionTrace, fingerprint: str) -> None:
        """Ship one session, and record it only once every batch came back confirmed."""
        spans = session_spans(trace, self.text)
        resource = self._resource(trace.session)
        sent = 0
        for index, batch in enumerate(_batches(spans, self.batch_spans)):
            self._post(trace.session.id, index, batch, resource)
            sent += len(batch)
        self.connection.execute(
            "INSERT OR REPLACE INTO otlp_delivery VALUES (?, ?, ?, ?, ?, ?)",
            [
                trace.session.id,
                self.backend.name,
                fingerprint,
                MAPPER_VERSION,
                sent,
                dt.datetime.now(dt.UTC),
            ],
        )

    def _resource(self, session: Session) -> resource_pb2.Resource:
        """What every span of this session is attributed to.

        `service.name` is the project directory's name, which is what routes a Honeycomb
        dataset per project; `--service-name` overrides it for a one-off run.
        """
        if self.service_name is None and session.project_dir is None:
            raise TimelessSessionError(
                f"Session {session.id} records no project_dir, so it has no service name. "
                f"The source filter excludes those, so this is schema drift."
            )
        service = self.service_name or Path(str(session.project_dir)).name
        return resource_pb2.Resource(
            attributes=_attributes(
                {
                    "service.name": service,
                    "aiobserve.exporter.version": MAPPER_VERSION,
                    # These spans were shipped from the store after the fact, not emitted
                    # live by the agent — a distinction a backend query cannot recover.
                    "aiobserve.telemetry.source": "store-export",
                }
            )
        )

    def _post(
        self,
        session_id: str,
        index: int,
        batch: list[trace_pb2.Span],
        resource: resource_pb2.Resource,
    ) -> None:
        """Send one batch and read the answer, retrying only what the backend asked us to."""
        payload = trace_service_pb2.ExportTraceServiceRequest(
            resource_spans=[
                trace_pb2.ResourceSpans(
                    resource=resource, scope_spans=[trace_pb2.ScopeSpans(spans=batch)]
                )
            ]
        ).SerializeToString()
        # Compressed here rather than by httpx: the payload is protobuf, it is large, and a
        # fixed mtime keeps the same batch encoding to the same bytes.
        body = gzip.compress(payload, mtime=0)
        headers = {
            "Content-Type": "application/x-protobuf",
            "Content-Encoding": "gzip",
            **self.backend.headers,
        }
        for attempt in range(1, MAX_ATTEMPTS + 1):
            # Every attempt is charged, including a retry: what a backend throttles on is
            # what actually arrives, not what we meant to send once.
            self.pacer.wait(len(batch))
            response = self.client.post(self.backend.endpoint, content=body, headers=headers)
            if response.is_success:
                self._check_rejections(session_id, index, response.content)
                return
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == MAX_ATTEMPTS:
                raise DeliveryError(
                    f"{self.backend.name} answered {response.status_code} for session "
                    f"{session_id} batch {index} after {attempt} attempt(s). Nothing was "
                    f"recorded as delivered; the next run sends the session again."
                )
            self.sleep(_backoff(response.headers.get("Retry-After"), attempt))

    def _check_rejections(self, session_id: str, index: int, content: bytes) -> None:
        """Crash on a partial acceptance: the run is stuck here until the mapper changes.

        A deterministic rejection — an attribute cap, a timestamp the backend refuses —
        makes this session a poison pill: every run crashes at it and the sessions behind it
        stop shipping. That is the intended shape. It is a mapper bug, the fix is a mapper
        change, and the `MAPPER_VERSION` bump that comes with it re-sends everything.
        """
        reply = trace_service_pb2.ExportTraceServiceResponse()
        reply.ParseFromString(content)
        if not reply.partial_success.rejected_spans:
            return
        raise RejectedSpansError(
            f"{self.backend.name} rejected {reply.partial_success.rejected_spans} span(s) of "
            f"session {session_id} batch {index}: "
            f"{reply.partial_success.error_message or 'no reason given'}. Nothing was "
            f"recorded as delivered, and no flag skips it — fix the mapper and bump "
            f"MAPPER_VERSION."
        )


def _backoff(retry_after: str | None, attempt: int) -> float:
    """How long to wait before the next attempt, honoring the backend's own answer."""
    if retry_after is not None and retry_after.strip().isdigit():
        return float(retry_after)
    return BACKOFF_SECONDS * 2 ** (attempt - 1)


@dataclass(frozen=True)
class Census:
    """What a run would ship, counted by shaping every session and sending nothing."""

    sessions: int
    spans: int
    # Compactions that survive the copied-prefix replay rule. `live_compactions` does not
    # reproduce this number — it keeps the copies a fork inherited — so a census that read
    # the view would over-report every fork copy in the corpus.
    compactions: int


class AmbiguousCompactionError(Exception):
    """One compaction appears twice in a session and the copied-prefix rule keeps both.

    A duplicated id is a fork's copy of its parent's compaction, so exactly one copy is the
    live one. Two would ship one compaction as two spans, which is a rule we can no longer
    apply rather than a count to fudge.
    """


def census(traces: Iterable[SessionTrace], text: TextPolicy = METADATA_ONLY) -> Census:
    """Count what a send would put on the wire, without sending it.

    Shapes each session exactly as `export()` does, so the total is the mapper's own answer
    rather than a SQL approximation of it, and crashes on a session whose duplicated
    compactions the replay rule cannot separate.
    """
    sessions = spans = compactions = 0
    for trace in traces:
        _check_one_live_copy(trace)
        shaped = session_spans(trace, text)
        sessions += 1
        spans += len(shaped)
        compactions += sum(1 for span in shaped if span.name == COMPACTION_SPAN)
    return Census(sessions=sessions, spans=spans, compactions=compactions)


def _check_one_live_copy(trace: SessionTrace) -> None:
    """Every compaction id a session holds twice must keep exactly one live copy."""
    runs = {run.id: run for run in trace.agent_runs}
    held: Counter[str] = Counter(compaction.id for compaction in trace.compactions)
    live: Counter[str] = Counter(
        compaction.id
        for compaction in trace.compactions
        if not copied_compaction(compaction, runs.get(compaction.source))
    )
    for compaction_id, count in held.items():
        if count > 1 and live[compaction_id] != 1:
            raise AmbiguousCompactionError(
                f"session {trace.session.id} holds compaction {compaction_id} {count} time(s) "
                f"and the copied-prefix rule keeps {live[compaction_id]} of them. Exactly one "
                f"copy is live; a fork shape this rule cannot separate has landed."
            )


def _batches(spans: list[trace_pb2.Span], size: int) -> Iterator[list[trace_pb2.Span]]:
    """The spans in POST-sized runs. Sequential and built here, so there is no queue to
    overflow — the failure shape that lost 82.9% of the prior importer's spans."""
    for start in range(0, len(spans), size):
        yield spans[start : start + size]
