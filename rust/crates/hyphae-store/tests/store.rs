//! The trace store: what the Rust exporter writes, and what it refuses.
//!
//! Every leaf runs against a real DuckDB file in a tempdir, built from the redacted
//! recordings under `tests/fixtures/` through the Rust extractor — DuckDB is as much the
//! thing under test as the code around it, so nothing here is mocked and nothing here
//! invents a row.
//!
//! Stage 1's spike read a copy of the canonical store instead, which put private transcript
//! text one failing assertion away from the test log. `common::assert_rows_equal` is what
//! replaced that: it names the table, row and column that differ and never the value.

mod common;

use duckdb::types::{ToSql, Value};
use hyphae_store::row::member;
use hyphae_store::{Store, StoreError, schema};
use tempfile::TempDir;

/// A fixture session with enough rows in enough tables to make a round trip mean something.
///
/// Derived rather than pinned: the corpus is discovered, so a hard-coded id would rot the
/// day a fixture is added or renamed.
fn representative_session(store: &Store) -> String {
    let rows = store
        .fetch(
            "SELECT c.session_id FROM \
             (SELECT session_id, count(*) AS calls FROM api_calls GROUP BY 1) c \
             JOIN (SELECT session_id, count(*) AS tools FROM tool_calls GROUP BY 1) t \
                 USING (session_id) \
             JOIN (SELECT session_id, count(*) AS runs FROM agent_runs GROUP BY 1) a \
                 USING (session_id) \
             ORDER BY c.calls DESC, c.session_id LIMIT 1",
            &[],
        )
        .expect("the store answers the session probe");
    rows.first()
        .expect("the fixture corpus holds a session with calls, tools and runs")
        .str("session_id")
        .expect("session_id is text")
        .to_owned()
}

/// The check the design's "write them once, beside the DDL" replaces `dataclasses.fields`
/// with: DuckDB's own account of each table, against the column list the crate inserts by.
#[test]
fn the_insert_column_lists_and_the_ddl_agree() {
    let scratch = TempDir::new().unwrap();
    let store = Store::create(&scratch.path().join("traces.duckdb")).unwrap();
    store.check_columns().expect("no table's columns drifted");
}

/// A store the Rust exporter created carries the schema version the Python one stamps.
#[test]
fn a_new_store_is_stamped_with_the_schema_version() {
    let scratch = TempDir::new().unwrap();
    let store = Store::create(&scratch.path().join("traces.duckdb")).unwrap();
    let held = store.fetch("SELECT schema_version FROM meta", &[]).unwrap();
    assert_eq!(held.len(), 1, "one stamp, not one per export");
    assert_eq!(
        held[0].i64("schema_version").unwrap(),
        i64::from(schema::SCHEMA_VERSION)
    );
}

/// A store of another vintage is refused, and the message names the version it holds.
///
/// Rust runs no migration: `export/duckdb.py`'s `migrate` stays the one place a store is
/// carried forward, so the remedy here is to extract into a fresh file.
#[test]
fn a_store_stamped_with_another_version_is_refused() {
    let scratch = TempDir::new().unwrap();
    let path = scratch.path().join("traces.duckdb");
    let store = Store::create(&path).unwrap();
    store
        .connection()
        .execute_batch("UPDATE meta SET schema_version = 3")
        .unwrap();
    drop(store);

    let error = Store::create(&path).expect_err("an older store is refused");
    let StoreError::SchemaVersion { held, reads, .. } = &error else {
        panic!("expected a schema-version error, got {error:?}");
    };
    assert_eq!(held, "3");
    assert_eq!(*reads, schema::SCHEMA_VERSION);
}

/// A database that is not ours is left alone rather than having our tables added to it.
#[test]
fn a_file_that_is_not_a_trace_store_is_refused() {
    let scratch = TempDir::new().unwrap();
    let path = scratch.path().join("someone-elses.duckdb");
    let foreign = duckdb::Connection::open(&path).unwrap();
    foreign
        .execute_batch("CREATE TABLE invoices (id INTEGER)")
        .unwrap();
    drop(foreign);

    let error = Store::create(&path).expect_err("a foreign database is refused");
    assert!(matches!(error, StoreError::NotOurs(_)), "got {error:?}");
}

