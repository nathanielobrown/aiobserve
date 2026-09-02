"""Carrying a store on disk forward to the schema this build writes.

Split from `test_duckdb.py`, which owns what an export puts in the store; this file owns what
happens to a store written by an older build, or by nobody we know.
"""

from pathlib import Path

import duckdb
import pytest

from hyphae.export.duckdb import DuckDbExporter, open_trace_store
from hyphae.export.schema import MIGRATIONS, SCHEMA_VERSION, SchemaVersionError
from hyphae.model import SessionTrace
from tests.conftest import (
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    FORK_RUN,
    NO_WAIT,
    SPINE,
    TraceFactory,
    stored_rows,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "traces.duckdb"


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


# The session `boundary_rows` plants its two runs under, and the instant they share.
BOUNDARY_SESSION = "boundary-session"
TIE_RUN = "boundary-fork-run"
PLAIN_RUN = "boundary-plain-run"
BOUNDARY_AT = "2026-08-30 22:05:03.220+00"


def boundary_rows(path: Path) -> None:
    """Plant the two back-fill cases no recorded fixture holds, into a version-7 store.

    The fork_origin fixture's copied compaction lands 1 ms before its fork's `started_at`, so
    it satisfies the rule's fork test and its timestamp test at once and can falsify neither.
    These split them. Hand-built rather than recorded: a fork whose copied compaction is
    stamped at the very instant of its first own record is a boundary Claude Code has not
    handed us, and the migration runs once per store, with nothing to re-run if it is wrong.
    """
    with duckdb.connect(str(path)) as aged:
        for run, is_fork, timestamp in (
            # The tie the rule's `<=` is for: the fork's copied compaction is the record its
            # own work starts at.
            (TIE_RUN, True, BOUNDARY_AT),
            # A run that forked nothing, whose compaction predates its start. Only the
            # `is_fork` test keeps this live compaction out of the flag.
            (PLAIN_RUN, False, "2026-08-30 22:05:00.000+00"),
        ):
            aged.execute(
                "INSERT INTO agent_runs (id, session_id, agent_type, is_fork, started_at) "
                "VALUES (?, ?, 'general-purpose', ?, ?)",
                [run, BOUNDARY_SESSION, is_fork, BOUNDARY_AT],
            )
            aged.execute(
                "INSERT INTO compactions "
                "(id, session_id, source, timestamp, trigger, pre_tokens, post_tokens, "
                "duration_ms) VALUES (?, ?, ?, ?, 'auto', 100, 10, 5)",
                [f"{run}-compaction", BOUNDARY_SESSION, run, timestamp],
            )


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
    old_store(db, trace, fixture_trace("fork_origin", FORK_ORIGIN))
    # ...plus the two boundary cases no fixture holds, so each half of the rule the back-fill
    # spells has a case that fails when it goes...
    boundary_rows(db)

    # ...then opening it migrates the file...
    exporter = DuckDbExporter(db, wait=NO_WAIT)
    # ...leaving every brief readable under the name the code now reads...
    stored = stored_rows(
        exporter.path, "SELECT brief FROM agent_runs WHERE session_id = ?", [SPINE]
    )
    assert sorted(str(brief) for (brief,) in stored) == briefs
    # ...and the copies flagged, which the migration has to derive from the rows alone: the
    # transcript line numbers the extractor reads are not in a store on disk. Against the
    # canonical store the rule it uses instead flags 4 of 1,367 compactions, the same 4 a
    # scan for a uuid two transcripts of one session both hold finds (2026-08-30). The
    # fixture's copy, and the planted fork whose copy sits at the instant its own work
    # starts — the tie the rule is written to include...
    assert stored_rows(
        exporter.path, "SELECT source FROM compactions WHERE replayed ORDER BY source"
    ) == sorted([(FORK_RUN,), (TIE_RUN,)])
    # ...while the planted run that forked nothing keeps its compaction, whatever its
    # timestamp says: a live compaction wrongly flagged would shrink a migrated corpus...
    assert stored_rows(
        exporter.path, "SELECT source FROM live_compactions ORDER BY source"
    ) == sorted([(FORK_ORIGIN_RUN,), (PLAIN_RUN,)])
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
