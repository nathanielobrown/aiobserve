//! What a value is marked up as: the syntax the record names, and the fallback behind it.
//!
//! A `Bash` call's command is shell, a `Read` result is the file it read, and anything else a
//! tool answered is tried as JSON. Which of the three a value takes is the pane's decision and
//! these leaves read it.
//!
//! **The painting is not ported.** Python marks a value up with Pygments, whose classes
//! `static/pygments.css` paints; nothing in Rust writes those classes (`highlight.rs` says why),
//! so every value prints as the characters the store holds and no `<pre>` wears a syntax. Each
//! arm below that Python reads through a lexer's spans is read here through the one thing the
//! choice still changes — JSON is re-laid out for reading and nothing else is — and the arms that
//! change nothing at all are named as the gap they are. `test_highlight.py` owns closing it.

use hyphae_testsupport::html::{Markup, classed, plain};
use hyphae_testsupport::selections::call_to;
use hyphae_testsupport::served::Served;

use axum::http::StatusCode;
use hyphae_store::Store;

/// A shell command with something for a lexer to find in it: a builtin, an operator, a quoted
/// string and a pipe. Planted rather than recorded — redaction flattened every command the fixture
/// corpus holds to `[redacted]` — and real in the sense that matters here: it is a line this
/// repository's own tasks run.
const COMMAND: &str = "cd /tmp && rg -n 'x' *.py | head -3";

/// And what a `Read` of a markdown file returns: the source, behind the line-number gutter Claude
/// Code adds. Planted for the same reason — a recorded file path reads `[redacted]`.
const READ: &str = "1\t# Title\n2\t\n3\t- an item\n";

/// What an `Edit` of a python file returns instead: a sentence about the file, which is the shape
/// the guards below exist to keep apart from the file itself.
const EDITED: &str = "The file /tmp/notes.py has been updated.";

/// And a command argument passed to a tool that runs no shell, for the same guards read the other
/// way round.
const NOT_RUN: &str = "ls -la";

#[tokio::test]
async fn a_bash_call_reads_the_command_it_ran_as_a_shell_reads_it() {
    // A `Bash` call previews the command itself, apart from the arguments it arrived in.
    //
    // The command is in the input JSON, escaped onto one line among the tool's other arguments,
    // and a reader who opened the call to read the command should not be reading it out of a JSON
    // string. So it is a value of the pane like the input and the result are, with the rest of a
    // long command behind its own route.
    let corpus = Served::corpus();
    let (session_id, source, tool_id) = call_to(&corpus.db(), "Bash");
    let (read_session, read_source, read_id) = call_to(&corpus.db(), "Read");
    let ran = {
        let ran =
            serde_json::json!({ "description": "look for x", "command": COMMAND }).to_string();
        // ...and the same argument on a `Read`, which runs nothing. Real in shape: 86 recorded
        // calls to tools other than `Bash` were passed a `command` of their own (the canonical
        // store, read 2026-08-20), 2 of them to `Read`.
        let not_run =
            serde_json::json!({ "file_path": "[redacted]", "command": NOT_RUN }).to_string();
        let (session, source, tool) = (session_id.clone(), source.clone(), tool_id.clone());
        let (other_session, other_source, other) =
            (read_session.clone(), read_source.clone(), read_id.clone());
        Served::planted(move |store: &Store| {
            let connection = store.connection();
            let sql = "UPDATE tool_calls SET input = ? \
                       WHERE session_id = ? AND source = ? AND id = ?";
            connection
                .execute(
                    sql,
                    duckdb::params![
                        ran.as_str(),
                        session.as_str(),
                        source.as_str(),
                        tool.as_str()
                    ],
                )
                .expect("the command is plantable");
            connection
                .execute(
                    sql,
                    duckdb::params![
                        not_run.as_str(),
                        other_session.as_str(),
                        other_source.as_str(),
                        other.as_str()
                    ],
                )
                .expect("the argument is plantable");
        })
    };
    let at = format!("/session/{session_id}/thread/{source}/tool/{tool_id}");
    let (status, page) = ran.page(&at).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // Every character the store holds is still there to read back...
    assert_eq!(plain(&markup.block("command")), COMMAND);
    // ...and nothing paints it, because nothing in this viewer paints anything. Python asks a
    // shell lexer here and reads `cd` back as a builtin and `&&` as an operator; the ask is
    // ported (`Syntax::Bash` reaches `highlight::lit`) and the answer is not, so what the page
    // serves is the command as stored. This absence is the gap, held still so that closing it
    // reddens here rather than passing unnoticed.
    assert!(!markup.block("command").contains("<span"));
    assert_eq!(markup.walled("command"), "");
    // The whole of it has a route of its own, serving the same characters — the syntax is spelled
    // once for the preview and once for the fetch, so the fetch is read for the mark too. A route
    // that fell back to JSON would serve the command as a JSON string.
    let (status, served) = ran.page(&format!("/fragment/command{at}")).await;
    assert_eq!(status, StatusCode::OK);
    let served = Markup::of(&served);
    assert_eq!(plain(&served.block("value")), COMMAND);
    assert_eq!(served.values("data-detail"), ["command"]);
    // And the input is still on the page as the record: the command is a reading of it.
    let input: serde_json::Value =
        serde_json::from_str(&plain(&markup.block("input"))).expect("the input is JSON");
    assert_eq!(input["command"], COMMAND);
    // A call to a tool that runs no command has none to show, though its arguments carry the word:
    // the arm is the tool's name. A page that marked that argument up as shell would be saying a
    // `Read` ran it.
    let read_at = format!("/session/{read_session}/thread/{read_source}/tool/{read_id}");
    let (status, read) = ran.page(&read_at).await;
    assert_eq!(status, StatusCode::OK);
    let read = Markup::of(&read);
    assert!(!read.values("data-detail").iter().any(|at| at == "command"));
    // The argument is still on the page inside the input it was passed in — as the record, not as
    // a shell. And the route the pane would have linked to has no such value to serve: the row is
    // there and the column under it is null, which is not a value of nothing but the absence of
    // one. A 200 would make the pane's missing link a bug rather than the only honest thing the
    // page can do.
    assert!(plain(&read.block("input")).contains(NOT_RUN));
    let missing = ran.get(&format!("/fragment/command{read_at}")).await;
    assert_eq!(missing.status(), StatusCode::NOT_FOUND);
}