/// A store another process holds for writing reports itself as locked, not as a crash.
///
/// Two traps sit in this leaf. The lock is per *process*, so the holder is a subprocess —
/// DuckDB hands a second connection in one process the instance it already has, and that
/// path never re-checks the lock. And the file this test opens must be one this process has
/// never opened before, for the same reason: the cached instance would answer instead. So
/// the holder creates the store as well as holding it.
#[test]
fn a_store_held_open_for_writing_reports_itself_locked() {
    let scratch = TempDir::new().unwrap();
    let path = scratch.path().join("traces.duckdb");
    // A `meta` table so the store passes for ours, since the lock is the subject here.
    let mut holder = std::process::Command::new(python())
        .args([
            "-c",
            "import duckdb, sys; connection = duckdb.connect(sys.argv[1]); \
             connection.execute('CREATE TABLE meta (schema_version INTEGER)'); \
             print('held', flush=True); sys.stdin.read()",
            &path.display().to_string(),
        ])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .expect("the holder process starts");
    // Waiting for its line rather than sleeping: DuckDB takes the lock when the first
    // statement runs, and the line comes after that one.
    let mut ready = String::new();
    std::io::BufRead::read_line(
        &mut std::io::BufReader::new(holder.stdout.take().expect("the holder has stdout")),
        &mut ready,
    )
    .expect("the holder reports that it holds the lock");
    assert_eq!(ready.trim(), "held");

    let error = Store::create(&path).expect_err("the second writer is refused");
    holder.kill().expect("the holder stops");
    // Reaped, or the tempdir is removed while a process still holds the file open.
    holder.wait().expect("the holder is reaped");
    assert!(matches!(error, StoreError::Locked { .. }), "got {error:?}");
}

/// The interpreter the repo's virtualenv owns, or the system one.
fn python() -> std::path::PathBuf {
    let venv = common::fixtures()
        .parent()
        .and_then(std::path::Path::parent)
        .expect("the repo is two levels above tests/fixtures")
        .join(".venv/bin/python");
    if venv.exists() {
        venv
    } else {
        "python3".into()
    }
}

/// The widest table round-trips: what `rows::of` built goes in through the appender and
/// comes back out of DuckDB the same values.
#[test]
fn the_widest_table_round_trips_through_the_appender() {
    let (_scratch, store) = common::fixture_store();
    let session_id = representative_session(&store);

    // 24 columns, every one of them a scalar — the whole reason the appender can write them.
    let written = common::session_rows(&store, &session_id, schema::WIDEST_TABLE);
    assert!(
        !written.is_empty(),
        "the probe picked a session with api calls"
    );
    assert_eq!(written[0].len(), 24, "api_calls is 24 columns wide");

    // Re-exported into a second store, then compared table by table: the same rows written
    // twice by the same path is the round trip the design's go/no-go asked for.
    let elsewhere = TempDir::new().unwrap();
    let copy = Store::create(&elsewhere.path().join("traces.duckdb")).unwrap();
    let extractor = hyphae_extract::Extractor::new(common::fixtures());
    let transcript = common::corpus_transcripts()
        .into_iter()
        .find(|path| {
            path.file_stem()
                .is_some_and(|stem| stem == session_id.as_str())
        })
        .expect("the probed session is one of the fixtures");
    let source = common::source(&transcript);
    copy.export(&extractor.extract(&source).unwrap(), &source.fingerprint)
        .unwrap();

    let mut carried = 0;
    for (table, _) in schema::TABLES {
        let once = common::session_rows(&store, &session_id, table);
        let twice = common::session_rows(&copy, &session_id, table);
        common::assert_rows_equal(table, &once, &twice);
        if !once.is_empty() {
            carried += 1;
        }
    }
    // A session with rows in only one or two tables would make the loop above vacuous.
    assert!(
        carried >= 6,
        "the probe picked a session touching {carried} tables, too few"
    );
}

