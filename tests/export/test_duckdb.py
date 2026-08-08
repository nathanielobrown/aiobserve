"""Writing a `SessionTrace` into the DuckDB trace store.

Traces come from the recorded fixtures rather than from hand-built dataclasses, so the
columns under test hold values a real transcript produced.
"""

import dataclasses
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from aiobserve.export.duckdb import (
    SCHEMA_VERSION,
    TABLES,  # every table a session owns — read off the exporter so a new one cannot slip past
    DuckDbExporter,
    SchemaVersionError,
)
from tests.conftest import TraceFactory

SPINE = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b"
DUPS = "8ee00a94-b01a-4394-b447-b065f74b11af"
OFFLOAD = "7e37bb35-4dcb-4e16-85be-55ac510c168e"
# The session whose fork replayed a sibling's history — see `tests/fixtures/fork_origin/`.
ORIGIN = "5a88789c-1da7-4f32-b631-40a7e243334b"
# The session that compacted twice — see `tests/fixtures/compaction/`.
COMPACTED = "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9"
# A session and the resume that copied its history forward — see `tests/fixtures/resume_pair/`.
ANCESTOR = "2352492b-1437-4427-ad51-70f35c75f663"
RESUMED = "0a76f771-5f5b-447e-852a-664fc972ea7c"


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
    # The spine session never compacted, so the compactions come from the session that did.
    compacted = fixture_trace("compaction", COMPACTED)

    with DuckDbExporter(db) as exporter:
        exporter.export(trace, "fingerprint-1")
        exporter.export(compacted, "fingerprint-2")

        # If a trace is exported, then each table holds exactly its entities, field for
        # field — including the `command_name`/`command_args` nulls on a plain prompt.
        assert rows(exporter, "sessions", type(trace.session), SPINE) == [
            dataclasses.astuple(trace.session)
        ]
        for table, entities in (
            ("turns", trace.turns),
            ("api_calls", trace.api_calls),
            ("tool_calls", trace.tool_calls),
            ("agent_runs", trace.agent_runs),
            ("pr_links", trace.pr_links),
            ("compactions", compacted.compactions),
        ):
            assert rows(exporter, table, type(entities[0]), entities[0].session_id) == sorted(
                dataclasses.astuple(entity) for entity in entities
            )
        assert counts(exporter)["raw_records"] == len(trace.raw_records) + len(
            compacted.raw_records
        )


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
            "api_calls": 7,
            "tool_calls": 9,
            "agent_runs": 2,
            "compactions": 0,
            "pr_links": 2,
            "offload_files": 0,
            "raw_records": 51,
        }

        # ...and the same session comes back shorter — one turn, one call, three lines,
        # and no PR link at all...
        trimmed = replace(
            trace,
            turns=trace.turns[:1],
            api_calls=trace.api_calls[:1],
            tool_calls=trace.tool_calls[:1],
            agent_runs=trace.agent_runs[:1],
            pr_links=[],
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
            "compactions": 0,
            "pr_links": 0,
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


def test_a_rollup_counts_replayed_work_once(db: Path, fixture_trace: TraceFactory):
    """A session's totals count a fork's copied history under whoever ran it, and once.

    Three readings of this fixture give three different totals, so the number is the whole
    argument: 7,196 output tokens if copies are counted wherever they appear, 4,904 if both
    copies are dropped, and 6,050 — the auditor's 1,146 plus the fork's own 4,904 — when
    each record counts under the transcript that ran it first.
    """
    trace = fixture_trace("fork_origin", ORIGIN)

    with DuckDbExporter(db) as exporter:
        # If a session ran an auditor and a fork that replayed it...
        exporter.export(trace, "fingerprint-1")
        rollup = exporter.connection.execute(
            "SELECT api_calls, output_tokens FROM session_rollups WHERE session_id = ?", [ORIGIN]
        ).fetchone()

        # ...then the rollup counts the copied message once and the fork's own work beside it...
        assert rollup == (3, 6050)
        # ...while the base table still holds the copy, flagged, so the archive keeps what
        # the fork's file recorded.
        assert exporter.connection.execute(
            "SELECT count(*), sum(output_tokens) FROM api_calls WHERE replayed"
        ).fetchone() == (1, 1146)


def test_a_corpus_rollup_counts_a_resumed_session_once(db: Path, fixture_trace: TraceFactory):
    """Work a resume copied from the session it continued counts under the original only.

    `/resume` writes the whole prior transcript into the new session's file, so the base
    tables hold both copies and the two rollups answer different questions: what this
    session's files say, and what this session added to the corpus.
    """
    ancestor = fixture_trace("resume_pair", ANCESTOR)
    resumed = fixture_trace("resume_pair", RESUMED)

    def rollup(exporter: DuckDbExporter, view: str) -> list[tuple[object, ...]]:
        return exporter.connection.execute(
            f"SELECT session_id, project_dir, turns, api_calls, tool_calls, compactions, "
            f"cost_usd, unpriced_api_calls FROM {view} ORDER BY started_at"
        ).fetchall()

    with DuckDbExporter(db) as exporter:
        # If a session and the resume that continued it are both exported...
        exporter.export(ancestor, "fingerprint-1")
        exporter.export(resumed, "fingerprint-2")

        # ...then each session's own rollup reports what its file holds, copies included —
        # four calls under the original, and five under the resume that copied them...
        assert rollup(exporter, "session_rollups") == [
            (ANCESTOR, "/Users/nob/repos/mycelia", 1, 4, 5, 1, pytest.approx(1.47611), 0),
            (RESUMED, "/Users/nob/repos/mycelia", 0, 5, 5, 1, pytest.approx(2.386974), 0),
        ]
        # ...while the corpus rollup credits every copied call, tool call and compaction to
        # the session that ran it first, leaving the resume its own single new call.
        assert rollup(exporter, "corpus_rollups") == [
            (ANCESTOR, "/Users/nob/repos/mycelia", 1, 4, 5, 1, pytest.approx(1.47611), 0),
            (RESUMED, "/Users/nob/repos/mycelia", 0, 1, 0, 0, pytest.approx(1.150518), 0),
        ]


def test_a_rollup_can_be_scoped_to_one_project(db: Path, fixture_trace: TraceFactory):
    """One store holds every project, and a rollup filters down to the one you asked about."""
    here = fixture_trace("spine", SPINE)
    # The same session under another checkout — invented, because the fixtures are all
    # mycelia sessions and the column, not the path, is what the test is about.
    elsewhere = fixture_trace("dup_uuid", DUPS)
    elsewhere = replace(elsewhere, session=replace(elsewhere.session, project_dir="/repos/other"))

    with DuckDbExporter(db) as exporter:
        # If two projects' sessions share the store...
        exporter.export(here, "fingerprint-1")
        exporter.export(elsewhere, "fingerprint-2")

        # ...then a rollup filtered by project reports that project's sessions and no others.
        assert exporter.connection.execute(
            "SELECT session_id FROM corpus_rollups WHERE project_dir = ?", ["/repos/other"]
        ).fetchall() == [(DUPS,)]
        assert exporter.connection.execute(
            "SELECT count(*) FROM corpus_rollups WHERE project_dir = ?",
            [here.session.project_dir],
        ).fetchone() == (1,)


def test_a_call_we_cannot_price_is_counted_out_of_the_total(db: Path, fixture_trace: TraceFactory):
    """A cost total says how many calls it left out, so it is never read as complete.

    Our price table is ours, not Claude Code's: a model it lacks prices as NULL rather
    than as free, and the rollup carries the gap beside the sum.
    """
    trace = fixture_trace("spine", SPINE)
    priced, unpriced = trace.api_calls[0], trace.api_calls[1]

    with DuckDbExporter(db) as exporter:
        # If a session holds a call whose model our table does not price — invented by
        # nulling a real call's cost, since every model the corpus used is priced...
        exporter.export(
            replace(trace, api_calls=[priced, replace(unpriced, cost_usd=None)]), "fingerprint-1"
        )

        # ...then the total sums the calls we could price, and says one was left out.
        assert exporter.connection.execute(
            "SELECT cost_usd, unpriced_api_calls FROM session_rollups WHERE session_id = ?", [SPINE]
        ).fetchone() == (pytest.approx(priced.cost_usd), 1)


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


def shape(path: Path) -> dict[str, list[str]]:
    """Every table in the file and its column names — what opening a store may not change."""
    with duckdb.connect(str(path), read_only=True) as connection:
        return {
            table: [
                row[0]
                for row in connection.execute(
                    f'SELECT column_name FROM (DESCRIBE "{table}")'
                ).fetchall()
            ]
            for (table,) in connection.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }


def old_store(path: Path) -> dict[str, list[str]]:
    """A store as an older schema left it: a stamped `meta`, and tables of that vintage.

    Hand-built rather than checked out: what matters is a `sessions` table the current
    view DDL cannot bind against, which every schema before version 6 had — the archives
    kept beside `data/traces.duckdb` are exactly such files.
    """
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE meta (schema_version INTEGER NOT NULL)")
        connection.execute("INSERT INTO meta VALUES (?)", [SCHEMA_VERSION - 1])
        connection.execute("CREATE TABLE sessions (id VARCHAR PRIMARY KEY, project_dir VARCHAR)")
    return shape(path)


def test_a_schema_version_mismatch_refuses_to_open(db: Path):
    """A store written by an older schema is left untouched, with a message saying what to do.

    There are no migrations while the project is early, so the message has to say what to
    do rather than leave a half-readable DB in place. The check has to run before any DDL:
    `CREATE TABLE IF NOT EXISTS` would otherwise add current-schema tables to the old file,
    and the view DDL would crash on the columns it lacks long before the version is read.
    """
    # If a store written by an older schema is opened...
    before = old_store(db)

    # ...then it says which version it holds and what to do about it — pointing at the store
    # guide rather than at a delete, because this file may be a pruned session's only home...
    with pytest.raises(SchemaVersionError, match="docs/store.md"):
        DuckDbExporter(db)

    # ...and not one table of it was written to.
    assert shape(db) == before


def test_a_foreign_database_refuses_to_open(db: Path):
    """A DuckDB file that is not a trace store is refused rather than built on top of."""
    # If the path names someone else's database...
    with duckdb.connect(str(db)) as connection:
        connection.execute("CREATE TABLE inventory (sku VARCHAR)")
    before = shape(db)

    # ...then opening it fails without adding our tables to it.
    with pytest.raises(SchemaVersionError, match="re-extract"):
        DuckDbExporter(db)
    assert shape(db) == before


def test_a_newer_schema_version_refuses_to_open(db: Path):
    """A store this build is too old to read is refused too, not just one it is too new for."""
    with DuckDbExporter(db) as exporter:
        exporter.connection.execute("UPDATE meta SET schema_version = ?", [SCHEMA_VERSION + 1])

    with pytest.raises(SchemaVersionError, match="docs/store.md"):
        DuckDbExporter(db)
