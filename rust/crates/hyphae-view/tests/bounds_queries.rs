//! What a page can weigh, held by the queries behind it rather than by the corpus's luck.
//!
//! Ported from the scanning half of `tests/view/test_bounds.py`. Two instruments, each with a
//! case of its own: no query behind a page or a fragment selects a fat column outside a call
//! that cuts it, and no viewer query hides a page size in its text. The per-value queries are
//! the declared exception and are held to it here too. What a *served* page weighs is priced
//! against the budgets, which are not ported yet.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

use duckdb::Connection;
use duckdb::types::Value;
use hyphae_store::manifest::{self, QueryMeta};
use hyphae_store::{macros, queries};
use hyphae_view::store::{Fragment, Page, Query, Whole};
use regex::Regex;

/// The columns a transcript can make arbitrarily large. Spelled by name because the scan is a
/// scan of query text; the leaf at the bottom holds each name to a column that exists.
const FAT: [&str; 12] = [
    "raw",
    "text",
    "thinking",
    "result",
    "input",
    "content",
    "brief",
    "description",
    "agent_type",
    "model",
    "prompt",
    "command_args",
];

/// What a query may wrap a fat column in and still be bounded: a fixed-width prefix of it, a
/// count of what it holds, the check that it parses, the window the model it names answers in,
/// or one of the library's own cutting macros.
///
/// Anything else puts the whole value on the page. Read at any depth —
/// `substr(coalesce(json_extract_string(input, …), …), 1, $n)` is a cut of whatever it wraps,
/// so what a bounding call opens is exempt to its close.
fn bounding() -> &'static BTreeSet<&'static str> {
    static BOUNDING: LazyLock<BTreeSet<&'static str>> = LazyLock::new(|| {
        let mut named = BTreeSet::from([
            "substr",
            "length",
            "json_valid",
            // A count of a JSON array is a number however long the array is.
            "json_array_length",
            "context_window",
        ]);
        named.extend(macros::BOUNDING.map(|(name, _)| name));
        named
    });
    &BOUNDING
}

/// Every word a statement names outside a bounding call, however deeply they nest.
fn named(sql: &str) -> Vec<String> {
    static TOKEN: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"[A-Za-z_][A-Za-z0-9_]*|\(|\)").expect("a literal pattern"));
    // Whether each open bracket opened a bounding call, and how many of those are still open.
    let mut opened: Vec<bool> = Vec::new();
    let mut bounding_depth = 0_usize;
    let mut word = String::new();
    let mut outside = Vec::new();
    for token in TOKEN.find_iter(sql) {
        let found = token.as_str();
        match found {
            "(" => {
                let bounds = bounding().contains(word.as_str());
                opened.push(bounds);
                bounding_depth += usize::from(bounds);
            }
            ")" => {
                bounding_depth -= usize::from(opened.pop().unwrap_or(false));
            }
            _ if bounding_depth == 0 => outside.push(found.to_owned()),
            _ => {}
        }
        word = if found == ")" {
            String::new()
        } else {
            found.to_lowercase()
        };
    }
    outside
}

/// The fat columns a statement selects outside a bounding call — what a page can't afford.
///
/// An output name is not a selected column, so `AS` and what follows it comes out first: a cut
/// column keeps the name of the column it cuts, and the cut is what the page shows. A quoted
/// string is not a column either — `'$.description'` names a key inside a value.
fn unbounded(sql: &str) -> BTreeSet<String> {
    static COMMENT: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"--[^\n]*").expect("a literal pattern"));
    static STRING: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"'[^']*'").expect("a literal pattern"));
    static ALIAS: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)\bAS\s+[A-Za-z_][A-Za-z0-9_]*").expect("a literal pattern")
    });
    let without_comments = COMMENT.replace_all(sql, " ");
    let without_strings = STRING.replace_all(&without_comments, " ");
    let without_aliases = ALIAS.replace_all(&without_strings, " ");
    named(&without_aliases)
        .into_iter()
        .filter(|word| FAT.contains(&word.as_str()))
        .collect()
}

/// The set a passing scan answers with, spelled once.
fn nothing() -> BTreeSet<String> {
    BTreeSet::new()
}

/// One name, as the scan hands it back.
fn only(column: &str) -> BTreeSet<String> {
    BTreeSet::from([column.to_owned()])
}

