"""Writing a `SessionTrace` into the DuckDB trace store.

Traces come from the recorded fixtures rather than from hand-built dataclasses, so the
columns under test hold values a real transcript produced.
"""

import dataclasses
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from hyphae.export.duckdb import (
    TABLES,  # every table a session owns — read off the exporter so a new one cannot slip past
    DuckDbExporter,
    open_trace_store,
)
from hyphae.export.schema import MIGRATIONS, SCHEMA_VERSION, SchemaVersionError
from hyphae.model import SessionTrace
from tests.conftest import FORK_RUN, NO_WAIT, TraceFactory, stored_rows

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
        table: stored_rows(exporter.path, f"SELECT count(*) FROM {table}")[0][0] for table in TABLES
    }


def rows(
    exporter: DuckDbExporter, table: str, columns: type, session: str = "%"
) -> list[tuple[object, ...]]:
    """Rows of `table` for one session, column order matching `columns`' fields."""
    names = ", ".join(f'"{field.name}"' for field in dataclasses.fields(columns))
    key = "id" if table == "sessions" else "session_id"
    return stored_rows(
        exporter.path, f"SELECT {names} FROM {table} WHERE {key} LIKE ? ORDER BY 1, 2", [session]
    )


def test_a_trace_round_trips(db: Path, fixture_trace: TraceFactory):
    """Every column of an exported trace reads back as it was written, nulls included."""
    trace = fixture_trace("spine", SPINE)
    # The spine session never compacted, so the compactions come from the session that did.
    compacted = fixture_trace("compaction", COMPACTED)

    exporter = DuckDbExporter(db, wait=NO_WAIT)
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
    assert counts(exporter)["raw_records"] == len(trace.raw_records) + len(compacted.raw_records)


