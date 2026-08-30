"""The local trace store: one DuckDB file, one table per model entity.

The DB is the archive, not a cache. Claude Code prunes transcripts from disk after a few
weeks, so a session stays here after its files are gone.

Writing is per-session replace inside one transaction — delete every row this session owns,
then insert the new ones. That makes re-extraction idempotent whatever changed, and it is
why a table added in a later slice must be added to `TABLES` too: a table left out of the
delete would keep stale rows forever.

Count through the rollup views rather than the base tables. The tables hold what each file
recorded, replays and resume copies included. `session_rollups` drops the replays, so a
record a fork copied counts under the transcript that ran it first. `corpus_rollups` drops
resume copies too, so a session totals only the work no earlier session already holds — use
it to sum across sessions, and `session_rollups` to ask what one session's files say.
"""

import datetime as dt
from dataclasses import fields
from pathlib import Path
from types import TracebackType
from typing import Any

import duckdb

from hyphae.export.schema import (
    SCHEMA_VERSION,
    SchemaVersionError,
    check_shape,
    check_version,
    migrate,
)
from hyphae.model import (
    AgentRun,
    ApiCall,
    Compaction,
    OffloadFile,
    PrLink,
    RawRecord,
    Session,
    SessionTrace,
    ToolCall,
    Turn,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    schema_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR PRIMARY KEY,
    project_dir VARCHAR,
    git_branch VARCHAR,
    version VARCHAR,
    entrypoint VARCHAR,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    active_ms BIGINT NOT NULL,
    transcript_path VARCHAR NOT NULL,
    title VARCHAR,
    agent_name VARCHAR
);
CREATE TABLE IF NOT EXISTS turns (
    id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    "index" INTEGER NOT NULL,
    prompt VARCHAR NOT NULL,
    command_name VARCHAR,
    command_args VARCHAR,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    replayed BOOLEAN NOT NULL,
    PRIMARY KEY (session_id, source, id)
);
CREATE TABLE IF NOT EXISTS api_calls (
    id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    turn_id VARCHAR,
    "index" INTEGER NOT NULL,
    model VARCHAR NOT NULL,
    -- NULL means no retry: the model asked for is the model that answered.
    fallback_from VARCHAR,
    effort VARCHAR,
    stop_reason VARCHAR,
    attribution_skill VARCHAR,
    request_id VARCHAR,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    input_tokens BIGINT NOT NULL,
    output_tokens BIGINT NOT NULL,
    cache_read_tokens BIGINT NOT NULL,
    cache_creation_tokens BIGINT NOT NULL,
    cache_5m_tokens BIGINT,
    cache_1h_tokens BIGINT,
    text VARCHAR NOT NULL,
    thinking VARCHAR NOT NULL,
    -- NULL means our price table lacks the model, not that the call was free.
    cost_usd DOUBLE,
    synthetic BOOLEAN NOT NULL,
    replayed BOOLEAN NOT NULL,
    PRIMARY KEY (session_id, source, id)
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    api_call_id VARCHAR NOT NULL,
    "index" INTEGER NOT NULL,
    name VARCHAR NOT NULL,
    server_side BOOLEAN NOT NULL,
    input VARCHAR NOT NULL,
    result VARCHAR,
    offload_file VARCHAR,
    is_error BOOLEAN NOT NULL,
    incomplete BOOLEAN NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_synthetic BOOLEAN NOT NULL,
    replayed BOOLEAN NOT NULL,
    PRIMARY KEY (session_id, source, id)
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    parent_agent_id VARCHAR,
    tool_use_id VARCHAR,
    agent_type VARCHAR NOT NULL,
    brief VARCHAR,
    model VARCHAR,
    workflow_id VARCHAR,
    spawn_depth INTEGER,
    is_fork BOOLEAN NOT NULL,
    fork_context_uuid VARCHAR,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    PRIMARY KEY (session_id, id)
);
CREATE TABLE IF NOT EXISTS compactions (
    id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    trigger VARCHAR NOT NULL,
    pre_tokens BIGINT NOT NULL,
    post_tokens BIGINT NOT NULL,
    duration_ms BIGINT NOT NULL,
    replayed BOOLEAN NOT NULL,
    PRIMARY KEY (session_id, source, id)
);
CREATE TABLE IF NOT EXISTS pr_links (
    session_id VARCHAR NOT NULL,
    line_no INTEGER NOT NULL,
    pr_number INTEGER NOT NULL,
    pr_url VARCHAR NOT NULL,
    pr_repository VARCHAR NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_id, line_no)
);
CREATE TABLE IF NOT EXISTS offload_files (
    session_id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    content VARCHAR NOT NULL,
    lossy_decode BOOLEAN NOT NULL,
    size_bytes BIGINT NOT NULL,
    PRIMARY KEY (session_id, name)
);
CREATE TABLE IF NOT EXISTS raw_records (
    session_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    line_no INTEGER NOT NULL,
    uuid VARCHAR,
    timestamp TIMESTAMPTZ,
    type VARCHAR NOT NULL,
    raw VARCHAR NOT NULL,
    PRIMARY KEY (session_id, source, line_no)
);
CREATE TABLE IF NOT EXISTS extract_state (
    session_id VARCHAR PRIMARY KEY,
    fingerprint VARCHAR NOT NULL,
    transcript_path VARCHAR NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL,
    extractor VARCHAR NOT NULL,
    extractor_version VARCHAR NOT NULL
);
"""


def _first_seen_view(view: str) -> str:
    """Which session gets credit for a row two sessions both hold.

    Ordering by start time makes the ancestor win a resume pair; the id breaks a tie between
    sessions that opened in the same millisecond.
    """
    return f"""
