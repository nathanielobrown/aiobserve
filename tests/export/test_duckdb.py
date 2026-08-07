"""Writing a `SessionTrace` into the DuckDB trace store.

Traces come from the recorded fixtures rather than from hand-built dataclasses, so the
columns under test hold values a real transcript produced.
"""

import dataclasses
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from aiobserve.export.duckdb import SCHEMA_VERSION, DuckDbExporter, SchemaVersionError
from tests.conftest import TraceFactory

SPINE = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b"
DUPS = "8ee00a94-b01a-4394-b447-b065f74b11af"
OFFLOAD = "7e37bb35-4dcb-4e16-85be-55ac510c168e"

# Table name to the model attribute holding its rows, for the count-everything assertions.
TABLES = {
    "sessions": None,
    "turns": "turns",
    "api_calls": "api_calls",
    "tool_calls": "tool_calls",
    "agent_runs": "agent_runs",
    "offload_files": "offload_files",
    "raw_records": "raw_records",
}


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "traces.duckdb"


def counts(exporter: DuckDbExporter) -> dict[str, int]:
    """Row counts per table, keyed by table name."""
    return {
        table: exporter.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # type: ignore[index]
        for table in TABLES
    }


def rows(
    exporter: DuckDbExporter, table: str, columns: type, session: str = "%"
) -> list[tuple[object, ...]]:
    """Rows of `table` for one session, column order matching `columns`' fields."""
    names = ", ".join(f'"{field.name}"' for field in dataclasses.fields(columns))
    key = "id" if table == "sessions" else "session_id"
    return exporter.connection.execute(
        f"SELECT {names} FROM {table} WHERE {key} LIKE ? ORDER BY 1, 2", [session]
    ).fetchall()


def test_a_trace_round_trips(db: Path, fixture_trace: TraceFactory):
    """Every column of an exported trace reads back as it was written, nulls included."""
    trace = fixture_trace("spine", SPINE)

    with DuckDbExporter(db) as exporter:
        exporter.export(trace, "fingerprint-1")

        # If a trace is exported, then each table holds exactly its entities, field for
        # field — including the `command_name`/`command_args` nulls on a plain prompt.
        assert rows(exporter, "sessions", type(trace.session)) == [
            dataclasses.astuple(trace.session)
        ]
        for table, entities in (
            ("turns", trace.turns),
            ("api_calls", trace.api_calls),
            ("tool_calls", trace.tool_calls),
            ("agent_runs", trace.agent_runs),
        ):
            assert rows(exporter, table, type(entities[0])) == sorted(
                dataclasses.astuple(entity) for entity in entities
            )
        assert counts(exporter)["raw_records"] == len(trace.raw_records)


def test_re_exporting_a_session_replaces_it_wholly(db: Path, fixture_trace: TraceFactory):
    """A second export of the same session leaves no row from the first behind.

    Idempotency rests on the delete covering every table a session owns. A table added
    later and forgotten in the delete would keep stale rows forever, so this counts them
    all rather than checking one.
    """
    trace = fixture_trace("spine", SPINE)
    # If a full trace is exported...
    with DuckDbExporter(db) as exporter:
        exporter.export(trace, "fingerprint-1")
        assert counts(exporter) == {
            "sessions": 1,
            "turns": 6,
            "api_calls": 6,
            "tool_calls": 9,
            "agent_runs": 2,
            "offload_files": 0,
            "raw_records": 43,
        }

        # ...and the same session comes back shorter — one turn, one call, three lines...
        trimmed = replace(
            trace,
            turns=trace.turns[:1],
            api_calls=trace.api_calls[:1],
            tool_calls=trace.tool_calls[:1],
            agent_runs=trace.agent_runs[:1],
            raw_records=trace.raw_records[:3],
        )
        exporter.export(trimmed, "fingerprint-2")

        # ...then the store holds the short version and nothing of the long one.
        assert counts(exporter) == {
            "sessions": 1,
            "turns": 1,
            "api_calls": 1,
            "tool_calls": 1,
            "agent_runs": 1,
            "offload_files": 0,
            "raw_records": 3,
        }
        assert rows(exporter, "turns", type(trace.turns[0])) == [
            dataclasses.astuple(trace.turns[0])
        ]


def test_a_replace_leaves_other_sessions_alone(db: Path, fixture_trace: TraceFactory):
    """Re-exporting one session does not touch another's rows."""
    spine = fixture_trace("spine", SPINE)
    other = fixture_trace("dup_uuid", DUPS)

    with DuckDbExporter(db) as exporter:
        exporter.export(spine, "fingerprint-spine")
        exporter.export(other, "fingerprint-other")
        before = rows(exporter, "raw_records", type(other.raw_records[0]), DUPS)

        # If the spine session is re-exported with everything but its session row dropped...
        exporter.export(
            replace(spine, turns=[], api_calls=[], tool_calls=[], raw_records=[]), "fingerprint-2"
        )

        # ...then the other session keeps every row it had.
        assert rows(exporter, "raw_records", type(other.raw_records[0]), DUPS) == before
        assert counts(exporter)["raw_records"] == len(other.raw_records)


