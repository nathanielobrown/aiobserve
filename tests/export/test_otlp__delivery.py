"""Delivery: what reaches the backend, what gets recorded, and what happens when it fails.

Real httpx against the in-process receiver, real protobuf, a real store — the design's seam.
Every leaf here is about the promise the exporter makes: at-least-once with stable ids, a
delivery row written only after the backend confirmed every batch.
"""

import datetime as dt
import os
from collections import Counter
from dataclasses import replace
from pathlib import Path

import duckdb
import httpx
import pytest
from opentelemetry.proto.trace.v1 import trace_pb2

from hyphae.export.duckdb import DuckDbExporter, open_trace_store
from hyphae.export.otlp import (
    MAPPER_VERSION,
    METADATA_ONLY,
    PlacelessSessionError,
    session_spans,
)
from hyphae.export.otlp_delivery import (
    DEFAULT_BATCH_SPANS,
    DEFAULT_RATE,
    Backend,
    ConfigurationError,
    DeliveryError,
    OtlpExporter,
    RejectedSpansError,
    named_backend,
)
from hyphae.export.schema import SCHEMA_VERSION
from hyphae.extract.store import StoreSource
from hyphae.pipeline import refresh
from tests.conftest import MYCELIA
from tests.export.conftest import (
    FIRST,
    KEY_SENTINEL,
    LIVE_ENV,
    SECOND,
    TIMEOUT,
    Clock,
    OffMachineRequestError,
    Receiver,
    RefusedWait,
    RefusingClock,
    Reply,
    deliver,
    delivery_rows,
    trace_of,
)

# Small enough that both recorded sessions overflow it several times, so a batch boundary is
# a real overflow of recorded spans rather than a planted one.
BOUND_BATCH = 7

# Spans per second for the leaves that assert pacing. Any rate works against the injected
# clock, which only moves when the exporter asks it to.
BOUND_RATE = 100.0


def batch_sizes(*sessions: list[trace_pb2.Span]) -> list[int]:
    """How `BOUND_BATCH` partitions each session's spans, in POST order."""
    return [
        min(BOUND_BATCH, len(spans) - start)
        for spans in sessions
        for start in range(0, len(spans), BOUND_BATCH)
    ]