CREATE OR REPLACE {view} first_seen AS
SELECT id AS session_id, row_number() OVER (ORDER BY started_at, id) AS rank FROM sessions;
"""


# Whether the table carries `replayed` — the flag a fork's copy of another transcript's
# records takes. Only `agent_runs` carries none: a run is described by its own pair of files,
# so no fork ever holds a copy of one.
_COUNTED: dict[str, bool] = {
    "turns": True,
    "api_calls": True,
    "tool_calls": True,
    "agent_runs": False,
    "compactions": True,
}


def _live_view(table: str, replayed: bool, view: str) -> str:
    """The rows of one table that count for the session whose files hold them."""
    where = " WHERE NOT replayed" if replayed else ""
    return f"CREATE OR REPLACE {view} live_{table} AS SELECT * FROM {table}{where};"


def _corpus_view(table: str, view: str) -> str:
    """The same rows, minus every one an earlier session already holds.

    A resume copies its ancestor's records verbatim into a new session file, so the same
    natural id appears under two session ids. Counting both doubles the corpus.
    """
    return f"""
CREATE OR REPLACE {view} corpus_{table} AS
SELECT * EXCLUDE (rank, owner_rank) FROM (
    SELECT e.*, f.rank, min(f.rank) OVER (PARTITION BY e.id) AS owner_rank
    FROM live_{table} e JOIN first_seen f USING (session_id)
) WHERE rank = owner_rank;
"""


def _rollup_view(name: str, prefix: str, view: str) -> str:
    """One row per session, counting the rows of the `prefix` family of views."""
    return f"""
