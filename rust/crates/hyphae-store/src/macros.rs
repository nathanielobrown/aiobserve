//! The SQL functions the query library is written against, installed on the connection a
//! query runs on.
//!
//! Ported from `src/hyphae/analyze/macros.py`, and only its `BOUNDING` set — the three
//! macros the node-page queries call to cut a fat column to the width of the column that
//! will print it. The rest of that module (`signature_line`, `rebuilt_context`,
//! `context_fill`, `context_added`, `context_window`) belongs to stage 3, and
//! `context_window` is generated from `extract/pricing.py` rather than written out.
//!
//! These are the one place this crate keeps a second copy of SQL the Python holds: they are
//! string constants inside a module, so unlike the query library they cannot be read off
//! disk. The parity diff of stage 2 is what catches a drift.

use duckdb::Connection;

/// One field of a tool call's input, cut to the width of the column that will print it.
/// Guarded because `input` holds whatever the transcript did, and `json_extract_string`
/// raises on a value that is not JSON.
const TOOL_ASKED: &str = r#"
CREATE OR REPLACE TEMP MACRO tool_asked(input, field, chars) AS
CASE WHEN json_valid(input)
     THEN substr(json_extract_string(input, '$.' || field), 1, chars + 1)
     END
"#;

/// A tool call's `file_path`, relative to the session's project directory when it sits
/// inside it. The repository comes off before the cut, not after, so the width is spent on
/// the tail that tells two paths apart rather than on their shared prefix.
const TOOL_PATH: &str = r#"
CREATE OR REPLACE TEMP MACRO tool_path(input, project_dir, chars) AS
CASE WHEN starts_with(tool_asked(input, 'file_path', chars + length(project_dir) + 1),
                      project_dir || '/')
     THEN substr(tool_asked(input, 'file_path', chars + length(project_dir) + 1),
                 length(project_dir) + 2)
     ELSE tool_asked(input, 'file_path', chars) END
"#;

/// What a tool call carried, for the rules that name one — one struct rather than a column
/// apiece, so a query adds the whole set with one expression.
const TOOL_FIELDS: &str = r#"
CREATE OR REPLACE TEMP MACRO tool_fields(input, project_dir, addressed, chars) AS {
    'path': tool_path(input, project_dir, chars),
    'command': tool_asked(input, 'command', chars),
    'description': tool_asked(input, 'description', chars),
    'subagent_type': tool_asked(input, 'subagent_type', chars),
    'skill': tool_asked(input, 'skill', chars),
    'args': tool_asked(input, 'args', chars),
    'to': tool_asked(input, 'to', chars),
    'addressed': substr(addressed, 1, chars + 1),
    'summary': tool_asked(input, 'summary', chars),
    'pattern': tool_asked(input, 'pattern', chars),
    'url': tool_asked(input, 'url', chars),
    'query': tool_asked(input, 'query', chars),
    'message': tool_asked(input, 'message', chars),
    'todos': CASE WHEN json_valid(input)
                  THEN json_array_length(input, '$.todos') END,
    'input_head': substr(input, 1, chars + 1)
}
"#;

/// In dependency order: `tool_path` and `tool_fields` are written in terms of the one above.
const DEFINITIONS: &[&str] = &[TOOL_ASKED, TOOL_PATH, TOOL_FIELDS];

/// Create the library's macros on `connection`, before any query file runs against it.
///
/// Temp macros, so this works on a read-only connection: what it creates lives in the
/// session's own catalog rather than in the store.
pub fn install(connection: &Connection) -> duckdb::Result<()> {
    for definition in DEFINITIONS {
        connection.execute_batch(definition)?;
    }
    Ok(())
}