def test_extract_state_records_what_produced_the_rows(db: Path, fixture_trace: TraceFactory):
    """Each exported session leaves a fingerprint, its path, and the extractor that ran."""
    trace = fixture_trace("spine", SPINE)

    with DuckDbExporter(db) as exporter:
        exporter.export(trace, "fingerprint-1")

        state = exporter.connection.execute(
            "SELECT session_id, fingerprint, transcript_path, extractor, extractor_version "
            "FROM extract_state"
        ).fetchall()
        assert state == [
            (
                SPINE,
                "fingerprint-1",
                trace.session.transcript_path,
                trace.extractor,
                trace.extractor_version,
            )
        ]
        # ...and `fingerprints()` is exactly the map the pipeline reads to skip work.
        assert exporter.fingerprints() == {SPINE: "fingerprint-1"}


def test_an_id_is_scoped_to_its_transcript(db: Path, fixture_trace: TraceFactory):
    """The same message id under two transcripts of one session is two rows, not a clash.

    A subagent inherits ids from its own API stream, so `message.id` repeats across the
    files of one session on ~2.6% of the corpus. Only the composite key survives that.
    """
    trace = fixture_trace("spine", SPINE)
    call = trace.api_calls[0]

    with DuckDbExporter(db) as exporter:
        # If one call is recorded under the main transcript and the same id under a
        # subagent's...
        exporter.export(
            replace(trace, api_calls=[call, replace(call, source="agent-a1d0bc50fe316ed8e")]),
            "fingerprint-1",
        )
        # ...then both rows are there...
        assert counts(exporter)["api_calls"] == 2

        # ...while a genuine repeat of the whole triple is rejected.
        with pytest.raises(duckdb.ConstraintException):
            exporter.export(replace(trace, api_calls=[call, call]), "fingerprint-2")


def test_an_agent_run_is_keyed_by_session_and_agent_id(db: Path, fixture_trace: TraceFactory):
    """One agentId may run under two sessions, but not twice under one.

    A resume copies its ancestor's `subagents/` files into the new session's directory, so
    the same agentId is extracted under both session ids — two of the 2764 agent
    transcripts on this machine (scanned 2026-08-07). Only the composite key holds both.
    """
    trace = fixture_trace("spine", SPINE)
    run = trace.agent_runs[0]
    other = fixture_trace("dup_uuid", DUPS)

    with DuckDbExporter(db) as exporter:
        # If one agent run is recorded under the session that spawned it and again under
        # the resume that inherited the file...
        exporter.export(trace, "fingerprint-1")
        exporter.export(replace(other, agent_runs=[replace(run, session_id=DUPS)]), "fingerprint-2")

        # ...then both rows are there, each under its own session...
        assert counts(exporter)["agent_runs"] == len(trace.agent_runs) + 1
        assert rows(exporter, "agent_runs", type(run), DUPS) == [
            dataclasses.astuple(replace(run, session_id=DUPS))
        ]

        # ...while one session claiming an agentId twice is rejected: the id names the
        # file that produced the run, and a directory holds it once.
        with pytest.raises(duckdb.ConstraintException):
            exporter.export(replace(trace, agent_runs=[run, run]), "fingerprint-3")


def test_an_offloaded_output_is_keyed_by_session_and_name(db: Path, fixture_trace: TraceFactory):
    """A `tool-results/` file is stored whole, and two sessions may hold the same name."""
    trace = fixture_trace("offload", OFFLOAD)
    (offloaded,) = trace.offload_files

    with DuckDbExporter(db) as exporter:
        # If two sessions each offloaded a file of the same name — invented: Claude Code
        # names these randomly and none of the 636 on this machine repeats (scanned
        # 2026-08-07) — then both survive, each with its content...
        exporter.export(trace, "fingerprint-1")
        spine = fixture_trace("spine", SPINE)
        exporter.export(replace(spine, offload_files=[replace(offloaded, session_id=SPINE)]), "f-2")
        assert counts(exporter)["offload_files"] == 2
        assert rows(exporter, "offload_files", type(offloaded), OFFLOAD) == [
            dataclasses.astuple(offloaded)
        ]

        # ...while one session claiming a name twice is rejected: a directory cannot
        # hold two files of one name, so a second row would be a parser bug.
        with pytest.raises(duckdb.ConstraintException):
            exporter.export(replace(trace, offload_files=[offloaded, offloaded]), "f-3")


def test_a_failed_export_changes_nothing(db: Path, fixture_trace: TraceFactory):
    """A trace that violates a key leaves the store exactly as it was."""
    trace = fixture_trace("spine", SPINE)

    with DuckDbExporter(db) as exporter:
        exporter.export(trace, "fingerprint-1")
        before = counts(exporter)

        # If an export raises partway through...
        with pytest.raises(duckdb.ConstraintException):
            exporter.export(replace(trace, turns=[*trace.turns, trace.turns[0]]), "fingerprint-2")

        # ...then the rows and the fingerprint from the good export both survive.
        assert counts(exporter) == before
        assert exporter.fingerprints() == {SPINE: "fingerprint-1"}


def test_a_schema_version_mismatch_refuses_to_open(db: Path):
    """A store written by a different schema tells the operator to start over.

    There are no migrations while the project is early, so the message has to say what
    to do rather than leave a half-readable DB in place.
    """
    with DuckDbExporter(db) as exporter:
        exporter.connection.execute("UPDATE meta SET schema_version = ?", [SCHEMA_VERSION + 1])

    with pytest.raises(SchemaVersionError, match="re-extract"):
        DuckDbExporter(db)
