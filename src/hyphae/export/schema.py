"""The version the whole store file carries, and the steps that carry a store to it.

Three modules create tables in the one DuckDB file — `export/duckdb.py`, `enrich/store.py`,
and `export/otlp_delivery.py` — so the version stamps the file rather than any one owner's
tables, and lives here instead of with one of them. This module imports nothing from
`hyphae`: the owners import it, and one of them already imports another.

`migrate` carries a store forward to `SCHEMA_VERSION` by the steps in `MIGRATIONS`, before
any owner's DDL touches the file. The archive can hold the only copy of a session Claude
Code has pruned from disk, so a schema change moves a store rather than replacing it.
"""

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
