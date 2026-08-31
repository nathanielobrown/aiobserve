//! What a pane previews of a fat value, and what the whole of it costs to open.
//!
//! A pane shows the head of one or two fat values with the way to the rest of each: `?detail=`
//! widens the preview and a fragment URL serves the value whole. These leaves read both halves,
//! and the wall the pane draws around prose — someone's words inside our page — that a program's
//! bytes never get.
//!
//! What a value is *marked up* as is next door in `node_code.rs`, because that is the half this
//! prototype leaves behind.

use hyphae_testsupport::html::{Markup, counted, plain};
use hyphae_testsupport::landmarks::{ANCESTOR, DENSE_TURN, MAIN, SLASH_TURN, SPINE};
use hyphae_testsupport::rows;
use hyphae_testsupport::selections::{call_to, turn_url};
use hyphae_testsupport::served::Served;

use std::collections::BTreeSet;

use axum::http::StatusCode;
use hyphae_store::queries::DETAIL_CHARS;
use hyphae_store::{Param, Store};
use hyphae_view::format::ELLIPSIS;
use hyphae_view::labels::label;

#[tokio::test]
async fn a_pane_previews_a_fat_value_and_offers_the_rest_as_its_own_fetch() {
    // A value past the pane's width is cut, counted, and fetched whole from its own URL.
    //
    // Planted rather than recorded: redaction flattened every long string in the corpus, so no
    // fixture prompt reaches the width and the cut would never fire. The plant is one recorded
    // turn's prompt, grown past the width, and what is read is the arithmetic — the head is
    // exactly the width, the count is the rest, and the fetch answers with the whole.
    let prompt = "x".repeat(DETAIL_CHARS * 2);
    let grown = {
        let planted = prompt.clone();
        Served::planted(move |store: &Store| {
            store
                .connection()
                .execute(
                    "UPDATE turns SET prompt = ? WHERE session_id = ? AND source = ? AND id = ?",
                    duckdb::params![planted, ANCESTOR, MAIN, DENSE_TURN],
                )
                .expect("the prompt is plantable");
        })
    };
    let turn = turn_url();
    let (status, page) = grown.page(&turn).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // The pane shows the width it budgeted for, marked where the value went on, and says how many
    // characters it left.
    assert_eq!(
        markup.field("data-detail", "prompt", "prompt"),
        format!("{}{ELLIPSIS}", "x".repeat(DETAIL_CHARS))
    );
    assert_eq!(
        markup.field("data-detail", "prompt", "cut"),
        counted((prompt.chars().count() - DETAIL_CHARS) as i64)
    );
    // The link beside it fetches the value alone, and that fetch is the whole of it.
    let url = markup.inside("data-detail", "prompt", "href");
    assert_eq!(url.len(), 1, "one way to the rest: {url:?}");
    let (status, whole) = grown.page(&url[0]).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        Markup::of(&whole).values("data-value"),
        [prompt.chars().count().to_string()]
    );
    // A reader who asks for less gets less, which is what makes the width a knob.
    let (_, narrow) = grown.page(&format!("{turn}?detail=10")).await;
    assert_eq!(
        Markup::of(&narrow).field("data-detail", "prompt", "prompt"),
        format!("{}{ELLIPSIS}", "x".repeat(10))
    );
    // The recorded prompt at that same URL fits, and a value that fits offers nothing: no count of
    // what is left, and no fetch of a rest that is not there.
    let served = Served::corpus();
    let (_, page) = served.page(&turn).await;
    let fits = Markup::of(&page);
    assert!(!fits.fields("data-detail", "prompt").contains_key("cut"));
    assert!(
        fits.inside("data-detail", "prompt", "data-whole")
            .is_empty()
    );
}

