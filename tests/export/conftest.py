"""An OTLP/HTTP endpoint in this process, and the store the export tiers ship from.

The design's chosen seam: real httpx, real protobuf, a real store, and a server that decodes
what arrived instead of a mock that records what was asked. It can be scripted to answer the
way a backend under load does — a partial rejection, a 429, a 500 — which is the only way to
test the failure paths the prior importer's data loss came from.
"""

import datetime as dt
import hashlib
import shutil
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb
import pytest
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from aiobserve.export.duckdb import open_trace_store
from aiobserve.export.otlp import METADATA_ONLY, Backend, OtlpExporter, TextPolicy
from aiobserve.extract.store import StoreSource
from aiobserve.model import SessionTrace
from aiobserve.pipeline import RefreshResult, SessionSource, refresh
from tests.conftest import FIXTURES, MYCELIA, SERVER_TOOLS, SPINE, build_store

# No request in these tests crosses a network, so a slow one is a hang, not a slow link.
TIMEOUT = 5.0

# The two sessions the export tiers ship, in the order `sessions()` lists them — the id
# order the poison-pill leaf reads as "later in the run".
FIRST = SERVER_TOOLS
SECOND = SPINE

# Planted onto the backend's headers, so a leak of the key has a distinct string to find.
KEY_SENTINEL = "planted-key-not-a-real-credential"


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
class Receiver:
    """A running OTLP endpoint: its URL, every request body it took, and what it answers."""

    url: str
    server: ThreadingHTTPServer
    bodies: list[bytes] = field(default_factory=list)
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

    def do_POST(self) -> None:  # noqa: N802 — the name `http.server` dispatches on
        receiver: Receiver = self.server.receiver  # pyrefly: ignore[missing-attribute]
        receiver.bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
        receiver.sent_headers.append({name.lower(): value for name, value in self.headers.items()})
        reply = receiver.reply
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

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the per-request line `http.server` prints to stderr."""


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
    connection = open_trace_store(store_path, read_only=False)
    yield connection
    connection.close()


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
    delays: list[float] | None = None,
    service_name: str | None = None,
    text: TextPolicy = METADATA_ONLY,
) -> RefreshResult:
    """One `export-otlp` pass over the store, exactly as the CLI runs it.

    Time is injected: a retry sleeps into `delays` rather than into the test's wall clock.
    """
    target = Backend(name=backend, endpoint=receiver.url, headers={"x-key": KEY_SENTINEL})
    recorded = delays if delays is not None else []
    with OtlpExporter(
        target, store, service_name=service_name, text=text, sleep=recorded.append
    ) as exporter:
        return refresh(Path(MYCELIA), extractor=StoreSource(store), exporter=exporter)


def trace_of(store: duckdb.DuckDBPyConnection, session_id: str) -> SessionTrace:
    """One session read back out of the store, the way the exporter is handed it."""
    return StoreSource(store).extract(SessionSource(id=session_id, files=(), fingerprint="x"))