def test_re_exporting_a_session_replaces_it_wholly(db: Path, fixture_trace: TraceFactory):
    """A second export of the same session leaves no row from the first behind.

    Idempotency rests on the delete covering every table a session owns. A table added
    later and forgotten in the delete would keep stale rows forever, so this counts them
    all rather than checking one.
    """
    trace = fixture_trace("spine", SPINE)
    # If a full trace is exported...
    exporter = DuckDbExporter(db, wait=NO_WAIT)
    exporter.export(trace, "fingerprint-1")
    assert counts(exporter) == {
        "sessions": 1,
        "turns": 6,
        "api_calls": 9,
        "tool_calls": 12,
        "agent_runs": 2,
        "compactions": 0,
        "pr_links": 2,
        "offload_files": 0,
        "raw_records": 57,
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
    assert rows(exporter, "turns", type(trace.turns[0])) == [dataclasses.astuple(trace.turns[0])]


def test_a_replace_leaves_other_sessions_alone(db: Path, fixture_trace: TraceFactory):
    """Re-exporting one session does not touch another's rows."""
    spine = fixture_trace("spine", SPINE)
    other = fixture_trace("dup_uuid", DUPS)

    exporter = DuckDbExporter(db, wait=NO_WAIT)
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

    exporter = DuckDbExporter(db, wait=NO_WAIT)
    exporter.export(trace, "fingerprint-1")

    state = stored_rows(
        exporter.path,
        "SELECT session_id, fingerprint, transcript_path, extractor, extractor_version "
        "FROM extract_state",
    )
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

    exporter = DuckDbExporter(db, wait=NO_WAIT)
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

    exporter = DuckDbExporter(db, wait=NO_WAIT)
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

    exporter = DuckDbExporter(db, wait=NO_WAIT)
    # If a session ran an auditor and a fork that replayed it...
    exporter.export(trace, "fingerprint-1")
    (rollup,) = stored_rows(
        exporter.path,
        "SELECT api_calls, output_tokens, compactions FROM session_rollups WHERE session_id = ?",
        [ORIGIN],
    )

    # ...then the rollup counts the copied message once and the fork's own work beside it,
    # and reads the compaction the fork inherited with that history the same way...
    assert rollup == (3, 6050, 1)
    # ...while the base tables still hold both copies, flagged, so the archive keeps what
    # the fork's file recorded.
    assert stored_rows(
        exporter.path, "SELECT count(*), sum(output_tokens) FROM api_calls WHERE replayed"
    ) == [(1, 1146)]
    assert stored_rows(exporter.path, "SELECT count(*) FROM compactions WHERE replayed") == [(1,)]


def test_a_corpus_rollup_counts_a_resumed_session_once(db: Path, fixture_trace: TraceFactory):
    """Work a resume copied from the session it continued counts under the original only.

    `/resume` writes the whole prior transcript into the new session's file, so the base
    tables hold both copies and the two rollups answer different questions: what this
    session's files say, and what this session added to the corpus.
    """
    ancestor = fixture_trace("resume_pair", ANCESTOR)
    resumed = fixture_trace("resume_pair", RESUMED)

    def rollup(exporter: DuckDbExporter, view: str) -> list[tuple[object, ...]]:
        return stored_rows(
            exporter.path,
            f"SELECT session_id, project_dir, turns, api_calls, tool_calls, compactions, "
            f"cost_usd, unpriced_api_calls FROM {view} ORDER BY started_at",
        )

    exporter = DuckDbExporter(db, wait=NO_WAIT)
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

    exporter = DuckDbExporter(db, wait=NO_WAIT)
    # If two projects' sessions share the store...
    exporter.export(here, "fingerprint-1")
    exporter.export(elsewhere, "fingerprint-2")

    # ...then a rollup filtered by project reports that project's sessions and no others.
    assert stored_rows(
        exporter.path,
        "SELECT session_id FROM corpus_rollups WHERE project_dir = ?",
        ["/repos/other"],
    ) == [(DUPS,)]
    assert stored_rows(
        exporter.path,
        "SELECT count(*) FROM corpus_rollups WHERE project_dir = ?",
        [here.session.project_dir],
    ) == [(1,)]


def test_a_call_we_cannot_price_is_counted_out_of_the_total(db: Path, fixture_trace: TraceFactory):
    """A cost total says how many calls it left out, so it is never read as complete.

    Our price table is ours, not Claude Code's: a model it lacks prices as NULL rather
    than as free, and the rollup carries the gap beside the sum.
    """
    trace = fixture_trace("spine", SPINE)
    priced, unpriced = trace.api_calls[0], trace.api_calls[1]

    exporter = DuckDbExporter(db, wait=NO_WAIT)
    # If a session holds a call whose model our table does not price — invented by
    # nulling a real call's cost, since every model the corpus used is priced...
    exporter.export(
        replace(trace, api_calls=[priced, replace(unpriced, cost_usd=None)]), "fingerprint-1"
    )

    # ...then the total sums the calls we could price, and says one was left out.
    assert stored_rows(
        exporter.path,
        "SELECT cost_usd, unpriced_api_calls FROM session_rollups WHERE session_id = ?",
        [SPINE],
    ) == [(pytest.approx(priced.cost_usd), 1)]


def test_an_offloaded_output_is_keyed_by_session_and_name(db: Path, fixture_trace: TraceFactory):
    """A `tool-results/` file is stored whole, and two sessions may hold the same name."""
    trace = fixture_trace("offload", OFFLOAD)
    (offloaded,) = trace.offload_files

    exporter = DuckDbExporter(db, wait=NO_WAIT)
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

    exporter = DuckDbExporter(db, wait=NO_WAIT)
    exporter.export(trace, "fingerprint-1")
    before = counts(exporter)

    # If an export raises partway through...
    with pytest.raises(duckdb.ConstraintException):
        exporter.export(replace(trace, turns=[*trace.turns, trace.turns[0]]), "fingerprint-2")

    # ...then the rows and the fingerprint from the good export both survive.
    assert counts(exporter) == before
    assert exporter.fingerprints() == {SPINE: "fingerprint-1"}


def test_a_view_definition_reaches_a_reader_without_a_re_extract(
    db: Path, fixture_trace: TraceFactory
):
    """A view is rebuilt from the code at every open, so editing one takes effect at once.

    The definitions live in `export/duckdb.py`, but a store on disk carries a copy of the
    text that was current when it was last extracted. A reader that answered off that copy
    would report yesterday's rule for as long as nothing re-extracted the file.
    """
    trace = fixture_trace("spine", SPINE)
    exporter = DuckDbExporter(db, wait=NO_WAIT)
    exporter.export(trace, "fingerprint-1")
    # If the file's stored view definitions are older than the code's — the state an
    # edit to `_live_view` leaves every store extracted before it...
    with duckdb.connect(str(db)) as stale:
        stale.execute("CREATE OR REPLACE VIEW live_turns AS SELECT * FROM turns WHERE false")
    assert stale_turns(db) == 0

    # ...then a reader answers off the code's definition, both directly...
    with open_trace_store(db, read_only=True, wait=NO_WAIT) as connection:
        assert connection.execute("SELECT count(*) FROM live_turns").fetchone() == (
            len(trace.turns),
        )
        # ...and through `session_rollups`, which reads the `live_*` family by name.
        assert connection.execute(
            "SELECT turns FROM session_rollups WHERE session_id = ?", [SPINE]
        ).fetchone() == (len(trace.turns),)
    # A reader cannot write, so it shadows the stale definition rather than repairing it...
    assert stale_turns(db) == 0
    # ...and the next open for write is what puts the current text back in the file.
    with open_trace_store(db, read_only=False, wait=NO_WAIT):
        pass
    assert stale_turns(db) == len(trace.turns)


def stale_turns(path: Path) -> int:
    """What `live_turns` answers off the definition stored in the file itself.

    A plain connection, so nothing refreshes the views first: this is the question only a
    store's own text can answer.
    """
    with duckdb.connect(str(path), read_only=True) as connection:
        return connection.execute("SELECT count(*) FROM live_turns").fetchone()[0]  # type: ignore[index]


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


def stamped_version(path: Path) -> int | None:
    """The version a store on disk carries, read without opening it as a store."""
    with duckdb.connect(str(path), read_only=True) as connection:
        row = connection.execute("SELECT schema_version FROM meta").fetchone()
    return None if row is None else row[0]


# The last version the migrations can carry a store forward from. A store older than this
# one has no path to the current schema, whatever else it holds.
OLDEST_MIGRATABLE = min(MIGRATIONS) - 1


def old_store(path: Path, *traces: SessionTrace) -> dict[str, list[str]]:
    """A store as schema 7 left it: `agent_runs.description` and compactions with no flag.

    Built by inverting both steps rather than by checking out the old code. They are the whole
    difference between that version and this one, so the file this leaves is what a version-7
    extract wrote, row for row — including the briefs and the copied compaction the rows below
    are read back for.
    """
    exporter = DuckDbExporter(path, wait=NO_WAIT)
    for index, trace in enumerate(traces):
        exporter.export(trace, f"fingerprint-{index}")
    with duckdb.connect(str(path)) as aged:
        aged.execute("ALTER TABLE agent_runs RENAME brief TO description")
        aged.execute("ALTER TABLE compactions DROP COLUMN replayed")
        aged.execute("UPDATE meta SET schema_version = ?", [OLDEST_MIGRATABLE])
    return shape(path)


def unmigratable_store(path: Path) -> dict[str, list[str]]:
    """A store of a vintage no migration step reaches: a stamped `meta`, and its own tables.

    Hand-built rather than checked out: what matters is a `sessions` table the current view
    DDL cannot bind against, which every schema before version 6 had — the archives kept
    beside `data/traces.duckdb` are exactly such files.
    """
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE meta (schema_version INTEGER NOT NULL)")
        connection.execute("INSERT INTO meta VALUES (?)", [OLDEST_MIGRATABLE - 1])
        connection.execute("CREATE TABLE sessions (id VARCHAR PRIMARY KEY, project_dir VARCHAR)")
    return shape(path)


def test_a_schema_version_no_migration_reaches_refuses_to_open(db: Path):
    """A store too old for any migration step is left untouched, with the remedy in its message.

    The check has to run before any DDL: `CREATE TABLE IF NOT EXISTS` would otherwise add
    current-schema tables to the old file, and the view DDL would crash on the columns it
    lacks long before the version is read.
    """
    # If a store no step can carry forward is opened...
    before = unmigratable_store(db)

    # ...then it says which version it holds and what to do about it — pointing at the store
    # guide rather than at a delete, because this file may be a pruned session's only home...
    with pytest.raises(SchemaVersionError, match=r"docs/store\.md"):
        DuckDbExporter(db, wait=NO_WAIT)

    # ...and not one table of it was written to.
    assert shape(db) == before


def test_an_older_store_is_migrated_and_keeps_its_rows(db: Path, fixture_trace: TraceFactory):
    """A store of an older vintage is carried forward on open, with every row still readable.

    Version 8 renamed `agent_runs.description` to `brief`; version 9 gave a compaction the
    `replayed` flag every other copied row already carried. Opening a version-7 store applies
    both in place: the archive can hold the only copy of a session Claude Code has pruned, so
    a schema change has to move a store forward rather than ask for a fresh one.
    """
    # If a store written at version 7 holds agent runs under the old column name, and a
    # session whose fork copied a compaction out of the transcript it forked...
    trace = fixture_trace("spine", SPINE)
    briefs = sorted(str(run.brief) for run in trace.agent_runs)
    assert any(briefs), "the spine fixture is the evidence here — its runs carry briefs"
    old_store(db, trace, fixture_trace("fork_origin", ORIGIN))

    # ...then opening it migrates the file...
    exporter = DuckDbExporter(db, wait=NO_WAIT)
    # ...leaving every brief readable under the name the code now reads...
    stored = stored_rows(
        exporter.path, "SELECT brief FROM agent_runs WHERE session_id = ?", [SPINE]
    )
    assert sorted(str(brief) for (brief,) in stored) == briefs
    # ...and the copy flagged, which the migration has to derive from the rows alone: the
    # transcript line numbers the extractor reads are not in a store on disk. Against the
    # canonical store the rule it uses instead flags 4 of 1,367 compactions, the same 4 a
    # scan for a uuid two transcripts of one session both hold finds (2026-08-30)...
    assert stored_rows(exporter.path, "SELECT source FROM compactions WHERE replayed") == [
        (FORK_RUN,)
    ]
    assert stored_rows(exporter.path, "SELECT count(*) FROM live_compactions") == [(1,)]
    # ...and the store stamped at the version this build writes.
    assert stored_rows(exporter.path, "SELECT schema_version FROM meta") == [(SCHEMA_VERSION,)]
    assert stamped_version(db) == SCHEMA_VERSION


def test_a_read_only_open_of_an_older_store_says_how_to_migrate(
    db: Path, fixture_trace: TraceFactory
):
    """A reader cannot migrate a store, so it is told what will: one open for write.

    The viewer and the query runner both open read-only, so this is the message a reader
    sees after a schema change lands.
    """
    # If a store of an older vintage is opened by a reader...
    before = old_store(db, fixture_trace("spine", SPINE))

    # ...then it is refused with the one action that fixes it, not with the fresh-store
    # remedy that would throw the archive away...
    with (
        pytest.raises(SchemaVersionError, match="for write"),
        open_trace_store(db, read_only=True, wait=NO_WAIT),
    ):
        pass

    # ...and the file it could not migrate is untouched.
    assert shape(db) == before
    assert stamped_version(db) == OLDEST_MIGRATABLE


def test_a_migration_step_that_raises_leaves_the_store_at_its_old_version(
    db: Path, fixture_trace: TraceFactory, monkeypatch: pytest.MonkeyPatch
):
    """A failed migration is a store nothing happened to, not one caught half-way.

    The steps and the stamp share one transaction. Without it a step that failed after an
    earlier one succeeded would leave a shape no version describes — the state no later run
    could reason about, since the stamp is all a store says about itself.
    """
    before = old_store(db, fixture_trace("spine", SPINE))

    def raise_after_dropping(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute("ALTER TABLE compactions DROP COLUMN duration_ms")
        raise RuntimeError("the step failed half-way")

    # If the last step fails after changing the store, with an earlier step's rename already
    # applied...
    monkeypatch.setitem(MIGRATIONS, SCHEMA_VERSION, raise_after_dropping)
    with pytest.raises(RuntimeError, match="half-way"):
        DuckDbExporter(db, wait=NO_WAIT)

    # ...then the file holds its original columns at its original version, and the next open
    # will try the whole migration again.
    assert shape(db) == before
    assert stamped_version(db) == OLDEST_MIGRATABLE


def test_a_current_store_is_migrated_by_no_step_at_all(
    db: Path, fixture_trace: TraceFactory, monkeypatch: pytest.MonkeyPatch
):
    """Opening a store at the current version runs no migration, however often it is opened."""
    exporter = DuckDbExporter(db, wait=NO_WAIT)
    exporter.export(fixture_trace("spine", SPINE), "fingerprint-1")
    before = shape(db)

    # If every registered step would raise, and a store already at the current version is
    # opened...
    def refuse(_connection: duckdb.DuckDBPyConnection) -> None:
        raise AssertionError("a step ran against a store that was already current")

    monkeypatch.setitem(MIGRATIONS, SCHEMA_VERSION, refuse)
    # ...then it opens, and opens again, unchanged.
    DuckDbExporter(db, wait=NO_WAIT)
    DuckDbExporter(db, wait=NO_WAIT)
    assert shape(db) == before
    assert stamped_version(db) == SCHEMA_VERSION


def foreign_store(path: Path) -> dict[str, list[str]]:
    """Someone else's DuckDB file: real tables, and no `meta` to read a version out of."""
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE inventory (sku VARCHAR)")
    return shape(path)


def test_a_foreign_database_refuses_to_open(db: Path):
    """A DuckDB file that is not a trace store is refused rather than built on top of."""
    # If the path names someone else's database...
    before = foreign_store(db)

    # ...then opening it fails without adding our tables to it.
    with pytest.raises(SchemaVersionError, match="re-extract"):
        DuckDbExporter(db, wait=NO_WAIT)
    assert shape(db) == before


def test_a_newer_schema_version_refuses_to_open(db: Path):
    """A store this build is too old to read is refused too, not just one it is too new for."""
    DuckDbExporter(db, wait=NO_WAIT)
    with duckdb.connect(str(db)) as ahead:
        ahead.execute("UPDATE meta SET schema_version = ?", [SCHEMA_VERSION + 1])

    with pytest.raises(SchemaVersionError, match=r"docs/store\.md"):
        DuckDbExporter(db, wait=NO_WAIT)
