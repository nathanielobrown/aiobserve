//! The `/query/{name}` page and the footer that links to it.
//!
//! Every page says what produced it. This tier is about the other half of that promise: the
//! citation is a link, and following it lands on the SQL this build ships — bound the way the page
//! bound it, so a reader who doubts a number can read the statement behind it.
//!
//! The bindings are never written down here. Each leaf reads the citation line off the page and
//! checks the link against it, so the two spellings of one fact — the comment a reader copies and
//! the URL a reader clicks — cannot drift apart.

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::sync::LazyLock;

use axum::http::StatusCode;
use duckdb::{Connection, ToSql};
use regex::Regex;

use hyphae_store::{Param, Store, macros, manifest, queries};
use hyphae_testsupport::corpus;
use hyphae_testsupport::html::{ESCAPED, Markup, SENTINEL, classed, plain};
use hyphae_testsupport::landmarks::SPINE;
use hyphae_testsupport::selections;
use hyphae_testsupport::served::Served;
use hyphae_view::citation::QUERY_URL;
use hyphae_view::highlight::{self, Syntax};
use hyphae_view::urls;

/// Every page the viewer serves, one URL each, off the route map the route sweep keeps total.
///
/// Listing them by hand read as coverage and was not: a session page opens the turns level, so no
/// page in the list ever ran a query the tools level cites. What is left out cites nothing — a
/// fragment carries no footer, and the query page is where a citation goes rather than a page that
/// makes one.
fn citing() -> Vec<String> {
    selections::scenarios()
        .into_iter()
        .filter(|(route, _)| !route.starts_with("/fragment/") && !route.starts_with(QUERY_URL))
        .map(|(_, url)| url)
        .collect()
}

/// The page whose citations name a library macro, off the same map.
fn tool_page() -> String {
    selections::scenarios()["/session/{session_id}/thread/{source}/tool/{tool_call_id}"].clone()
}

/// The bindings a citation line quotes, keyed by parameter — `-- queries/x.sql a=1 b=2`.
fn bound(line: &str) -> BTreeMap<String, String> {
    line.split_whitespace()
        .skip(2)
        .map(|binding| {
            let (name, value) = binding.split_once('=').expect("a binding is `name=value`");
            (name.to_owned(), value.to_owned())
        })
        .collect()
}

/// The bindings a query page shows, keyed by parameter.
///
/// Trimmed, because the formatter stands a cell's value on a line of its own: what the page shows
/// is the value, and the indentation around it is the markup's own.
fn echoed(html: &str) -> BTreeMap<String, String> {
    static BINDING: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"data-binding="(\w+)">([^<]*)<"#).expect("a pattern"));
    BINDING
        .captures_iter(html)
        .map(|found| (found[1].to_owned(), found[2].trim().to_owned()))
        .collect()
}

/// The path and the query string of one URL, split.
fn split(href: &str) -> (&str, &str) {
    href.split_once('?').unwrap_or((href, ""))
}

/// The parameters a URL's query string carries, keyed by name, unquoted.
///
/// The reverse of `view::urls::quoted`, which is what wrote them: a `+` is a space and a `%XX` is
/// the byte it names. Written here rather than pulled in so the test tier owes no dependency the
/// viewer itself does not have.
fn asked(query: &str) -> BTreeMap<String, String> {
    if query.is_empty() {
        return BTreeMap::new();
    }
    query
        .split('&')
        .map(|pair| {
            let (name, value) = pair.split_once('=').expect("a parameter is `name=value`");
            (name.to_owned(), unquoted(value))
        })
        .collect()
}