#[tokio::test]
async fn a_preview_is_cut_at_the_ceiling_and_the_fetch_behind_it_is_not() {
    // The same arithmetic read against the ceiling `docs/viewer-bounds.md` declares, on the other
    // mount that draws it: a tool call's arguments rather than a turn's prompt. The planted value
    // is longer than the ceiling on every tool call, so whichever the store lists first is one
    // whose page has to cut.
    // Longer than the widest a page previews, planted because no fixture carries one: the
    // corpus's largest tool input is 438 characters and the ceiling is 4,000.
    let long = 5_000;
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute("UPDATE tool_calls SET input = ?", ["x".repeat(long)])
            .expect("the input is plantable");
    });
    let row = rows::one(
        &served.db(),
        "SELECT session_id, source, id FROM tool_calls ORDER BY 1, 2, 3 LIMIT 1",
        &[],
    );
    let at = format!(
        "/session/{}/thread/{}/tool/{}",
        row.str("session_id").expect("a session id"),
        row.str("source").expect("a thread"),
        row.str("id").expect("a tool call id"),
    );
    let fetch = format!("/fragment/input{at}");
    // The default: the preview stops at the ceiling and marks where it stopped.
    let (status, page) = served.page(&at).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        cut_at(&Markup::of(&page), "input", "input"),
        Some(DETAIL_CHARS)
    );
    assert!(
        page.contains(&fetch),
        "the cut mark links to the whole value"
    );
    // A knob only goes down, and the cut moves with it.
    let (status, narrow) = served.page(&format!("{at}?detail=100")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cut_at(&Markup::of(&narrow), "input", "input"), Some(100));
    // The fetch behind the mark is the whole value, which is what makes the cut safe. It names the
    // value rather than the column it came from: it is one value, alone.
    let (status, whole) = served.page(&fetch).await;
    assert_eq!(status, StatusCode::OK);
    let whole = Markup::of(&whole);
    assert_eq!(cut_at(&whole, "input", "value"), None, "the fetch is uncut");
    assert_eq!(
        whole.field("data-detail", "input", "value").chars().count(),
        long
    );
}

/// Where a value was cut, or nothing where it arrived whole: the ellipsis is the mark, so a value
/// that carries none is one nothing was left out of.
fn cut_at(markup: &Markup, mounted: &str, field: &str) -> Option<usize> {
    markup
        .field("data-detail", mounted, field)
        .strip_suffix(ELLIPSIS)
        .map(|kept| kept.chars().count())
}

/// A prompt in the markdown a person or an agent writes one in: a heading, a list, a link and a
/// fenced block. Planted rather than recorded — redaction flattened every fixture prompt to a line
/// of its own — and real in the shape that matters here: it is how the briefs in `plans/` are
/// written.
const MARKDOWN_PROMPT: &str = "# The task\n\nRead `docs/viewer.md`, then:\n\n\
                               - price it\n- land it\n\n```py\nbudget = 1\n```\n";

