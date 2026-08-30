"""The file-wide schema: what each owner's DDL declares, and the guard that pins it.

Three modules create tables in the one DuckDB file, so the leaves here open a store the way
the code does and then bend it: the drift a guard has to catch is what a real open leaves
behind when a column is renamed and the version is not bumped.
"""

import hashlib
import os
from pathlib import Path

import duckdb
import pytest

from hyphae.enrich.store import _SCHEMA as ENRICHMENT_SCHEMA
from hyphae.enrich.store import EnrichmentStore
from hyphae.export.duckdb import _SCHEMA as TRACE_SCHEMA
from hyphae.export.duckdb import DuckDbExporter, open_trace_store
from hyphae.export.otlp_delivery import _DELIVERY_SCHEMA as DELIVERY_SCHEMA
from hyphae.export.otlp_delivery import Backend, OtlpExporter
from hyphae.export.schema import (
    SCHEMA_VERSION,
    SchemaShapeError,
    check_shape,
    declared_shape,
    table_ddl,
)
from tests.conftest import lock_is_free


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "traces.duckdb"


# Every module that creates a stored table, and the digest of its table DDL at the current
# `SCHEMA_VERSION`. Update a digest and the version together, never one alone: the pair is
# the whole point of the leaf below. Views are left out of the digest — `CREATE OR REPLACE`
# rebuilds one at every open, so no store can drift from it.
DDL_OWNERS = [
    pytest.param(
        "hyphae.export.duckdb",
        TRACE_SCHEMA,
        "7252a61c6ebbf68580bf2136854843f4d2f546dadb82e1172fa44535d00178a8",
        id="trace",
    ),
    pytest.param(
        "hyphae.enrich.store",
        ENRICHMENT_SCHEMA,
        "a24819f7cb8b1b09ad8e6d00661c6dbe26dc0495c2534bd534b1fcb3d7e9c14c",
        id="enrichment",
    ),
    pytest.param(
        "hyphae.export.otlp_delivery",
        DELIVERY_SCHEMA,
        "90437444e97303d1fa036146bd70d273e40aaaf1f541ee6625d08f6eae2bce2f",
        id="otlp-delivery",
    ),
]


@pytest.mark.parametrize(("owner", "ddl", "digest"), DDL_OWNERS)
def test_no_owners_tables_can_change_without_the_schema_version(
    owner: str, ddl: str, digest: str
) -> None:
    """Editing a DDL without bumping `SCHEMA_VERSION` fails here rather than at an INSERT.

    Opening a store compares versions, and `CREATE TABLE IF NOT EXISTS` leaves an existing
    table alone. So a renamed column in a store that still stamps the old version passes the
    version check and then crashes on the first write. `check_shape` catches that at the
    door, but only the version carries the fix forward to a store already on disk — nothing
    at runtime ties the DDL to the version, and this is the tie.
    """
    current = hashlib.sha256(table_ddl(ddl).encode()).hexdigest()
    assert current == digest, (
        f"The tables {owner} declares changed at SCHEMA_VERSION {SCHEMA_VERSION}. Bump the "
        f"version and add the migration step that carries an existing store across the "
        f"change, then set this digest to {current}."
    )


def test_a_declared_shape_holds_every_table_a_ddl_creates_and_none_of_its_views() -> None:
    """The shape is derived by running the DDL, so nothing hand-written can drift from it."""
    shape = declared_shape(TRACE_SCHEMA)

    # If a DDL is declared, then its shape names each table's columns...
    assert "brief" in shape["agent_runs"]
    assert "description" not in shape["agent_runs"]
    assert shape.keys() >= {"sessions", "turns", "api_calls", "agent_runs", "meta"}

    # ...and leaves out the views an owner declares beside its tables: a view is rebuilt at
    # every open, so a store cannot hold a stale one.
    assert "enriched_turns" not in declared_shape(ENRICHMENT_SCHEMA)