def test_the_receiver_decodes_what_the_exporter_encoded(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """The spans that arrive are the spans the mapper built, field for field."""
    # If both recorded sessions are exported...
    result = deliver(store, receiver)
    assert result.extracted == [FIRST, SECOND]
    # ...then the receiver decodes each session's whole span list, in session order, with
    # every field intact — ids, times and attributes included. Every other leaf that reads
    # decoded spans rests on this one.
    assert receiver.spans == [
        *session_spans(trace_of(store, FIRST)),
        *session_spans(trace_of(store, SECOND)),
    ]


def test_the_resource_names_the_project_and_the_exporter(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """Every batch carries the project as its service, and says which exporter shaped it."""
    # If the sessions of `/Users/nob/repos/mycelia` are exported...
    deliver(store, receiver)
    # ...then each request's resource routes to a service named for the directory, and
    # carries the version a re-shaping would bump...
    assert receiver.attributes(receiver.resources[0]) == {
        "service.name": "mycelia",
        "hyphae.exporter.version": MAPPER_VERSION,
        "hyphae.telemetry.source": "store-export",
    }
    # ...and an operator who wants another dataset overrides the service name.
    receiver.bodies.clear()
    store.execute("DELETE FROM otlp_delivery")
    deliver(store, receiver, service_name="mycelia-backfill")
    assert receiver.attributes(receiver.resources[0])["service.name"] == "mycelia-backfill"


def test_a_session_with_no_project_and_no_service_name_crashes(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A session naming no project has no dataset to route to, and says which drift that is."""
    # If a session carrying times but no `project_dir` reaches the exporter — planted, since
    # the recorded session with no `project_dir` records no times either, and the source
    # filter places neither...
    trace = trace_of(store, FIRST)
    placeless = replace(trace, session=replace(trace.session, project_dir=None))
    backend = Backend(name="generic", endpoint=receiver.url, headers={"x-key": KEY_SENTINEL})
    # ...then it crashes before anything is sent, naming the session and the drift it is:
    # no place, rather than the no-clock drift the mapper refuses sessions for.
    with (
        OtlpExporter(backend, store, service_name=None, text=METADATA_ONLY) as exporter,
        pytest.raises(PlacelessSessionError, match=FIRST),
    ):
        exporter.export(placeless, "fingerprint")
    assert receiver.bodies == []


def test_a_confirmed_session_records_one_delivery_row(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A session the backend confirmed leaves one row saying what was shipped, and when."""
    # If the store's sessions are exported...
    before = dt.datetime.now(dt.UTC)
    deliver(store, receiver)
    fingerprints = dict(
        store.execute("SELECT session_id, fingerprint FROM extract_state").fetchall()
    )
    # ...then each one has a row under this backend, carrying the fingerprint that was
    # shipped and the mapper version that shaped it — the two things a later run compares
    # against to decide whether to send again...
    for session_id, _, fingerprint, mapper_version, spans_sent, delivered_at in delivery_rows(
        store
    ):
        assert (fingerprint, mapper_version) == (fingerprints[session_id], MAPPER_VERSION)
        assert before <= delivered_at <= dt.datetime.now(dt.UTC)
        assert spans_sent == len(session_spans(trace_of(store, session_id)))
    assert [row[0] for row in delivery_rows(store)] == [FIRST, SECOND]


def test_spans_sent_counts_what_the_receiver_took(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """The recorded manifest sums to the spans the receiver decoded, not to what was built.

    It is what a future `--verify` compares against, so a count taken from the sender's own
    intent would prove nothing about delivery.
    """
    deliver(store, receiver)
    assert sum(row[4] for row in delivery_rows(store)) == len(receiver.spans)


def test_an_unchanged_session_is_not_sent_again(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A second pass over an unchanged store sends nothing at all."""
    # If everything was delivered once...
    deliver(store, receiver)
    delivered_at = [row[5] for row in delivery_rows(store)]
    receiver.bodies.clear()
    # ...then a second run skips every session — no request, and the ledger untouched.
    # This is what lets the command run on a schedule instead of duplicating the corpus.
    result = deliver(store, receiver)
    assert result.skipped == [FIRST, SECOND]
    assert receiver.bodies == []
    assert [row[5] for row in delivery_rows(store)] == delivered_at


def test_a_changed_fingerprint_re_sends_the_whole_session(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A session whose extract changed ships again, whole."""
    # If a delivered session is re-extracted under a new fingerprint...
    deliver(store, receiver)
    receiver.bodies.clear()
    store.execute("UPDATE extract_state SET fingerprint = ? WHERE session_id = ?", ["moved", FIRST])
    # ...then it ships again in full — an append-only backend cannot be patched, so the
    # unit of correction is the whole session...
    result = deliver(store, receiver)
    assert result.extracted == [FIRST]
    assert receiver.spans == session_spans(trace_of(store, FIRST))
    # ...and the row now records the fingerprint that was actually shipped.
    assert [(row[0], row[2]) for row in delivery_rows(store)] == [
        (FIRST, "moved"),
        (SECOND, "fixture"),
    ]


def test_a_stale_mapper_version_re_sends_everything(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A span-shaping change re-sends the corpus, the way an extractor upgrade re-extracts it."""
    # If everything was delivered, and the mapper is then changed — recorded here by
    # rewriting the version the rows carry, which is what a bump looks like to the reader...
    deliver(store, receiver)
    receiver.bodies.clear()
    store.execute("UPDATE otlp_delivery SET mapper_version = ?", ["0"])
    # ...then no session counts as delivered, every span goes again, and the rows come back
    # carrying the current version. This is the only recovery path from a mapper bug.
    result = deliver(store, receiver)
    assert result.extracted == [FIRST, SECOND]
    assert len(receiver.spans) == sum(
        len(session_spans(trace_of(store, session))) for session in (FIRST, SECOND)
    )
    assert {row[3] for row in delivery_rows(store)} == {MAPPER_VERSION}


def test_delivery_is_recorded_per_backend(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """The same store shipped to two backends records — and re-sends — for each separately."""
    # If the store is delivered to one backend and then to a second...
    deliver(store, receiver, backend="generic")
    receiver.bodies.clear()
    deliver(store, receiver, backend="second")
    # ...then the second sees the full corpus, since nothing it holds was ever shipped...
    assert len(receiver.spans) == sum(
        len(session_spans(trace_of(store, session))) for session in (FIRST, SECOND)
    )
    # ...and each backend keeps its own row per session.
    assert [(row[0], row[1]) for row in delivery_rows(store)] == [
        (FIRST, "generic"),
        (FIRST, "second"),
        (SECOND, "generic"),
        (SECOND, "second"),
    ]


def test_a_server_error_crashes_and_the_next_run_re_sends(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A failed export writes nothing, and the whole session goes again next run."""
    # If the backend answers 500 to everything...
    receiver.reply.status = 500
    clock = Clock()
    with pytest.raises(DeliveryError, match=FIRST):
        deliver(store, receiver, clock=clock)
    # ...then the run crashes with nothing recorded — the failure prior art's issue #2 hid
    # by recording "attempted" — after backing off between attempts...
    assert delivery_rows(store) == []
    assert clock.delays
    # ...and when the backend recovers, the next run ships both sessions whole.
    receiver.reply.status = 200
    receiver.bodies.clear()
    result = deliver(store, receiver)
    assert result.extracted == [FIRST, SECOND]
    assert receiver.spans == [
        *session_spans(trace_of(store, FIRST)),
        *session_spans(trace_of(store, SECOND)),
    ]


def test_a_rejected_span_crashes_and_poisons_the_run(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A backend that accepts the request but refuses spans stops the run, every run."""
    # If the backend answers 200 while reporting that it kept nothing...
    receiver.reply.rejected_spans = 3
    receiver.reply.error_message = "attribute limit exceeded"
    with pytest.raises(RejectedSpansError) as raised:
        deliver(store, receiver)
    # ...then the crash names the session and the batch an operator has to look at, and
    # carries neither transcript text nor the backend key...
    message = str(raised.value)
    assert FIRST in message and "batch 0" in message
    assert KEY_SENTINEL not in message
    # ...nothing is recorded as delivered...
    assert delivery_rows(store) == []
    # ...and the corpus stays stuck there: a deterministic rejection is a mapper bug we need
    # to see, so there is no skip flag — every later run crashes at the same session, and
    # the session behind it never ships until the mapper changes.
    receiver.bodies.clear()
    with pytest.raises(RejectedSpansError, match=FIRST):
        deliver(store, receiver)
    assert {span.trace_id for span in receiver.spans} == {
        session_spans(trace_of(store, FIRST))[0].trace_id
    }


def test_the_ledger_survives_a_re_extract(
    store: duckdb.DuckDBPyConnection, store_path: Path, receiver: Receiver
) -> None:
    """Re-extracting a session leaves its delivery row alone."""
    # If a delivered session is extracted again — the replace transaction that deletes every
    # row the session owns...
    deliver(store, receiver)
    trace = trace_of(store, FIRST)
    store.close()
    with DuckDbExporter(store_path) as exporter:
        exporter.export(trace, "re-extracted")
    # ...then its delivery row is still there. A table swept into the replace by reflex
    # would erase the ledger on every extract, and every later run would duplicate the corpus.
    reopened = open_trace_store(store_path, read_only=True)
    assert [row[0] for row in delivery_rows(reopened)] == [FIRST, SECOND]
    reopened.close()


def test_the_ledger_is_created_without_a_schema_bump(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A store that has never been exported grows the table on first use, and stays readable."""
    # If a store written by `extract` — which knows nothing of OTLP — is exported...
    assert store.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'otlp_delivery'"
    ).fetchone() == (0,)
    deliver(store, receiver)
    # ...then the table appears beside the enrichment tables, and the schema version is
    # untouched: the ledger is not part of the shape `extract` and the viewer agree on.
    assert store.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'otlp_delivery'"
    ).fetchone() == (1,)
    assert store.execute("SELECT schema_version FROM meta").fetchone() == (SCHEMA_VERSION,)


def test_a_multi_batch_session_ships_every_span_exactly_once(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A session too big for one POST is split across POSTs, losing and repeating nothing."""
    # If both recorded sessions are shipped with the batch bound below their span counts...
    shaped = {session: session_spans(trace_of(store, session)) for session in (FIRST, SECOND)}
    assert all(len(spans) > BOUND_BATCH for spans in shaped.values())
    deliver(store, receiver, batch_spans=BOUND_BATCH)
    # ...then each session arrives as `ceil(n / size)` POSTs, none of them over the bound...
    assert [
        len(request.resource_spans[0].scope_spans[0].spans) for request in receiver.requests
    ] == batch_sizes(shaped[FIRST], shaped[SECOND])
    # ...and what the receiver decoded is the whole span list in order, with no span in two
    # batches. Prior art's issue #1 was a batching bug that lost 82.9% of its spans while
    # every request came back 200.
    assert receiver.spans == [*shaped[FIRST], *shaped[SECOND]]
    assert len({span.span_id for span in receiver.spans}) == len(receiver.spans)


def test_the_shipping_defaults_are_the_measured_ones() -> None:
    """An unbound run batches and paces at the numbers the design measured."""
    # Every other leaf in this file binds its own batch size and rate, so without this pin
    # the tier passes at any defaults — including the ones prior art's issue #6 lost ~40% of
    # its spans at. 2,000 spans puts the biggest recorded session at ~15 POSTs, and 300/s is
    # the rate that landed 177 of 177 (`plans/otlp-export/design.md`).
    assert (DEFAULT_BATCH_SPANS, DEFAULT_RATE) == (2_000, 300.0)


def test_a_failure_part_way_through_re_sends_the_batches_that_landed(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A crash mid-session records nothing, and the next run ships that session whole."""
    # If the backend takes the first batch and then breaks...
    first = session_spans(trace_of(store, FIRST))
    receiver.replies = [Reply()]
    receiver.reply.status = 500
    with pytest.raises(DeliveryError, match=FIRST):
        deliver(store, receiver, batch_spans=BOUND_BATCH)
    # ...then the run leaves no row at all, even though a batch did land — a session is
    # delivered whole or not delivered...
    assert delivery_rows(store) == []
    assert receiver.spans[:BOUND_BATCH] == first[:BOUND_BATCH]
    # ...and when the backend recovers, the whole session goes again, first batch included.
    receiver.reply.status = 200
    result = deliver(store, receiver, batch_spans=BOUND_BATCH)
    assert result.extracted == [FIRST, SECOND]
    assert [row[0] for row in delivery_rows(store)] == [FIRST, SECOND]
    # That re-send is the honest duplicate cost of at-least-once with stable ids: across the
    # two runs the backend holds the first batch twice, and the batch it refused four times.
    sent = Counter(span.span_id for span in receiver.spans)
    assert sent[first[0].span_id] == 2
    assert sent[first[BOUND_BATCH].span_id] == 4


@pytest.mark.parametrize(
    "reply",
    [
        Reply(status=400),
        Reply(status=429),
        Reply(status=500),
        Reply(rejected_spans=1, error_message="attribute limit exceeded"),
    ],
    ids=["client-error", "throttled-until-exhausted", "server-error", "partial-success"],
)
def test_only_a_clean_acceptance_records_a_delivery(
    store: duckdb.DuckDBPyConnection, receiver: Receiver, reply: Reply
) -> None:
    """A row means 2xx with zero rejections — no other answer writes one."""
    # `delivered` is the only word this system says about a remote it cannot query, so its
    # whole meaning is that no other backend answer, refusal or throttle produces it.
    receiver.reply = reply
    with pytest.raises((DeliveryError, RejectedSpansError)):
        deliver(store, receiver, clock=Clock())
    assert delivery_rows(store) == []


def test_a_throttled_batch_waits_the_delay_the_backend_named(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """A 429 carrying `Retry-After` is retried after that delay and then lands."""
    # If the backend throttles the first request and takes everything after it...
    receiver.replies = [Reply(status=429, retry_after=7)]
    clock = Clock()
    result = deliver(store, receiver, clock=clock)
    # ...then the exporter waited what the header asked for rather than its own backoff —
    # the waits after it are the rate bucket's, which the pacing leaf covers...
    assert clock.delays[0] == 7.0
    # ...and the retry is invisible downstream: one extra request, both sessions delivered,
    # one row each.
    assert len(receiver.bodies) == 3
    assert result.extracted == [FIRST, SECOND]
    assert [row[0] for row in delivery_rows(store)] == [FIRST, SECOND]


def test_the_bucket_paces_the_sends_through_the_injected_clock(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """Spans leave at the configured rate, and the wait is asked of the injected clock."""
    # If a run at a bound rate and batch size ships both sessions...
    shaped = [session_spans(trace_of(store, session)) for session in (FIRST, SECOND)]
    clock = Clock()
    deliver(store, receiver, clock=clock, batch_spans=BOUND_BATCH, rate=BOUND_RATE)
    # ...then each POST charges its own span count to the bucket, so the wait before a batch
    # is what the batch before it cost, and the first send is free. Issue #6 measured 40%
    # silent server-side loss without a limiter and 0% with one; a wall-clock version of this
    # assertion would be both slow and a flake, so the leaf asserts the delays *requested*.
    sizes = batch_sizes(*shaped)
    assert clock.delays == pytest.approx([count / BOUND_RATE for count in sizes[:-1]])


def test_every_wait_goes_through_the_injected_clock(
    store: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """Neither the rate bucket nor the retry backoff reaches for the module clock."""
    # If the bucket has to pace a multi-batch run against a clock that refuses to wait...
    with pytest.raises(RefusedWait):
        deliver(store, receiver, clock=RefusingClock(), batch_spans=BOUND_BATCH, rate=BOUND_RATE)
    # ...and if a throttled batch has to back off against the same clock...
    receiver.replies = [Reply(status=429, retry_after=7)]
    with pytest.raises(RefusedWait):
        deliver(store, receiver, clock=RefusingClock())
    # ...then both crash out of the injected callable. A waiter that called `time.sleep`
    # directly would pass every other leaf here while sleeping for real in CI.
    assert delivery_rows(store) == []


def test_the_payload_travels_gzipped(store: duckdb.DuckDBPyConnection, receiver: Receiver) -> None:
    """Every request is gzip-encoded protobuf, and says so in its headers."""
    deliver(store, receiver)
    # If a batch is shipped, the bytes on the wire carry gzip's magic number and the header
    # that lets a collector inflate them...
    assert all(headers["content-encoding"] == "gzip" for headers in receiver.sent_headers)
    assert all(body[:2] == b"\x1f\x8b" for body in receiver.raw_bodies)
    # ...and they are smaller than the protobuf inside them. Every other leaf reads the
    # inflated payload, so a missing encode step would otherwise pass the whole tier.
    assert sum(len(body) for body in receiver.raw_bodies) < sum(
        len(body) for body in receiver.bodies
    )


@pytest.mark.parametrize(
    ("name", "key_env", "header", "endpoint"),
    [
        (
            "honeycomb",
            "HONEYCOMB_API_KEY",
            "x-honeycomb-team",
            "https://api.honeycomb.io/v1/traces",
        ),
        (
            "logfire",
            "LOGFIRE_API_KEY",
            "authorization",
            "https://logfire-us.pydantic.dev/v1/traces",
        ),
    ],
)
def test_a_named_backend_sends_its_key_under_its_own_header(
    store: duckdb.DuckDBPyConnection,
    receiver: Receiver,
    name: str,
    key_env: str,
    header: str,
    endpoint: str,
) -> None:
    """Each named backend ships to its own endpoint with its key in the header it reads."""
    # If a named backend is configured with nothing but its key variable...
    resolved = named_backend(name, {key_env: KEY_SENTINEL})
    # ...then it resolves to the endpoint prior art verified (`claude-otel:114`)...
    assert (resolved.name, resolved.endpoint) == (name, endpoint)
    # ...and shipping through it puts the key on the wire under that backend's own header
    # name, bare — Logfire refuses an `authorization: Bearer …`, and a reflex prefix there is
    # a 401 an hour into a backfill...
    deliver(
        store,
        receiver,
        target=named_backend(name, {key_env: KEY_SENTINEL, "OTLP_ENDPOINT": receiver.url}),
    )
    assert receiver.sent_headers[0][header] == KEY_SENTINEL
    # ...while a run whose key variable is unset refuses before it reads a session.
    with pytest.raises(ConfigurationError, match=key_env):
        named_backend(name, {})


def test_a_request_that_leaves_this_machine_is_refused() -> None:
    """No leaf in this tier can reach a real backend by accident."""
    # The guard is autouse and lifts only for a leaf marked `live`, so a run that forgets the
    # receiver's URL fails here rather than billing a backend and handing it a transcript.
    with pytest.raises(OffMachineRequestError, match=r"example\.com"):
        httpx.Client(timeout=TIMEOUT).post("https://example.com/v1/traces", content=b"")


@pytest.mark.live
@pytest.mark.slow  # A real round trip to a third-party backend, over the network.
def test_a_live_send_is_accepted(store: duckdb.DuckDBPyConnection) -> None:
    """An opt-in run ships two recorded sessions to a real backend, which accepts them."""
    # This is the only leaf that can touch auth and dataset routing at all, and it never runs
    # green in CI. It skips unless a backend is named and its key is set, rather than faking
    # the one thing no receiver we wrote can prove.
    name = os.environ.get(LIVE_ENV, "").strip()
    if not name:
        pytest.skip(f"{LIVE_ENV} names no backend to ship to")
    try:
        backend = named_backend(name, os.environ)
    except ConfigurationError as missing:
        pytest.skip(str(missing))
    # Any refusal — a status, or a nonzero `partial_success` — raises out of `export()`, so
    # reaching the rows means the backend took every span of both sessions.
    with OtlpExporter(backend, store) as exporter:
        result = refresh(Path(MYCELIA), extractor=StoreSource(store), exporter=exporter)
    assert result.extracted == [FIRST, SECOND]
    assert [row[0] for row in delivery_rows(store)] == [FIRST, SECOND]
