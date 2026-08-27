"""The version the whole store file carries, and where it is read from.

Three modules create tables in the one DuckDB file — `export/duckdb.py`, `enrich/store.py`,
and `export/otlp_delivery.py` — so the version stamps the file rather than any one owner's
tables, and lives here instead of with one of them. This module imports nothing from
`hyphae`: the owners import it, and one of them already imports another.
"""

import duckdb

# Bumped whenever any owner's stored tables change. A store that holds another version is
# refused: there are no migrations while the project is early.
SCHEMA_VERSION = 8

# The remedy a version-mismatch message carries when no migration can help, written once
# because getting it wrong is expensive: a store can be the only copy of a session Claude Code
# has pruned from disk, so the operator is sent to `docs/store.md` — which holds the check —
# rather than to `rm`.
SCHEMA_MISMATCH_REMEDY = (
    "Extract into a fresh store. This one may hold the only copy of a pruned session — "
    "read docs/store.md before deleting it."
)


class SchemaVersionError(Exception):
    """The store on disk was written by a version of the schema this build cannot use."""


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
