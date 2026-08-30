"""An OTLP/HTTP endpoint in this process, and the store the export tiers ship from.

The design's chosen seam: real httpx, real protobuf, a real store, and a server that decodes
what arrived instead of a mock that records what was asked. It can be scripted to answer the
way a backend under load does — a partial rejection, a 429, a 500 — which is the only way to
test the failure paths the prior importer's data loss came from.
"""

import datetime as dt
import gzip
import hashlib
import shutil
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, override

import duckdb
import httpx
import pytest
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from hyphae.export.duckdb import open_trace_store
from hyphae.export.otlp import METADATA_ONLY, TextPolicy
from hyphae.export.otlp_delivery import (
    DEFAULT_BATCH_SPANS,
    DEFAULT_RATE,
    Backend,
    OtlpExporter,
)
from hyphae.extract.store import StoreSource
from hyphae.model import SessionTrace
from hyphae.pipeline import RefreshResult, SessionSource, refresh
from tests.conftest import FIXTURES, MYCELIA, SERVER_TOOLS, SPINE, build_store

# No request in these tests crosses a network, so a slow one is a hang, not a slow link.
TIMEOUT = 5.0

# The two sessions the export tiers ship, in the order `sessions()` lists them — the id
# order the poison-pill leaf reads as "later in the run".
FIRST = SERVER_TOOLS
SECOND = SPINE

# Planted onto the backend's headers, so a leak of the key has a distinct string to find.
KEY_SENTINEL = "planted-key-not-a-real-credential"

# Names the backend an opt-in live send ships to. Unset, every leaf here stays on this machine.
LIVE_ENV = "HYPHAE_LIVE_OTLP"

# The only hosts a test may reach. Anything else is a real backend: billed, and handed a
# transcript.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass
class Reply:
    """What the receiver answers with. Mutate it to script a backend's bad day."""

    status: int = 200
    # Spans the backend refuses. Nonzero is the deterministic-rejection shape: HTTP 200 with
    # a body saying part of the batch never landed.
    rejected_spans: int = 0
    error_message: str = ""
    # Seconds, sent as `Retry-After` when set.
    retry_after: int | None = None


@dataclass
class Clock:
    """The injected time seam: every wait the exporter asked for, and a clock that honors it.

    Tests assert the delays *requested*, never wall-clock elapsed time, so a pacing leaf is
    exact and costs nothing.
    """

    delays: list[float] = field(default_factory=list)
    now: float = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.now += seconds


class RefusedWait(Exception):
    """Raised by a clock that refuses to wait, to prove a waiter went through the seam."""


class RefusingClock(Clock):
    """A clock that crashes instead of waiting. A waiter reaching `time.sleep` directly
    misses it entirely — and sleeps for real in CI."""

    @override
    def sleep(self, seconds: float) -> None:
        raise RefusedWait(f"the exporter asked to wait {seconds}s")


@dataclass
class Receiver:
    """A running OTLP endpoint: its URL, every request body it took, and what it answers."""

    url: str
    server: ThreadingHTTPServer
    # Inflated, so a leaf sweeping the payload for a leaked string reads plaintext.
    bodies: list[bytes] = field(default_factory=list)
    # Exactly what arrived, before the receiver decoded the transfer encoding.
    raw_bodies: list[bytes] = field(default_factory=list)
    # Answered in order, one per request, before `reply` takes over for the rest.
    replies: list[Reply] = field(default_factory=list)
    # One entry per request, so a leaf can prove the key it asserts absent elsewhere was
    # in fact sent — otherwise that assertion passes on an exporter that sends no headers.
    sent_headers: list[dict[str, str]] = field(default_factory=list)
    reply: Reply = field(default_factory=Reply)

    @property
    def requests(self) -> list[trace_service_pb2.ExportTraceServiceRequest]:
        """Each request decoded — the assertion surface, rather than the raw bytes."""
        decoded = []
        for body in self.bodies:
            request = trace_service_pb2.ExportTraceServiceRequest()
            request.ParseFromString(body)
            decoded.append(request)
        return decoded

    @property
    def spans(self) -> list[trace_pb2.Span]:
        """Every span the receiver decoded, across every request, in arrival order."""
        return [
            span
            for request in self.requests
            for resource_spans in request.resource_spans
            for scope_spans in resource_spans.scope_spans
            for span in scope_spans.spans
        ]

    @property
    def resources(self) -> list[resource_pb2.Resource]:
        return [
            resource_spans.resource
            for request in self.requests
            for resource_spans in request.resource_spans
        ]

    def attributes(self, resource: resource_pb2.Resource) -> dict[str, Any]:
        """One resource's attributes as a plain dict, for readable assertions."""
        return {attribute.key: any_value(attribute.value) for attribute in resource.attributes}


def any_value(value: common_pb2.AnyValue) -> Any:
    """The one field an OTLP `AnyValue` set. An empty one is a mapper bug, so it crashes."""
    which = value.WhichOneof("value")
    assert which is not None, "an attribute arrived carrying no value at all"
    return getattr(value, which)


def attributes(span: trace_pb2.Span) -> dict[str, Any]:
    """One span's attributes as a plain dict, so a leaf can compare the whole set at once."""
    return {attribute.key: any_value(attribute.value) for attribute in span.attributes}


def digest(session_id: str, kind: str, source: str, natural_id: str) -> bytes:
    """The span id the design specifies, recomputed here rather than imported.

    Digest **bytes** sliced to 8 — `hexdigest()[:8]` is also 8 bytes and would pass any
    length-only assertion while giving 32-bit ids.
    """
    return hashlib.sha256(f"{session_id}/{kind}/{source}/{natural_id}".encode()).digest()[:8]