#[tokio::test]
async fn a_pane_reads_what_a_person_or_a_model_wrote_as_the_markdown_it_was_written_in() {
    // A pane renders prose as markdown, the same way the fetch that replaces it already did.
    //
    // The preview and the whole value are one value shown twice — the fetch swaps into the block
    // the preview sat in — so a pane that printed the characters and a fetch that rendered them
    // told a reader the head and the rest were written in different things.
    //
    // Every value a pane previews that prose was written into is swept, because the choice is a
    // flag per value: what a turn was asked and what followed its slash command, and what an api
    // call said and what it thought. A run's two are swept where the run's own leaf plants them.
    let corpus = Served::corpus();
    let call_id = rows::one(
        &corpus.db(),
        "SELECT id FROM live_api_calls WHERE session_id = $session_id AND source = $source \
         AND turn_id = $turn_id ORDER BY \"index\" LIMIT 1",
        &[
            ("session_id", ANCESTOR.into()),
            ("source", MAIN.into()),
            ("turn_id", DENSE_TURN.into()),
        ],
    )
    .str("id")
    .expect("an api call id")
    .to_owned();
    let written = {
        let call_id = call_id.clone();
        Served::planted(move |store: &Store| {
            let connection = store.connection();
            connection
                .execute(
                    "UPDATE turns SET prompt = ? WHERE session_id = ? AND source = ? AND id = ?",
                    duckdb::params![MARKDOWN_PROMPT, ANCESTOR, MAIN, DENSE_TURN],
                )
                .expect("the prompt is plantable");
            connection
                .execute(
                    "UPDATE turns SET command_args = ? \
                     WHERE session_id = ? AND source = ? AND id = ?",
                    duckdb::params![MARKDOWN_PROMPT, SPINE, MAIN, SLASH_TURN],
                )
                .expect("the arguments are plantable");
            connection
                .execute(
                    "UPDATE api_calls SET text = ?, thinking = ? \
                     WHERE session_id = ? AND source = ? AND id = ?",
                    duckdb::params![
                        MARKDOWN_PROMPT,
                        MARKDOWN_PROMPT,
                        ANCESTOR,
                        MAIN,
                        call_id.as_str()
                    ],
                )
                .expect("the call's words are plantable");
        })
    };
    let call = format!("/session/{ANCESTOR}/thread/{MAIN}/call/{call_id}");
    let slash = format!("/session/{SPINE}/thread/{MAIN}/turn/{SLASH_TURN}");
    // Each value beside the page that previews it and the fetch that opens it whole.
    let previewed = [
        (
            "prompt",
            turn_url(),
            format!("/fragment/prompt/session/{ANCESTOR}/thread/{MAIN}/turn/{DENSE_TURN}"),
        ),
        (
            "command_args",
            slash,
            format!("/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{SLASH_TURN}"),
        ),
        (
            "text",
            call.clone(),
            format!("/fragment/text/session/{ANCESTOR}/thread/{MAIN}/call/{call_id}"),
        ),
        (
            "thinking",
            call,
            format!("/fragment/thinking/session/{ANCESTOR}/thread/{MAIN}/call/{call_id}"),
        ),
    ];
    for (field, page, fetch) in previewed {
        let (_, served) = written.page(&page).await;
        let pane = Markup::of(&served).prose(field);
        // The heading is a heading, the list is a list, and the fenced block is marked up in the
        // language a lexer read it as — the same lexer the viewer reads code with...
        assert!(pane.contains("<h1>The task</h1>"), "{field}");
        assert_eq!(pane.matches("<li>").count(), 2, "{field}");
        assert!(pane.contains("<pre class=\"code python\">"), "{field}");
        // ...so none of the marks a reader wrote are left standing in the text.
        assert!(!plain(&pane).contains('#'), "{field}");
        assert!(!plain(&pane).contains("```"), "{field}");
        // And the value the fetch brings back is rendered the same, because it is the same value:
        // this prompt fits the pane's width, so the head is the whole of it.
        let (_, opened) = written.page(&fetch).await;
        assert_eq!(Markup::of(&opened).prose(field), pane, "{field}");
    }
}

/// What a subagent sends back to the agent that spawned it: prose, written in markdown, with a
/// heading and a list in it. Planted for the reason the prompt above is — redaction flattened
/// every recorded report to `[redacted]` — and real in shape: it is the report this repository
/// asks its own implementer runs for.
const REPORT: &str = "## Done\n\n- landed the branch\n- `mise run check` is green\n";

/// One recorded run whose spawning call carried both a prompt and a result: the session, and the
/// run. Read out of the store because those two facts are the leaf's whole subject.
fn a_spawned_run(db: &std::path::Path) -> (String, String) {
    let row = rows::one(
        db,
        "SELECT a.session_id, a.id FROM live_agent_runs a JOIN live_tool_calls t \
         ON t.session_id = a.session_id AND t.id = a.tool_use_id AND t.source <> a.id \
         WHERE json_extract_string(t.input, '$.prompt') IS NOT NULL AND t.result IS NOT NULL \
         ORDER BY 1, 2 LIMIT 1",
        &[],
    );
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.str("id").expect("a run id").to_owned(),
    )
}