/// One `Read` of `file`, returning `result`, planted over a recorded `Bash` call — and the URL of
/// its page. Written once because the leaf below reads four files through it.
fn a_read_of(file: &'static str, result: &'static str, tool: &'static str) -> (Served, String) {
    let corpus = Served::corpus();
    let (session_id, source, tool_id) = call_to(&corpus.db(), "Bash");
    let served = {
        let input = serde_json::json!({ "file_path": file }).to_string();
        let (session, thread, id) = (session_id.clone(), source.clone(), tool_id.clone());
        Served::planted(move |store: &Store| {
            store
                .connection()
                .execute(
                    "UPDATE tool_calls SET name = ?, input = ?, result = ? \
                     WHERE session_id = ? AND source = ? AND id = ?",
                    duckdb::params![
                        tool,
                        input.as_str(),
                        result,
                        session.as_str(),
                        thread.as_str(),
                        id.as_str()
                    ],
                )
                .expect("the read is plantable");
        })
    };
    (
        served,
        format!("/session/{session_id}/thread/{source}/tool/{tool_id}"),
    )
}

#[tokio::test]
async fn a_read_of_a_markdown_file_shows_the_source_and_does_not_render_it() {
    // A `Read` result is evidence, so a markdown file is shown as the source it is rather than
    // rendered. Rendering would turn the `#` the file holds into a heading and lose the characters
    // the agent was actually shown. What the file was is read off the path it was read from, which
    // is the only thing in the record that says so.
    let (read, at) = a_read_of("/tmp/notes.md", READ, "Read");
    let (status, page) = read.page(&at).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // The source, whole, and every character of it — including the line numbers Claude Code
    // prefixes each line with, which Python keeps out of the lexer's way and this viewer never
    // hands to one.
    assert_eq!(plain(&markup.block("result")), READ);
    // ...nowhere in the preview, and nowhere else on the page either: the pane heads itself with
    // an `<h1>` and this file's `#` must not have made a second one.
    assert!(!markup.block("result").contains("<h1>"));
    assert_eq!(page.matches("<h1>").count(), 1);
    // The whole fetch reads the same way, off the same file name.
    let (_, served) = read.page(&format!("/fragment/result{at}")).await;
    assert_eq!(plain(&Markup::of(&served).block("value")), READ);
}

/// A tool's answer written on one line, which JSON re-laid out for reading is not. The one thing
/// the syntax a record names still changes in this prototype: a value read as JSON comes back
/// indented, and a value read as anything else comes back as the store holds it.
const ONE_LINE: &str = r#"{"ok": true, "rows": [1, 2, 3]}"#;

