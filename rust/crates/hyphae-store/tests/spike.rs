//! Stage 1 of the design: the go/no-go on the store path.
//!
//! Every leaf runs against real data — one session lifted out of a copy of the canonical
//! store at `data/traces.duckdb`, which the Python exporter wrote. Nothing here invents a
//! row. The copy is gitignored and read-only to these tests: they open it without the write
//! lock and write only into a tempdir.
//!
//! Until the Rust extractor lands (stage 2), that copy is the corpus. The testing plan wants
//! these leaves fed by `tests/fixtures/` through the Rust path instead; re-point
//! [`corpus`] when there is one.

use std::path::PathBuf;

use duckdb::types::{ToSql, Value};
use hyphae_store::row::member;
use hyphae_store::{Store, schema};
use tempfile::TempDir;

/// The copy of the canonical store these tests read. Absent means the spike was run without
/// its corpus, which is a setup error rather than a reason to test nothing.
fn corpus() -> Store {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../data/traces.duckdb");
    if !path.exists() {
        panic!(
            "no store at {}. Copy the canonical one there first: \
             `cp -c ../hyphae/data/traces.duckdb data/traces.duckdb`",
            path.display()
        );
    }
    Store::open_read_only(&path).expect("the corpus copy opens read-only")
}

/// A session worth copying whole: enough api and tool calls to exercise the widest table and
/// the nested `tools` struct, at least one agent run so the tables hanging off a subagent are
/// not empty, and few enough records to move in a second.
///
/// Derived rather than pinned. A session id is private data and a store is re-extracted, so
/// a hard-coded one would either leak or rot; the counts are what the test actually needs.
/// Every count groups one narrow column, which is cheap even against the whole archive.
fn representative_session(corpus: &Store) -> String {
    let rows = corpus
        .fetch(
            r#"
            SELECT c.session_id
            FROM (SELECT session_id, count(*) AS calls FROM api_calls GROUP BY 1) c
            JOIN (SELECT session_id, count(*) AS tools FROM tool_calls GROUP BY 1) t
                USING (session_id)
            JOIN (SELECT session_id, count(*) AS records FROM raw_records GROUP BY 1) r
                USING (session_id)
            JOIN (SELECT session_id, count(*) AS runs FROM agent_runs GROUP BY 1) a
                USING (session_id)
            WHERE c.calls BETWEEN 20 AND 400
              AND t.tools BETWEEN 20 AND 400
              AND a.runs BETWEEN 1 AND 50
              AND r.records < 4000
            ORDER BY c.session_id
            LIMIT 1
            "#,
            &[],
        )
        .expect("the corpus answers the session probe");
    let session = rows
        .first()
        .expect("the corpus holds a session of a size worth copying");
    session.str("session_id").unwrap().to_owned()
}

/// Every row one session owns, table by table, in the crate's own column order.
fn session_rows(corpus: &Store, session_id: &str, table: &str) -> Vec<Vec<Value>> {
    let columns = schema::columns(table).expect("a table this crate declares");
    let selected = columns
        .iter()
        .map(|column| format!("\"{column}\""))
        .collect::<Vec<_>>()
        .join(", ");
    // `sessions` keys on the session id itself; every other table carries it as a column.
    let key = if table == "sessions" {
        "id"
    } else {
        "session_id"
    };
    let session: &dyn ToSql = &session_id;
    corpus
        .fetch(
            &format!("SELECT {selected} FROM {table} WHERE {key} = $session_id"),
            &[("session_id", session)],
        )
        .expect("the corpus answers a table read")
        .into_iter()
        .map(|row| row.values().to_vec())
        .collect()
}