#[test]
fn the_fat_column_scan_catches_one() {
    // The scan is worth its green only if it flags a select the pages must not contain. The
    // statements are invented — no shipped query selects a fat column whole, which is exactly
    // why the instrument needs its own case.
    assert_eq!(
        unbounded("SELECT r.raw FROM raw_records r -- text"),
        only("raw")
    );
    assert_eq!(
        unbounded("SELECT substr(r.raw, 1, 200) AS raw_head FROM raw_records r"),
        nothing()
    );
    // A count of a value is a number, and a page can afford any number.
    assert_eq!(
        unbounded("SELECT length(r.raw) AS raw_chars FROM raw_records r"),
        nothing()
    );
    // A cut column may keep the name of the column it cuts, and the name is not the value...
    assert_eq!(
        unbounded("SELECT substr(e.description, 1, 200) AS description FROM turns e"),
        nothing()
    );
    // ...but the column under that name still counts.
    assert_eq!(
        unbounded("SELECT e.description AS description FROM turns e"),
        only("description")
    );
    // A cut of what a call read out of a fat column is a cut, however deep the call nests...
    let parsed = "json_extract_string(t.input, '$.file_path')";
    assert_eq!(
        unbounded(&format!(
            "SELECT substr(coalesce({parsed}, t.input), 1, 200) AS head FROM tools t"
        )),
        nothing()
    );
    // ...and the check that a value parses hands back a flag rather than the value.
    assert_eq!(
        unbounded("SELECT json_valid(t.input) AS ok FROM tools t"),
        nothing()
    );
    // As does a count of how many items a value holds, whatever each of them weighs.
    assert_eq!(
        unbounded("SELECT json_array_length(t.input, '$.todos') AS n FROM tools t"),
        nothing()
    );
    // The window lookup is the same kind of read: a model goes in and a number comes back.
    assert_eq!(
        unbounded("SELECT context_window(c.model) AS window FROM api_calls c"),
        nothing()
    );
    assert_eq!(
        unbounded("SELECT c.model AS model FROM api_calls c"),
        only("model")
    );
    // A key inside a JSON path is a string, not the column that happens to share its name...
    assert_eq!(
        unbounded("SELECT substr(json_extract_string(t.input, '$.description'), 1, 9) AS a FROM t"),
        nothing()
    );
    // ...while a fat column read by a call that is not a cut is the whole value on the page.
    assert_eq!(
        unbounded(&format!("SELECT {parsed} AS path FROM tools t")),
        only("input")
    );
    assert_eq!(
        unbounded("SELECT coalesce(substr(t.input, 1, 9), t.result) AS head FROM tools t"),
        only("result")
    );
}

#[test]
fn every_macro_the_scan_trusts_cuts_the_value_it_reads() {
    // The scan cannot see through a macro call, so what it trusts by name is checked by body.
    // Without this the trust is a list: a macro that stopped cutting would go on being read as
    // bounding, and every query calling it would keep its green while serving whole values.
    // The signature comes off first — a parameter named `input` is a name, not a column read.
    for (name, statement) in macros::BOUNDING {
        let (_, body) = statement
            .split_once(") AS")
            .unwrap_or_else(|| panic!("{name} is a macro definition"));
        // This says a cut is *there*, not that it is the right one: a body cutting at ten
        // thousand times the width it was asked for still passes here. The width is the leaf
        // below.
        assert_eq!(unbounded(body), nothing(), "{name}");
    }
}

/// What `tool_fields` extracts, read off the macro's own body rather than listed here: `path`
/// comes from `file_path` and `todos` answers a number, so the leaf below asks for those two by
/// name and feeds every other member a saturating value under its own key.
fn field_keys() -> Vec<String> {
    static KEY: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"'(\w+)':").expect("a literal pattern"));
    let body = macros::BOUNDING
        .iter()
        .find(|(name, _)| *name == "tool_fields")
        .map(|(_, body)| *body)
        .expect("the bounding set declares tool_fields");
    KEY.captures_iter(body)
        .map(|found| found[1].to_owned())
        .filter(|key| key != "path" && key != "todos")
        .collect()
}