#[tokio::test]
async fn a_run_page_reads_the_call_that_spawned_it_for_the_ask_and_the_answer() {
    // A run's page says what it was asked and what it sent back, off the call that spawned it.
    //
    // Neither fact is on the run's own row. Claude Code records the ask in the spawning `Agent`
    // call's `prompt` and what the parent received as that call's `result`, so the page reads both
    // from there. The answer is deliberately what the parent got rather than the run's last turn:
    // a run that stopped without reporting told its parent nothing, and a page that showed its
    // last turn instead would put words in the parent's mouth.
    let corpus = Served::corpus();
    let (session_id, run_id) = a_spawned_run(&corpus.db());
    let spawned = {
        let (session, run) = (session_id.clone(), run_id.clone());
        let asked = serde_json::json!({ "prompt": MARKDOWN_PROMPT }).to_string();
        Served::planted(move |store: &Store| {
            store
                .connection()
                .execute(
                    "UPDATE tool_calls SET input = json_merge_patch(input, ?), result = ? \
                     WHERE session_id = ? AND id = (SELECT tool_use_id FROM agent_runs \
                       WHERE session_id = ? AND id = ?)",
                    duckdb::params![
                        asked.as_str(),
                        REPORT,
                        session.as_str(),
                        session.as_str(),
                        run.as_str()
                    ],
                )
                .expect("the spawning call is plantable");
        })
    };
    let run = format!("/session/{session_id}/run/{run_id}");
    let (status, page) = spawned.page(&run).await;
    assert_eq!(status, StatusCode::OK);
    let pane = Markup::of(&page);
    // Both are on the pane, rendered as the markdown they were written in rather than as the JSON
    // the ask was stored inside — and beside the brief, which is a third thing: the line the
    // spawning agent typed to name the run, not the instructions it gave.
    assert!(pane.prose("prompt").contains("<h1>The task</h1>"));
    assert!(pane.prose("result").contains("<h2>Done</h2>"));
    assert_eq!(pane.values("data-detail"), ["brief", "prompt", "result"]);
    // And each has a route of its own that answers with the whole value, filed under the same name
    // the preview sat under, so the fetch swaps into its own block.
    let (_, asked) = spawned.page(&format!("/fragment/prompt{run}")).await;
    let (_, answered) = spawned.page(&format!("/fragment/result{run}")).await;
    let (asked, answered) = (Markup::of(&asked), Markup::of(&answered));
    assert_eq!(asked.values("data-detail"), ["prompt"]);
    assert_eq!(answered.values("data-detail"), ["result"]);
    assert_eq!(asked.prose("prompt"), pane.prose("prompt"));
    assert_eq!(answered.prose("result"), pane.prose("result"));
}

#[tokio::test]
async fn a_run_nobody_asked_in_words_shows_no_ask_and_serves_none() {
    // A run whose spawning call carried no prompt has no ask to show, and its route 404s.
    //
    // Two ways a run reaches the store without one: spawned by a tool that takes something other
    // than a prompt — a `Workflow` names a workflow — and recorded with no spawning call at all,
    // which is what a resumed or forked transcript replays. Neither is an empty value: nothing on
    // the pane links to the route, so a request for it is a URL somebody kept.
    let served = Served::corpus();
    for (named, sql) in [
        (
            "spawned by a tool that takes no prompt",
            "SELECT a.session_id, a.id FROM live_agent_runs a JOIN live_tool_calls t \
             ON t.session_id = a.session_id AND t.id = a.tool_use_id AND t.source <> a.id \
             WHERE json_extract_string(t.input, '$.prompt') IS NULL ORDER BY 1, 2 LIMIT 1",
        ),
        (
            "recorded with no spawning call",
            "SELECT a.session_id, a.id FROM live_agent_runs a WHERE NOT EXISTS ( \
               SELECT 1 FROM live_tool_calls t WHERE t.session_id = a.session_id \
                AND t.id = a.tool_use_id AND t.source <> a.id) ORDER BY 1, 2 LIMIT 1",
        ),
    ] {
        let row = rows::one(&served.db(), sql, &[]);
        let run = format!(
            "/session/{}/run/{}",
            row.str("session_id").expect("a session id"),
            row.str("id").expect("a run id"),
        );
        let (status, pane) = served.page(&run).await;
        assert_eq!(status, StatusCode::OK, "{named}");
        assert!(
            !Markup::of(&pane)
                .values("data-detail")
                .iter()
                .any(|name| name == "prompt"),
            "{named}"
        );
        let asked = served.get(&format!("/fragment/prompt{run}")).await;
        assert_eq!(asked.status(), StatusCode::NOT_FOUND, "{named}");
    }
}