/// A fresh store in a tempdir holding exactly one session, copied out of the corpus.
///
/// The appender writes every table. That it can is the finding stage 1 exists to make: the
/// schema is flat scalars end to end, so the nested-value gap the design feared never comes
/// up on the write side — `the_appender_refuses_a_nested_value` bounds that claim.
fn one_session_store(corpus: &Store, session_id: &str) -> (TempDir, Store) {
    let scratch = TempDir::new().expect("a tempdir for the scratch store");
    let store = Store::create(&scratch.path().join("traces.duckdb")).expect("a fresh store");
    for (table, _) in schema::TABLES {
        let rows = session_rows(corpus, session_id, table);
        store
            .append_rows(table, &rows)
            .unwrap_or_else(|error| panic!("the appender writes `{table}`: {error}"));
    }
    (scratch, store)
}

/// The check the design's "write them once, beside the DDL" replaces `dataclasses.fields`
/// with: DuckDB's own account of each table, against the column list the crate inserts by.
#[test]
fn the_insert_column_lists_and_the_ddl_agree() {
    let scratch = TempDir::new().unwrap();
    let store = Store::create(&scratch.path().join("traces.duckdb")).unwrap();
    store.check_columns().expect("no table's columns drifted");
}

/// The widest table, round-tripped: the rows the Python exporter wrote go through the Rust
/// appender into a fresh store and come back the same values.
#[test]
fn the_widest_table_round_trips_through_the_appender() {
    let corpus = corpus();
    let session_id = representative_session(&corpus);
    let (_scratch, store) = one_session_store(&corpus, &session_id);

    let written = session_rows(&corpus, &session_id, schema::WIDEST_TABLE);
    let read_back = session_rows(&store, &session_id, schema::WIDEST_TABLE);
    assert!(
        !written.is_empty(),
        "the probe picked a session with api calls"
    );
    // 24 columns, every one of them a scalar — the whole reason the appender can write them.
    assert_eq!(written[0].len(), 24, "api_calls is 24 columns wide");
    assert_eq!(
        read_back, written,
        "every value of every api call came back as it went in"
    );

    // The rest of the session's tables too, so the round trip covers every column list
    // rather than the widest one alone.
    let mut carried = 0;
    for (table, _) in schema::TABLES {
        let from_python = session_rows(&corpus, &session_id, table);
        let from_rust = session_rows(&store, &session_id, table);
        assert_eq!(from_rust, from_python, "`{table}` round-tripped unchanged");
        if !from_python.is_empty() {
            carried += 1;
        }
    }
    // A session with rows in only one or two tables would make the loop above vacuous.
    assert!(
        carried >= 6,
        "the probe picked a session touching {carried} tables, too few to prove much"
    );
}

/// Both insert paths write the same rows, so the choice between them is cost rather than
/// correctness.
///
/// The design named prepared `INSERT` batches as the fallback if the appender could not
/// carry the schema. It can, so the appender is the path stage 2 builds on — but the
/// fallback stays exercised here, because it is also the shape Python's exporter uses and
/// the parity diff will compare against it.
#[test]
fn both_insert_paths_write_the_same_rows() {
    let corpus = corpus();
    let session_id = representative_session(&corpus);
    let written = session_rows(&corpus, &session_id, schema::WIDEST_TABLE);

    let appended_dir = TempDir::new().unwrap();
    let appended = Store::create(&appended_dir.path().join("traces.duckdb")).unwrap();
    appended
        .append_rows(schema::WIDEST_TABLE, &written)
        .expect("the appender writes the widest table");

    let inserted_dir = TempDir::new().unwrap();
    let inserted = Store::create(&inserted_dir.path().join("traces.duckdb")).unwrap();
    inserted
        .insert_rows(schema::WIDEST_TABLE, &written)
        .expect("prepared INSERTs write the widest table");

    assert_eq!(
        session_rows(&appended, &session_id, schema::WIDEST_TABLE),
        written,
        "the appender's rows match what went in"
    );
    assert_eq!(
        session_rows(&inserted, &session_id, schema::WIDEST_TABLE),
        written,
        "the INSERT path's rows match what went in"
    );
}