CREATE OR REPLACE {view} {name} AS
SELECT
    s.id AS session_id,
    s.project_dir,
    s.title,
    s.started_at,
    s.ended_at,
    -- Time from the first record to the last, which includes every gap the user spent
    -- away; `active_ms` is what Claude Code reported working.
    date_diff('millisecond', s.started_at, s.ended_at) AS wall_ms,
    s.active_ms,
    (SELECT count(*) FROM {prefix}_turns t WHERE t.session_id = s.id) AS turns,
    (SELECT count(*) FROM {prefix}_api_calls c WHERE c.session_id = s.id) AS api_calls,
    (SELECT count(*) FROM {prefix}_tool_calls tc WHERE tc.session_id = s.id) AS tool_calls,
    (SELECT count(*) FROM {prefix}_agent_runs a WHERE a.session_id = s.id) AS agent_runs,
    (SELECT count(*) FROM {prefix}_compactions k WHERE k.session_id = s.id) AS compactions,
    (SELECT coalesce(sum(c.input_tokens), 0) FROM {prefix}_api_calls c
        WHERE c.session_id = s.id) AS input_tokens,
    (SELECT coalesce(sum(c.output_tokens), 0) FROM {prefix}_api_calls c
        WHERE c.session_id = s.id) AS output_tokens,
    (SELECT coalesce(sum(c.cache_read_tokens), 0) FROM {prefix}_api_calls c
        WHERE c.session_id = s.id) AS cache_read_tokens,
    (SELECT coalesce(sum(c.cache_creation_tokens), 0) FROM {prefix}_api_calls c
        WHERE c.session_id = s.id) AS cache_creation_tokens,
    -- Sums only the calls our price table prices; `unpriced_api_calls` says how many it
    -- left out, so a total is never read as complete without checking.
    (SELECT coalesce(sum(c.cost_usd), 0) FROM {prefix}_api_calls c
        WHERE c.session_id = s.id) AS cost_usd,
    (SELECT count(*) FROM {prefix}_api_calls c
        WHERE c.session_id = s.id AND c.cost_usd IS NULL) AS unpriced_api_calls
FROM sessions s;
"""


def refresh_views(connection: duckdb.DuckDBPyConnection, *, read_only: bool) -> None:
    """Rebuild every view of the store from the definitions above, on any open connection.

    Every open runs this, so a definition edited here answers the next query — a store on
    disk is never read through the text that was current when it was last extracted.

    A read-only connection cannot replace a stored view, so it builds the same statements as
    `TEMP VIEW`s instead. Those shadow the stored ones of the same name for the life of the
    connection, including inside a stored view that names one — so a reader sees this code's
    rules whether or not a writer has been past since they changed. It costs about 3 ms on a
    15 GB store, which is the whole of what a reader pays for the guarantee.
    """
    view = "TEMP VIEW" if read_only else "VIEW"
    # `first_seen` leads: the corpus views read it, and the rollups read those.
    connection.execute(
        "".join(
            [
                _first_seen_view(view),
                *(_live_view(table, replayed, view) for table, replayed in _COUNTED.items()),
                *(_corpus_view(table, view) for table in _COUNTED),
                _rollup_view("session_rollups", "live", view),
                _rollup_view("corpus_rollups", "corpus", view),
            ]
        )
    )


# Table name to the dataclass whose fields are its columns, in order. Every table a
# session owns belongs here — this list drives both the insert and the delete, and
# `extract/store.py` reads the same rows back off it, so a new column reaches both sides.
TABLES: dict[str, type] = {
    "sessions": Session,
    "turns": Turn,
    "api_calls": ApiCall,
    "tool_calls": ToolCall,
    "agent_runs": AgentRun,
    "compactions": Compaction,
    "pr_links": PrLink,
    "offload_files": OffloadFile,
    "raw_records": RawRecord,
}
# `sessions` keys on the session id itself; every other table carries it as a column.
SESSION_KEY = {"sessions": "id"}


def open_trace_store(path: Path, *, read_only: bool) -> duckdb.DuckDBPyConnection:
    """Open a store an extract already wrote, for a reader or a writer that comes after one.

    Creates nothing: a path with no store behind it is a typo rather than a new store, and
    `DuckDbExporter` stays the only thing that writes the DDL. A write open migrates a store
    of an older vintage; a read-only one cannot, and says so. `read_only` has no default
    because DuckDB admits one writer at a time — a reader that takes the write lock by
    accident locks the viewer out.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} holds no trace store. Run `hp extract` first.")
    connection = duckdb.connect(str(path), read_only=read_only)
    try:
        connection.execute("SET TimeZone='UTC'")
        if not read_only:
            migrate(connection, path)
        check_version(connection, path)
        # After the version check: a store of another vintage holds tables these views
        # cannot bind, and its refusal has to name the version rather than a column.
        refresh_views(connection, read_only=read_only)
    except Exception:
        # Nothing was handed out, so no `with` block will close it: a refusal that kept the
        # connection would hold DuckDB's write lock until the process ends.
        connection.close()
        raise
    return connection


