//! One row of a query result, read by column name.
//!
//! The design's row-typing decision, option A: the SQL owns the shape, so a page reads
//! columns by name the way `view/store.py` hands back `dict`s. A missing column or a wrong
//! type is a loud error here, exactly as Python's `KeyError` is there.

use chrono::{DateTime, TimeZone, Utc};
use duckdb::types::TimeUnit;

/// What a column holds, as DuckDB names it. Re-exported because [`Row::value`] hands one back:
/// a caller reading a nested `LIST` or `STRUCT` needs the type without a duckdb dependency of
/// its own.
pub use duckdb::types::Value;

/// What a getter refuses, named by the column it was asked for.
#[derive(Debug, thiserror::Error)]
pub enum RowError {
    #[error("no column named `{column}` in the row; it has: {available}")]
    MissingColumn { column: String, available: String },
    #[error("column `{column}` is {found}, not {expected}")]
    WrongType {
        column: String,
        expected: &'static str,
        found: &'static str,
    },
    #[error("column `{column}` holds {unit:?} {count}, which is not an instant chrono can name")]
    UnrepresentableInstant {
        column: String,
        unit: TimeUnit,
        count: i64,
    },
}

/// What a value is, for an error message.
///
/// Written out rather than taken from `Value::data_type`, which is `todo!()` for every
/// nested variant — asking a `LIST` or `STRUCT` what type it is panics, and the values a
/// node-page query hands back include both.
fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NULL",
        Value::Boolean(_) => "BOOLEAN",
        Value::TinyInt(_) | Value::SmallInt(_) | Value::Int(_) | Value::BigInt(_) => "an integer",
        Value::HugeInt(_) | Value::UHugeInt(_) => "a huge integer",
        Value::UTinyInt(_) | Value::USmallInt(_) | Value::UInt(_) | Value::UBigInt(_) => {
            "an unsigned integer"
        }
        Value::Float(_) | Value::Double(_) | Value::Decimal(_) => "a number",
        Value::Timestamp(..) => "a timestamp",
        Value::Text(_) | Value::Enum(_) => "text",
        Value::Blob(_) | Value::Geometry(_) => "a blob",
        Value::List(_) | Value::Array(_) => "a LIST",
        Value::Struct(_) => "a STRUCT",
        Value::Map(_) => "a MAP",
        Value::Union(_) => "a UNION",
        _ => "a value of a type this crate does not name",
    }
}

/// One member of a `STRUCT` value by name, or `None` when the value is not a struct or has
/// no such member.
///
/// The nested read a node-page query needs — `view_call_header.sql` answers with a struct of
/// a struct and a list. Written here because `OrderedMap::get` takes `&String`, so every call
/// site would otherwise allocate its own key.
pub fn member<'a>(value: &'a Value, name: &str) -> Option<&'a Value> {
    match value {
        Value::Struct(members) => members.get(&name.to_owned()),
        _ => None,
    }
}

/// One row: the column names of the statement that produced it, and its values in order.
#[derive(Debug, Clone)]
pub struct Row {
    columns: Vec<String>,
    values: Vec<Value>,
}

impl Row {
    pub fn new(columns: Vec<String>, values: Vec<Value>) -> Self {
        assert_eq!(
            columns.len(),
            values.len(),
            "a row must carry one value per column"
        );
        Self { columns, values }
    }

    pub fn columns(&self) -> &[String] {
        &self.columns
    }

    /// The values in the statement's own column order — what an insert binds.
    pub fn values(&self) -> &[Value] {
        &self.values
    }

    /// The raw value of one column — the way a nested `LIST` or `STRUCT` is read.
    pub fn value(&self, column: &str) -> Result<&Value, RowError> {
        self.columns
            .iter()
            .position(|name| name == column)
            .map(|at| &self.values[at])
            .ok_or_else(|| RowError::MissingColumn {
                column: column.to_owned(),
                available: self.columns.join(", "),
            })
    }

    /// Whether the column is SQL NULL. A NULL is a value the query meant, so every typed
    /// getter below refuses it and the caller asks this first.
    pub fn is_null(&self, column: &str) -> Result<bool, RowError> {
        Ok(matches!(self.value(column)?, Value::Null))
    }

    pub fn str(&self, column: &str) -> Result<&str, RowError> {
        match self.value(column)? {
            Value::Text(text) | Value::Enum(text) => Ok(text),
            other => Err(self.wrong_type(column, "text", other)),
        }
    }

    pub fn opt_str(&self, column: &str) -> Result<Option<&str>, RowError> {
        match self.value(column)? {
            Value::Null => Ok(None),
            _ => self.str(column).map(Some),
        }
    }

    pub fn i64(&self, column: &str) -> Result<i64, RowError> {
        match self.value(column)? {
            Value::TinyInt(number) => Ok(i64::from(*number)),
            Value::SmallInt(number) => Ok(i64::from(*number)),
            Value::Int(number) => Ok(i64::from(*number)),
            Value::BigInt(number) => Ok(*number),
            // A `SUM` over a BIGINT column answers HUGEINT, which is DuckDB refusing to
            // overflow rather than a column of a different type — every counted column a
            // viewer query rolls up arrives this way. Narrowed rather than carried, because
            // Python reads the same value as a plain `int`; a total that genuinely does not
            // fit is the loud error the design asks for.
            Value::HugeInt(number) => i64::try_from(*number)
                .map_err(|_| self.wrong_type(column, "an integer", &Value::HugeInt(*number))),
            other => Err(self.wrong_type(column, "an integer", other)),
        }
    }

    pub fn opt_i64(&self, column: &str) -> Result<Option<i64>, RowError> {
        match self.value(column)? {
            Value::Null => Ok(None),
            _ => self.i64(column).map(Some),
        }
    }

