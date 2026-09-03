"""The local trace store: one DuckDB file, one table per model entity.

The DB is the archive, not a cache. Claude Code prunes transcripts from disk after a few
weeks, so a session stays here after its files are gone.

Writing is per-session replace inside one transaction — delete every row this session owns,
then insert the new ones. That makes re-extraction idempotent whatever changed, and it is
why a table added in a later slice must be added to the `TABLES` registry too: a table left
out of the delete would keep stale rows forever.

Count through the rollup views rather than the base tables. The tables hold what each file
recorded, replays and resume copies included. `session_rollups` drops the replays, so a
record a fork copied counts under the transcript that ran it first. `corpus_rollups` drops
resume copies too, so a session totals only the work no earlier session already holds — use
it to sum across sessions, and `session_rollups` to ask what one session's files say.
"""

import datetime as dt
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
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
    """One row per session, counting the rows of the `prefix` family of views.

    One grouped pass per family joined onto the sessions, rather than a correlated subquery
    per column: eleven correlations over five relations re-scan the api calls seven times, and
    on the corpus family each scan pays for the whole replay exclusion again. Every join is a
    LEFT JOIN and every column it feeds coalesces, so a session with none of a kind still
    reports the zero its readers sum and sort by.
    """
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
    coalesce(t.turns, 0) AS turns,
    coalesce(c.api_calls, 0) AS api_calls,
    coalesce(tc.tool_calls, 0) AS tool_calls,
    coalesce(a.agent_runs, 0) AS agent_runs,
    coalesce(k.compactions, 0) AS compactions,
    coalesce(c.input_tokens, 0) AS input_tokens,
    coalesce(c.output_tokens, 0) AS output_tokens,
    coalesce(c.cache_read_tokens, 0) AS cache_read_tokens,
    coalesce(c.cache_creation_tokens, 0) AS cache_creation_tokens,
    -- Sums only the calls our price table prices; `unpriced_api_calls` says how many it
    -- left out, so a total is never read as complete without checking.
    coalesce(c.cost_usd, 0) AS cost_usd,
    coalesce(c.unpriced_api_calls, 0) AS unpriced_api_calls
FROM sessions s
LEFT JOIN (SELECT session_id, count(*) AS turns
           FROM {prefix}_turns GROUP BY session_id) t ON t.session_id = s.id
LEFT JOIN (SELECT session_id, count(*) AS tool_calls
           FROM {prefix}_tool_calls GROUP BY session_id) tc ON tc.session_id = s.id
LEFT JOIN (SELECT session_id, count(*) AS agent_runs
           FROM {prefix}_agent_runs GROUP BY session_id) a ON a.session_id = s.id
LEFT JOIN (SELECT session_id, count(*) AS compactions
           FROM {prefix}_compactions GROUP BY session_id) k ON k.session_id = s.id
LEFT JOIN (
    SELECT session_id,
        count(*) AS api_calls,
        sum(input_tokens) AS input_tokens,
        sum(output_tokens) AS output_tokens,
        sum(cache_read_tokens) AS cache_read_tokens,
        sum(cache_creation_tokens) AS cache_creation_tokens,
        sum(cost_usd) AS cost_usd,
        count(*) FILTER (cost_usd IS NULL) AS unpriced_api_calls
    FROM {prefix}_api_calls GROUP BY session_id) c ON c.session_id = s.id;
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


@dataclass(frozen=True)
class TableSpec:
    """Everything a session-owned table's rows need: their shape, their owner, their order."""

    # The dataclass whose fields are the table's columns, in order. `tests/export/
    # test_schema.py` holds the DDL to it, so a column added to one side and not the other
    # fails there rather than at an insert.
    model: type
    # The column carrying the session id, which the per-session delete and read both filter
    # on. `sessions` keys on the id itself; every other table carries it as a column.
    session_key: str
    # The rest of the primary key, which a single session's read orders by. List order
    # carries no meaning — the model's lists are keyed by natural ids — but a stable one
    # keeps two exports of an unchanged session byte-identical.
    order: tuple[str, ...]