class DuckDbExporter:
    """Writes traces into one DuckDB file. Usable as a context manager."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        try:
            # Timestamps go in as UTC and must come back as UTC, whatever the machine's clock
            # is set to.
            self.connection.execute("SET TimeZone='UTC'")
            # All three before any DDL: a file this build cannot write must be left exactly
            # as it was. `migrate` carries an older store forward, and `check_shape` catches
            # a table that drifted with no migration behind it — which `CREATE TABLE IF NOT
            # EXISTS` would otherwise skip over and leave to fail at the first insert.
            self._check_store_is_ours()
            migrate(self.connection, self.path)
            check_shape(self.connection, _SCHEMA)
            self.connection.execute(_SCHEMA)
            # After the tables: every view reads them.
            refresh_views(self.connection, read_only=False)
            self._stamp_schema_version()
        except Exception:
            # Nothing here was ever handed out, so no `with` block will close it: a refusal
            # that kept the connection would hold DuckDB's write lock until the process ends.
            self.connection.close()
            raise

    def __enter__(self) -> "DuckDbExporter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _check_store_is_ours(self) -> None:
        """Refuse a file that is someone else's database, before any DDL touches it.

        Runs first because the damage is silent otherwise: `CREATE TABLE IF NOT EXISTS`
        would add our tables to a file that has nothing to do with us, and an operator points
        one here by mistake. An empty file is a new store; anything else has to carry the
        stamp `migrate` then reads.
        """
        tables = {
            name
            for (name,) in self.connection.execute(
                "SELECT table_name FROM duckdb_tables()"
            ).fetchall()
        }
        if tables and "meta" not in tables:
            raise SchemaVersionError(
                f"{self.path} holds tables this build did not write. Point at a different "
                f"file, or delete this one and re-extract."
            )

    def _stamp_schema_version(self) -> None:
        if self.connection.execute("SELECT schema_version FROM meta").fetchone() is None:
            self.connection.execute("INSERT INTO meta VALUES (?)", [SCHEMA_VERSION])

    def fingerprints(self) -> dict[str, str]:
        rows = self.connection.execute(
            "SELECT session_id, fingerprint FROM extract_state"
        ).fetchall()
        return dict(rows)

    def export(self, trace: SessionTrace, fingerprint: str) -> None:
        """Replace everything held for this session, or roll back leaving it untouched."""
        session_id = trace.session.id
        # Read off `TABLES` rather than listed again: a table named there and forgotten
        # here would be deleted on every export and never inserted. Each table's name is
        # its `SessionTrace` list, except `sessions`, which is the one row the rest hang off.
        rows = {
            table: [trace.session] if table == "sessions" else getattr(trace, table)
            for table in TABLES
        }
        self.connection.begin()
        try:
            for table in TABLES:
                key = SESSION_KEY.get(table, "session_id")
                self.connection.execute(f"DELETE FROM {table} WHERE {key} = ?", [session_id])
            self.connection.execute("DELETE FROM extract_state WHERE session_id = ?", [session_id])
            for table, entities in rows.items():
                self._insert(table, entities)
            self.connection.execute(
                "INSERT INTO extract_state VALUES (?, ?, ?, ?, ?, ?)",
                [
                    session_id,
                    fingerprint,
                    trace.session.transcript_path,
                    dt.datetime.now(dt.UTC),
                    trace.extractor,
                    trace.extractor_version,
                ],
            )
        except Exception:
            self.connection.rollback()
            raise
        self.connection.commit()

    def _insert(self, table: str, entities: list[Any]) -> None:
        if not entities:
            return
        columns = [field.name for field in fields(TABLES[table])]
        quoted = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        self.connection.executemany(
            f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})",
            [[getattr(entity, column) for column in columns] for entity in entities],
        )
