//! The shared SQL library: that the Rust copy still says what the Python one says, and that
//! every macro in it answers on a connection the crate installed it on.
//!
//! `src/hyphae/analyze/macros.py` is the authority (`CLAUDE.md`), and these are the only two
//! macro bodies in the repo that are not read off one file — so the first leaf reads the
//! Python module's own text and compares it, rather than trusting a copy made once.

use duckdb::Connection;
use hyphae_store::macros;

mod common;

/// The Python module the Rust one is a copy of.
fn python_macros() -> String {
    std::fs::read_to_string(common::repo().join("src/hyphae/analyze/macros.py"))
        .expect("the Python macro module is readable")
}

/// One triple-quoted assignment out of the Python source, as written.
///
/// A hand parser rather than a Python run: the module holds nothing but literals, and the
/// suite reads the repo elsewhere already. It refuses anything it does not understand rather
/// than returning an empty body that would make the comparison below vacuous.
fn literal(source: &str, name: &str) -> String {
    let assignment = format!("\n{name} = ");
    let at = source
        .find(&assignment)
        .unwrap_or_else(|| panic!("`{name}` is assigned in analyze/macros.py"));
    let rest = &source[at + assignment.len()..];
    let body = rest
        .strip_prefix("r\"\"\"")
        .or_else(|| rest.strip_prefix("\"\"\""))
        .unwrap_or_else(|| panic!("`{name}` is assigned a triple-quoted literal"));
    let end = body
        .find("\"\"\"")
        .unwrap_or_else(|| panic!("`{name}`'s literal is closed"));
    body[..end].to_owned()
}

/// The price table as `extract/pricing.py` writes it, in file order.
fn python_context_windows() -> Vec<(String, i64)> {
    let source = std::fs::read_to_string(common::repo().join("src/hyphae/extract/pricing.py"))
        .expect("the Python price table is readable");
    let at = source
        .find("CONTEXT_WINDOWS: dict[str, int] = {")
        .expect("the price table declares CONTEXT_WINDOWS");
    let body = &source[at..];
    let end = body.find("\n}").expect("the table literal is closed");
    body[..end]
        .lines()
        .skip(1)
        .map(str::trim)
        .filter(|line| line.starts_with('"'))
        .map(|line| {
            let (model, window) = line.split_once(": ").expect("a `model: window` entry");
            let window = window.trim_end_matches(',').replace('_', "");
            (
                model.trim_matches('"').to_owned(),
                window.parse().expect("a window in tokens"),
            )
        })
        .collect()
}

/// The macro names, in the order Python's `DEFINITIONS` installs them, beside the name of the
/// literal each body comes from. `context_window` is absent: it is generated, not copied.
const COPIED: &[(&str, &str)] = &[
    ("signature_line", "_SIGNATURE_LINE"),
    ("rebuilt_context", "_REBUILT_CONTEXT"),
    ("context_fill", "_CONTEXT_FILL"),
    ("context_added", "_CONTEXT_ADDED"),
    ("tool_asked", "_TOOL_ASKED"),
    ("tool_path", "_TOOL_PATH"),
    ("tool_fields", "_TOOL_FIELDS"),
];

/// The whole library, byte for byte, against the module it was copied from.
///
/// `setup()` is the comparison rather than the constants one at a time, because the install
/// *order* is part of what the two consumers share: `tool_path` is written in terms of
/// `tool_asked`, and a connection that got them the other way round has neither.
#[test]
fn the_installed_library_is_byte_for_byte_the_python_one() {
    let source = python_macros();
    let mut definitions: Vec<String> = COPIED
        .iter()
        .map(|(_, literal_name)| literal(&source, literal_name))
        .collect();
    // Python's dependency order: the four shared rules, the generated window table, then the
    // three bounding macros. `COPIED` holds them in file order, so the window goes in at 4.
    definitions.insert(4, macros::context_window_text());
    let expected = definitions
        .iter()
        .map(|definition| definition.trim())
        .collect::<Vec<_>>()
        .join(";\n")
        + ";";
    assert_eq!(macros::setup(), expected);
}

/// The generated half: the numbers behind `context_window` still come from one table.
///
/// Order is asserted along with the pairs. The macro is a `CASE` chain, so a reordering is a
/// different string even though it answers the same — and the string is what the two
/// implementations must agree on.
#[test]
fn the_context_window_table_matches_the_python_price_table() {
    let ours: Vec<(String, i64)> = hyphae_extract::pricing::CONTEXT_WINDOWS
        .iter()
        .map(|(model, window)| ((*model).to_owned(), *window))
        .collect();
    assert_eq!(ours, python_context_windows());
}

/// Every macro answers on a connection `install` ran against — including the two written in
/// terms of the others, which is what the install order buys.
#[test]
fn every_macro_answers_on_an_installed_connection() {
    let connection = Connection::open_in_memory().expect("an in-memory database");
    macros::install(&connection).expect("the library installs");
    let call = |sql: &str| -> String {
        connection
            .query_row(&format!("SELECT ({sql})::VARCHAR"), [], |row| row.get(0))
            .unwrap_or_else(|error| panic!("`{sql}` answers: {error}"))
    };
    // A first line with a path in the middle of the sentence, which is the case the macro
    // exists for.
    assert_eq!(
        call("signature_line('failed   in /Users/someone/repo/file.py, twice\nand again')"),
        "failed in <path>, twice"
    );
    // 1,000 tokens written against 100 read is 90% of the cache: over both bars.
    assert_eq!(call("rebuilt_context(1000, 100, 500, 50)"), "true");
    assert_eq!(call("rebuilt_context(1000, 100, 5000, 50)"), "false");
    let tokens = "{'cache_read_tokens': 4, 'cache_creation_tokens': 3, \
                  'input_tokens': 2, 'output_tokens': 1}";
    assert_eq!(call(&format!("context_fill({tokens})")), "10");
    assert_eq!(call(&format!("context_added({tokens})")), "6");
    assert_eq!(call("context_window('claude-opus-5')"), "200000");
    // A model the table lacks answers NULL rather than a scale the viewer would invent.
    assert!(
        connection
            .query_row(
                "SELECT context_window('no-such-model') IS NULL",
                [],
                |row| { row.get::<_, bool>(0) }
            )
            .expect("the macro answers for an unpriced model")
    );
    let input = r#"'{"file_path": "/repo/src/deep/file.py", "command": "ls"}'"#;
    assert_eq!(call(&format!("tool_asked({input}, 'command', 40)")), "ls");
    // The repository prefix comes off before the cut, so the width is spent on the tail.
    assert_eq!(
        call(&format!("tool_path({input}, '/repo', 40)")),
        "src/deep/file.py"
    );
    assert_eq!(
        call(&format!("tool_fields({input}, '/repo', NULL, 40).path")),
        "src/deep/file.py"
    );
}

/// `needed_by` hands back the whole setup for a statement that calls any macro, and nothing
/// for one that calls none.
#[test]
fn the_setup_rides_along_only_with_a_statement_that_calls_a_macro() {
    assert_eq!(
        macros::needed_by("SELECT context_fill(c) FROM api_calls c"),
        macros::setup()
    );
    assert_eq!(macros::needed_by("SELECT 1"), "");
    // Named, not parsed: a mention in a comment gets the definitions too, which costs a
    // reader nothing and is the safe way to be wrong.
    assert_eq!(
        macros::needed_by("-- tool_path(x)\nSELECT 1"),
        macros::setup()
    );
}
