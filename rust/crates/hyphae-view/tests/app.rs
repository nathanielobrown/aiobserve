//! The app around the node browser: what every response owes a reader, whatever it serves.
//!
//! A page cites the query behind it, names each id in its URL by the word in front of it, asks for
//! no asset the viewer does not ship, and reads a store it never writes to. Every expectation is
//! derived from the store the app is serving rather than written down, so a fixture added to the
//! corpus does not silently stop being covered.
//!
//! The stylesheet is `style.rs`; the session list `app_list.rs` and its filter form
//! `app_filters.rs`; the header above a node `app_headers.rs`; what a page does with untrusted
//! text `routes.rs`. The node pages themselves are `node.rs` and the NavTree beside them
//! `nav_tree.rs`, each with its neighbours.

use std::collections::{BTreeMap, BTreeSet};

use axum::http::StatusCode;
use regex::Regex;
use serde_json::Value;

use hyphae_store::{Param, queries};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{
    BASH_TOOL, DENSE_CALL, DENSE_CALL_TURN, DENSE_TOOL, FORK_ORIGIN, FORK_ORIGIN_RUN, MAIN,
    MISSING, SLASH_TURN, SPINE, SPINE_RUN,
};
use hyphae_testsupport::rows;
use hyphae_testsupport::selections;
use hyphae_testsupport::served::Served;
use hyphae_view::nodes::Kind;

#[tokio::test]
async fn a_node_page_cites_every_query_it_ran() {
    // A node page's footer holds one re-runnable line per query behind it.
    //
    // The session node is the case with the most reads behind one page: its own header, the level
    // of the NavTree under it, and the runs and compactions every level needs to place. Each line
    // carries the bindings this request made rather than the query file's defaults, which is what
    // makes it a citation and not a filename.
    let served = Served::corpus();
    let (_, page) = served.page(&format!("/session/{SPINE}?log=3")).await;
    let cited = Markup::of(&page).fields("id", "citation");
    let nav = queries::NAV_CHARS;
    let log = queries::LOG_CHARS;
    assert_eq!(
        cited,
        BTreeMap::from([
            (
                "view_session_header".to_owned(),
                format!(
                    "-- queries/view_session_header.sql session_id={SPINE} \
                     head_chars=100 item_chars=60 head_items=5"
                ),
            ),
            (
                "view_nav_tree_turns".to_owned(),
                format!(
                    "-- queries/view_nav_tree_turns.sql session_id={SPINE} source={MAIN} \
                     nav_chars={nav}"
                ),
            ),
            // A run is printed twice on this page — as a NavTree row and as a children log row —
            // so the citation says which of the two widths this request read them at: the wider.
            (
                "view_runs".to_owned(),
                format!("-- queries/view_runs.sql session_id={SPINE} chip_chars={log}"),
            ),
            (
                "view_compactions".to_owned(),
                format!(
                    "-- queries/view_compactions.sql session_id={SPINE} source={MAIN} \
                     chip_chars={nav}"
                ),
            ),
            // The whole thread in outline, which is what places the runs: no window, so no paging.
            (
                "session_timeline".to_owned(),
                format!("-- queries/session_timeline.sql session_id={SPINE} log_chars={log}"),
            ),
        ])
    );
}

#[test]
fn every_id_a_url_carries_is_named_by_the_word_in_front_of_it() {
    // Every id in a path has a word in front of it saying what kind of id it is.
    //
    // The one rule the URL scheme is built on (`docs/viewer.md`), and it has two halves. No two
    // ids sit side by side: read a path that breaks that and the eye pairs the segments the wrong
    // way — a turn and something under it, where the second id is really the thread the turn is
    // on. And the word in front *names* the id, which is what the first half alone does not say:
    // `/session/{session_id}/unattributed/{source}` puts no two ids together and still calls a
    // thread by the name of the bucket hanging off it.
    //
    // Naming is checked across the table rather than against a list of words, which would be the
    // rule written twice: an id kind that follows two different words is one of the two lying.
    // That catches a word changed at one route and misses a parameter used at exactly one — for
    // those, the closed registry in `bounds.rs` is what holds the shape.
    //
    // `{kind}` is the one parameter that counts as a word rather than an id: it carries a member
    // of `nodes::Kind`, and every one of those is a bare literal segment.
    assert!(
        Kind::ALL
            .iter()
            .all(|kind| kind.word().chars().all(char::is_alphabetic))
    );
    let mounted = hyphae_view::routes::paths();
    assert!(!mounted.is_empty(), "the app exposes no routes");
    let mut naming: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut carried = false;
    for path in &mounted {
        let segments: Vec<&str> = path
            .split('/')
            .filter(|part| !part.is_empty())
            .map(|part| if part == "{kind}" { "kind" } else { part })
            .collect();
        for (at, part) in segments.iter().enumerate() {
            if !part.starts_with('{') {
                continue;
            }
            assert!(at > 0, "{path} opens on an id nothing names");
            assert!(
                !segments[at - 1].starts_with('{'),
                "{path} puts two ids side by side"
            );
            // The parameter's own name, past the `*` an offloaded file path's wildcard carries.
            let named = part.trim_matches(['{', '}']).trim_start_matches('*');
            naming
                .entry(named.to_owned())
                .or_default()
                .insert(segments[at - 1].to_owned());
            carried = true;
        }
    }
    assert!(carried, "no route carries an id");
    for (parameter, words) in &naming {
        assert_eq!(
            words.len(),
            1,
            "{parameter} is called {words:?} at different routes"
        );
    }
}

