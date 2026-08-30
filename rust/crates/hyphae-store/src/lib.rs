//! The trace store: the DuckDB file `hp extract` writes and every viewer page reads.
//!
//! It holds the ported DDL, both insert paths, the per-session replace one extraction
//! writes, rows read by column name, and enough of the query library to run one node-page
//! query.
//! `src/hyphae/export/duckdb.py` and `src/hyphae/view/store.py` stay the authority for
//! everything here until the Python store retires.

pub mod macros;
pub mod param;
pub mod queries;
pub mod row;
pub mod rows;
pub mod schema;
pub mod store;

pub use param::Param;
pub use row::{Row, RowError, Value};
pub use store::{Store, StoreError};
