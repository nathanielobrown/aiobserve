"""The version the whole store file carries, and the two guards that hold a file to it.

Three modules create tables in the one DuckDB file — `export/duckdb.py`, `enrich/store.py`,
and `export/otlp_delivery.py` — so the version stamps the file rather than any one owner's
tables, and lives here instead of with one of them. This module imports nothing from
`hyphae`: the owners import it, and one of them already imports another.

Both guards run before an owner's DDL touches a file. `migrate` carries a store forward to
`SCHEMA_VERSION` by the steps in `MIGRATIONS`. `check_shape` refuses a store whose tables no
longer match the DDL that declares them, which is what a rename with no migration behind it
leaves: `CREATE TABLE IF NOT EXISTS` skips the table that exists, `SELECT *` views rebind
across the rename, and the mismatch surfaces at the first insert as a binder error naming a
column, with no version and no remedy in it.
"""

import re
from collections.abc import Callable
from pathlib import Path

import duckdb

# Bumped whenever any owner's stored tables change. A store older than this is carried
# forward by `MIGRATIONS`; one older than every step there is refused.
SCHEMA_VERSION = 8

# The remedy a version-mismatch message carries when no migration can help, written once
# because getting it wrong is expensive: a store can be the only copy of a session Claude Code
# has pruned from disk, so the operator is sent to `docs/store.md` — which holds the check —
# rather than to `rm`.
SCHEMA_MISMATCH_REMEDY = (
    "Extract into a fresh store. This one may hold the only copy of a pruned session — "
    "read docs/store.md before deleting it."
)

# What a reader is told about a store a writer would migrate. Only a write open can: DuckDB
# admits one writer at a time, and a read-only connection cannot run the steps.
MIGRATE_REMEDY = "Open it for write once to migrate it — `hp extract` or `hp enrich` will."


class SchemaVersionError(Exception):
    """The store on disk was written by a version of the schema this build cannot use."""


class SchemaShapeError(Exception):
    """A store's tables no longer match the DDL that declares them."""


_VIEW_STATEMENT = re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b", re.IGNORECASE)


def table_ddl(ddl: str) -> str:
    """The statements of `ddl` that create tables, with every view statement dropped.

    Views are excluded everywhere the schema is pinned or compared: they are `CREATE OR
    REPLACE`, so every open rebuilds them from the code and no store can hold a stale one.
    """
    return ";".join(
        statement for statement in ddl.split(";") if not _VIEW_STATEMENT.search(statement)
    )


def declared_shape(ddl: str) -> dict[str, set[str]]:
    """The tables a DDL creates and the columns of each, derived by running it.

    Against a scratch in-memory database, so the answer is DuckDB's rather than a second
    copy of the schema kept by hand beside the first.
    """
    with duckdb.connect() as scratch:
        scratch.execute(table_ddl(ddl))
        rows = scratch.execute(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c "
            "JOIN information_schema.tables t USING (table_catalog, table_schema, table_name) "
            "WHERE t.table_type = 'BASE TABLE'"
        ).fetchall()
    shape: dict[str, set[str]] = {}
    for table, column in rows:
        shape.setdefault(table, set()).add(column)
    return shape


def check_shape(connection: duckdb.DuckDBPyConnection, ddl: str) -> None:
    """Refuse a store whose tables have drifted from `ddl`, before that DDL runs against it.

    Call it from whatever owns the DDL, immediately before executing it. A declared table the
    store lacks is not drift — the enrichment and delivery tables exist only once those
    layers have run — so what this catches is a table that exists with other columns.
    """
    declared = declared_shape(ddl)
    drifted = []
    for table in sorted(declared):
        stored = _stored_columns(connection, table)
        if stored is None or stored == declared[table]:
            continue
        extra = ", ".join(sorted(stored - declared[table])) or "nothing the code lacks"
        missing = ", ".join(sorted(declared[table] - stored)) or "nothing the store lacks"
        drifted.append(f"  {table}: the store has {extra}; the code expects {missing}")
    if drifted:
        raise SchemaShapeError(
            f"{_database_path(connection)} holds tables this build's schema does not "
            f"describe:\n" + "\n".join(drifted) + "\nAdd a migration step to "
            "src/hyphae/export/schema.py and bump SCHEMA_VERSION. Read docs/store.md first: "
            "this store may hold the only copy of a pruned session."
        )