/// A row the table refuses rolls the whole batch back, leaving the store as it was.
///
/// The `INSERT` path's transaction, which is what `export/duckdb.py:export` relies on to make
/// a re-extract all-or-nothing. Proven on a duplicate primary key, because every session's
/// rows already carry one.
#[test]
fn a_refused_insert_leaves_the_table_as_it_was() {
    let corpus = corpus();
    let session_id = representative_session(&corpus);
    let written = session_rows(&corpus, &session_id, schema::WIDEST_TABLE);

    let scratch = TempDir::new().unwrap();
    let store = Store::create(&scratch.path().join("traces.duckdb")).unwrap();
    store.insert_rows(schema::WIDEST_TABLE, &written).unwrap();

    // The same rows again: every one of them collides on (session_id, source, id).
    store
        .insert_rows(schema::WIDEST_TABLE, &written)
        .expect_err("a duplicate primary key is refused");
    assert_eq!(
        session_rows(&store, &session_id, schema::WIDEST_TABLE),
        written,
        "the first batch survived the rolled-back second"
    );
}

/// A TIMESTAMPTZ the Python exporter wrote reads back as the same instant, in UTC, through
/// chrono — and survives the trip through the Rust appender unchanged.
#[test]
fn a_timestamptz_reads_back_as_the_same_instant() {
    let corpus = corpus();
    let session_id = representative_session(&corpus);
    let (_scratch, store) = one_session_store(&corpus, &session_id);

    let session: &dyn ToSql = &session_id;
    // The store's own rendering of the instant, which is what a citation of these rows
    // reproduces: an independent reading of the column chrono is compared against.
    let sql = "SELECT started_at, strftime(started_at, '%Y-%m-%d %H:%M:%S') AS printed \
               FROM sessions WHERE id = $session_id";
    let from_python = corpus.fetch(sql, &[("session_id", session)]).unwrap();
    let from_rust = store.fetch(sql, &[("session_id", session)]).unwrap();

    let instant = from_python[0].timestamp("started_at").unwrap();
    assert_eq!(
        instant.format("%Y-%m-%d %H:%M:%S").to_string(),
        from_python[0].str("printed").unwrap(),
        "chrono names the instant DuckDB prints, with no zone shift"
    );
    assert_eq!(
        from_rust[0].timestamp("started_at").unwrap(),
        instant,
        "the appender wrote the instant back unchanged"
    );
}