#[test]
fn every_macro_the_scan_trusts_answers_one_character_past_the_width() {
    // Each bounding macro is run at three widths and asked how much it gives back. The scan's
    // trust is a bound; this is the protocol on top of it (`format::cut` marks a value that came
    // back longer than the width, so a macro that saturates *under* the width serves a silently
    // truncated value, and one that saturates over it serves a fat column). Every arm gets a
    // value far past the widest width, so each answer is a saturation rather than a whole value
    // that happened to fit.
    let connection = Connection::open_in_memory().expect("an in-memory database opens");
    macros::install(&connection).expect("the macros install");
    // The paths are invented: the shape — inside the project, outside it, no project at all —
    // is the whole point, and no recorded session carries all three at these lengths.
    let project = "/Users/planted/repos/hyphae";
    let long = "v".repeat(400);
    let inside = format!(r#"{{"file_path": "{project}/src/{long}.py"}}"#);
    let outside = format!(r#"{{"file_path": "/opt/homebrew/{long}.py"}}"#);
    let keys = field_keys();
    for chars in [10_i64, 60, 300] {
        let width = usize::try_from(chars).expect("a width fits") + 1;
        // A field read straight.
        let asked: String = connection
            .query_row(
                "SELECT tool_asked(?, 'file_path', ?)",
                duckdb::params![inside, chars],
                |row| row.get(0),
            )
            .expect("the macro answers");
        assert_eq!(asked.chars().count(), width);
        // The relativized path is the arm that spends width on a prefix it then throws away:
        // what comes back is the tail, and it is as long as any other arm's.
        let relative: String = connection
            .query_row(
                "SELECT tool_path(?, ?, ?)",
                duckdb::params![inside, project, chars],
                |row| row.get(0),
            )
            .expect("the macro answers");
        assert_eq!(relative.chars().count(), width);
        assert!(relative.starts_with("src/"), "{chars}");
        // A path the project does not contain, and a session that has no project directory,
        // both take the absolute arm — still at the width, still marked.
        let absolute: String = connection
            .query_row(
                "SELECT tool_path(?, ?, ?)",
                duckdb::params![outside, project, chars],
                |row| row.get(0),
            )
            .expect("the macro answers");
        assert_eq!(absolute.chars().count(), width);
        let projectless: String = connection
            .query_row(
                "SELECT tool_path(?, ?, ?)",
                duckdb::params![inside, Option::<&str>::None, chars],
                |row| row.get(0),
            )
            .expect("the macro answers");
        assert_eq!(projectless.chars().count(), width);
        // And the struct the tool formatters read: every string member of it is a cut of its
        // own, so one member left whole would serve a fat column through a bounded-looking
        // call. Asked with a saturating value under every name it extracts, and the keys read
        // off the macro's own body — so a member added without a cut has to fail here.
        let fat = "f".repeat(400);
        let mut fields = keys
            .iter()
            .map(|key| format!(r#""{key}": "{fat}""#))
            .collect::<Vec<_>>();
        fields.push(format!(r#""file_path": "{project}/{fat}""#));
        let asked_with = format!("{{{}}}", fields.join(", "));
        let answered: Value = connection
            .query_row(
                "SELECT tool_fields(?, ?, ?, ?)",
                duckdb::params![asked_with, project, fat, chars],
                |row| row.get(0),
            )
            .expect("the macro answers");
        let Value::Struct(members) = answered else {
            panic!("tool_fields answers a struct");
        };
        let mut wanted = keys.clone();
        wanted.push("path".to_owned());
        wanted.push("todos".to_owned());
        wanted.sort();
        let mut held = members.keys().cloned().collect::<Vec<_>>();
        held.sort();
        assert_eq!(held, wanted);
        for (member, value) in members.iter() {
            // `todos` is a count and answers a number, which is why it is asked for by name.
            if member == "todos" {
                continue;
            }
            let Value::Text(text) = value else {
                panic!("{member} is a cut string");
            };
            assert_eq!(text.chars().count(), width, "{member}");
        }
    }
}

#[test]
fn no_page_or_fragment_query_selects_a_fat_column_whole() {
    // Every query behind a page or a fragment is bounded in SQL, however large the record.
    for stem in Page::ALL
        .map(Query::stem)
        .into_iter()
        .chain(Fragment::ALL.map(Query::stem))
    {
        assert_eq!(unbounded(queries::load(stem)), nothing(), "{stem}");
    }
}

#[test]
fn a_per_value_query_returns_the_one_value_it_is_named_for() {
    // The per-value queries are the exception, and they are the exception by declaration. They
    // select a fat column whole — that is what they are for. What keeps the bound is that the
    // unit is one row of one value, so the fetch tops out at the largest value in the store
    // rather than at a page's worth of them.
    for whole in Whole::ALL {
        let stem = whole.stem();
        assert_ne!(unbounded(queries::load(stem)), nothing(), "{stem}");
    }
}

#[test]
fn every_viewer_query_is_declared_as_a_page_a_fragment_or_a_value() {
    // A viewer query lands in one of the three sets, so the scans above cannot miss it. Without
    // this, a query shipped under `view_` but named in no enum is scanned by nothing and can
    // select a fat column onto a page with the whole tier still green.
    let declared = Page::ALL
        .map(Query::stem)
        .into_iter()
        .chain(Fragment::ALL.map(Query::stem))
        .chain(Whole::ALL.map(Query::stem))
        .collect::<BTreeSet<_>>();
    let shipped = manifest::manifest()
        .keys()
        .cloned()
        .collect::<BTreeSet<_>>();
    // Every query the viewer owns is scanned by one of the leaves above...
    for name in &shipped {
        if name.starts_with(queries::VIEW_PREFIX) {
            assert!(declared.contains(name.as_str()), "{name} is in no catalog");
        }
    }
    // ...and every name declared is a query that ships, timelines shared with the runner too.
    for stem in &declared {
        assert!(shipped.contains(*stem), "{stem} names no shipped query");
    }
}

/// What follows each LIMIT in a statement, comments cut — a parameter, or a number.
fn limits(sql: &str) -> Vec<String> {
    static COMMENT: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"--[^\n]*").expect("a literal pattern"));
    static LIMIT: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"\bLIMIT\s+([^\s;]+)").expect("a literal pattern"));
    let without_comments = COMMENT.replace_all(sql, " ");
    LIMIT
        .captures_iter(&without_comments)
        .map(|found| found[1].to_owned())
        .collect()
}

#[test]
fn the_limit_scan_catches_a_literal_page_size() {
    // The scan below is worth its green: it flags the page size no caller can change. Both
    // statements are invented — every shipped query binds its limit, which is exactly why the
    // instrument needs a case of its own.
    assert_eq!(limits("SELECT * FROM raw_records LIMIT 100;"), ["100"]);
    assert_eq!(
        limits("SELECT * FROM raw_records LIMIT $page_records -- LIMIT 100"),
        ["$page_records"]
    );
}

#[test]
fn every_page_size_in_a_viewer_query_is_a_bound_parameter() {
    // No viewer query hides a page size in its text, so every bound is one a reader can see:
    // the rule rather than a list of the parameters that exist today. A query landing with a
    // literal `LIMIT 100` is a size nobody can bind down to reach its boundary in a test, and
    // nobody can bind up when a real corpus needs more.
    let shipped: &BTreeMap<String, QueryMeta> = manifest::manifest();
    for (name, meta) in shipped {
        if !name.starts_with(queries::VIEW_PREFIX) {
            continue;
        }
        for limit in limits(queries::load(name)) {
            let bound = limit
                .strip_prefix('$')
                .unwrap_or_else(|| panic!("{name} limits by a literal: {limit}"));
            assert!(
                meta.params.contains_key(bound),
                "{name} limits by ${bound}, which it does not declare"
            );
        }
    }
}

#[test]
fn every_fat_column_is_still_a_column() {
    // The scan is spelled in column names, so a rename must fail here rather than pass. Read
    // against the described corpus rather than the bare one: `description` is a column of the
    // enrichment tables, which a store no pass has touched does not have.
    let db = hyphae_testsupport::cache::enriched_store();
    let named = hyphae_testsupport::rows::all(
        &db,
        "SELECT column_name FROM duckdb_columns() WHERE schema_name = 'main'",
        &[],
    )
    .into_iter()
    .map(|row| row.str("column_name").expect("a name").to_owned())
    .collect::<BTreeSet<_>>();
    for column in FAT {
        assert!(named.contains(column), "{column} is no longer a column");
    }
}
