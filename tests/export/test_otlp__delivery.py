"""Delivery: what reaches the backend, what gets recorded, and what happens when it fails.

Real httpx against the in-process receiver, real protobuf, a real store — the design's seam.
Every leaf here is about the promise the exporter makes: at-least-once with stable ids, a
delivery row written only after the backend confirmed every batch.
"""

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from aiobserve.export.duckdb import SCHEMA_VERSION, DuckDbExporter, open_trace_store
from aiobserve.export.otlp import (
    MAPPER_VERSION,
    DeliveryError,
    RejectedSpansError,
    session_spans,
)
from tests.export.conftest import (
    FIRST,
    KEY_SENTINEL,
    SECOND,
    Receiver,
    deliver,
    delivery_rows,
    trace_of,
)


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
        "aiobserve.exporter.version": MAPPER_VERSION,
        "aiobserve.telemetry.source": "store-export",
    }
    # ...and an operator who wants another dataset overrides the service name.
    receiver.bodies.clear()
    store.execute("DELETE FROM otlp_delivery")
    deliver(store, receiver, service_name="mycelia-backfill")
    assert receiver.attributes(receiver.resources[0])["service.name"] == "mycelia-backfill"


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
    delays: list[float] = []
    with pytest.raises(DeliveryError, match=FIRST):
        deliver(store, receiver, delays=delays)
    # ...then the run crashes with nothing recorded — the failure prior art's issue #2 hid
    # by recording "attempted" — after backing off between attempts...
    assert delivery_rows(store) == []
    assert delays
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