#[tokio::test]
async fn a_per_value_fragment_returns_the_one_value_it_names() {
    // Opening one tool call's result fetches that call's and nothing else from the same call.
    //
    // The per-value routes are the exception to the payload bound — they ship a fat column whole —
    // so what keeps the bound is that the unit really is one value. A fragment that quietly
    // carried its siblings would be a page of them under another name.
    let served = Served::corpus();
    let db = served.db();
    let siblings: Vec<String> = rows::all(
        &db,
        "SELECT id FROM live_tool_calls \
         WHERE session_id = $session AND source = $thread AND api_call_id = $call",
        &[
            ("session", Param::from(FORK_ORIGIN)),
            ("thread", Param::from(FORK_ORIGIN_RUN)),
            ("call", Param::from(DENSE_CALL)),
        ],
    )
    .iter()
    .map(|row| row.str("id").expect("a tool id").to_owned())
    .collect();
    assert!(siblings.iter().any(|id| id == DENSE_TOOL) && siblings.len() > 1);
    let (_, fragment) = served
        .page(&format!(
            "/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
        ))
        .await;
    // The value it was asked for arrives, and it is not empty...
    let whole = rows::one(
        &db,
        "SELECT length(result) AS held FROM live_tool_calls \
         WHERE id = $tool AND session_id = $session",
        &[
            ("tool", Param::from(DENSE_TOOL)),
            ("session", Param::from(FORK_ORIGIN)),
        ],
    )
    .i64("held")
    .expect("a length");
    assert_eq!(
        Markup::of(&fragment).values("data-value"),
        vec![whole.to_string()]
    );
    // ...and no sibling of the same call rode along with it.
    for other in &siblings {
        assert!(other == DENSE_TOOL || !fragment.contains(other), "{other}");
    }
}

#[tokio::test]
async fn a_fragment_cites_the_query_that_fetched_it() {
    // Every whole-value fragment carries the query and the keys it was fetched by.
    //
    // A fragment arrives on a page that has already been served, so it cannot ride the footer the
    // pages share: each one carries the line itself. All nine routes hand one shared seam their
    // own keys, so each is here — a seam pinned through one route alone would still let another
    // cite a key it was not fetched by.
    let served = Served::corpus();
    let keyed = format!("session_id={FORK_ORIGIN} source={FORK_ORIGIN_RUN}");
    let head = queries::HEADER_CHARS;
    let fetched = [
        (
            format!(
                "/fragment/text/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}"
            ),
            format!("-- queries/view_call_text.sql {keyed} api_call_id={DENSE_CALL}"),
        ),
        (
            format!(
                "/fragment/thinking/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}"
            ),
            format!("-- queries/view_call_thinking.sql {keyed} api_call_id={DENSE_CALL}"),
        ),
        (
            format!(
                "/fragment/input/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
            ),
            format!("-- queries/view_tool_input.sql {keyed} tool_call_id={DENSE_TOOL}"),
        ),
        (
            format!(
                "/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
            ),
            format!(
                "-- queries/view_tool_result.sql {keyed} tool_call_id={DENSE_TOOL} head_chars={head}"
            ),
        ),
        // The command a `Bash` call ran, which only a `Bash` call has — so this one is keyed off
        // the thread that holds one rather than off the dense call above.
        (
            format!("/fragment/command/session/{SPINE}/thread/{MAIN}/tool/{BASH_TOOL}"),
            format!(
                "-- queries/view_tool_command.sql session_id={SPINE} source={MAIN} \
                 tool_call_id={BASH_TOOL}"
            ),
        ),
        (
            format!(
                "/fragment/prompt/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/turn/{DENSE_CALL_TURN}"
            ),
            format!("-- queries/view_turn_prompt.sql {keyed} turn_id={DENSE_CALL_TURN}"),
        ),
        // The arguments of a slash turn, which only the one recorded slash turn has.
        (
            format!("/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{SLASH_TURN}"),
            format!(
                "-- queries/view_turn_command_args.sql session_id={SPINE} source={MAIN} \
                 turn_id={SLASH_TURN}"
            ),
        ),
        // A run is keyed by the session and its own id: a run has one home, so no thread names it.
        (
            format!("/fragment/brief/session/{FORK_ORIGIN}/run/{FORK_ORIGIN_RUN}"),
            format!(
                "-- queries/view_run_brief.sql session_id={FORK_ORIGIN} run_id={FORK_ORIGIN_RUN}"
            ),
        ),
        // The record route keys on a line number rather than an id. Fetched off a subagent thread
        // at a line past the first, so neither key can be a constant the fixture hides.
        (
            format!("/fragment/record/session/{SPINE}/thread/{SPINE_RUN}/line/2"),
            format!("-- queries/view_record.sql session_id={SPINE} source={SPINE_RUN} line_no=2"),
        ),
    ];
    for (url, expected) in &fetched {
        let (_, fragment) = served.page(url).await;
        assert_eq!(
            Markup::of(fragment.as_str()).values("data-query"),
            vec![expected.clone()],
            "{url}"
        );
    }
}

