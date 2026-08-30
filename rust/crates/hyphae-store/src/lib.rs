//! The trace store: the DuckDB file `hp extract` writes and every viewer page reads.
//!
//! Stage 1 of the Rust prototype (`plans/rust-prototype/design.md`) — the go/no-go spike on
//! the store path, kept as the crate's skeleton. It holds the ported DDL, both insert paths,
//! rows read by column name, and enough of the query library to run one node-page query.
//! `src/hyphae/export/duckdb.py` and `src/hyphae/view/store.py` stay the authority for
//! everything here until the Python store retires.

pub mod macros;
pub mod queries;
pub mod row;
pub mod schema;
pub mod store;

pub use row::{Row, RowError};
pub use store::{Store, StoreError};