/// One percent-encoded value read back.
fn unquoted(value: &str) -> String {
    let mut bytes = Vec::with_capacity(value.len());
    let mut walk = value.bytes();
    while let Some(byte) = walk.next() {
        match byte {
            b'+' => bytes.push(b' '),
            b'%' => {
                let pair: String = [walk.next(), walk.next()]
                    .map(|digit| digit.expect("an escape names two digits") as char)
                    .iter()
                    .collect();
                bytes.push(u8::from_str_radix(&pair, 16).expect("an escape is hexadecimal"));
            }
            _ => bytes.push(byte),
        }
    }
    String::from_utf8(bytes).expect("a decoded parameter is text")
}

#[tokio::test]
async fn every_citation_a_page_carries_links_to_the_query_it_names() {
    // Each line in the footer is a link to its own query, carrying that line's bindings.
    //
    // The footer's own count is checked against the links so a page that cites five queries and
    // shows four is a failure rather than a quieter page.
    let served = Served::corpus();
    for path in citing() {
        let (status, page) = served.page(&path).await;
        assert_eq!(status, StatusCode::OK, "{path}");
        let markup = Markup::of(&page);
        let lines = markup.fields("id", "citation");
        let names = markup.inside("id", "citation", "data-field");
        let hrefs = markup.inside("id", "citation", "href");
        assert!(!names.is_empty(), "{path}");
        // The same names either way, and each cited once — sorted, because `fields` keys by name
        // and the order the footer prints them in is the order of the reads behind the page.
        let mut cited = names.clone();
        cited.sort();
        assert_eq!(cited, lines.keys().cloned().collect::<Vec<_>>(), "{path}");
        assert_eq!(
            markup.values("data-citations"),
            vec![names.len().to_string()],
            "{path}"
        );
        for (name, href) in names.iter().zip(&hrefs) {
            let href = html_escape::decode_html_entities(href).into_owned();
            let (target, query) = split(&href);
            // The link goes to the query the line names...
            assert_eq!(target, format!("{QUERY_URL}/{name}"), "{path}");
            // ...carrying exactly the bindings the line quotes, and no others.
            assert_eq!(asked(query), bound(&lines[name]), "{path} {name}");
            // ...and it answers.
            assert_eq!(served.page(&href).await.0, StatusCode::OK, "{href}");
        }
    }
}

#[tokio::test]
async fn a_citation_quotes_every_binding_its_query_takes() {
    // A page cites what it ran — all of it, not the bindings that happen to vary by page.
    //
    // A width has a production default, so a citation leaving it out reads as a run at that
    // default. That is true until the day a page picks its own width, and it is already two
    // spellings of one habit: a reader comparing the line under one page with the line under the
    // next cannot tell a query bound differently from a query cited differently.
    //
    // Every parameter the manifest declares and not exactly them: a page may bind more than the
    // file takes — the sessions list composes its own sort, page and widths around a query that
    // declares one (`view::listing`) — and what it composed is part of what it ran.
    let served = Served::corpus();
    for path in citing() {
        let (_, page) = served.page(&path).await;
        let lines = Markup::of(&page).fields("id", "citation");
        assert!(!lines.is_empty(), "{path}");
        for (name, line) in &lines {
            let quoted = bound(line);
            for parameter in manifest::entry(name).params.keys() {
                assert!(quoted.contains_key(parameter), "{path} {name} {parameter}");
            }
        }
    }
}

#[tokio::test]
async fn the_query_page_serves_the_statement_the_citation_named() {
    // Following a citation lands on that query's file, whole, under the bindings cited.
    //
    // The session node is the page with the most reads behind it, so every one of its links is
    // followed rather than the first. The SQL is compared to the file this build ships: a page
    // that reformatted or cut a statement would be showing a reader something they cannot run.
    let served = Served::corpus();
    let (_, page) = served.page(&format!("/session/{SPINE}")).await;
    let markup = Markup::of(&page);
    let lines = markup.fields("id", "citation");
    for href in markup.inside("id", "citation", "href") {
        let href = html_escape::decode_html_entities(&href).into_owned();
        let name = split(&href)
            .0
            .strip_prefix(&format!("{QUERY_URL}/"))
            .expect("a citation links to the query page")
            .to_owned();
        let (_, shown) = served.page(&href).await;
        let served_page = Markup::of(&shown);
        assert_eq!(served_page.values("data-sql"), vec![name.clone()]);
        assert_eq!(plain(&served_page.block("sql")), queries::load(&name));
        // And the bindings are echoed as the page ran them, so the statement reads in context.
        assert_eq!(echoed(&shown), bound(&lines[&name]), "{name}");
    }
}

