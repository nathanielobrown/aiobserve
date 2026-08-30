//! One value a query is bound to: the binding half of the seam [`crate::Row`] answers.
//!
//! Ported from `ParamValue` in `src/hyphae/analyze/queries.py` — a closed set of the three
//! kinds a query manifest declares, plus the blank a filter left. A `&dyn ToSql` would take
//! anything DuckDB can bind; this takes what a query file asks for, so a call site reads as
//! the binding it is and a nested value is refused at the type rather than at run time.

use chrono::NaiveDate;
use duckdb::ToSql;
use duckdb::types::{Null, ToSqlOutput};

/// One bound value of a query.
#[derive(Debug, Clone, PartialEq)]
pub enum Param {
    Text(String),
    Int(i64),
    Date(NaiveDate),
    /// A filter the reader left blank, which every query spells `$x IS NULL`.
    Absent,
}

impl From<&str> for Param {
    fn from(value: &str) -> Self {
        Self::Text(value.to_owned())
    }
}

impl From<&String> for Param {
    fn from(value: &String) -> Self {
        Self::Text(value.clone())
    }
}

impl From<String> for Param {
    fn from(value: String) -> Self {
        Self::Text(value)
    }
}

impl From<i64> for Param {
    fn from(value: i64) -> Self {
        Self::Int(value)
    }
}

impl From<usize> for Param {
    fn from(value: usize) -> Self {
        Self::Int(value as i64)
    }
}

impl From<NaiveDate> for Param {
    fn from(value: NaiveDate) -> Self {
        Self::Date(value)
    }
}

/// A value the reader may or may not have supplied: `None` binds as SQL NULL.
impl<T: Into<Param>> From<Option<T>> for Param {
    fn from(value: Option<T>) -> Self {
        match value {
            Some(value) => value.into(),
            None => Self::Absent,
        }
    }
}

impl ToSql for Param {
    fn to_sql(&self) -> duckdb::Result<ToSqlOutput<'_>> {
        match self {
            Self::Text(value) => Ok(ToSqlOutput::from(value.as_str())),
            Self::Int(value) => Ok(ToSqlOutput::from(*value)),
            Self::Date(value) => value.to_sql(),
            Self::Absent => Ok(ToSqlOutput::from(Null)),
        }
    }
}