#[tokio::test]
async fn the_pane_walls_what_a_session_wrote_as_a_quote_and_leaves_a_payload_as_code() {
    // Prose a person or a model wrote is walled like a quotation; a program's bytes are not.
    //
    // Where the wall is drawn is the stylesheet's; which values get one is the server's, and a
    // class is the whole of what a served page can say about it. So the reading is a partition
    // over three panes rather than a hit on one detail: every value on each page is either walled
    // or not, and a fourth detail added on either side lands here instead of passing by not being
    // named.
    //
    // The three panes between them carry all five prose values the viewer previews — a run's
    // brief, the ask it was given and the answer it sent back, and what an api call said and
    // thought — beside a tool call's three, which are what a program was passed and what it handed
    // back. The rule is the same one that decides whether a value renders as markdown: a value is
    // prose or it is a payload, and no value is both.
    let served = Served::corpus();
    let (spawned_session, spawned_run) = a_spawned_run(&served.db());
    let said = rows::one(
        &served.db(),
        "SELECT session_id, source, id FROM live_api_calls WHERE length(text) > 0 \
         AND length(thinking) > 0 ORDER BY 1, 2, 3 LIMIT 1",
        &[],
    );
    let (ran_session, ran_source, ran_tool) = call_to(&served.db(), "Bash");
    let panes = [
        (
            "a run's pane",
            format!("/session/{spawned_session}/run/{spawned_run}"),
            ["brief", "prompt", "result"].as_slice(),
        ),
        (
            "an api call's pane",
            format!(
                "/session/{}/thread/{}/call/{}",
                said.str("session_id").expect("a session id"),
                said.str("source").expect("a thread"),
                said.str("id").expect("an api call id"),
            ),
            ["text", "thinking"].as_slice(),
        ),
        (
            "a tool call's pane",
            format!("/session/{ran_session}/thread/{ran_source}/tool/{ran_tool}"),
            [].as_slice(),
        ),
    ];
    for (named, url, quoted) in panes {
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "{named}");
        let markup = Markup::of(&page);
        let shown: BTreeSet<String> = markup.values("data-detail").into_iter().collect();
        let walls: BTreeSet<String> = shown
            .iter()
            .filter(|name| {
                markup.inside("data-detail", name, "class")[0]
                    .split_whitespace()
                    .any(|class| class == "quoted")
            })
            .cloned()
            .collect();
        let quoted: BTreeSet<String> = quoted.iter().map(|name| (*name).to_owned()).collect();
        // The page shows what the partition is about — an empty pane would satisfy any rule — and
        // every value on it falls on the side its author put it.
        assert!(!shown.is_empty(), "{named}");
        assert_eq!(walls, &quoted & &shown, "{named}");
        assert!(quoted.is_subset(&shown), "{named}");
    }
}

/// The five fat columns a node page previews: the node URL, the value route, and where the store
/// keeps it. Each `{0}` is the id the query answers with.
const COLUMNS: [(&str, &str, &str, &str); 5] = [
    (
        "command_args",
        "/session/{session}/thread/{source}/turn/{0}",
        "/fragment/args/session/{session}/thread/{source}/turn/{0}",
        "SELECT id, length(command_args) AS held FROM live_turns \
         WHERE session_id = $session_id AND source = $source AND command_name IS NOT NULL \
         AND length(command_args) > 0 ORDER BY length(command_args) DESC LIMIT 1",
    ),
    (
        "prompt",
        "/session/{session}/thread/{source}/turn/{0}",
        "/fragment/prompt/session/{session}/thread/{source}/turn/{0}",
        // Of a turn that was typed rather than run: a slash turn's prompt is the `<command-…>`
        // wrapper, which the pane shows as the two values inside it instead.
        "SELECT id, length(prompt) AS held FROM live_turns \
         WHERE session_id = $session_id AND source = $source AND command_name IS NULL \
         AND length(prompt) > 0 ORDER BY length(prompt) DESC LIMIT 1",
    ),
    (
        "input",
        "/session/{session}/thread/{source}/tool/{0}",
        "/fragment/input/session/{session}/thread/{source}/tool/{0}",
        "SELECT id, length(input) AS held FROM live_tool_calls \
         WHERE session_id = $session_id AND source = $source AND length(input) > 0 \
         ORDER BY length(input) DESC LIMIT 1",
    ),
    (
        "result",
        "/session/{session}/thread/{source}/tool/{0}",
        "/fragment/result/session/{session}/thread/{source}/tool/{0}",
        "SELECT id, length(result) AS held FROM live_tool_calls \
         WHERE session_id = $session_id AND source = $source AND length(result) > 0 \
         ORDER BY length(result) DESC LIMIT 1",
    ),
    (
        "text",
        "/session/{session}/thread/{source}/call/{0}",
        "/fragment/text/session/{session}/thread/{source}/call/{0}",
        "SELECT id, length(text) AS held FROM live_api_calls \
         WHERE session_id = $session_id AND source = $source AND length(text) > 0 \
         ORDER BY length(text) DESC LIMIT 1",
    ),
];

