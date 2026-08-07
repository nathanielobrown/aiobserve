"""The local trace store: one DuckDB file, one table per model entity.

The DB is the archive, not a cache. Claude Code prunes transcripts from disk after a few
weeks, so a session stays here after its files are gone.

Writing is per-session replace inside one transaction — delete every row this session owns,
then insert the new ones. That makes re-extraction idempotent whatever changed, and it is
why a table added in a later slice must be added to `_TABLES` too: a table left out of the
delete would keep stale rows forever.
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
    OffloadFile,
    RawRecord,
    Session,
    SessionTrace,
    ToolCall,
    Turn,
)

# Bumped whenever the DDL below changes. There are no migrations while the project is
# early: a mismatch tells the operator to delete the DB and re-extract.
SCHEMA_VERSION = 4

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
    transcript_path VARCHAR NOT NULL
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
    PRIMARY KEY (session_id, source, id)
);
CREATE TABLE IF NOT EXISTS api_calls (
    id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    turn_id VARCHAR,
    "index" INTEGER NOT NULL,
    model VARCHAR NOT NULL,
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
    PRIMARY KEY (session_id, source, id)
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    api_call_id VARCHAR NOT NULL,
    "index" INTEGER NOT NULL,
    name VARCHAR NOT NULL,
    input VARCHAR NOT NULL,
    result VARCHAR,
    offload_file VARCHAR,
    is_error BOOLEAN NOT NULL,
    incomplete BOOLEAN NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_synthetic BOOLEAN NOT NULL,
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
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    PRIMARY KEY (session_id, id)
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

# Table name to the dataclass whose fields are its columns, in order. Every table a
# session owns belongs here — this list drives both the insert and the delete.
_TABLES: dict[str, type] = {
    "sessions": Session,
    "turns": Turn,
    "api_calls": ApiCall,
    "tool_calls": ToolCall,
    "agent_runs": AgentRun,
    "offload_files": OffloadFile,
    "raw_records": RawRecord,
}
# `sessions` keys on the session id itself; every other table carries it as a column.
_SESSION_KEY = {"sessions": "id"}


class SchemaVersionError(Exception):
    """The DB on disk was written by a different version of this schema."""


class DuckDbExporter:
    """Writes traces into one DuckDB file. Usable as a context manager."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        # Timestamps go in as UTC and must come back as UTC, whatever the machine's clock
        # is set to.
        self.connection.execute("SET TimeZone='UTC'")
        self.connection.execute(_SCHEMA)
        self._check_schema_version()

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
        row = self.connection.execute("SELECT schema_version FROM meta").fetchone()
        if row is None:
            self.connection.execute("INSERT INTO meta VALUES (?)", [SCHEMA_VERSION])
            return
        if row[0] != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"{self.path} holds schema version {row[0]}, this build writes "
                f"{SCHEMA_VERSION}. Delete the database and re-extract."
            )

    def fingerprints(self) -> dict[str, str]:
        rows = self.connection.execute(
            "SELECT session_id, fingerprint FROM extract_state"
        ).fetchall()
        return dict(rows)

    def export(self, trace: SessionTrace, fingerprint: str) -> None:
        """Replace everything held for this session, or roll back leaving it untouched."""
        session_id = trace.session.id
        rows = {
            "sessions": [trace.session],
            "turns": trace.turns,
            "api_calls": trace.api_calls,
            "tool_calls": trace.tool_calls,
            "agent_runs": trace.agent_runs,
            "offload_files": trace.offload_files,
            "raw_records": trace.raw_records,
        }
        self.connection.begin()
        try:
            for table in _TABLES:
                key = _SESSION_KEY.get(table, "session_id")
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
        columns = [field.name for field in fields(_TABLES[table])]
        quoted = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        self.connection.executemany(
            f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})",
            [[getattr(entity, column) for column in columns] for entity in entities],
        )