/// A TIMESTAMPTZ the Rust exporter wrote is the instant the Python exporter wrote for the
/// same recording, and reads back through chrono with no zone shift.
#[test]
fn a_timestamptz_matches_the_instant_the_python_store_holds() {
    let (_scratch, store) = common::fixture_store();
    let session_id = representative_session(&store);

    let session: &dyn ToSql = &session_id;
    // The store's own rendering of the instant beside chrono's reading of the same column.
    let sql = "SELECT strftime(started_at, '%Y-%m-%d %H:%M:%S.%g') AS printed, started_at \
               FROM sessions WHERE id = $session_id";
    let ours = store.fetch(sql, &[("session_id", session)]).unwrap();
    let instant = ours[0].timestamp("started_at").unwrap();
    assert_eq!(
        instant.format("%Y-%m-%d %H:%M:%S%.3f").to_string(),
        ours[0].str("printed").unwrap(),
        "chrono names the instant DuckDB prints, with no zone shift"
    );

    // And the Python exporter, over the same transcript, put the same instant there. A
    // timestamp is not private, so this one value can be compared directly.
    let transcript = common::corpus_transcripts()
        .into_iter()
        .find(|path| {
            path.file_stem()
                .is_some_and(|stem| stem == session_id.as_str())
        })
        .expect("the probed session is one of the fixtures");
    assert_eq!(
        python_started_at(&transcript),
        ours[0].str("printed").unwrap()
    );
}

/// What the Python extractor makes of one transcript's `started_at`, printed the same way.
///
/// Shelling out rather than keeping a second store around: one value is wanted, and the
/// Python side is the authority the port is measured against.
fn python_started_at(transcript: &std::path::Path) -> String {
    let script = "import sys; sys.path.insert(0, sys.argv[1]); \
                  from pathlib import Path; \
                  from hyphae.extract.claude_code import ClaudeCodeExtractor; \
                  from hyphae.pipeline import SessionSource; \
                  from hyphae.sessions import SessionFiles; \
                  t = Path(sys.argv[2]); f = SessionFiles(id=t.stem, transcript=t); \
                  s = SessionSource(id=t.stem, files=tuple(f.files()), fingerprint='x'); \
                  at = ClaudeCodeExtractor().extract(s).session.started_at; \
                  print(at.strftime('%Y-%m-%d %H:%M:%S.') + f'{at.microsecond // 1000:03d}')";
    let repo = common::fixtures()
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_owned();
    let run = std::process::Command::new(python())
        .args([
            "-c",
            script,
            &repo.join("src").display().to_string(),
            &transcript.display().to_string(),
        ])
        .current_dir(&repo)
        .output()
        .expect("a Python interpreter is available");
    assert!(
        run.status.success(),
        "python failed: {}",
        String::from_utf8_lossy(&run.stderr)
    );
    String::from_utf8(run.stdout)
        .expect("python printed UTF-8")
        .trim()
        .to_owned()
}

/// Re-exporting one session replaces its rows and leaves every other session alone.
///
/// `export/duckdb.py:export` deletes by session key across every table inside one
/// transaction. A table missing from that list would leak rows on every re-run, so the
/// count assertion below is per table rather than over the store as a whole.
#[test]
fn re_exporting_a_session_replaces_its_rows_and_touches_no_other() {
    let (_scratch, store) = common::fixture_store();
    let session_id = representative_session(&store);
    let before = common::table_counts(&store);

    let transcript = common::corpus_transcripts()
        .into_iter()
        .find(|path| {
            path.file_stem()
                .is_some_and(|stem| stem == session_id.as_str())
        })
        .expect("the probed session is one of the fixtures");
    let source = common::source(&transcript);
    let trace = hyphae_extract::Extractor::new(common::fixtures())
        .extract(&source)
        .unwrap();
    store.export(&trace, "a-second-fingerprint").unwrap();

    assert_eq!(
        common::table_counts(&store),
        before,
        "no table grew or shrank"
    );
    // And the state row was replaced rather than doubled.
    let state = store
        .fetch(
            "SELECT count(*) AS n FROM extract_state WHERE session_id = $session_id",
            &[("session_id", &session_id as &dyn ToSql)],
        )
        .unwrap();
    assert_eq!(state[0].i64("n").unwrap(), 1);
    assert_eq!(
        store.fingerprints().unwrap()[&session_id],
        "a-second-fingerprint"
    );
}

