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
import hashlib
import time
from collections.abc import Callable, Iterator, Mapping
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

from aiobserve.model import MAIN_SOURCE, ApiCall, Session, SessionTrace, Turn

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

# The delimiter the id keys join on, and the invariant every component must hold to.
DELIMITER = "/"

# A span with no positive duration renders as an invisible sliver, and the store holds
# plenty (a turn whose only record is its prompt). One millisecond is the floor.
MINIMUM_DURATION = dt.timedelta(milliseconds=1)

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


def session_spans(trace: SessionTrace) -> list[trace_pb2.Span]:
    """Every span one session becomes, root first.

    Metadata only: nothing here carries transcript text. Slice 1 ships the session, its
    turns and its model calls; tool calls, agent runs, compactions and PR events follow.
    """
    session = trace.session
    if session.started_at is None or session.ended_at is None:
        raise TimelessSessionError(
            f"Session {session.id} records no timestamps but reached the mapper. The source "
            f"filter excludes the sessions that hold none, so this is schema drift."
        )
    turns = {(turn.source, turn.id): turn for turn in trace.turns}
    children = [
        *(_turn_span(session, turn) for turn in trace.turns if not turn.replayed),
        *(_chat_span(session, call, turns) for call in trace.api_calls if not call.replayed),
    ]
    return [_root_span(trace, children), *children]


class TimelessSessionError(Exception):
    """A session with no recorded times reached the mapper, which cannot time its root."""


def _root_span(trace: SessionTrace, children: list[trace_pb2.Span]) -> trace_pb2.Span:
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
            # Ids, never the title: `ai-title` is model-written from the conversation.
            "logfire.msg": f"session {session.id}",
        },
    )


def _turn_span(session: Session, turn: Turn) -> trace_pb2.Span:
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
            "logfire.msg": f"turn {turn.id}",
        },
    )


def _chat_span(
    session: Session, call: ApiCall, turns: dict[tuple[str, str], Turn]
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
            "logfire.msg": f"chat {call.model}",
        },
    )


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
        batch_spans: int = DEFAULT_BATCH_SPANS,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.backend = backend
        self.connection = connection
        # None routes each session to a service named for its project directory.
        self.service_name = service_name
        self.batch_spans = batch_spans
        # A seam, so a test asserts the delay a retry *asked* for instead of waiting it out.
        self.sleep = sleep
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
        spans = session_spans(trace)
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
        headers = {"Content-Type": "application/x-protobuf", **self.backend.headers}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self.client.post(self.backend.endpoint, content=payload, headers=headers)
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


def _batches(spans: list[trace_pb2.Span], size: int) -> Iterator[list[trace_pb2.Span]]:
    """The spans in POST-sized runs. Sequential and built here, so there is no queue to
    overflow — the failure shape that lost 82.9% of the prior importer's spans."""
    for start in range(0, len(spans), size):
        yield spans[start : start + size]