#[tokio::test]
async fn what_a_result_is_read_as_is_the_file_the_record_names() {
    // The suffix on the path a `Read` was given picks the syntax, and everything else falls back
    // to JSON. Python reads that choice back through a lexer's spans; here it is read through the
    // re-layout, which is the half of `highlight::lit` that did port: the same one line of JSON
    // comes back indented under a name this viewer has no syntax for and untouched under one it
    // has, because a markdown file is not JSON however well it parses as some.
    let (unknown, at) = a_read_of("/tmp/notes.bin", ONE_LINE, "Read");
    let (status, page) = unknown.page(&at).await;
    assert_eq!(status, StatusCode::OK);
    let fallen_back = plain(&Markup::of(&page).block("result"));
    assert!(fallen_back.contains('\n'), "{fallen_back}");
    let (named, at) = a_read_of("/tmp/notes.md", ONE_LINE, "Read");
    let (_, page) = named.page(&at).await;
    assert_eq!(plain(&Markup::of(&page).block("result")), ONE_LINE);
    // And a tool that names a file without returning one falls back too. `Edit` and `Write` name a
    // file whose suffix this viewer has a lexer for in 30,491 recorded calls (the canonical store,
    // read 2026-08-20), and what they return is a sentence about the file — a page that read it as
    // that file would be claiming it is the file.
    let (edited, at) = a_read_of("/tmp/notes.py", EDITED, "Edit");
    let (_, page) = edited.page(&at).await;
    let markup = Markup::of(&page);
    assert_eq!(markup.block("result"), EDITED);
    assert!(!markup.block("result").contains("<span"));
    // The rule is spelled once for the preview and once for the whole fetch, so both are read
    // here: the second query answers off the same file name as the first.
    let (_, served) = edited.page(&format!("/fragment/result{at}")).await;
    assert_eq!(Markup::of(&served).block("value"), EDITED);
}

/// And what a tool answering in words returns, with a brace in it so that the arm below is a page
/// falling back rather than a page finding nothing to parse.
const PLAIN_RESULT: &str = "Found 3 matches in {src}/hyphae, none in the viewer.";

#[tokio::test]
async fn a_result_no_file_names_is_json_where_it_parses_and_the_stored_characters_where_it_does_not()
 {
    // A tool's answer is read as JSON when it is JSON, and printed as stored when it is not.
    //
    // The third arm of one rule. A result whose file the record names keeps that file's syntax
    // (the leaf above); everything else is tried as JSON, because that is what a tool that does
    // not answer in prose answers in. The fallback is what makes trying safe: a value that does
    // not parse comes back whole and unmarked rather than lexed as broken JSON, so nothing a
    // reader opened the call for is lost to a guess about what it was.
    //
    // Planted on a recorded `Bash` call, which names no file: what a `Bash` call ran is beside the
    // result on the same page, so the plant also holds the two apart.
    let corpus = Served::corpus();
    let (session_id, source, tool_id) = call_to(&corpus.db(), "Bash");
    let at = format!("/session/{session_id}/thread/{source}/tool/{tool_id}");
    let fetch = format!("/fragment/result{at}");
    let answering = |result: &'static str| {
        let (session, thread, id) = (session_id.clone(), source.clone(), tool_id.clone());
        Served::planted(move |store: &Store| {
            store
                .connection()
                .execute(
                    "UPDATE tool_calls SET result = ? \
                     WHERE session_id = ? AND source = ? AND id = ?",
                    duckdb::params![result, session.as_str(), thread.as_str(), id.as_str()],
                )
                .expect("the result is plantable");
        })
    };
    let answered = answering(ONE_LINE);
    let (status, page) = answered.page(&at).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // Read as the JSON it is, and indented for reading: the store holds one line and a reader
    // opening a result wants the shape of it. Python paints it too, and says so in the `<pre>`'s
    // class; here the class is empty because nothing was painted.
    let shown = plain(&markup.block("result"));
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&shown).expect("the result is JSON"),
        serde_json::from_str::<serde_json::Value>(ONE_LINE).expect("the plant is JSON"),
    );
    assert!(shown.contains('\n'));
    assert_eq!(markup.walled("result"), "");
    // And the fetch that replaces the preview reads the same way, off the same rule.
    let (_, served) = answered.page(&fetch).await;
    let served = Markup::of(&served);
    assert_eq!(served.walled("value"), markup.walled("result"));
    assert_eq!(
        classed(&served.block("value")),
        classed(&markup.block("result"))
    );
    // A result that is not JSON comes back as the store holds it: no class, because nothing on the
    // page claims to know what this is — and every character of it, which is what proves the parse
    // that failed swallowed nothing.
    let said = answering(PLAIN_RESULT);
    let (_, page) = said.page(&at).await;
    let markup = Markup::of(&page);
    assert_eq!(markup.walled("result"), "");
    assert_eq!(markup.block("result"), PLAIN_RESULT);
    let (_, served) = said.page(&fetch).await;
    assert_eq!(Markup::of(&served).block("value"), PLAIN_RESULT);
}