/// One real node-page query, run against a store this crate created, with the macros
/// `view/store.py` installs — and its nested `tools` value read back whole.
///
/// This is the go/no-go: `view_call_header.sql` answers with a
/// `STRUCT(first STRUCT(name, fields STRUCT(..)), names LIST(VARCHAR))`, which is the shape
/// the design flagged. Reading it needs no Rust type declared for the query's result.
#[test]
fn a_node_page_query_reads_its_nested_struct_and_list() {
    let corpus = corpus();
    let session_id = representative_session(&corpus);
    let (_scratch, store) = one_session_store(&corpus, &session_id);

    // A call that went on to make tool calls, so `tools` is a struct rather than NULL.
    let session: &dyn ToSql = &session_id;
    let target = store
        .fetch(
            "SELECT c.source, c.id FROM live_api_calls c WHERE c.session_id = $session_id \
             AND EXISTS (SELECT 1 FROM live_tool_calls t WHERE t.session_id = c.session_id \
             AND t.source = c.source AND t.api_call_id = c.id) \
             ORDER BY c.source, c.\"index\" LIMIT 1",
            &[("session_id", session)],
        )
        .unwrap();
    let target = target.first().expect("the session has a call with tools");
    let source = target.str("source").unwrap().to_owned();
    let api_call_id = target.str("id").unwrap().to_owned();

    let source_param: &dyn ToSql = &source;
    let call_param: &dyn ToSql = &api_call_id;
    // The widths the viewer's node page binds (`docs/viewer-bounds.md` defaults).
    let head_chars: &dyn ToSql = &120_i32;
    let detail_chars: &dyn ToSql = &4000_i32;
    let rows = store
        .fetch(
            hyphae_store::queries::VIEW_CALL_HEADER,
            &[
                ("session_id", session),
                ("source", source_param),
                ("api_call_id", call_param),
                ("head_chars", head_chars),
                ("detail_chars", detail_chars),
            ],
        )
        .expect("the node-page query runs against a store this crate created");

    let row = rows.first().expect("the call has a header row");
    // The scalars the pane prints, through the typed getters.
    assert_eq!(row.str("api_call_id").unwrap(), api_call_id);
    assert!(row.i64("call_index").unwrap() >= 0);
    assert!(
        row.i64("tool_calls").unwrap() > 0,
        "the call made tool calls"
    );
    row.timestamp("started_at").unwrap();

    // The nested value, read whole rather than through a typed getter — the design's
    // option A: the SQL owns the shape, and no Rust type declares it.
    let tools = row.value("tools").unwrap();
    let Some(Value::List(names)) = member(tools, "names") else {
        panic!("`tools.names` came back as {:?}, not a LIST", tools);
    };
    assert_eq!(
        names.len() as i64,
        row.i64("tool_calls").unwrap(),
        "the LIST holds one name per tool call the row counted"
    );
    let first = member(tools, "first").expect("`tools.first` is a member of the struct");
    assert!(
        matches!(member(first, "name"), Some(Value::Text(_))),
        "`tools.first.name` came back as {:?}, not text",
        member(first, "name")
    );
    // A struct nested two deep inside another, which is as deep as the library goes.
    let fields = member(first, "fields").expect("`tools.first.fields` is a member");
    assert!(
        matches!(fields, Value::Struct(_)),
        "`tools.first.fields` came back as {fields:?}, not a STRUCT"
    );
    assert!(
        member(fields, "input_head").is_some(),
        "the `tool_fields` macro's last member survived the trip"
    );
}

/// The gap the design feared, bounded: the appender does refuse a nested value, but it
/// refuses it as a typed error rather than the panic the design expected — and no column in
/// this store's schema is nested, so the write path never meets one.
#[test]
fn the_appender_refuses_a_nested_value() {
    let scratch = TempDir::new().unwrap();
    let store = Store::create(&scratch.path().join("traces.duckdb")).unwrap();
    store
        .connection()
        .execute_batch("CREATE TABLE probe (xs INTEGER[])")
        .unwrap();
    let mut appender = store.connection().appender("probe").unwrap();

    let nested = Value::List(vec![Value::Int(1), Value::Int(2)]);
    let error = appender
        .append_row([&nested as &dyn ToSql])
        .expect_err("the appender cannot write a LIST");
    assert!(
        error.to_string().contains("List") && error.to_string().contains("not yet supported"),
        "the refusal names the gap: {error}"
    );

    // And the design's fallback would not rescue it: a prepared INSERT refuses the same
    // value at the same bind step. The two paths share `ToSql`, so a nested column would
    // need its value composed in SQL, not bound — a correction stage 2 should carry.
    let refused = store
        .connection()
        .execute("INSERT INTO probe VALUES (?)", [&nested as &dyn ToSql])
        .expect_err("a prepared INSERT cannot bind a LIST either");
    assert!(
        refused.to_string().contains("List") && refused.to_string().contains("not yet supported"),
        "the INSERT path refuses it the same way: {refused}"
    );

    // No table this crate creates has a column the appender would refuse.
    for (table, columns) in schema::TABLES {
        for column in *columns {
            let kind: String = store
                .connection()
                .query_row(
                    "SELECT data_type FROM information_schema.columns \
                     WHERE table_name = ? AND column_name = ?",
                    [table, column],
                    |row| row.get(0),
                )
                .unwrap();
            assert!(
                !kind.contains('[') && !kind.contains("STRUCT") && !kind.contains("MAP"),
                "{table}.{column} is {kind}, which the appender cannot write"
            );
        }
    }
}
