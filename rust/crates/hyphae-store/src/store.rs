//! Opening a trace store, writing rows into it, and reading rows back out.
//!
//! Ported from `src/hyphae/export/duckdb.py` and the connection half of
//! `src/hyphae/view/store.py`. What is deliberately left in Python: `migrate` and
//! `check_shape`. The prototype writes fresh stores and reads one already at
//! `SCHEMA_VERSION`, so every opener refuses anything else rather than carrying it forward —
//! a store that needs a migration is handed back to `hp` the Python binary.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use chrono::Utc;
use duckdb::types::{ToSql, Value};
use duckdb::{Config, Connection};
use hyphae_model::SessionTrace;

use crate::param::Param;
use crate::row::Row;
use crate::{macros, rows, schema};

/// What the store refuses, and why.
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("{0} holds no trace store. Run `hp extract` first.")]
    NoStore(PathBuf),
    #[error(
        "{path} holds schema version {held}, this build reads {reads}. Extract into a fresh \
         store. This one may hold the only copy of a pruned session — read docs/store.md \
         before deleting it."
    )]
    SchemaVersion {
        path: PathBuf,
        held: String,
        reads: i32,
    },
    #[error(
        "{0} holds tables this build did not write. Point at a different file, or delete this \
         one and re-extract."
    )]
    NotOurs(PathBuf),
    #[error("{path} is held by another process for writing")]
    Locked { path: PathBuf },
    #[error("no table named `{0}` in the schema")]
    UnknownTable(String),
    #[error("table `{table}`: the DDL creates {ddl:?} but the crate's column list says {listed:?}")]
    ColumnDrift {
        table: String,
        ddl: Vec<String>,
        listed: Vec<String>,
    },
    #[error("row {at} of `{table}` carries {values} value(s) for {columns} column(s)")]
    RowWidth {
        table: String,
        at: usize,
        values: usize,
        columns: usize,
    },
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    DuckDb(#[from] duckdb::Error),
    #[error(transparent)]
    Row(#[from] crate::row::RowError),
}

/// DuckDB's wording when another process holds the store's write lock. Matched on text
/// because the error it arrives as covers every other I/O failure too — the same reason
/// `view/store.py` matches it.
const LOCKED: &str = "Conflicting lock is held";

/// One open connection to one trace store.
pub struct Store {
    connection: Connection,
    path: PathBuf,
}

/// The path and nothing else: a store's connection has no useful debug rendering, and its
/// rows are private.
impl std::fmt::Debug for Store {
    fn fmt(&self, out: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        out.debug_struct("Store").field("path", &self.path).finish()
    }
}

impl Store {
    /// Create the store's tables and views at `path`, or open one that already has them.
    ///
    /// The write path. It runs no migration: a store of an older vintage is refused with the
    /// remedy, because Python's `migrate` is what carries one forward.
    pub fn create(path: &Path) -> Result<Self, StoreError> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let store = Self::connect(path, Config::default())?;
        // First, because the damage is silent otherwise: `CREATE TABLE IF NOT EXISTS` would
        // add our tables to a file that has nothing to do with us, and an operator points one
        // here by mistake.
        store.check_store_is_ours()?;
        store.connection.execute_batch(schema::SCHEMA)?;
        // After the tables: every view below reads them.
        store.connection.execute_batch(&schema::views())?;
        store.connection.execute(
            "INSERT INTO meta SELECT ? WHERE NOT EXISTS (SELECT 1 FROM meta)",
            [schema::SCHEMA_VERSION],
        )?;
        store.check_version()?;
        Ok(store)
    }

    /// Refuse a file that is someone else's database, before any DDL touches it.
    ///
    /// An empty file is a new store; anything else has to carry the `meta` stamp.
    fn check_store_is_ours(&self) -> Result<(), StoreError> {
        let mut statement = self.connection.prepare(
            "SELECT count(*) FILTER (table_name = 'meta'), count(*) FROM duckdb_tables()",
        )?;
        let (stamped, tables): (i64, i64) =
            statement.query_row([], |row| Ok((row.get(0)?, row.get(1)?)))?;
        if tables > 0 && stamped == 0 {
            return Err(StoreError::NotOurs(self.path.clone()));
        }
        Ok(())
    }

    /// Refuse a store this build's schema does not fit, naming the version it holds.
    fn check_version(&self) -> Result<(), StoreError> {
        let held = self.held_schema_version()?;
        if held == Some(schema::SCHEMA_VERSION) {
            return Ok(());
        }
        Err(StoreError::SchemaVersion {
            path: self.path.clone(),
            held: held.map_or_else(|| "nothing".to_owned(), |version| version.to_string()),
            reads: schema::SCHEMA_VERSION,
        })
    }

    /// The version stamped in an open store, or `None` when it carries no stamp at all.
    ///
    /// Asking the catalog has to be its own statement: DuckDB binds every table a query names
    /// before any filter in that same query can spare it.
    fn held_schema_version(&self) -> Result<Option<i32>, StoreError> {
        let stamped: i64 = self.connection.query_row(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'meta'",
            [],
            |row| row.get(0),
        )?;
        if stamped == 0 {
            return Ok(None);
        }
        let mut statement = self.connection.prepare("SELECT schema_version FROM meta")?;
        Ok(statement
            .query_map([], |row| row.get::<usize, i32>(0))?
            .next()
            .transpose()?)
    }

    /// Session id to the fingerprint the store holds, for every session it holds.
    ///
    /// Sessions whose files are gone from disk stay in here: the store is the archive.
    pub fn fingerprints(&self) -> Result<HashMap<String, String>, StoreError> {
        let mut statement = self
            .connection
            .prepare("SELECT session_id, fingerprint FROM extract_state")?;
        let rows = statement
            .query_map([], |row| {
                Ok((row.get::<usize, String>(0)?, row.get::<usize, String>(1)?))
            })?
            .collect::<duckdb::Result<HashMap<_, _>>>()?;
        Ok(rows)
    }

    /// Replace everything the store holds for this session, or roll back leaving it untouched.
    ///
    /// The delete runs over every table in [`schema::TABLES`], which is why a table added to
    /// the schema must be added there too: one left out of the delete would keep stale rows
    /// forever, while one left out of the insert would lose the session's rows on every
    /// re-extraction. Both halves read the same list, so neither can drift alone.
    pub fn export(&self, trace: &SessionTrace, fingerprint: &str) -> Result<(), StoreError> {
        let session_id = &trace.session.id;
        let batches = rows::of(trace);
        let state = rows::extract_state(trace, fingerprint, Utc::now());
        self.connection.execute_batch("BEGIN TRANSACTION")?;
        let outcome = (|| -> Result<(), StoreError> {
            for (table, _) in schema::TABLES {
                let key = schema::session_key(table);
                self.connection.execute(
                    &format!("DELETE FROM {table} WHERE {key} = ?"),
                    [session_id],
                )?;
            }
            self.connection.execute(
                "DELETE FROM extract_state WHERE session_id = ?",
                [session_id],
            )?;
            for (table, rows) in &batches {
                self.append_rows(table, rows)?;
            }
            self.append_rows(schema::EXTRACT_STATE.0, std::slice::from_ref(&state))
        })();
        match outcome {
            Ok(()) => {
                self.connection.execute_batch("COMMIT")?;
                Ok(())
            }
            Err(error) => {
                self.connection.execute_batch("ROLLBACK")?;
                Err(error)
            }
        }
    }

    /// Open a store an extract already wrote, without taking the write lock.
    ///
    /// Creates nothing: a path with no store behind it is a typo rather than a new store.
    /// `read_only` is not a parameter because DuckDB admits one writer at a time — a reader
    /// that takes the write lock by accident locks the viewer out.
    ///
    /// The version is checked here as `open_trace_store` checks it for a Python reader. A
    /// reader cannot migrate, so a store of another vintage has to be refused with a message
    /// naming the version — unchecked, it reaches the viewer as a binder error naming a
    /// column, with no version and no remedy in it.
    pub fn open_read_only(path: &Path) -> Result<Self, StoreError> {
        if !path.exists() {
            return Err(StoreError::NoStore(path.to_owned()));
        }
        let config = Config::default().access_mode(duckdb::AccessMode::ReadOnly)?;
        let store = Self::connect(path, config)?;
        store.check_version()?;
        Ok(store)
    }

    /// Open a store an extract already wrote, taking the write lock.
    ///
    /// For a writer that comes after an extract — enrichment is the one there is. Creates
    /// nothing and migrates nothing: [`Store::create`] stays the only thing that writes the
    /// pipeline's DDL, and a store of an older vintage is refused with the remedy rather
    /// than carried forward, as Python's `open_trace_store` would.
    pub fn open_for_write(path: &Path) -> Result<Self, StoreError> {
        if !path.exists() {
            return Err(StoreError::NoStore(path.to_owned()));
        }
        let store = Self::connect(path, Config::default())?;
        store.check_version()?;
        Ok(store)
    }

    fn connect(path: &Path, config: Config) -> Result<Self, StoreError> {
        let connection = Connection::open_with_flags(path, config).map_err(|error| {
            if error.to_string().contains(LOCKED) {
                StoreError::Locked {
                    path: path.to_owned(),
                }
            } else {
                StoreError::DuckDb(error)
            }
        })?;
        // Timestamps go in as UTC and must come back as UTC, whatever the machine's clock is
        // set to.
        connection.execute_batch("SET TimeZone='UTC'")?;
        // The library's shared SQL functions, which the query files call by name.
        macros::install(&connection)?;
        Ok(Self {
            connection,
            path: path.to_owned(),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn connection(&self) -> &Connection {
        &self.connection
    }

    /// Run one statement and hand back its rows keyed by column name — Python's `fetch`.
    ///
    /// Values come back as `duckdb::types::Value`, nested `LIST` and `STRUCT` included, so a
    /// query the SQL library owns needs no Rust type declared for its result.
    pub fn fetch(&self, sql: &str, params: &[(&str, Param)]) -> Result<Vec<Row>, StoreError> {
        let bound = params
            .iter()
            .map(|(name, value)| (*name, value as &dyn ToSql))
            .collect::<Vec<_>>();
        let mut statement = self.connection.prepare(sql)?;
        let mut answered = statement.query(bound.as_slice())?;
        let mut rows = Vec::new();
        // Column names come off the first row: DuckDB knows the result's shape only once the
        // statement has run.
        while let Some(answer) = answered.next()? {
            let columns = answer.as_ref().column_names();
            let values = (0..columns.len())
                .map(|at| answer.get::<usize, Value>(at))
                .collect::<duckdb::Result<Vec<_>>>()?;
            rows.push(Row::new(columns, values));
        }
        Ok(rows)
    }

    /// Write the same rows through prepared `INSERT` statements — the fallback path.
    ///
    /// The shape Python's exporter uses (`executemany`), kept because it binds every type
    /// DuckDB can bind, nested values included, where the appender binds only flat ones.
    /// One transaction, so a row that violates a constraint leaves the table as it was.
    pub fn insert_rows(&self, table: &str, rows: &[Vec<Value>]) -> Result<(), StoreError> {
        let columns = self.columns_of(table)?;
        self.check_widths(table, columns.len(), rows)?;
        let quoted = columns
            .iter()
            .map(|column| format!("\"{column}\""))
            .collect::<Vec<_>>()
            .join(", ");
        let placeholders = vec!["?"; columns.len()].join(", ");
        let sql = format!("INSERT INTO {table} ({quoted}) VALUES ({placeholders})");
        self.connection.execute_batch("BEGIN TRANSACTION")?;
        let outcome = (|| -> Result<(), StoreError> {
            let mut statement = self.connection.prepare(&sql)?;
            for row in rows {
                let bound: Vec<&dyn ToSql> = row.iter().map(|value| value as &dyn ToSql).collect();
                statement.execute(bound.as_slice())?;
            }
            Ok(())
        })();
        match outcome {
            Ok(()) => {
                self.connection.execute_batch("COMMIT")?;
                Ok(())
            }
            Err(error) => {
                self.connection.execute_batch("ROLLBACK")?;
                Err(error)
            }
        }
    }

    /// Write rows into `table` through DuckDB's appender — the bulk path.
    ///
    /// Each row's values must be in [`schema::TABLES`] order. The appender writes into
    /// DuckDB's own vectors rather than binding a statement per row, which is what makes it
    /// the fast path; what it cannot write is a nested value, and the error says so. It opens
    /// no transaction of its own, and flushing inside one enrolls the rows in it.
    ///
    /// Empty batches are common — most sessions link no PR and offload nothing — so they are
    /// a no-op rather than an appender opened for nothing.
    pub fn append_rows(&self, table: &str, rows: &[Vec<Value>]) -> Result<(), StoreError> {
        if rows.is_empty() {
            return Ok(());
        }
        let columns = self.columns_of(table)?;
        self.check_widths(table, columns.len(), rows)?;
        let mut appender = self.connection.appender(table)?;
        for row in rows {
            let bound: Vec<&dyn ToSql> = row.iter().map(|value| value as &dyn ToSql).collect();
            appender.append_row(bound.as_slice())?;
        }
        appender.flush()?;
        Ok(())
    }

    /// Every table's DDL columns as DuckDB reports them, against the crate's own list.
    ///
    /// The check that replaces `dataclasses.fields`: the design asks for the insert columns
    /// written out beside the DDL, and this is what stops the two from drifting apart.
    pub fn check_columns(&self) -> Result<(), StoreError> {
        for (table, listed) in schema::TABLES
            .iter()
            .chain(std::iter::once(&schema::EXTRACT_STATE))
        {
            let mut statement = self.connection.prepare(
                "SELECT column_name FROM information_schema.columns \
                 WHERE table_name = ? ORDER BY ordinal_position",
            )?;
            let ddl: Vec<String> = statement
                .query_map([table], |row| row.get::<usize, String>(0))?
                .collect::<duckdb::Result<Vec<_>>>()?;
            let listed: Vec<String> = listed.iter().map(|name| (*name).to_owned()).collect();
            if ddl != listed {
                return Err(StoreError::ColumnDrift {
                    table: (*table).to_owned(),
                    ddl,
                    listed,
                });
            }
        }
        Ok(())
    }

    fn columns_of(&self, table: &str) -> Result<&'static [&'static str], StoreError> {
        if table == schema::EXTRACT_STATE.0 {
            return Ok(schema::EXTRACT_STATE.1);
        }
        schema::columns(table).ok_or_else(|| StoreError::UnknownTable(table.to_owned()))
    }

    fn check_widths(
        &self,
        table: &str,
        columns: usize,
        rows: &[Vec<Value>],
    ) -> Result<(), StoreError> {
        for (at, row) in rows.iter().enumerate() {
            if row.len() != columns {
                return Err(StoreError::RowWidth {
                    table: table.to_owned(),
                    at,
                    values: row.len(),
                    columns,
                });
            }
        }
        Ok(())
    }
}
