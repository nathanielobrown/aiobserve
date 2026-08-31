//! The one exemption from the page ceiling: a fetch whose unit is a single stored value.
//!
//! Ported from `tests/view/test_bounds__values.py`. A per-value fragment serves what the store
//! holds, so no page size can bound it. What holds it instead is that it serves that value and
//! nothing rendering could multiply out of it, and that every value on its way to a page or a
//! log is cut in SQL before it gets there.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

use axum::http::StatusCode;
use duckdb::params;
use hyphae_store::{Store, queries};
use hyphae_testsupport::budgets::PAGE_BYTES;
use hyphae_testsupport::cache;
use hyphae_testsupport::html::{Markup, plain};
use hyphae_testsupport::landmarks::{
    ANCESTOR, DENSE_TOOL, DENSE_TURN_CALL, FORK_ORIGIN, FORK_ORIGIN_RUN, MAIN, SPINE, SPINE_RUN,
};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::format::ELLIPSIS;
use hyphae_view::nodes::SPEECH_MARK;
use regex::Regex;

#[tokio::test]
async fn a_deeply_nested_value_is_served_at_the_size_it_was_stored() {
    // A per-value fetch serves the value it names, not what indenting could turn it into.
    // Indenting is the one thing that can break the per-value exemption, because it is
    // quadratic in nesting: 10 KB of nothing but `[` indents to 50 MB, and past the parser's own
    // stack the fragment answered 500 rather than anything at all. Both values are invented and
    // have to be — nothing recorded nests remotely this deep, which is the point.
    let indents_huge = format!("{}{}", "[".repeat(5_000), "]".repeat(5_000));
    let overflows_the_parser = format!("{}{}", "[".repeat(10_000), "]".repeat(10_000));
    let (nested, deeper) = (indents_huge.clone(), overflows_the_parser.clone());
    let served = Served::planted(move |store: &Store| {
        let connection = store.connection();
        connection
            .execute(
                "UPDATE tool_calls SET input = ?, result = ? WHERE session_id = ?",
                params![nested, nested, FORK_ORIGIN],
            )
            .expect("the tool calls nest");
        connection
            .execute(
                "UPDATE raw_records SET raw = ? WHERE session_id = ?",
                params![deeper, ANCESTOR],
            )
            .expect("the records nest");
    });
    let tool =
        format!("/fragment/{{}}/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}");
    let fetched = [
        (tool.replace("{}", "input"), indents_huge.len()),
        (tool.replace("{}", "result"), indents_huge.len()),
        (
            format!("/fragment/record/session/{ANCESTOR}/thread/{MAIN}/line/1"),
            overflows_the_parser.len(),
        ),
    ];
    // Each fragment answers, and weighs the value it names plus a page of chrome at most.
    for (url, stored) in fetched {
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "{url}");
        assert!(page.len() < stored + PAGE_BYTES, "{url}: {}", page.len());
    }
}

/// Every value a children log's rows print, as a reader sees it — the marks and all.
fn printed(page: &str) -> Vec<String> {
    let markup = Markup::of(page);
    markup
        .values("data-child")
        .into_iter()
        .flat_map(|key| markup.fields("data-child", &key).into_values())
        .collect()
}

/// Every title the NavTree half of a node page draws.
///
/// Read by pattern rather than through the parser: the same `title` field names a node in three
/// places on the page, and what tells the NavTree's copy from the pane's is which side of the
/// reading pane it fell on.
fn titles(page: &str) -> Vec<String> {
    static TITLE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?s)<span data-field="title">(.*?)</span>"#).expect("a literal pattern")
    });
    let tree = page
        .split_once(r#"<article id="reading-pane">"#)
        .expect("a node page has a reading pane")
        .0;
    TITLE
        .captures_iter(tree)
        .map(|found| found[1].to_owned())
        .collect()
}