# Every table a session owns. This registry drives the insert, the delete, and
# `extract/store.py`'s read back out, so a new table or column reaches every side at once.
TABLES: dict[str, TableSpec] = {
    "sessions": TableSpec(Session, session_key="id", order=("id",)),
    "turns": TableSpec(Turn, session_key="session_id", order=("source", "id")),
    "api_calls": TableSpec(ApiCall, session_key="session_id", order=("source", "id")),
    "tool_calls": TableSpec(ToolCall, session_key="session_id", order=("source", "id")),
    "agent_runs": TableSpec(AgentRun, session_key="session_id", order=("id",)),
    "compactions": TableSpec(Compaction, session_key="session_id", order=("source", "id")),
    "pr_links": TableSpec(PrLink, session_key="session_id", order=("line_no",)),
    "offload_files": TableSpec(OffloadFile, session_key="session_id", order=("name",)),
    "raw_records": TableSpec(RawRecord, session_key="session_id", order=("source", "line_no")),
}


# How long a caller queues behind whoever holds the store, in seconds. One of these is what
# every opener in the codebase passes, and which one says who is waiting: a page is a person
# watching a spinner, a command is not. An extract's holds are per session and last tens of
# milliseconds (`docs/store.md`), so a page that waits a second outlasts several of them.
PAGE_WAIT = 1.0
CLI_WAIT = 10.0

# How often the wait retries. Flat rather than backed off with jitter: the contenders are two
# local processes, so there is no herd to spread out, and one interval is easy to explain in a
# doc and to assert on in a test.
_POLL = 0.025

# DuckDB's wording when another process holds the file. Matched on text because the exception
# it arrives as covers every other I/O failure too — and the line itself is worth keeping, as
# it names the pid of the process holding on.
_LOCKED = "Conflicting lock is held"


class StoreLocked(Exception):
    """Another process held the store longer than this caller was willing to wait."""


def _connect(path: Path, *, read_only: bool, wait: float) -> duckdb.DuckDBPyConnection:
    """Take DuckDB's file lock, giving whoever holds it `wait` seconds to let go.

    DuckDB admits one process at a time and offers no lock timeout of its own, so the waiting
    is ours: retry until the budget is spent, then say who is holding what. Any other I/O
    failure is the caller's to see whole and at once.
    """
    deadline = time.monotonic() + wait
    while True:
        try:
            return duckdb.connect(str(path), read_only=read_only)
        except duckdb.IOException as error:
            if _LOCKED not in str(error):
                raise
            if time.monotonic() >= deadline:
                raise StoreLocked(
                    f"{path} was still held after {wait:g}s. Wait for the command using it "
                    f"to finish, or stop it. {error}"
                ) from error
            time.sleep(_POLL)


