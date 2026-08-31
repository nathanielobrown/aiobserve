//! What one row of a children log says, cell by cell.
//!
//! A row is the whole of a child a reader gets without opening it, so what each cell prints is the
//! subject here: what a tool was asked, what a call said and which tools it went on to call, and
//! how much of each the column it sits in will hold. The name itself — the one string every
//! surface printing this node has to agree on — is `node_titles.rs`.

use std::collections::BTreeMap;

use duckdb::params;
use regex::Regex;
use serde_json::json;

use hyphae_store::{Param, Store, queries};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{MAIN, SEARCH_TOOL, SPINE};
use hyphae_testsupport::rows;
use hyphae_testsupport::selections;
use hyphae_testsupport::served::Served;
use hyphae_view::format::ELLIPSIS;

/// The project a planted session is said to have run in, so a path can sit inside it or outside.
const PROJECT: &str = "/Users/planted/repos/hyphae";

/// The api call of the corpus that made the most tool calls, and the first `wanted` of them.
///
/// A row is dressed by writing over a recorded call, so the leaves need one wide enough to dress:
/// the count comes back with it, and the caller asserts on it rather than trusting the corpus.
fn a_call_and_its_tools(
    db: &std::path::Path,
    wanted: usize,
) -> (String, String, String, i64, Vec<String>) {
    let row = rows::one(
        db,
        "SELECT session_id, source, api_call_id, count(*) AS held FROM live_tool_calls \
         GROUP BY 1, 2, 3 ORDER BY 4 DESC, 1, 2, 3 LIMIT 1",
        &[],
    );
    let session_id = row.str("session_id").expect("a session id").to_owned();
    let source = row.str("source").expect("a thread").to_owned();
    let call_id = row.str("api_call_id").expect("a call id").to_owned();
    let held = row.i64("held").expect("a count");
    let store = Store::open_read_only(db).expect("the store opens read only");
    let tools = store
        .fetch(
            "SELECT id FROM live_tool_calls WHERE session_id = $session AND source = $thread \
             AND api_call_id = $call ORDER BY \"index\"",
            &[
                ("session", Param::from(session_id.as_str())),
                ("thread", Param::from(source.as_str())),
                ("call", Param::from(call_id.as_str())),
            ],
        )
        .expect("the store answers")
        .iter()
        .take(wanted)
        .map(|row| row.str("id").expect("a tool id").to_owned())
        .collect();
    (session_id, source, call_id, held, tools)
}

/// One statement per dressed tool call, run over a planted store.
fn dressed(named: Vec<(String, String, String)>) -> impl Fn(&Store) {
    move |store: &Store| {
        for (tool_id, name, sent) in &named {
            store
                .connection()
                .execute(
                    "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
                    params![name, sent, tool_id],
                )
                .expect("the call takes the planted input");
        }
    }
}

/// The project a session ran in, written over or cleared.
fn housed(session_id: String, project: Option<&'static str>) -> impl Fn(&Store) {
    move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE sessions SET project_dir = ? WHERE id = ?",
                params![project, session_id],
            )
            .expect("the session takes the planted project");
    }
}