#[tokio::test]
async fn a_long_value_is_cut_before_it_reaches_a_page_or_a_fragment() {
    // Every preview is truncated before it reaches a page, so no one huge value can bloat it.
    // The four widths the viewer cuts to, checked at once against one planted store: a list
    // row's, a NavTree row's title, a children log row's, and a pane's — a header's strings at
    // one cut and the one value it is about at another, wider one. The oversized values are
    // invented: redaction flattened every recorded string to a few characters, so no fixture
    // reaches a cap.
    let db = cache::corpus_store();
    // One turn of each kind, because a pane shows one arm or the other: a plain turn's prompt,
    // and a slash turn's command with its arguments.
    let turn_id = rows::one(
        &db,
        "SELECT id FROM turns WHERE session_id = $session AND source = 'main' \
         AND command_name IS NULL ORDER BY \"index\"",
        &[("session", SPINE.into())],
    )
    .str("id")
    .expect("an id")
    .to_owned();
    let command_id = rows::one(
        &db,
        "SELECT id FROM turns WHERE session_id = $session AND source = 'main' \
         AND command_name IS NOT NULL ORDER BY \"index\"",
        &[("session", SPINE.into())],
    )
    .str("id")
    .expect("an id")
    .to_owned();
    // And one tool call to dress as a command, on a page of its own: what a tool row shows is
    // read out of the input JSON rather than selected, so the two strings a command row prints
    // are cut on the way out and nowhere else. It has to be a second call, because the one below
    // keeps an input that is not JSON — the arm that shows the input as stored.
    let asked = rows::one(
        &db,
        "SELECT session_id, source, api_call_id, id FROM live_tool_calls \
         WHERE session_id <> $session ORDER BY session_id, source, api_call_id, \"index\"",
        &[("session", ANCESTOR.into())],
    );
    let (asked_session, asked_source, asked_call, asked_id) = (
        asked.str("session_id").expect("an id").to_owned(),
        asked.str("source").expect("a thread").to_owned(),
        asked.str("api_call_id").expect("an id").to_owned(),
        asked.str("id").expect("an id").to_owned(),
    );
    // And one tool call whose own page the sweep below reads, on the session whose tool rows the
    // plant overflows.
    let named = rows::one(
        &db,
        "SELECT source, id FROM live_tool_calls WHERE session_id = $session \
         ORDER BY source, \"index\"",
        &[("session", ANCESTOR.into())],
    );
    let (named_source, named_id) = (
        named.str("source").expect("a thread").to_owned(),
        named.str("id").expect("an id").to_owned(),
    );
    // Each value is planted well past its own cap, onto the real row a fixture recorded...
    let long = "x".repeat(queries::DETAIL_CHARS + 5_000);
    let (turn, command, dressed) = (turn_id.clone(), command_id.clone(), asked_id.clone());
    let planted = long.clone();
    let served = Served::planted(move |store: &Store| {
        let connection = store.connection();
        let long = &planted;
        for (statement, bound) in [
            (
                "UPDATE sessions SET title = ?, project_dir = ?, git_branch = ?, version = ?, \
                 entrypoint = ? WHERE id = ?",
                params![long, long, long, long, long, SPINE].to_vec(),
            ),
            (
                "UPDATE turns SET prompt = ? WHERE session_id = ? AND id = ?",
                params![long, SPINE, turn].to_vec(),
            ),
            (
                "UPDATE turns SET command_name = ?, command_args = ? \
                 WHERE session_id = ? AND id = ?",
                params![long, long, SPINE, command].to_vec(),
            ),
            (
                "UPDATE agent_runs SET brief = ?, agent_type = ?, model = ? WHERE session_id = ?",
                params![long, long, long, SPINE].to_vec(),
            ),
            (
                "UPDATE api_calls SET text = ?, model = ?, fallback_from = ? WHERE session_id = ?",
                params![long, long, long, ANCESTOR].to_vec(),
            ),
            (
                "UPDATE tool_calls SET input = ?, name = ? WHERE session_id = ?",
                params![long, long, ANCESTOR].to_vec(),
            ),
            (
                // The two strings a command row prints have to differ: the sub-line under a
                // row's title is what the call was *for*, and a description the title already
                // says is dropped rather than printed twice.
                "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
                params![
                    "Bash",
                    serde_json::json!({"description": format!("for {long}"), "command": long})
                        .to_string(),
                    dressed
                ]
                .to_vec(),
            ),
        ] {
            connection
                .execute(statement, duckdb::params_from_iter(bound))
                .expect("the plant lands");
        }
    });
    let listing = served.page("/sessions").await.1;
    let session = served.page(&format!("/session/{SPINE}")).await.1;
    let turn = served
        .page(&format!("/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"))
        .await
        .1;
    let slash = served
        .page(&format!("/session/{SPINE}/thread/{MAIN}/turn/{command_id}"))
        .await
        .1;
    let run = served
        .page(&format!("/session/{SPINE}/run/{SPINE_RUN}"))
        .await
        .1;
    let call = served
        .page(&format!(
            "/session/{ANCESTOR}/thread/{MAIN}/call/{DENSE_TURN_CALL}"
        ))
        .await
        .1;
    let dressed = served
        .page(&format!(
            "/session/{asked_session}/thread/{asked_source}/call/{asked_call}"
        ))
        .await
        .1;
    let ran = served
        .page(&format!(
            "/session/{asked_session}/thread/{asked_source}/tool/{asked_id}"
        ))
        .await
        .1;
    let tool = served
        .page(&format!(
            "/session/{ANCESTOR}/thread/{named_source}/tool/{named_id}"
        ))
        .await
        .1;
    // ...and what each of them shows is its cap, not the value. The list's cuts are the viewer's
    // own composition rather than its query's, because its filters read the whole values — a
    // project path cut to a head would match no session under a longer one.
    let listed = Markup::of(&listing);
    let row = listed.fields("data-session-id", SPINE);
    // Marked as cut, not merely short enough: a row's strings are the ones a page multiplies, so
    // a value that ended at the width and one that was stopped there have to read apart.
    let at_list = format!("{}{ELLIPSIS}", "x".repeat(queries::LIST_CHARS));
    assert_eq!(row["title"], at_list);
    assert_eq!(row["project_dir"], at_list);
    // And each member of the lists beside them, at the narrower width a member takes.
    assert!(row["agent_types"].starts_with(&format!(
        "{}{ELLIPSIS}",
        "x".repeat(queries::LIST_ITEM_CHARS)
    )));
    // A path too long for the filter box to suggest whole is left out of it rather than cut:
    // half a path fills the filter in with a value that matches nothing. Bounded by the box
    // still being full — an absence read off an empty list is no absence at all.
    let offered = listed.suggestions();
    assert!(!offered.is_empty());
    assert!(!offered.iter().any(|path| path.contains('x')));
    // A NavTree row is a line in the NavTree, so its title takes the narrowest cut of the four —
    // the same one whatever kind of node the row stands for. Cut and marked as cut: every column
    // a title is composed from comes back one character past the width, so a row that fills the
    // line says the value went on.
    let drawn = titles(&session);
    let widest = drawn
        .iter()
        .map(|title| title.chars().count())
        .max()
        .expect("the NavTree draws titles");
    let cut = format!("{}{ELLIPSIS}", "x".repeat(queries::NAV_CHARS));
    // ADAPTED: Python reads the one widest title, which its `max` resolves to whichever came
    // first in the page. Two of them tie at the width, so the set is taken instead — the tie is
    // a fact about the page rather than about either language, and reading it out this way also
    // says which forms filled the line rather than trusting document order to pick one.
    assert_eq!(widest, cut.chars().count());
    assert_eq!(
        drawn
            .into_iter()
            .filter(|title| title.chars().count() == widest)
            .collect::<BTreeSet<_>>(),
        // The plain form, and an agent run's, which leads with the bracket its type opens and
        // spends a character of the width on it.
        BTreeSet::from([
            cut.clone(),
            format!("[{}", cut.chars().skip(1).collect::<String>())
        ])
    );
    // A children log row is a line of a table, so it takes the next cut up — and every value the
    // plant reached is marked where it was cut, not merely short enough. Per value and not at
    // the maximum: a maximum is satisfied by whichever sibling overflowed furthest, which is how
    // a whole column of silently-truncated values hid behind a marked neighbour here.
    //
    // What the three pages between them print: a plain turn's prompt and a slash turn's command
    // with its arguments, a tool's name, the head of what it was asked read out of an input that
    // is not JSON and out of one that is, and the command that head describes.
    let pane = session
        .split_once(r#"<article id="reading-pane">"#)
        .expect("a node page has a reading pane")
        .1;
    let reached: Vec<String> = printed(pane)
        .into_iter()
        .chain(printed(&call))
        .chain(printed(&dressed))
        .filter(|value| value.contains('x'))
        .collect();
    assert_eq!(reached.len(), 6, "{reached:#?}");
    // A whole column wide and marked as stopped there — but not a run of `x` alone: a tool the
    // viewer names by its own field leads its title with that tool's glyph, and the glyph is
    // spent out of the width like any character.
    for value in &reached {
        assert_eq!(
            value.chars().count(),
            queries::LOG_CHARS + ELLIPSIS.chars().count(),
            "{value}"
        );
        assert!(value.ends_with(&format!("x{ELLIPSIS}")), "{value}");
    }
    // And the pane heads the node it is about at the widest of the three, because nothing on the
    // page repeats it. Every kind, not the session alone: the NavTree built the row the pane
    // stands on and cut its words to a NavTree row's width, and a title that took the NavTree's
    // word for it would head a turn with a third of the prompt it is about.
    //
    // Every string a header prints is cut at that width and says so, whether it heads the pane
    // or sits in the facts under it — a value that ends at the width with no mark is one a
    // reader cannot tell from a value that simply ended there.
    //
    // Swept over the whole header rather than field by field: which fields a header prints grows
    // with the store, and a list written out here would go on passing while the field added
    // beside it truncated in silence.
    let headed = format!("{}{ELLIPSIS}", "x".repeat(queries::HEADER_CHARS));
    for (shown, kind) in [
        (&session, "session"),
        (&turn, "turn"),
        (&slash, "turn"),
        (&call, "call"),
        (&run, "run"),
        (&tool, "tool"),
    ] {
        let filled: BTreeMap<String, String> = Markup::of(shown)
            .fields("data-body", kind)
            .into_iter()
            .filter(|(_, value)| value.contains('x'))
            .collect();
        // The plant reached this pane at all, so a sweep finding nothing is a sweep that proves
        // nothing...
        assert!(!filled.is_empty(), "{kind}");
        // ...and everything it reached is cut to the header's width and marked there. Two kinds
        // lead their title with something the width is spent on before the value: a run's
        // bracketed agent type, which the plant made long too, so the cut lands inside the
        // bracket; and a call whose answer was words, which leads with the speech mark and so
        // cuts two characters of value earlier.
        let spoken = format!(
            "{SPEECH_MARK} {}",
            headed
                .chars()
                .skip(SPEECH_MARK.chars().count() + 1)
                .collect::<String>()
        );
        let mut cut_at = BTreeSet::from([headed.clone()]);
        match kind {
            "run" => {
                cut_at.insert(format!("[{}", headed.chars().skip(1).collect::<String>()));
            }
            "call" => {
                cut_at.insert(spoken);
            }
            _ => {}
        }
        assert_eq!(
            filled.into_values().collect::<BTreeSet<_>>(),
            cut_at,
            "{kind}"
        );
    }
    // A pane reads one node, so its strings take a header's cut — and the one value the node is
    // about takes the widest of the four, with the rest of it offered as its own fetch.
    let at_detail = format!("{}{ELLIPSIS}", "x".repeat(queries::DETAIL_CHARS));
    let read = Markup::of(&turn);
    assert_eq!(read.field("data-detail", "prompt", "prompt"), at_detail);
    assert_eq!(
        read.inside("data-detail", "prompt", "data-whole"),
        ["prompt"]
    );
    // A slash turn shows the same two widths on one page: the command it ran is a word the pane
    // leads with, cut to a header's width, and what followed it is a second value of the turn,
    // cut to a pane's and offering the rest of itself like the prompt does.
    let read = Markup::of(&slash);
    assert_eq!(
        read.field("data-command", &command_id, "command_name"),
        headed
    );
    assert_eq!(
        read.field("data-detail", "command_args", "command_args"),
        at_detail
    );
    assert_eq!(
        read.inside("data-detail", "command_args", "data-whole"),
        ["command_args"]
    );
    assert_eq!(
        Markup::of(&run).field("data-detail", "brief", "brief"),
        at_detail
    );
    assert_eq!(
        Markup::of(&call).field("data-detail", "text", "text"),
        at_detail
    );
    // A detail the page marks up is cut the same way and says so the same way, which no other
    // assertion here reaches: the mark lands inside the highlighted block, where it is one more
    // character for the lexer to make of what it will. Read back through the markup, because a
    // value that came back marked up is only cut if a reader still sees the cut.
    assert_eq!(plain(&Markup::of(&ran).block("command")), at_detail);
}