#[tokio::test]
async fn every_value_a_pane_previews_is_fetchable_whole_from_its_own_url() {
    // One route per column rather than one per row: a tool call's input and its result are two
    // values a reader opens apart, and a route that served the row whole would send the other one
    // every time. Each is checked against the length the store holds, which is what proves the
    // fetch is untruncated rather than merely longer than the preview.
    let served = Served::corpus();
    let bound: &[(&str, Param)] = &[("session_id", SPINE.into()), ("source", MAIN.into())];
    for (name, node, fragment, sql) in COLUMNS {
        let row = rows::one(&served.db(), sql, bound);
        let node_id = row.str("id").expect("a node id");
        let held = row.i64("held").expect("a length");
        let filled = |shape: &str| {
            shape
                .replace("{session}", SPINE)
                .replace("{source}", MAIN)
                .replace("{0}", node_id)
        };
        // The pane previews it under its own name...
        let (status, page) = served.page(&filled(node)).await;
        assert_eq!(status, StatusCode::OK, "{name}");
        let page = Markup::of(&page);
        assert!(!page.field("data-detail", name, name).is_empty(), "{name}");
        // ...and its own route answers with every character the store holds. Reached by URL rather
        // than by the pane's link, which the pane only draws when there is a rest to offer — every
        // value this corpus records fits inside the preview.
        let (status, served_whole) = served.page(&filled(fragment)).await;
        assert_eq!(status, StatusCode::OK, "{name}");
        let whole = Markup::of(&served_whole);
        assert_eq!(whole.values("data-value"), [held.to_string()], "{name}");
        // The fetch replaces the section the preview sat in, so it comes back filed under the same
        // name: what a value is styled as — the rail that tells an ask from an answer — hangs off
        // that name, and a fragment that dropped it would open unstyled.
        assert_eq!(whole.values("data-detail"), [name], "{name}");
        // And a value that is not prose comes back marked up the way the preview was. The fragment
        // files it under `value` and the pane under the column's own name, so the two `<pre>`
        // classes are what compare — one rule decides both, and this is the reading that would see
        // them part again.
        if name == "input" || name == "result" {
            assert_eq!(whole.walled("value"), page.walled(name), "{name}");
        }
    }
    // And a run's brief, which is the one fat column that hangs off the session rather than a
    // thread, so its route takes no source.
    let row = rows::one(
        &served.db(),
        "SELECT session_id, id, length(brief) AS held FROM live_agent_runs \
         WHERE length(brief) > 0 ORDER BY length(brief) DESC LIMIT 1",
        &[],
    );
    let run = format!(
        "/session/{}/run/{}",
        row.str("session_id").expect("a session id"),
        row.str("id").expect("a run id"),
    );
    let (_, page) = served.page(&run).await;
    assert!(
        !Markup::of(&page)
            .field("data-detail", "brief", "brief")
            .is_empty()
    );
    let (_, whole) = served.page(&format!("/fragment/brief{run}")).await;
    let whole = Markup::of(&whole);
    assert_eq!(
        whole.values("data-value"),
        [row.i64("held").expect("a length").to_string()]
    );
    assert_eq!(whole.values("data-detail"), ["brief"]);
    // The brief is what a run was asked to do, so it is labelled as a brief and not as a
    // description of the run — the word the enrichment pass owns.
    assert_eq!(
        (label("brief"), label("description")),
        ("Task brief", "Description")
    );
}