#[tokio::test]
async fn a_tool_row_says_what_the_tool_was_asked() {
    // A tool call is titled by the input that identifies it, not by its size.
    //
    // What identifies one differs by tool, so the title reads the field rather than the name. A
    // tool the viewer knows reads its own field under its own glyph (`view/formatters.rs`): a file
    // tool is its path, and a path inside the session's own project reads relative to it — the
    // repository is the frame the reader is holding, and an absolute path spends the width of the
    // column saying where the machine keeps it. A command is what ran, with what it was for under
    // it — unless the title says that already, and then nothing reads under it. A tool the
    // registry does not name falls to the shape rule the store applies to any input at all: a
    // `file_path`, else a `description`, else the head of the input as stored.
    //
    // Planted: the fixture corpus is redacted, so no recorded tool call carries a path, a
    // description or a command — only the shape around them survives redaction.
    let corpus = Served::corpus();
    let (session_id, source, call_id, held, tools) = a_call_and_its_tools(&corpus.db(), 4);
    assert!(
        held >= 4,
        "the plant needs an api call with four tool calls"
    );
    let asked: Vec<(String, String, String)> = vec![
        // A file the session's own project holds, and one it does not.
        (
            tools[0].clone(),
            "Read".to_owned(),
            json!({ "file_path": format!("{PROJECT}/src/hyphae/view/app.py") }).to_string(),
        ),
        (
            tools[1].clone(),
            "Read".to_owned(),
            json!({ "file_path": "/etc/hosts" }).to_string(),
        ),
        // A command, which carries both what it was for and what it ran.
        (
            tools[2].clone(),
            "Bash".to_owned(),
            json!({ "command": "git status --short", "description": "Read the tree" }).to_string(),
        ),
        // And a tool the registry does not name, whose input carries none of the fields the shape
        // rule reads either — so it falls back to the input as stored, with the tool's own name
        // still leading the row.
        (
            tools[3].clone(),
            "StructuredOutput".to_owned(),
            json!({ "schema": "Findings", "strict": true }).to_string(),
        ),
    ];
    let asked_first = asked[0].2.clone();
    let housing = housed(session_id.clone(), Some(PROJECT));
    let dressing = dressed(asked);
    let served = Served::planted(move |store: &Store| {
        housing(store);
        dressing(store);
    });
    let (_, page) = served
        .page(&format!(
            "/session/{session_id}/thread/{source}/call/{call_id}"
        ))
        .await;
    let markup = Markup::of(&page);
    let read = |tool_id: &str| markup.fields("data-child", &format!("tool:{tool_id}"));
    let rows: Vec<BTreeMap<String, String>> = tools.iter().map(|id| read(id)).collect();
    // The project's own file reads from the project root, and the one outside it in full.
    assert_eq!(rows[0]["title"], "📖 src/hyphae/view/app.py");
    assert_eq!(rows[1]["title"], "📖 /etc/hosts");
    // The command reads as what ran, with what it was for under it.
    assert_eq!(rows[2]["title"], "⚡ git status --short");
    assert_eq!(rows[2]["about"], "Read the tree");
    // And the unnamed tool shows the input as stored, under no glyph: the registry has no rule
    // for it, so the row keeps the tool's name in the column beside the title.
    assert_eq!(rows[3]["title"], r#"{"schema":"Findings","strict":true}"#);
    assert_eq!(rows[3]["name"], "StructuredOutput");
    assert!(!rows[3].contains_key("about"));

    // A directory whose name merely starts with the project's reads absolute: `hyphae2` is not
    // inside `hyphae`, and without the separator the guard carries it would relativise to
    // `/src/x.py` — a path that looks like it sits at the repository root. Real: 2,053 of the
    // 67,252 `file_path` rows in the recorded store share the project's prefix from outside it.
    let sibling = format!("{PROJECT}2/src/x.py");
    let beside_them: Vec<(String, String, String)> = vec![
        (
            tools[0].clone(),
            "Read".to_owned(),
            json!({ "file_path": sibling.clone() }).to_string(),
        ),
        // A `Bash` call that also names a file. The tool's own rule wins over the shape rule the
        // store would have applied — a `Bash` call is what it ran, whatever else the input carries
        // — and what it was for reads underneath.
        (
            tools[1].clone(),
            "Bash".to_owned(),
            json!({
                "file_path": format!("{PROJECT}/notes.md"),
                "description": "Read the notes",
                "command": "cat notes.md",
            })
            .to_string(),
        ),
        // And a command with nothing saying what it was for, which heads the same way and prints
        // no second line rather than a dash under it.
        (
            tools[2].clone(),
            "Bash".to_owned(),
            json!({ "command": "ls" }).to_string(),
        ),
        // An `Agent` call, whose title is the type the run was spawned as and then the brief —
        // which is the same `description` a second line would print.
        (
            tools[3].clone(),
            "Agent".to_owned(),
            json!({ "subagent_type": "implementer", "description": "Close the audit nits" })
                .to_string(),
        ),
    ];
    let housing = housed(session_id.clone(), Some(PROJECT));
    let dressing = dressed(beside_them);
    let guarded = Served::planted(move |store: &Store| {
        housing(store);
        dressing(store);
    });
    let (_, edges) = guarded
        .page(&format!(
            "/session/{session_id}/thread/{source}/call/{call_id}"
        ))
        .await;
    let markup = Markup::of(&edges);
    let beside: Vec<BTreeMap<String, String>> = tools
        .iter()
        .map(|id| markup.fields("data-child", &format!("tool:{id}")))
        .collect();
    assert_eq!(beside[0]["title"], format!("📖 {sibling}"));
    assert_eq!(beside[1]["title"], "⚡ cat notes.md");
    assert_eq!(beside[1]["about"], "Read the notes");
    assert_eq!(beside[2]["title"], "⚡ ls");
    assert!(!beside[2].contains_key("about"));
    // And the row whose title already says what the call was for prints nothing under it: the
    // brief is inside the title an `Agent` row heads with, so a second line would be the same
    // sentence twice on one row. The `Bash` rows above are the other side of the rule — there the
    // description says something the command does not, which is why the line exists at all.
    assert_eq!(beside[3]["title"], "👉 [implementer] Close the audit nits");
    assert!(!beside[3].contains_key("about"));

    // A session whose project the store never recorded has no frame to read a path against, so
    // the path reads absolute rather than against nothing.
    let housing = housed(session_id.clone(), None);
    let dressing = dressed(vec![(
        tools[0].clone(),
        "Read".to_owned(),
        asked_first.clone(),
    )]);
    let homeless = Served::planted(move |store: &Store| {
        housing(store);
        dressing(store);
    });
    let (_, loose) = homeless
        .page(&format!(
            "/session/{session_id}/thread/{source}/call/{call_id}"
        ))
        .await;
    assert_eq!(
        Markup::of(&loose).field("data-child", &format!("tool:{}", tools[0]), "title"),
        format!("📖 {PROJECT}/src/hyphae/view/app.py")
    );
}

#[tokio::test]
async fn a_call_row_says_what_the_call_said_and_which_tools_it_called() {
    // A turn's calls log carries each call's own words and the tools that call went on to make.
    //
    // A page of api calls used to be a page of model names and counts: every row said the same
    // model, and the only way to learn what a call did was to open it. The row now carries the
    // head of what the call itself said — its own text, not a description of it — and the titles
    // of the tool calls it made, in the order it made them, under the count that says how many.
    // The titles are the shared derivation the tools log reads, so a call's row and the log inside
    // it name the same tool the same way.
    //
    // The call is picked from a turn whose other calls made tool calls too, and its two tools are
    // dressed in reverse order of their index. A row that named the turn's tools rather than the
    // call's, or named the call's in the order the store happens to hold them, prints a different
    // string here.
    //
    // Planted: redaction leaves a recorded call's text trimmed and no recorded tool call with a
    // path or a description in its input.
    let corpus = Served::corpus();
    let db = corpus.db();
    let row = rows::one(
        &db,
        "SELECT c.session_id, c.source, c.turn_id, c.id, count(*) AS held FROM live_api_calls c \
         JOIN live_tool_calls t \
           ON t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id \
         JOIN live_turns u ON u.session_id = c.session_id AND u.source = c.source \
          AND u.id = c.turn_id \
         WHERE EXISTS (SELECT 1 FROM live_api_calls o JOIN live_tool_calls ot \
           ON ot.session_id = o.session_id AND ot.source = o.source AND ot.api_call_id = o.id \
          WHERE o.session_id = c.session_id AND o.source = c.source AND o.turn_id = c.turn_id \
           AND o.id <> c.id) \
         GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC, 1, 2, 3, 4 LIMIT 1",
        &[],
    );
    let session_id = row.str("session_id").expect("a session id").to_owned();
    let source = row.str("source").expect("a thread").to_owned();
    let turn_id = row.str("turn_id").expect("a turn id").to_owned();
    let call_id = row.str("id").expect("a call id").to_owned();
    let held = row.i64("held").expect("a count");
    assert_eq!(held, 2, "the plant names both of the call's tools");
    let store = Store::open_read_only(&db).expect("the store opens read only");
    let tools: Vec<String> = store
        .fetch(
            "SELECT id FROM live_tool_calls WHERE session_id = $session AND source = $thread \
             AND api_call_id = $call ORDER BY \"index\" LIMIT 2",
            &[
                ("session", Param::from(session_id.as_str())),
                ("thread", Param::from(source.as_str())),
                ("call", Param::from(call_id.as_str())),
            ],
        )
        .expect("the store answers")
        .iter()
        .map(|row| row.str("id").expect("a tool id").to_owned())
        .collect();
    let said = "I will read the app and then check what the NavTree is standing on.";
    let housing = housed(session_id.clone(), Some(PROJECT));
    let dressing = dressed(vec![
        // A file inside the session's own project, and a command that says what it was for: the
        // two derivations the tools log's own rows show.
        (
            tools[0].clone(),
            "Read".to_owned(),
            json!({ "file_path": format!("{PROJECT}/src/hyphae/view/app.py") }).to_string(),
        ),
        (
            tools[1].clone(),
            "Bash".to_owned(),
            json!({ "command": "git status", "description": "Read the tree" }).to_string(),
        ),
    ]);
    let (spoke, thread, turn, call, first) = (
        session_id.clone(),
        source.clone(),
        turn_id.clone(),
        call_id.clone(),
        tools[0].clone(),
    );
    let served = Served::planted(move |store: &Store| {
        housing(store);
        store
            .connection()
            .execute(
                "UPDATE api_calls SET text = ? WHERE id = ?",
                params![said, call],
            )
            .expect("the call takes the planted text");
        // Every tool the turn's *other* calls made, named so that a row reaching past its own
        // call would print the word.
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET name = 'Bash', input = ? \
                 WHERE session_id = ? AND source = ? AND api_call_id <> ? AND api_call_id IN \
                 (SELECT id FROM api_calls WHERE session_id = ? AND source = ? AND turn_id = ?)",
                params![
                    json!({ "command": "git log", "description": "Another call asked" })
                        .to_string(),
                    spoke,
                    thread,
                    call,
                    spoke,
                    thread,
                    turn
                ],
            )
            .expect("the sibling calls take the planted input");
        dressing(store);
        // And the first of them is moved to the end of the call's order, so that the order the
        // store holds the two rows in and the order the call made them in disagree. A row that
        // printed the tools in the order they came back names them the other way round.
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET \"index\" = 90000 WHERE id = ?",
                params![first],
            )
            .expect("the call takes the planted order");
    });
    let (_, page) = served
        .page(&format!(
            "/session/{session_id}/thread/{source}/turn/{turn_id}"
        ))
        .await;
    let row = Markup::of(&page).fields("data-child", &format!("call:{call_id}"));
    // What the call said stands in the row beside the model that said it...
    assert_eq!(row["text"], said);
    // ...and the tools it called are named, in the order it called them and no others: what the
    // re-indexed call asked for last comes last, under the count of them. Each is named by its own
    // tool's rule, glyph and all, so the words here and the words on the tool's own row are one
    // derivation (`view/formatters.rs`) — the `Bash` row says what ran rather than what the caller
    // said it was for.
    assert_eq!(
        row["tool_titles"],
        "⚡ git status, 📖 src/hyphae/view/app.py"
    );
    assert_eq!(row["tool_calls"], held.to_string());

    // The same column over the recording rather than a plant, because a plant can only show what
    // this test dressed: `SPINE` holds one api call that asked for two different tools at once,
    // and each is named under its own tool's glyph rather than the first one's
    // (`tests/fixtures/spine/README.md`).
    let recorded = rows::one(
        &db,
        "SELECT c.turn_id, c.id FROM live_api_calls c JOIN live_tool_calls t \
          ON t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id \
         WHERE t.id = $tool",
        &[("tool", Param::from(SEARCH_TOOL))],
    );
    let recorded_turn = recorded.str("turn_id").expect("a turn id").to_owned();
    let recorded_call = recorded.str("id").expect("a call id").to_owned();
    let (_, served) = corpus
        .page(&format!(
            "/session/{SPINE}/thread/{MAIN}/turn/{recorded_turn}"
        ))
        .await;
    let named = Markup::of(&served).field(
        "data-child",
        &format!("call:{recorded_call}"),
        "tool_titles",
    );
    // The command it ran leads, because that is the order it asked in, and the search reads as
    // what was searched for — the field the registry names a `ToolSearch` call by.
    assert!(named.starts_with("⚡ ls -la "), "{named}");
    assert!(named.ends_with(", 🧰 select:PushNotification"), "{named}");

    // Both are cut to the column's width and marked where they were cut, like every other string
    // a row of a hundred prints: a call that talked for a page and called forty tools is a row,
    // not a page of one.
    let long_said = "s".repeat(queries::LOG_CHARS + 40);
    let long_path = format!("src/hyphae/{}.sql", "v".repeat(queries::LOG_CHARS));
    let housing = housed(session_id.clone(), Some(PROJECT));
    let dressing = dressed(vec![(
        tools[0].clone(),
        "Read".to_owned(),
        json!({ "file_path": format!("{PROJECT}/{long_path}") }).to_string(),
    )]);
    let (spoken, call) = (long_said.clone(), call_id.clone());
    let reach = Served::planted(move |store: &Store| {
        housing(store);
        store
            .connection()
            .execute(
                "UPDATE api_calls SET text = ? WHERE id = ?",
                params![spoken, call],
            )
            .expect("the call takes the planted text");
        dressing(store);
    });
    let (_, wide) = reach
        .page(&format!(
            "/session/{session_id}/thread/{source}/turn/{turn_id}"
        ))
        .await;
    let cut = Markup::of(&wide).fields("data-child", &format!("call:{call_id}"));
    assert_eq!(
        cut["text"],
        format!("{}{ELLIPSIS}", &long_said[..queries::LOG_CHARS])
    );
    let titled = format!("📖 {long_path}");
    let held: String = titled.chars().take(queries::LOG_CHARS).collect();
    assert_eq!(cut["tool_titles"], format!("{held}{ELLIPSIS}"));

    // A call that answered with tool calls and no text prints nothing rather than the dash a
    // missing value takes: `api_calls.text` is NOT NULL, so a call that said nothing holds the
    // empty string, and the column beside it already names what answered.
    let call = call_id.clone();
    let silent = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute("UPDATE api_calls SET text = '' WHERE id = ?", params![call])
            .expect("the call takes the empty text");
    });
    let (_, quiet) = silent
        .page(&format!(
            "/session/{session_id}/thread/{source}/turn/{turn_id}"
        ))
        .await;
    assert_eq!(
        Markup::of(&quiet).fields("data-child", &format!("call:{call_id}"))["text"],
        ""
    );
}