@contextmanager
def open_trace_store(
    path: Path, *, read_only: bool, wait: float
) -> Generator[duckdb.DuckDBPyConnection]:
    """Open a store an extract already wrote, for a reader or a writer that comes after one.

    The one way into an existing store: every reader and every writer but the extractor
    itself goes through here, so the version check, the views, the waiting and the closing
    are written once. Creates nothing — a path with no store behind it is a typo rather than
    a new store, and `DuckDbExporter` stays the only thing that writes the DDL. A write open
    migrates a store of an older vintage; a read-only one cannot, and says so.

    `wait` is how many seconds to queue behind another process, and has no default because
    the caller is the only one who knows whether a person is watching: pass `PAGE_WAIT` or
    `CLI_WAIT`. Past the budget it raises `StoreLocked`.

    Keyword-only from `read_only` on, and it has no default either: DuckDB admits one writer
    at a time, so a reader that takes the write lock by accident locks the viewer out.

    Timestamps come back as UTC whatever the machine's clock is set to: a page rendered or a
    corpus window measured in local time reproduces no citation of the same rows.

    The block owns the connection, including on the way out through an exception — a refusal
    that kept it would hold DuckDB's lock until the process ended.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} holds no trace store. Run `hp extract` first.")
    connection = _connect(path, read_only=read_only, wait=wait)
    try:
        connection.execute("SET TimeZone='UTC'")
        if not read_only:
            migrate(connection, path)
        check_version(connection, path)
        # After the version check: a store of another vintage holds tables these views
        # cannot bind, and its refusal has to name the version rather than a column.
        refresh_views(connection, read_only=read_only)
        yield connection
    finally:
        connection.close()


class DuckDbExporter:
    """Writes traces into one DuckDB file, holding the file only while it writes.

    Nothing here keeps a connection open between calls. Construction prepares the store and
    lets go, `fingerprints()` reads it, and each `export()` takes the write lock for one
    transaction. That is what lets `hp view` answer pages while `hp extract` runs: the
    extract spends most of its time parsing, and the store is free throughout. `wait` is the
    budget every one of those opens will queue behind another process for.
    """

    def __init__(self, path: Path, *, wait: float) -> None:
        self.path = path
        self.wait = wait
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = _connect(path, read_only=False, wait=wait)
        try:
            # Timestamps go in as UTC and must come back as UTC, whatever the machine's clock
            # is set to.
            connection.execute("SET TimeZone='UTC'")
            # All three before any DDL: a file this build cannot write must be left exactly
            # as it was. `migrate` carries an older store forward, and `check_shape` catches
            # a table that drifted with no migration behind it — which `CREATE TABLE IF NOT
            # EXISTS` would otherwise skip over and leave to fail at the first insert.
            self._check_store_is_ours(connection)
            migrate(connection, self.path)
            check_shape(connection, _SCHEMA)
            connection.execute(_SCHEMA)
            # After the tables: every view reads them.
            refresh_views(connection, read_only=False)
            self._stamp_schema_version(connection)
        finally:
            # Always, refusal included: a connection nothing hands back would hold DuckDB's
            # write lock until the process ends.
            connection.close()

    @contextmanager
    def _writing(self) -> Generator[duckdb.DuckDBPyConnection]:
        """The store held for one write and let go — deliberately leaner than the opener.

        `__init__` already migrated the file and built its views, and rebuilding the views
        costs about 60 ms per open on a 9.5 GB store against 5 ms for the write itself
        (`docs/store.md`). `check_version` stays: another build could have moved the schema
        on since.
        """
        connection = _connect(self.path, read_only=False, wait=self.wait)
        try:
            connection.execute("SET TimeZone='UTC'")
            check_version(connection, self.path)
            yield connection
        finally:
            connection.close()

    def _check_store_is_ours(self, connection: duckdb.DuckDBPyConnection) -> None:
        """Refuse a file that is someone else's database, before any DDL touches it.

        Runs first because the damage is silent otherwise: `CREATE TABLE IF NOT EXISTS`
        would add our tables to a file that has nothing to do with us, and an operator points
        one here by mistake. An empty file is a new store; anything else has to carry the
        stamp `migrate` then reads.
        """
        tables = {
            name
            for (name,) in connection.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }
        if tables and "meta" not in tables:
            raise SchemaVersionError(
                f"{self.path} holds tables this build did not write. Point at a different "
                f"file, or delete this one and re-extract."
            )

    def _stamp_schema_version(self, connection: duckdb.DuckDBPyConnection) -> None:
        if connection.execute("SELECT schema_version FROM meta").fetchone() is None:
            connection.execute("INSERT INTO meta VALUES (?)", [SCHEMA_VERSION])

    def fingerprints(self) -> dict[str, str]:
        """What each stored session was last extracted from, read without taking the lock."""
        with open_trace_store(self.path, read_only=True, wait=self.wait) as connection:
            rows = connection.execute(
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
        with self._writing() as connection:
            connection.begin()
            try:
                for table, spec in TABLES.items():
                    connection.execute(
                        f"DELETE FROM {table} WHERE {spec.session_key} = ?", [session_id]
                    )
                connection.execute("DELETE FROM extract_state WHERE session_id = ?", [session_id])
                for table, entities in rows.items():
                    self._insert(connection, table, entities)
                connection.execute(
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
                connection.rollback()
                raise
            connection.commit()

    def _insert(
        self, connection: duckdb.DuckDBPyConnection, table: str, entities: list[Any]
    ) -> None:
        if not entities:
            return
        columns = [field.name for field in fields(TABLES[table].model)]
        quoted = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})",
            [[getattr(entity, column) for column in columns] for entity in entities],
        )