#[tokio::test]
async fn a_fragment_naming_nothing_is_a_404() {
    // A per-value fragment for an id the store lacks is a 404, not an empty box.
    let served = Served::corpus();
    let (status, body) = served
        .page(&format!(
            "/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{MISSING}"
        ))
        .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(!body.contains(MISSING));
}

#[tokio::test]
async fn every_asset_a_page_asks_for_is_one_the_viewer_ships() {
    // No page reaches off the machine for an asset, and none writes an inline style.
    //
    // Both are things the policy in `app::CSP` forbids, and both fail the same way: loudly in a
    // browser and silently in this tier, because a blocked asset and a dropped attribute leave a
    // 200 behind. Read off what each route served — the fragments included, which no other
    // page-level sweep renders — rather than off the code that builds it: a component composes
    // another component, so what a source scan reads is never the page a reader gets.
    //
    // The enriched store, because the description and friction fragments are 404 until a pass has
    // written to the store.
    let served = Served::enriched();
    let offsite = Regex::new(r#"(?:src|href)="(\w+:)?//[^"]*""#).expect("a pattern");
    let mut swept = 0;
    for url in selections::scenarios().into_values() {
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "{url}");
        // Every `src` and `href` the page writes is a path on this server...
        assert!(!offsite.is_match(&page), "{url}");
        // ...and nothing carries a style attribute. This is the trap the cost badge's decile
        // classes exist to dodge: a wash written inline is a badge no reader ever sees.
        assert!(!page.contains(" style=\""), "{url}");
        // ...and nothing wears the class htmx paints, which the config below stops it painting.
        assert!(!page.contains("htmx-indicator"), "{url}");
        swept += 1;
    }
    assert!(swept > 1, "the scenario file named no page to sweep");
}

#[tokio::test]
async fn the_frame_every_page_arrives_in_asks_only_for_assets_the_viewer_serves() {
    // Each asset the base page names is served from this app, htmx included.
    let served = Served::corpus();
    let (_, page) = served.page("/").await;
    let named = Regex::new(r#"(?:src|href)="(/static/[^"]*)""#).expect("a pattern");
    let assets: Vec<String> = named
        .captures_iter(&page)
        .map(|found| found[1].to_owned())
        .collect();
    assert!(
        assets.iter().any(|asset| asset.contains("htmx")),
        "{assets:?}"
    );
    for asset in &assets {
        let (status, _) = served.page(asset).await;
        assert_eq!(status, StatusCode::OK, "{asset}");
    }
    // A clean page is not enough: htmx writes a `<style>` block of its own for the indicator class
    // as it loads, which the policy blocks and the browser reports on every page. This meta is
    // what stops it writing one — htmx merges the config before it paints. Read back through the
    // renderer's escaping: it quotes every attribute with `"` and escapes the JSON's own quotes to
    // `&#34;`, so what the browser parses is the config and what the source holds is not.
    let meta = Regex::new(r#"<meta name="htmx-config" content="([^"]*)">"#).expect("a pattern");
    let found = meta.captures(&page).expect("the frame carries the config");
    let config: Value = serde_json::from_str(&html_escape::decode_html_entities(&found[1]))
        .expect("the config is JSON once unescaped");
    assert_eq!(config["includeIndicatorStyles"], Value::Bool(false));
}

#[tokio::test]
async fn serving_the_store_leaves_it_read_only() {
    // Nothing the viewer serves writes to the store it is pointed at.
    let served = Served::corpus();
    let before = std::fs::metadata(served.db())
        .expect("the store file is there")
        .modified()
        .expect("the filesystem records a modification time");
    served.page("/").await;
    served.page(&format!("/session/{SPINE}")).await;
    served.page(&format!("/session/{MISSING}")).await;
    let after = std::fs::metadata(served.db())
        .expect("the store file is still there")
        .modified()
        .expect("the filesystem records a modification time");
    assert_eq!(before, after);
}
