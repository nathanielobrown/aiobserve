//! The DDL of the trace store, and the column list of every table it creates.
//!
//! Ported statement-for-statement from `src/hyphae/export/duckdb.py`, which stays the
//! authority until the Python exporter retires. The one deliberate difference is
//! [`TABLES`]: Python derives each insert's columns from `dataclasses.fields`, and the
//! design asks for them written out beside the DDL instead, so a column added to one and
//! not the other cannot drift silently. [`crate::store::Store::check_columns`] is what
//! holds the two together.

/// Bumped whenever any owner's stored tables change; mirrors `export/schema.py`.
pub const SCHEMA_VERSION: i32 = 8;

/// The tables, and the `first_seen` view every rollup below is ranked by.
pub const SCHEMA: &str = r#"
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
"#;

/// Whether the table carries `replayed` — the flag set on a fork's copy of another
/// transcript's records. The rest of a session's countable tables carry no such copies.
const COUNTED: &[(&str, bool)] = &[
    ("turns", true),
    ("api_calls", true),
    ("tool_calls", true),
    ("agent_runs", false),
    ("compactions", false),
];

/// Every table a session owns, and its columns in DDL order. Drives both the insert and the
/// per-session delete: a table named here and forgotten in one of them leaks rows forever.
pub const TABLES: &[(&str, &[&str])] = &[
    (
        "sessions",
        &[
            "id",
            "project_dir",
            "git_branch",
            "version",
            "entrypoint",
            "started_at",
            "ended_at",
            "active_ms",
            "transcript_path",
            "title",
            "agent_name",
        ],
    ),
    (
        "turns",
        &[
            "id",
            "session_id",
            "source",
            "index",
            "prompt",
            "command_name",
            "command_args",
            "started_at",
            "ended_at",
            "replayed",
        ],
    ),
    (
        "api_calls",
        &[
            "id",
            "session_id",
            "source",
            "turn_id",
            "index",
            "model",
            "fallback_from",
            "effort",
            "stop_reason",
            "attribution_skill",
            "request_id",
            "started_at",
            "ended_at",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "cache_5m_tokens",
            "cache_1h_tokens",
            "text",
            "thinking",
            "cost_usd",
            "synthetic",
            "replayed",
        ],
    ),
    (
        "tool_calls",
        &[
            "id",
            "session_id",
            "source",
            "api_call_id",
            "index",
            "name",
            "server_side",
            "input",
            "result",
            "offload_file",
            "is_error",
            "incomplete",
            "started_at",
            "ended_at",
            "duration_synthetic",
            "replayed",
        ],
    ),
    (
        "agent_runs",
        &[
            "id",
            "session_id",
            "parent_agent_id",
            "tool_use_id",
            "agent_type",
            "brief",
            "model",
            "workflow_id",
            "spawn_depth",
            "is_fork",
            "fork_context_uuid",
            "started_at",
            "ended_at",
        ],
    ),
    (
        "compactions",
        &[
            "id",
            "session_id",
            "source",
            "timestamp",
            "trigger",
            "pre_tokens",
            "post_tokens",
            "duration_ms",
        ],
    ),
    (
        "pr_links",
        &[
            "session_id",
            "line_no",
            "pr_number",
            "pr_url",
            "pr_repository",
            "timestamp",
        ],
    ),
    (
        "offload_files",
        &[
            "session_id",
            "name",
            "content",
            "lossy_decode",
            "size_bytes",
        ],
    ),
    (
        "raw_records",
        &[
            "session_id",
            "source",
            "line_no",
            "uuid",
            "timestamp",
            "type",
            "raw",
        ],
    ),
];

/// The widest table, which is what the stage-1 spike round-trips.
pub const WIDEST_TABLE: &str = "api_calls";

/// The columns of one table in DDL order, or `None` when no table goes by that name.
pub fn columns(table: &str) -> Option<&'static [&'static str]> {
    TABLES
        .iter()
        .find(|(name, _)| *name == table)
        .map(|(_, columns)| *columns)
}

/// The rows of one table that count for the session whose files hold them.
fn live_view(table: &str, replayed: bool) -> String {
    let where_clause = if replayed { " WHERE NOT replayed" } else { "" };
    format!("CREATE OR REPLACE VIEW live_{table} AS SELECT * FROM {table}{where_clause};")
}

/// The same rows, minus every one an earlier session already holds — a resume copies its
/// ancestor's records verbatim, so the same natural id appears under two session ids.
fn corpus_view(table: &str) -> String {
    format!(
        r#"
CREATE OR REPLACE VIEW corpus_{table} AS
SELECT * EXCLUDE (rank, owner_rank) FROM (
    SELECT e.*, f.rank, min(f.rank) OVER (PARTITION BY e.id) AS owner_rank
    FROM live_{table} e JOIN first_seen f USING (session_id)
) WHERE rank = owner_rank;
"#
    )
}

/// One row per session, counting the rows of the `prefix` family of views.
fn rollup_view(name: &str, prefix: &str) -> String {
    format!(
        r#"
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
"#
    )
}

/// Every view the store carries, in the order they must be created: the `live_` set, the
/// `corpus_` set built on it, then the two rollups built on both.
pub fn views() -> String {
    let mut sql = String::new();
    for (table, replayed) in COUNTED {
        sql.push_str(&live_view(table, *replayed));
    }
    for (table, _) in COUNTED {
        sql.push_str(&corpus_view(table));
    }
    sql.push_str(&rollup_view("session_rollups", "live"));
    sql.push_str(&rollup_view("corpus_rollups", "corpus"));
    sql
}