#[tokio::test]
async fn a_query_page_carries_the_definitions_its_statement_runs_under() {
    // A statement calling a library macro does not run alone, and the page says so.
    //
    // The footer promises a line a shell re-runs, and four viewer queries now call a macro the
    // consumer installs first (`store::macros`). A reader who pastes one of those into a bare
    // `duckdb` gets a catalog error and no way to find out why, so the page carries the setup
    // above the statement. A query that calls none carries nothing extra.
    let served = Served::corpus();
    for name in manifest::manifest().keys() {
        let (_, page) = served.page(&format!("{QUERY_URL}/{name}")).await;
        // The whole catalog, because a footer cites by name: a query file the page cannot render
        // is a dead link in every footer that ran it.
        assert!(page.contains(&format!(r#"data-sql="{name}""#)), "{name}");
        assert!(page.contains(r#"data-field="sql""#), "{name}");
        let calls = !macros::needed_by(queries::load(name)).is_empty();
        assert_eq!(page.contains(r#"data-field="macros""#), calls, "{name}");
        if calls {
            assert_eq!(
                plain(&Markup::of(&page).block("macros")),
                macros::setup(),
                "{name}"
            );
        }
    }
}

#[tokio::test]
async fn what_a_query_page_shows_runs_in_a_shell_that_installed_nothing() {
    // The promise end to end: the page a citation links to, pasted, answers.
    //
    // The connection is opened the way a reader's shell opens one — read-only over the store, with
    // no macro on it — and what runs is what the page prints, in the order it prints it, under the
    // bindings the citing page quoted. A cited value comes back as the text of the citation line,
    // which is the one place a reader copies it from.
    let served = Served::corpus();
    let (_, tool) = served.page(&tool_page()).await;
    let markup = Markup::of(&tool);
    let lines = markup.fields("id", "citation");
    let mut ran = 0;
    for href in markup.inside("id", "citation", "href") {
        let href = html_escape::decode_html_entities(&href).into_owned();
        let name = split(&href)
            .0
            .strip_prefix(&format!("{QUERY_URL}/"))
            .expect("a citation links to the query page")
            .to_owned();
        let (_, page) = served.page(&href).await;
        if !page.contains(r#"data-field="macros""#) {
            continue;
        }
        let shown = Markup::of(&page);
        let config = duckdb::Config::default()
            .access_mode(duckdb::AccessMode::ReadOnly)
            .expect("a read-only connection");
        let shell = Connection::open_with_flags(served.db(), config).expect("the store opens");
        shell
            .execute_batch(&plain(&shown.block("macros")))
            .expect("the printed setup runs");
        // Bound out of the citation line the way a reader's own `--param` would be: what is quoted
        // as `NULL` is the filter nobody named, and what reads as a number binds as one.
        let quoted = bound(&lines[&name]);
        let held: Vec<(&str, Param)> = quoted
            .iter()
            .map(|(parameter, text)| {
                let value = match text.as_str() {
                    "NULL" => Param::Absent,
                    held => held
                        .parse::<i64>()
                        .map_or_else(|_| Param::Text(held.to_owned()), Param::Int),
                };
                (parameter.as_str(), value)
            })
            .collect();
        let binding: Vec<(&str, &dyn ToSql)> = held
            .iter()
            .map(|(parameter, value)| (*parameter, value as &dyn ToSql))
            .collect();
        let mut statement = shell
            .prepare(&plain(&shown.block("sql")))
            .expect("the printed statement prepares");
        // Drained, because `query` hands back a lazy cursor: a statement whose rows nobody pulls
        // is a statement this leaf never ran.
        let mut answered = statement
            .query(binding.as_slice())
            .expect("the printed statement runs");
        while answered.next().expect("the store answers").is_some() {}
        ran += 1;
    }
    // The tool page is on the list because it cites queries that need the setup — if it stops
    // doing that this leaf proves nothing, and says so rather than passing empty.
    assert!(
        ran > 0,
        "no citation on the tool page names a macro any more"
    );
}

#[tokio::test]
async fn a_query_asked_for_with_no_bindings_still_serves() {
    // The page is a reader's entry point as much as a link target — the URL alone is enough.
    let served = Served::corpus();
    let (status, page) = served.page(&format!("{QUERY_URL}/view_sessions")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        plain(&Markup::of(&page).block("sql")),
        queries::load("view_sessions")
    );
    assert_eq!(echoed(&page), BTreeMap::new());
}

#[tokio::test]
async fn only_a_name_the_library_declares_is_served() {
    // A name outside the manifest is a 404, and the response repeats nothing back.
    let served = Served::corpus();
    for name in [
        // A name no build ships...
        "nope",
        // ...the file name rather than the query name, which is the near miss a reader makes...
        "view_sessions.sql",
        // ...and a name shaped like a path out of the query directory. It is a miss before
        // anything is read, which is what keeps the route from being a file server.
        "..%2f..%2fpyproject",
        "..%2f..%2f.env",
    ] {
        let (status, page) = served.page(&format!("{QUERY_URL}/{name}")).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{name}");
        assert!(!page.contains(name), "{name}");
    }
}

#[tokio::test]
async fn the_sheet_paints_only_classes_the_highlighter_can_emit() {
    // Every class `static/pygments.css` styles is one the lexers actually write.
    //
    // A hand-written sheet's failure mode is a rule nobody can see fail — a class from another
    // language, or a typo. Each syntax is swept over real material of its own: the JSON is every
    // value the viewer marks up, out of the recorded corpus, and the other four are files this
    // repo ships — its queries, its docs, its Python modules, and the commands it runs on itself.
    // The docs are swept twice, the second time behind the line numbers a `Read` result carries,
    // so what a reader sees of a file is what the sweep saw.
    //
    // **This is the gap, stated.** `highlight::lit` escapes and re-lays out, and paints nothing
    // (`highlight.rs` says why), so the sweep emits no class at all and the sheet is painting a
    // vocabulary this build cannot write. The assertion below is what is true today; when the
    // highlighter lands it becomes `painted ⊆ emitted` over the same material, and this leaf is
    // the one that has to fail first.
    let served = Served::corpus();
    let (status, sheet) = served.page("/static/pygments.css").await;
    assert_eq!(status, StatusCode::OK);
    // Selectors only: a comment names files, and `.py` in one is not a class anyone styles.
    let commented = Regex::new(r"(?s)/\*.*?\*/").expect("a pattern");
    let class = Regex::new(r"\.([a-z]{1,3}\d?)\b").expect("a pattern");
    let stripped = commented.replace_all(&sheet, "");
    // Pygments' classes are one to three letters, which leaves this viewer's own class names
    // (`code`, `plain`, `lineno`) out of the comparison.
    let painted: BTreeSet<String> = stripped
        .split('{')
        .flat_map(|rule| class.captures_iter(rule).map(|found| found[1].to_owned()))
        .collect();
    assert!(!painted.is_empty(), "the sheet paints no Pygments class");
    let mut emitted: BTreeSet<String> = BTreeSet::new();
    // Counted per syntax, because the claim below is an absence: a sweep that read no Python is a
    // sweep that proves nothing about Python, and the emptiness would look the same either way.
    let mut swept: BTreeMap<&str, usize> = BTreeMap::new();
    let mut sweep = |text: &str, syntax: Syntax| {
        emitted.extend(classed(&highlight::lit(Some(text), syntax).html));
        *swept.entry(syntax.word()).or_default() += 1;
    };
    for name in manifest::manifest().keys() {
        sweep(queries::load(name), Syntax::Sql);
    }
    let store = Store::open_read_only(&served.db()).expect("the store opens read only");
    for row in store
        .fetch(
            "SELECT input AS value FROM live_tool_calls \
             UNION ALL SELECT result FROM live_tool_calls \
             UNION ALL SELECT raw FROM raw_records",
            &[],
        )
        .expect("the store answers")
    {
        if let Some(value) = row.opt_str("value").expect("a value column") {
            sweep(value, Syntax::Json);
        }
    }
    for path in shipped("docs", "md") {
        let prose = std::fs::read_to_string(&path).expect("a doc reads");
        sweep(&prose, Syntax::Markdown);
        sweep(&numbered(&prose), Syntax::Markdown);
    }
    for path in shipped("src", "py") {
        sweep(
            &std::fs::read_to_string(&path).expect("a module reads"),
            Syntax::Python,
        );
    }
    for command in commands() {
        sweep(&command, Syntax::Bash);
    }
    // Every syntax the viewer names read real material of its own, and none of it was painted.
    let read: BTreeSet<&str> = swept
        .iter()
        .filter(|(_, count)| **count > 0)
        .map(|(word, _)| *word)
        .collect();
    assert_eq!(
        read,
        BTreeSet::from(["bash", "json", "markdown", "python", "sql"])
    );
    assert_eq!(emitted, BTreeSet::new(), "{swept:?}");
}

/// One file behind the line-number gutter the `Read` tool writes down its left.
fn numbered(text: &str) -> String {
    text.split_inclusive('\n')
        .enumerate()
        .map(|(at, line)| format!("{}\t{line}", at + 1))
        .collect()
}

/// Every file of one suffix this repo ships under `directory`, sorted.
fn shipped(directory: &str, suffix: &str) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut walk = vec![corpus::repo().join(directory)];
    while let Some(at) = walk.pop() {
        for entry in std::fs::read_dir(&at).expect("the directory reads") {
            let path = entry.expect("an entry").path();
            if path.is_dir() {
                walk.push(path);
            } else if path.extension().is_some_and(|found| found == suffix) {
                found.push(path);
            }
        }
    }
    found.sort();
    found
}

/// Every shell script this repo runs on itself.
///
/// The hooks only. Python sweeps `mise.toml`'s task lines too, and reaching them from here means a
/// TOML parser this workspace does not otherwise carry — for material of the same syntax the hooks
/// already supply. Worth adding when the highlighter lands and the sweep starts deciding
/// something; not for a set this build proves empty.
fn commands() -> Vec<String> {
    shipped(".claude/hooks", "sh")
        .iter()
        .map(|path| std::fs::read_to_string(path).expect("a hook reads"))
        .collect()
}

#[tokio::test]
async fn a_citations_bindings_are_printed_back_inert() {
    // The one place a request's own text reaches rendering: the page prints what the citation
    // bound without binding it to anything. So the sentinel goes in the query string.
    let served = Served::corpus();
    let asked = format!(
        "{QUERY_URL}/view_sessions?session_id={}",
        urls::quoted(SENTINEL)
    );
    let (status, page) = served.page(&asked).await;
    assert_eq!(status, StatusCode::OK);
    assert!(!page.contains(SENTINEL), "raw sentinel in {asked}");
    assert!(page.contains(ESCAPED), "escaped sentinel in {asked}");
    // And a citation that bound nothing says so rather than printing an empty list.
    let (_, bare) = served.page(&format!("{QUERY_URL}/view_sessions")).await;
    assert!(bare.contains("Cited with no bindings."));
}