#[tokio::test]
async fn the_two_prose_columns_of_a_calls_log_are_bounded_by_the_stylesheet() {
    // What a call said and which tools it called are held to their columns by CSS alone.
    //
    // Both columns carry model prose in a table a browser sizes by its content, and neither the
    // query nor the template can bound what that does to a row: the cut those two values arrive
    // under is 300 characters, which is four lines of a wide column and a row as tall as a
    // paragraph. So the shape is the stylesheet's, and nothing renders CSS — a rule dropped here
    // is a page that still serves, still passes, and reads like a wall.
    //
    // The floor under the words is the half of this a fixture cannot show. Most api calls say
    // nothing at all, so a column sized by its content collapses to the width of the few rows that
    // filled it, and the two lines it is meant to show arrive one word wide. Found in a browser;
    // pinned here.
    let served = Served::corpus();
    // A turn page whose calls log has both columns, read the way a browser reads them: by the
    // class the cell carries.
    let (_, page) = served.page(&selections::turn_url()).await;
    assert!(page.contains(r#"class="said""#) && page.contains(r#"class="called""#));
    let (_, sheet) = served.page("/static/style.css").await;
    let style = Regex::new(r"(?s)/\*.*?\*/")
        .expect("a pattern")
        .replace_all(&sheet, "");
    let block = Regex::new(r"([^{}]+)\{([^{}]*)\}").expect("a pattern");
    let rules: Vec<(String, String)> = block
        .captures_iter(&style)
        .map(|found| (found[1].trim().to_owned(), found[2].to_owned()))
        .filter(|(selector, _)| selector.contains("td.said") || selector.contains("td.called"))
        .collect();
    // Every property the sheet sets on one of the two columns, or on the span inside it.
    let declared = |cell: &str| -> BTreeMap<String, String> {
        let named = format!("td.{cell}");
        rules
            .iter()
            .filter(|(selector, _)| selector.contains(&named))
            .flat_map(|(_, body)| body.split(';'))
            .filter_map(|part| part.split_once(':'))
            .map(|(name, value)| (name.trim().to_owned(), value.trim().to_owned()))
            .collect()
    };
    let (said, called) = (declared("said"), declared("called"));
    // Both are capped, so a column of prose cannot push the numbers a reader counts by off the
    // side of the pane, and both are dim, so the row still scans as a row.
    assert_eq!(said["max-width"], "26rem");
    assert_eq!(called["max-width"], "26rem");
    assert_eq!(said["color"], "var(--dim)");
    assert_eq!(called["color"], "var(--dim)");
    // The words wrap and stop at two lines, however long the 300 characters run...
    assert_eq!(said["display"], "-webkit-box");
    assert_eq!(said["overflow"], "hidden");
    assert_eq!(said["-webkit-line-clamp"], "2");
    assert_eq!(said["line-clamp"], "2");
    // ...they never collapse to the width of the calls that said nothing...
    assert_eq!(said["min-width"], "16rem");
    // ...and the list of tool titles is one line, cut with an ellipsis rather than wrapped.
    assert_eq!(called["white-space"], "nowrap");
    assert_eq!(called["text-overflow"], "ellipsis");
    assert_eq!(called["overflow"], "hidden");
}