def one(spans: list[trace_pb2.Span], span: bytes) -> trace_pb2.Span:
    """The single span carrying an id, so a miss reads as a missing span, not an index error."""
    found = [candidate for candidate in spans if candidate.span_id == span]
    assert len(found) == 1, f"expected one span keyed {span.hex()}, found {len(found)}"
    return found[0]


def nanos(value: dt.datetime) -> int:
    """A timestamp in the units a span carries it, in integers — a float loses microseconds."""
    delta = value - dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


class _Handler(BaseHTTPRequestHandler):
    """Answers one POST the way an OTLP collector does, per the receiver's current `reply`."""

    # `do_POST` is the name `http.server` dispatches on.
    def do_POST(self) -> None:
        receiver: Receiver = self.server.receiver  # pyrefly: ignore[missing-attribute]
        arrived = self.rfile.read(int(self.headers["Content-Length"]))
        receiver.raw_bodies.append(arrived)
        encoding = self.headers.get("Content-Encoding")
        receiver.bodies.append(gzip.decompress(arrived) if encoding == "gzip" else arrived)
        receiver.sent_headers.append({name.lower(): value for name, value in self.headers.items()})
        reply = receiver.replies.pop(0) if receiver.replies else receiver.reply
        body = trace_service_pb2.ExportTraceServiceResponse(
            partial_success=trace_service_pb2.ExportTracePartialSuccess(
                rejected_spans=reply.rejected_spans, error_message=reply.error_message
            )
        ).SerializeToString()
        self.send_response(reply.status)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(body)))
        if reply.retry_after is not None:
            self.send_header("Retry-After", str(reply.retry_after))
        self.end_headers()
        self.wfile.write(body)

    # `format` shadows the builtin, and is the name `http.server` calls this by; ruff excuses
    # the shadowing on an `@override`, where the name is the base class's to choose.
    @override
    def log_message(self, format: str, *args: Any) -> None:
        """Silence the per-request line `http.server` prints to stderr."""


class OffMachineRequestError(Exception):
    """A test tried to reach a host that is not this machine's loopback."""


@pytest.fixture(autouse=True)
def offline(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse any request that leaves this machine, unless the leaf is marked `live`.

    Without this the offline guarantee rests on review, and the failure mode is expensive in
    both directions: it bills a real backend and hands it a transcript.
    """
    if request.node.get_closest_marker("live"):
        return
    send = httpx.Client.send

    def guarded(client: httpx.Client, outgoing: httpx.Request, **kwargs: Any) -> httpx.Response:
        if outgoing.url.host not in LOOPBACK:
            raise OffMachineRequestError(
                f"a test tried to reach {outgoing.url.host}. Only {sorted(LOOPBACK)} are "
                f"allowed; a real backend needs the `live` marker and {LIVE_ENV}."
            )
        return send(client, outgoing, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", guarded)


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    """An OTLP endpoint on an ephemeral port, torn down with the test."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address[:2]
    running = Receiver(url=f"http://{host}:{port}/v1/traces", server=server)
    server.receiver = running  # pyrefly: ignore[missing-attribute]
    # A short poll interval: the default 0.5s is paid on every shutdown, once per test.
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield running
    server.shutdown()
    server.server_close()
    thread.join(timeout=TIMEOUT)
    assert not thread.is_alive(), "the receiver thread outlived its server"


@pytest.fixture(scope="session")
def delivered_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Two recorded sessions of the analyzed project, built once and copied per test."""
    path = tmp_path_factory.mktemp("delivery") / "traces.duckdb"
    build_store(
        path,
        [FIXTURES / "server_tools" / f"{FIRST}.jsonl", FIXTURES / "spine" / f"{SECOND}.jsonl"],
    )
    return path


@pytest.fixture
def store_path(delivered_db: Path, tmp_path: Path) -> Path:
    """This test's own copy of that store: every export leaf writes delivery rows."""
    path = tmp_path / "traces.duckdb"
    shutil.copyfile(delivered_db, path)
    return path


@pytest.fixture
def store(store_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    with open_trace_store(store_path, read_only=False) as connection:
        yield connection


def delivery_rows(connection: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    """The whole ledger, in a stable order. `delivered_at` is last, so a leaf that cannot
    compare a clock slices it off."""
    return connection.execute(
        "SELECT session_id, backend, fingerprint, mapper_version, spans_sent, delivered_at"
        " FROM otlp_delivery ORDER BY session_id, backend"
    ).fetchall()


def deliver(
    store: duckdb.DuckDBPyConnection,
    receiver: Receiver,
    *,
    backend: str = "generic",
    target: Backend | None = None,
    clock: Clock | None = None,
    batch_spans: int = DEFAULT_BATCH_SPANS,
    rate: float = DEFAULT_RATE,
    service_name: str | None = None,
    text: TextPolicy = METADATA_ONLY,
) -> RefreshResult:
    """One `export-otlp` pass over the store, exactly as the CLI runs it.

    Time is injected: every wait goes into `clock` rather than into the test's wall clock.
    """
    shipping = target or Backend(
        name=backend, endpoint=receiver.url, headers={"x-key": KEY_SENTINEL}
    )
    waited = clock or Clock()
    with OtlpExporter(
        shipping,
        store,
        service_name=service_name,
        text=text,
        batch_spans=batch_spans,
        rate=rate,
        monotonic=waited.monotonic,
        sleep=waited.sleep,
    ) as exporter:
        return refresh(Path(MYCELIA), extractor=StoreSource(store), exporter=exporter)


def trace_of(store: duckdb.DuckDBPyConnection, session_id: str) -> SessionTrace:
    """One session read back out of the store, the way the exporter is handed it."""
    return StoreSource(store).extract(SessionSource(id=session_id, files=(), fingerprint="x"))
