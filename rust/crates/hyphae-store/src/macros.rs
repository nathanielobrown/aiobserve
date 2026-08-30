//! The SQL functions the query library is written against, installed on the connection a
//! query runs on.
//!
//! Ported from `src/hyphae/analyze/macros.py`, which stays the authority. A query file names
//! one of these and will not run without it, so both consumers install the whole set rather
//! than the subset a file needs.
//!
//! These are the one place this crate keeps a second copy of SQL the Python holds: they are
//! string constants inside a module, so unlike the query library they cannot be read off
//! disk. `context_window` is the exception — it is generated from the price table in
//! [`hyphae_extract::pricing`], the way Python generates it from `extract/pricing.py`, so the
//! numbers still live in one place per implementation. `tests/macros.rs` compares the
//! generated text against the Python module's own table.

use std::fmt::Write as _;
use std::sync::LazyLock;

use duckdb::Connection;
use hyphae_extract::pricing::CONTEXT_WINDOWS;

/// The line a failure is grouped by: its first, whitespace collapsed, with every absolute
/// path standing as `<path>`. Two queries group on it, and a group key that drifted between
/// them would count the same failure two ways.
const SIGNATURE_LINE: &str = r"
CREATE OR REPLACE TEMP MACRO signature_line(text) AS
regexp_replace(
    regexp_replace(trim(split_part(text, chr(10), 1)), '\s+', ' ', 'g'),
    '(^|\s)/[^\s]*[^\s.,;:]',
    '\1<path>',
    'g'
)
";

/// Whether one api call rebuilt the context it already had: it wrote at least `min_tokens` to
/// the cache, and wrote at least `min_pct` of everything it cached. Neither number is a fact
/// about Claude Code, so both stay bound parameters.
const REBUILT_CONTEXT: &str = "
CREATE OR REPLACE TEMP MACRO rebuilt_context(creation_tokens, read_tokens, min_tokens, min_pct)
AS creation_tokens >= min_tokens
   AND creation_tokens * 100 >= min_pct * (creation_tokens + read_tokens)
";

/// Where one api call left the model's context window: everything the reply was billed for.
const CONTEXT_FILL: &str = "
CREATE OR REPLACE TEMP MACRO context_fill(call) AS
call.cache_read_tokens + call.cache_creation_tokens + call.input_tokens + call.output_tokens
";

/// How much of that fill the call itself put there — the fill less the cache it read.
const CONTEXT_ADDED: &str = "
CREATE OR REPLACE TEMP MACRO context_added(call) AS
call.cache_creation_tokens + call.input_tokens + call.output_tokens
";

/// One field of a tool call's input, cut to the width of the column that will print it.
/// Guarded because `input` holds whatever the transcript did, and `json_extract_string`
/// raises on a value that is not JSON.
const TOOL_ASKED: &str = r"
CREATE OR REPLACE TEMP MACRO tool_asked(input, field, chars) AS
CASE WHEN json_valid(input)
     THEN substr(json_extract_string(input, '$.' || field), 1, chars + 1)
     END
";

/// A tool call's `file_path`, relative to the session's project directory when it sits
/// inside it. The repository comes off before the cut, not after, so the width is spent on
/// the tail that tells two paths apart rather than on their shared prefix.
const TOOL_PATH: &str = r"
CREATE OR REPLACE TEMP MACRO tool_path(input, project_dir, chars) AS
CASE WHEN starts_with(tool_asked(input, 'file_path', chars + length(project_dir) + 1),
                      project_dir || '/')
     THEN substr(tool_asked(input, 'file_path', chars + length(project_dir) + 1),
                 length(project_dir) + 2)
     ELSE tool_asked(input, 'file_path', chars) END
";

/// What a tool call carried, for the rules that name one — one struct rather than a column
/// apiece, so a query adds the whole set with one expression.
const TOOL_FIELDS: &str = r"
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
";

/// The window each model answers in, written out of [`CONTEXT_WINDOWS`].
///
/// Generated rather than bound as a parameter so a query names a model and gets a number,
/// with the constant still defined in one place. Byte-for-byte what `analyze/macros.py`
/// builds from the same table, including the leading newline and the four-space indent —
/// [`setup`] prints it for a reader to paste, and two spellings of one rule read as two rules.
pub fn context_window_text() -> String {
    let mut written =
        String::from("\nCREATE OR REPLACE TEMP MACRO context_window(model) AS CASE model\n");
    for (model, window) in CONTEXT_WINDOWS {
        writeln!(written, "    WHEN '{model}' THEN {window}").expect("a String never fails");
    }
    written.push_str("END\n");
    written
}

/// Every macro a shipped query may call, in dependency order — `tool_path` and `tool_fields`
/// are written in terms of the ones above them. Installed as a set rather than per query:
/// which macros a file needs is the file's business, and a connection holding some of them is
/// a connection where a query fails on the ones it does not.
fn definitions() -> &'static [String] {
    static DEFINITIONS: LazyLock<Vec<String>> = LazyLock::new(|| {
        vec![
            SIGNATURE_LINE.to_owned(),
            REBUILT_CONTEXT.to_owned(),
            CONTEXT_FILL.to_owned(),
            CONTEXT_ADDED.to_owned(),
            context_window_text(),
            TOOL_ASKED.to_owned(),
            TOOL_PATH.to_owned(),
            TOOL_FIELDS.to_owned(),
        ]
    });
    &DEFINITIONS
}

/// The same set as one script a reader can paste, which is what the viewer prints above a
/// statement that calls any of them. Semicolons and the install order are the whole
/// difference: what a consumer does on your behalf, written out.
pub fn setup() -> String {
    let script = definitions()
        .iter()
        .map(|definition| definition.trim())
        .collect::<Vec<_>>()
        .join(";\n");
    format!("{script};")
}

/// The setup `sql` must run under, or nothing when it calls no macro at all.
///
/// Named by hand rather than parsed: a statement mentioning one of these in a comment gets
/// the definitions too, which costs a reader nothing and is the safe way to be wrong.
pub fn needed_by(sql: &str) -> String {
    let called = NAMES.iter().any(|name| sql.contains(&format!("{name}(")));
    if called { setup() } else { String::new() }
}

/// What each definition above declares, for [`needed_by`] to scan for.
const NAMES: &[&str] = &[
    "signature_line",
    "rebuilt_context",
    "context_fill",
    "context_added",
    "context_window",
    "tool_asked",
    "tool_path",
    "tool_fields",
];

/// Create the library's macros on `connection`, before any query file runs against it.
///
/// Temp macros, so this works on a read-only connection: what it creates lives in the
/// session's own catalog rather than in the store.
pub fn install(connection: &Connection) -> duckdb::Result<()> {
    for definition in definitions() {
        connection.execute_batch(definition)?;
    }
    Ok(())
}