def _stored_columns(connection: duckdb.DuckDBPyConnection, table: str) -> set[str] | None:
    """The columns a store holds for one table, or None when it holds no such table."""
    columns = {
        name
        for (name,) in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table]
        ).fetchall()
    }
    return columns or None


def _database_path(connection: duckdb.DuckDBPyConnection) -> str:
    """The file a connection is open on, for a message that has to name it.

    Read off the connection rather than passed in, so every owner's refusal names the file
    DuckDB really has open — including the one owner that is handed a connection and never
    sees a path.
    """
    row = connection.execute(
        "SELECT path FROM duckdb_databases() WHERE database_name = current_database()"
    ).fetchone()
    return str(row[0]) if row and row[0] else "This store"


def _rename_agent_run_description_to_brief(connection: duckdb.DuckDBPyConnection) -> None:
    """7 -> 8: `agent_runs.description` became `brief`, the name the spawner's text goes by."""
    connection.execute("ALTER TABLE agent_runs RENAME description TO brief")


# Each step keyed by the version it produces, so a store at version N is carried forward by
# every step above N in order. A step edits tables in place: the archive can hold the only
# copy of a session Claude Code has pruned, so a schema change moves a store rather than
# asking for a fresh one.
MIGRATIONS: dict[int, Callable[[duckdb.DuckDBPyConnection], None]] = {
    8: _rename_agent_run_description_to_brief,
}


def missing_steps(held: int) -> list[int]:
    """The versions between `held` and this build's that no migration step produces.

    Empty means a store at `held` can be carried forward. Ask before refusing one, so a
    reader is told which of the two remedies applies to the file in front of it.
    """
    return [version for version in range(held + 1, SCHEMA_VERSION + 1) if version not in MIGRATIONS]


def held_schema_version(connection: duckdb.DuckDBPyConnection) -> int | None:
    """The version stamped in an open store, or None when it carries no stamp at all.

    None covers both a file that is not a trace store and one that crashed between its DDL
    and its stamp, so every reader can say "holds nothing" instead of raising a catalog
    error on someone else's database — and, raising nothing, leaves its caller free to close
    the connection. Asking the catalog has to be its own statement: DuckDB binds every table
    a query names before any filter in that same query can spare it.
    """
    stamped = connection.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'meta'"
    ).fetchone()
    if not stamped or not stamped[0]:
        return None
    row = connection.execute("SELECT schema_version FROM meta").fetchone()
    return None if row is None else row[0]


def check_version(connection: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Refuse a store this build's schema does not fit, with the remedy that fits the file.

    Call it after `migrate` on a write connection, and on its own for a read-only one: a
    reader cannot migrate, so a store an open for write would carry forward is refused here
    with that instruction rather than with the fresh-store remedy.
    """
    held = held_schema_version(connection)
    if held == SCHEMA_VERSION:
        return
    migratable = held is not None and held < SCHEMA_VERSION and not missing_steps(held)
    raise SchemaVersionError(
        f"{path} holds schema version {held or 'nothing'}, this build reads {SCHEMA_VERSION}. "
        f"{MIGRATE_REMEDY if migratable else SCHEMA_MISMATCH_REMEDY}"
    )


def migrate(connection: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Carry a store forward to `SCHEMA_VERSION`, or refuse one no step can reach.

    Call it on a write connection before any DDL. A store already at `SCHEMA_VERSION`, and one
    carrying no stamp at all — a fresh file, or one that crashed between its DDL and its
    stamp — are left alone. The steps and the stamp share one transaction, so a failure
    leaves the file at the version it came in at rather than in a shape no version describes.
    """
    held = held_schema_version(connection)
    if held is None or held == SCHEMA_VERSION:
        return
    if held > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"{path} holds schema version {held}, this build writes {SCHEMA_VERSION}. "
            f"Migrations only run forward — update hyphae. {SCHEMA_MISMATCH_REMEDY}"
        )
    if missing_steps(held):
        raise SchemaVersionError(
            f"{path} holds schema version {held}, this build writes {SCHEMA_VERSION}, and no "
            f"migration reaches version {missing_steps(held)[0]}. {SCHEMA_MISMATCH_REMEDY}"
        )
    connection.begin()
    try:
        for version in range(held + 1, SCHEMA_VERSION + 1):
            MIGRATIONS[version](connection)
        connection.execute("UPDATE meta SET schema_version = ?", [SCHEMA_VERSION])
    except Exception:
        connection.rollback()
        raise
    connection.commit()
