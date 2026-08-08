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

from aiobserve.model import (
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

# Bumped whenever the DDL below changes. There are no migrations while the project is
# early: a mismatch refuses the store and says to extract into a fresh one.
SCHEMA_VERSION = 7

# The remedy every version-mismatch message carries, written once because getting it wrong is
# expensive: a store can be the only copy of a session Claude Code has pruned from disk, so
# the operator is sent to `docs/store.md` — which holds the check — rather than to `rm`.
SCHEMA_MISMATCH_REMEDY = (
    "Extract into a fresh store. This one may hold the only copy of a pruned session — "
    "read docs/store.md before deleting it."
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
    description VARCHAR,
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
-- Which session gets credit for a row two sessions both hold. Ordering by start time makes
-- the ancestor win a resume pair; the id breaks a tie between sessions that opened in the
-- same millisecond.
CREATE OR REPLACE VIEW first_seen AS
SELECT id AS session_id, row_number() OVER (ORDER BY started_at, id) AS rank FROM sessions;
"""

# Whether the table carries `replayed` — the flag slice 3 sets on a fork's copy of another
# transcript's records. The rest of a session's countable tables carry no such copies.
_COUNTED: dict[str, bool] = {
    "turns": True,
    "api_calls": True,
    "tool_calls": True,
    "agent_runs": False,
    "compactions": False,
}


def _live_view(table: str, replayed: bool) -> str:
    """The rows of one table that count for the session whose files hold them."""
    where = " WHERE NOT replayed" if replayed else ""
    return f"CREATE OR REPLACE VIEW live_{table} AS SELECT * FROM {table}{where};"


def _corpus_view(table: str) -> str:
    """The same rows, minus every one an earlier session already holds.

    A resume copies its ancestor's records verbatim into a new session file, so the same
    natural id appears under two session ids. Counting both doubles the corpus.
    """
    return f"""
CREATE OR REPLACE VIEW corpus_{table} AS
SELECT * EXCLUDE (rank, owner_rank) FROM (
    SELECT e.*, f.rank, min(f.rank) OVER (PARTITION BY e.id) AS owner_rank
    FROM live_{table} e JOIN first_seen f USING (session_id)
) WHERE rank = owner_rank;
"""


def _rollup_view(name: str, prefix: str) -> str:
    """One row per session, counting the rows of the `prefix` family of views."""
    return f"""
CREATE OR REPLACE VIEW {name} AS
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


_VIEWS = "".join(
    [
        *(_live_view(table, replayed) for table, replayed in _COUNTED.items()),
        *(_corpus_view(table) for table in _COUNTED),
        _rollup_view("session_rollups", "live"),
        _rollup_view("corpus_rollups", "corpus"),
    ]
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


class SchemaVersionError(Exception):
    """The DB on disk was written by a different version of this schema."""


def held_schema_version(connection: duckdb.DuckDBPyConnection) -> int | None:
    """The version stamped in an open store, or None when it carries no stamp at all.

    None covers both a file that is not a trace store and one that crashed between its DDL
    and its stamp, so every reader can say "holds nothing" instead of raising a catalog
    error on someone else's database. The `duckdb_tables()` join is what makes that
    possible: `SELECT ... FROM meta` on a foreign file crashes before the check can speak.
    """
    row = connection.execute(
        "SELECT schema_version FROM duckdb_tables() t"
        " LEFT JOIN meta ON true WHERE t.table_name = 'meta'"
    ).fetchone()
    return None if row is None else row[0]


def open_trace_store(path: Path, *, read_only: bool) -> duckdb.DuckDBPyConnection:
    """Open a store an extract already wrote, for a reader or a writer that comes after one.

    Creates nothing: a path with no store behind it is a typo rather than a new store, and
    `DuckDbExporter` stays the only thing that writes the DDL. `read_only` has no default
    because DuckDB admits one writer at a time — a reader that takes the write lock by
    accident locks the viewer out.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} holds no trace store. Run `aiobserve extract` first.")
    connection = duckdb.connect(str(path), read_only=read_only)
    connection.execute("SET TimeZone='UTC'")
    held = held_schema_version(connection)
    if held != SCHEMA_VERSION:
        connection.close()
        raise SchemaVersionError(
            f"{path} holds schema version {held or 'nothing'}, this build reads "
            f"{SCHEMA_VERSION}. {SCHEMA_MISMATCH_REMEDY}"
        )
    return connection


class DuckDbExporter:
    """Writes traces into one DuckDB file. Usable as a context manager."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        # Timestamps go in as UTC and must come back as UTC, whatever the machine's clock
        # is set to.
        self.connection.execute("SET TimeZone='UTC'")
        # Before any DDL: a file this build cannot read must be left exactly as it was.
        self._check_schema_version()
        self.connection.execute(_SCHEMA)
        # After the tables: every view below reads them.
        self.connection.execute(_VIEWS)
        self._stamp_schema_version()

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

    def _check_schema_version(self) -> None:
        """Refuse a file this build's DDL does not fit, before that DDL touches it.

        Runs first because the damage is silent otherwise: `CREATE TABLE IF NOT EXISTS`
        would add current tables to a file written by an older schema, and the kept
        archives beside the live store are exactly the files an operator points here by
        mistake. An empty file is a new store; anything else has to carry our stamp.
        """
        tables = {
            name
            for (name,) in self.connection.execute(
                "SELECT table_name FROM duckdb_tables()"
            ).fetchall()
        }
        if not tables:
            return
        if "meta" not in tables:
            raise SchemaVersionError(
                f"{self.path} holds tables this build did not write. Point at a different "
                f"file, or delete this one and re-extract."
            )
        row = self.connection.execute("SELECT schema_version FROM meta").fetchone()
        # An empty `meta` is a store that crashed between its DDL and its stamp.
        if row is not None and row[0] != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"{self.path} holds schema version {row[0]}, this build writes "
                f"{SCHEMA_VERSION}. {SCHEMA_MISMATCH_REMEDY}"
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