    pub fn f64(&self, column: &str) -> Result<f64, RowError> {
        match self.value(column)? {
            Value::Float(number) => Ok(f64::from(*number)),
            Value::Double(number) => Ok(*number),
            other => Err(self.wrong_type(column, "a number", other)),
        }
    }

    pub fn opt_f64(&self, column: &str) -> Result<Option<f64>, RowError> {
        match self.value(column)? {
            Value::Null => Ok(None),
            _ => self.f64(column).map(Some),
        }
    }

    pub fn bool(&self, column: &str) -> Result<bool, RowError> {
        match self.value(column)? {
            Value::Boolean(flag) => Ok(*flag),
            other => Err(self.wrong_type(column, "BOOLEAN", other)),
        }
    }

    /// A TIMESTAMPTZ as the instant it names. Always UTC: every connection this crate opens
    /// sets `TimeZone='UTC'`, so a store read on a machine in another zone reads the same.
    pub fn timestamp(&self, column: &str) -> Result<DateTime<Utc>, RowError> {
        let (unit, count) = match self.value(column)? {
            Value::Timestamp(unit, count) => (*unit, *count),
            other => return Err(self.wrong_type(column, "a timestamp", other)),
        };
        Utc.timestamp_micros(unit.to_micros(count)).single().ok_or(
            RowError::UnrepresentableInstant {
                column: column.to_owned(),
                unit,
                count,
            },
        )
    }

    pub fn opt_timestamp(&self, column: &str) -> Result<Option<DateTime<Utc>>, RowError> {
        match self.value(column)? {
            Value::Null => Ok(None),
            _ => self.timestamp(column).map(Some),
        }
    }

    /// One member of a `STRUCT` column, as the integer it holds — the shape a node's context
    /// arrives in (`view_nav_tree_turns.sql`).
    ///
    /// `None` for a NULL column, a NULL member, and a member the struct does not declare: a
    /// level whose nodes end on no window leaves the member out, and a model our price table
    /// has no window for answers NULL inside it. Both are a bar the page does not draw.
    pub fn member_i64(&self, column: &str, name: &str) -> Result<Option<i64>, RowError> {
        let held = match self.value(column)? {
            Value::Null => return Ok(None),
            other => other,
        };
        match member(held, name) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::TinyInt(number)) => Ok(Some(i64::from(*number))),
            Some(Value::SmallInt(number)) => Ok(Some(i64::from(*number))),
            Some(Value::Int(number)) => Ok(Some(i64::from(*number))),
            Some(Value::BigInt(number)) => Ok(Some(*number)),
            Some(other) => Err(RowError::WrongType {
                column: format!("{column}.{name}"),
                expected: "an integer",
                found: type_name(other),
            }),
        }
    }

    /// The members of a `LIST` column of text — the cut lists a header query answers with.
    pub fn strings(&self, column: &str) -> Result<Vec<&str>, RowError> {
        let held = match self.value(column)? {
            Value::Null => return Ok(Vec::new()),
            Value::List(members) | Value::Array(members) => members,
            other => return Err(self.wrong_type(column, "a LIST", other)),
        };
        held.iter()
            .map(|member| match member {
                Value::Text(text) | Value::Enum(text) => Ok(text.as_str()),
                other => Err(self.wrong_type(column, "a LIST of text", other)),
            })
            .collect()
    }

    /// The members of a `LIST` of `STRUCT` as rows of their own — a nested result read the way
    /// the outer result is.
    ///
    /// `view_numbers.sql` answers with a group per model, and what prices those groups reads a
    /// row. So the members become rows rather than the reader learning a second way to read a
    /// value.
    pub fn structs(&self, column: &str) -> Result<Vec<Row>, RowError> {
        let held = match self.value(column)? {
            Value::Null => return Ok(Vec::new()),
            Value::List(members) | Value::Array(members) => members,
            other => return Err(self.wrong_type(column, "a LIST", other)),
        };
        held.iter()
            .map(|entry| match entry {
                Value::Struct(members) => Ok(Row::new(
                    members.keys().cloned().collect(),
                    members.values().cloned().collect(),
                )),
                other => Err(self.wrong_type(column, "a LIST of structs", other)),
            })
            .collect()
    }

    /// The members of a `LIST` of `STRUCT` as the name and count each holds — the counted lists
    /// a session-list row prints, where the column counted differs per list.
    pub fn counts(&self, column: &str, count: &str) -> Result<Vec<(String, i64)>, RowError> {
        let held = match self.value(column)? {
            Value::Null => return Ok(Vec::new()),
            Value::List(members) | Value::Array(members) => members,
            other => return Err(self.wrong_type(column, "a LIST", other)),
        };
        held.iter()
            .map(|entry| {
                let named = match member(entry, "name") {
                    Some(Value::Text(text) | Value::Enum(text)) => text.clone(),
                    _ => return Err(self.wrong_type(column, "a LIST of named structs", entry)),
                };
                let counted = match member(entry, count) {
                    Some(Value::TinyInt(number)) => i64::from(*number),
                    Some(Value::SmallInt(number)) => i64::from(*number),
                    Some(Value::Int(number)) => i64::from(*number),
                    Some(Value::BigInt(number)) => *number,
                    _ => return Err(self.wrong_type(column, "a LIST of counted structs", entry)),
                };
                Ok((named, counted))
            })
            .collect()
    }

    fn wrong_type(&self, column: &str, expected: &'static str, found: &Value) -> RowError {
        RowError::WrongType {
            column: column.to_owned(),
            expected,
            found: type_name(found),
        }
    }
}
