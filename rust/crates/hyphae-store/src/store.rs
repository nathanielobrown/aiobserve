//! Opening a trace store, writing rows into it, and reading rows back out.
//!
//! Ported from `src/hyphae/export/duckdb.py` and the connection half of
//! `src/hyphae/view/store.py`, cut to what stage 1 of the design needs: no fingerprints, no
//! per-session replace transaction, no migrations. Stage 2 owns those.

use std::path::{Path, PathBuf};

use duckdb::types::{ToSql, Value};
use duckdb::{Config, Connection};

use crate::row::Row;
use crate::{macros, schema};

/// What the store refuses, and why.
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("{0} holds no trace store. Run `hp extract` first.")]
    NoStore(PathBuf),
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

impl Store {
    /// Create the store's tables and views at `path`, or open one that already has them.
    ///
    /// The write path. Unlike Python's exporter this runs no migration and checks no shape:
    /// stage 1 writes fresh stores into a tempdir, and a store that needs carrying forward
    /// is stage 2's problem.
    pub fn create(path: &Path) -> Result<Self, StoreError> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let store = Self::connect(path, Config::default())?;
        store.connection.execute_batch(schema::SCHEMA)?;
        // After the tables: every view below reads them.
        store.connection.execute_batch(&schema::views())?;
        store.connection.execute(
            "INSERT INTO meta SELECT ? WHERE NOT EXISTS (SELECT 1 FROM meta)",
            [schema::SCHEMA_VERSION],
        )?;
        Ok(store)
    }

    /// Open a store an extract already wrote, without taking the write lock.
    ///
    /// Creates nothing: a path with no store behind it is a typo rather than a new store.
    /// `read_only` is not a parameter because DuckDB admits one writer at a time — a reader
    /// that takes the write lock by accident locks the viewer out.
    pub fn open_read_only(path: &Path) -> Result<Self, StoreError> {
        if !path.exists() {
            return Err(StoreError::NoStore(path.to_owned()));
        }
        let config = Config::default().access_mode(duckdb::AccessMode::ReadOnly)?;
        Self::connect(path, config)
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
    pub fn fetch(&self, sql: &str, params: &[(&str, &dyn ToSql)]) -> Result<Vec<Row>, StoreError> {
        let mut statement = self.connection.prepare(sql)?;
        let mut answered = statement.query(params)?;
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

    /// Write rows into `table` through DuckDB's appender — the bulk path.
    ///
    /// Each row's values must be in [`schema::TABLES`] order. The appender writes into
    /// DuckDB's own vectors rather than binding a statement per row, which is what makes it
    /// the fast path; what it cannot write is a nested value, and the error says so.
    pub fn append_rows(&self, table: &str, rows: &[Vec<Value>]) -> Result<(), StoreError> {
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

    /// Every table's DDL columns as DuckDB reports them, against the crate's own list.
    ///
    /// The check that replaces `dataclasses.fields`: the design asks for the insert columns
    /// written out beside the DDL, and this is what stops the two from drifting apart.
    pub fn check_columns(&self) -> Result<(), StoreError> {
        for (table, listed) in schema::TABLES {
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