/// A refused write rolls the whole export back, leaving the session's earlier rows intact.
///
/// Provoked by exporting a trace whose api calls collide on their own primary key: the
/// delete clears the session first, so the failure lands mid-transaction with the table
/// already emptied. Only a rollback puts the first export's rows back.
#[test]
fn a_refused_export_rolls_back_to_the_previous_rows() {
    let (_scratch, store) = common::fixture_store();
    let session_id = representative_session(&store);
    let before = common::table_counts(&store);

    let transcript = common::corpus_transcripts()
        .into_iter()
        .find(|path| {
            path.file_stem()
                .is_some_and(|stem| stem == session_id.as_str())
        })
        .expect("the probed session is one of the fixtures");
    let source = common::source(&transcript);
    let mut trace = hyphae_extract::Extractor::new(common::fixtures())
        .extract(&source)
        .unwrap();
    // Every api call duplicated: the second copy of each collides on (session_id, source, id).
    let duplicated = trace.api_calls.clone();
    trace.api_calls.extend(duplicated);

    store
        .export(&trace, "never-committed")
        .expect_err("a duplicate primary key is refused");
    assert_eq!(
        common::table_counts(&store),
        before,
        "the failed export left no trace"
    );
    assert_eq!(
        store.fingerprints().unwrap()[&session_id],
        "fixture",
        "the fingerprint is still the committed one"
    );
}

/// Both write paths put the same rows in, so the choice between them is cost, not
/// correctness.
///
/// The design named prepared `INSERT` batches as the fallback if the appender could not
/// carry the schema. It can, so `export` uses the appender — but the fallback stays
/// exercised, because it is the shape `export/duckdb.py` uses.
#[test]
fn both_write_paths_put_the_same_rows_in() {
    let (_corpus_dir, corpus) = common::fixture_store();
    let session_id = representative_session(&corpus);
    let written = common::session_rows(&corpus, &session_id, schema::WIDEST_TABLE);

    let appended_dir = TempDir::new().unwrap();
    let appended = Store::create(&appended_dir.path().join("traces.duckdb")).unwrap();
    appended
        .append_rows(schema::WIDEST_TABLE, &written)
        .expect("the appender writes it");

    let inserted_dir = TempDir::new().unwrap();
    let inserted = Store::create(&inserted_dir.path().join("traces.duckdb")).unwrap();
    inserted
        .insert_rows(schema::WIDEST_TABLE, &written)
        .expect("prepared INSERTs write it");

    common::assert_rows_equal(
        schema::WIDEST_TABLE,
        &common::session_rows(&appended, &session_id, schema::WIDEST_TABLE),
        &written,
    );
    common::assert_rows_equal(
        schema::WIDEST_TABLE,
        &common::session_rows(&inserted, &session_id, schema::WIDEST_TABLE),
        &written,
    );
}

/// One real node-page query, run against a store the Rust exporter wrote, with the macros
/// `view/store.py` installs — and its nested `tools` value read back whole.
///
/// This is stage 1's go/no-go: `view_call_header.sql` answers with a
/// `STRUCT(first STRUCT(name, fields STRUCT(..)), names LIST(VARCHAR))`, which is the shape
/// the design flagged. Reading it needs no Rust type declared for the query's result.
#[test]
fn a_node_page_query_reads_its_nested_struct_and_list() {
    let (_scratch, store) = common::fixture_store();
    let session_id = representative_session(&store);

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
        .expect("the node-page query runs against a store this crate wrote");

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
        panic!("`tools.names` did not come back as a LIST");
    };
    assert_eq!(
        names.len() as i64,
        row.i64("tool_calls").unwrap(),
        "the LIST holds one name per tool call the row counted"
    );
    let first = member(tools, "first").expect("`tools.first` is a member of the struct");
    assert!(
        matches!(member(first, "name"), Some(Value::Text(_))),
        "`tools.first.name` did not come back as text"
    );
    // A struct nested two deep inside another, which is as deep as the library goes.
    let fields = member(first, "fields").expect("`tools.first.fields` is a member");
    assert!(
        matches!(fields, Value::Struct(_)),
        "`tools.first.fields` did not come back as a STRUCT"
    );
    assert!(
        member(fields, "input_head").is_some(),
        "the `tool_fields` macro's last member survived the trip"
    );
}

/// The gap the design feared, bounded: the appender does refuse a nested value, but no
/// column in this store's schema is nested, so the write path never meets one.
#[test]
fn the_appender_refuses_a_nested_value_and_no_column_is_one() {
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
    // need its value composed in SQL, not bound.
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