def test_a_renamed_trace_column_is_refused_with_the_table_and_column_named(db: Path) -> None:
    """The shape a rename without a migration leaves is caught before the first insert.

    This is the recorded bug: the DDL said `brief`, the store still held `description`, and
    the `live_*` views are `SELECT *`, so they rebound across the rename and hid it. What
    reached the operator was a binder error naming a column, with no version and no remedy.
    """
    # If a store's `agent_runs` no longer holds the column the DDL declares...
    with DuckDbExporter(db) as exporter:
        exporter.connection.execute("ALTER TABLE agent_runs RENAME brief TO description")

    # ...then opening it says which table drifted, which column each side has, and where to
    # read before touching an archive.
    with pytest.raises(SchemaShapeError) as refused:
        DuckDbExporter(db)
    message = str(refused.value)
    assert "agent_runs" in message
    assert "description" in message and "brief" in message
    assert "docs/store.md" in message


def test_a_renamed_enrichment_column_is_refused_the_same_way(db: Path) -> None:
    """Every owner gets the guard, not just the trace tables.

    Enrichment reproduced the same bug: a store written before `friction` existed died at
    open with `Binder Error: Table "e" does not have a column named "friction"`.
    """
    # If the enrichment tables exist and one of them has drifted from its DDL...
    DuckDbExporter(db).close()
    EnrichmentStore(db).close()
    with duckdb.connect(str(db)) as connection:
        connection.execute("ALTER TABLE turn_enrichments RENAME friction TO struggle")

    # ...then enrichment refuses the store by name...
    with pytest.raises(SchemaShapeError) as refused:
        EnrichmentStore(db)
    assert "turn_enrichments" in str(refused.value)
    assert "friction" in str(refused.value)

    # ...and lets go of the file, which a refusal holding DuckDB's single writer lock would
    # not: nothing was handed back, so no `with` block will ever close it.
    assert lock_is_free(db), f"the refusal kept the write lock: {refused.value}"


def test_a_renamed_delivery_column_is_refused_the_same_way(db: Path) -> None:
    """The third owner: the OTLP delivery ledger, which lives in the same file."""
    backend = Backend(name="test", endpoint="http://127.0.0.1:1/v1/traces")
    with DuckDbExporter(db) as exporter:
        OtlpExporter(backend, exporter.connection).close()
        exporter.connection.execute("ALTER TABLE otlp_delivery RENAME spans_sent TO spans")

        with pytest.raises(SchemaShapeError) as refused:
            OtlpExporter(backend, exporter.connection)
    assert "otlp_delivery" in str(refused.value)
    assert "spans_sent" in str(refused.value)


def test_a_table_a_ddl_declares_but_the_store_lacks_is_not_drift(db: Path) -> None:
    """A store holds enrichment and delivery tables only once those layers have run.

    Absence is the normal state of a fresh store, so the guard has to read it as "nothing to
    compare" rather than as drift — otherwise no store could be opened until every layer had.
    """
    with DuckDbExporter(db) as exporter:
        tables = {
            name
            for (name,) in exporter.connection.execute(
                "SELECT table_name FROM duckdb_tables()"
            ).fetchall()
        }
        # If the store holds none of another owner's tables...
        assert not tables & (declared_shape(ENRICHMENT_SCHEMA).keys())
        assert not tables & (declared_shape(DELIVERY_SCHEMA).keys())

        # ...then checking those DDLs against it passes.
        check_shape(exporter.connection, ENRICHMENT_SCHEMA)
        check_shape(exporter.connection, DELIVERY_SCHEMA)


# Names a real trace store for the opt-in check below. Off by default: the store holds
# private session data, and only this machine has one.
LIVE_STORE = "HYPHAE_LIVE_STORE"


@pytest.mark.skipif(
    LIVE_STORE not in os.environ, reason=f"set {LIVE_STORE} to a real trace store to run"
)
def test_the_real_archive_matches_every_owners_ddl() -> None:
    """The archive on this machine still fits the code that reads it.

    The leaf the bug went past: the fixtures are built by the current DDL every time, so
    only a store with history in it can drift. Read-only throughout — the archive is the
    only copy of every pruned session (`docs/store.md`).
    """
    archive = Path(os.environ[LIVE_STORE])
    connection = open_trace_store(archive, read_only=True)
    try:
        for ddl in (TRACE_SCHEMA, ENRICHMENT_SCHEMA, DELIVERY_SCHEMA):
            check_shape(connection, ddl)
    finally:
        connection.close()
