//! The node page's frame: the pane a kind dispatches to, the chain down to it, and its mark.
//!
//! One node of every kind, picked out of the store by `hyphae_testsupport::selections` rather
//! than pinned, so a re-recorded corpus moves the selection instead of reddening the tier. What
//! the pane *holds* per kind is the other node leaves; what every page shares is here.

use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{ANCESTOR, DENSE_TURN, MAIN, MISSING, SPINE};
use hyphae_testsupport::marks::mark;
use hyphae_testsupport::rows;
use hyphae_testsupport::selections::{KINDS, node_url, pages, turn_url};
use hyphae_testsupport::served::{self, Served};

use std::collections::BTreeSet;

use axum::http::StatusCode;
use hyphae_view::columns::Shape;
use hyphae_view::nodes::Kind;

#[tokio::test]
async fn every_kind_of_node_serves_a_page_that_says_what_it_is() {
    // Swept per kind rather than over one node because the pane dispatches on the kind and each
    // arm renders different facts. What is checked is the frame every page shares: the right
    // pane, a chain that ends at the selection, and a tree whose selected row is the same node.
    let served = Served::corpus();
    for (kind, _, _) in KINDS {
        let url = node_url(&served.db(), kind);
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "GET {url}");
        let markup = Markup::of(&page);
        // The pane is the one for this kind, and it carries the node's own facts.
        assert_eq!(markup.values("data-body"), [kind], "{url}");
        assert!(!markup.fields("data-body", kind).is_empty(), "{url}");
        // The crumbs run outermost first and end at the selection, the row the NavTree marks.
        let crumbs = markup.values("data-crumb");
        let selected = markup.values("data-selected");
        assert_eq!(selected.len(), 1, "one selection on {url}: {selected:?}");
        let selected = &selected[0];
        let first = crumbs.first().expect("a chain starts somewhere");
        let last = crumbs.last().expect("a chain ends somewhere");
        assert!(first.starts_with("session:"), "{url}: {first}");
        assert_eq!(last, selected, "{url}");
        // And the selection's own row links to the URL that was asked for.
        assert_eq!(
            markup.inside("data-nav-tree", selected, "href")[0],
            url,
            "{url}"
        );
        // Four places on this page name the node, and every one of them says what kind it is with
        // the same character: the pane's heading, the browser tab, the last crumb, and the row the
        // tree marks. A reader learns eight marks once and then reads a tree without reading a
        // title — which is the whole of what the mark buys, so a surface missing it is a surface
        // where the same node looks like something else.
        let glyph = mark(kind);
        assert_eq!(markup.icons("data-body", kind), [glyph], "{url}");
        assert_eq!(
            page.matches(&format!("<title>{glyph} ")).count(),
            1,
            "{url}"
        );
        assert_eq!(markup.icons("data-crumb", last), [glyph], "{url}");
        assert_eq!(markup.icons("data-nav-tree", selected), [glyph], "{url}");
        // And a crumb above the selection is marked as what *it* is, not as what the page is
        // about: the chain says the kind of every step down to here.
        assert_eq!(
            markup.icons("data-crumb", first),
            [mark("session")],
            "{url}"
        );
        // Every one of those marks is decoration and the markup says so. It stands for a word
        // already on the page — the pane's kind, the crumb's field name, the row's class — so a
        // screen reader passes over it and reads the title instead of announcing a character it
        // has no word for (`.claude/rules/viewer-ui.md`).
        for (attribute, key) in [
            ("data-body", kind),
            ("data-crumb", last.as_str()),
            ("data-nav-tree", selected.as_str()),
        ] {
            assert_eq!(
                markup.inside(attribute, key, "aria-hidden"),
                ["true"],
                "{url} at {attribute}"
            );
        }
    }
}

#[tokio::test]
async fn a_node_the_store_does_not_hold_is_a_404() {
    // Every key a node URL carries is read, so a miss on any one of them is nothing. The session
    // is swapped on every kind and the node's own id on every kind that has one: a page that
    // answered on the session alone would be a page about some other session's turn. An empty
    // bucket is a miss too — it is a node that is not there rather than an empty one.
    let served = Served::corpus();
    for (kind, _, _) in KINDS {
        let url = node_url(&served.db(), kind);
        let session_id = url.split('/').nth(2).expect("a node URL names a session");
        let elsewhere = url.replacen(session_id, MISSING, 1);
        let (status, _) = served.page(&elsewhere).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{elsewhere}");
        let tail = url.rsplit('/').next().expect("a URL ends in something");
        if tail != session_id {
            let gone = url.replace(tail, MISSING);
            let (status, _) = served.page(&gone).await;
            assert_eq!(status, StatusCode::NOT_FOUND, "{gone}");
        }
    }
}

#[tokio::test]
async fn a_turn_node_serves_the_turn_the_store_holds() {
    // The pane says what the store says about the turn it was asked for.
    let served = Served::corpus();
    let url = turn_url();
    let (status, page) = served.page(&url).await;
    assert_eq!(status, StatusCode::OK);
    let row = rows::one(
        &served.db(),
        "SELECT t.\"index\" AS turn_index, \
         (SELECT count(*) FROM live_api_calls c WHERE c.session_id = t.session_id \
           AND c.source = t.source AND c.turn_id = t.id) AS api_calls, \
         (SELECT count(*) FROM live_tool_calls tc JOIN live_api_calls c \
           ON c.session_id = tc.session_id AND c.source = tc.source AND c.id = tc.api_call_id \
           WHERE tc.session_id = t.session_id AND tc.source = t.source AND c.turn_id = t.id) \
           AS tool_calls \
         FROM live_turns t WHERE t.session_id = $session_id AND t.source = $source \
         AND t.id = $turn_id",
        &[
            ("session_id", ANCESTOR.into()),
            ("source", MAIN.into()),
            ("turn_id", DENSE_TURN.into()),
        ],
    );
    let markup = Markup::of(&page);
    let shown = markup.fields("data-body", "turn");
    // The turn's own place in its thread, and the two counts under it — the api calls it made,
    // and the tool calls those made.
    let index = row.i64("turn_index").expect("a turn index");
    let calls = row.i64("api_calls").expect("a call count");
    let tools = row.i64("tool_calls").expect("a tool count");
    assert_eq!(shown["turn_index"], index.to_string());
    assert_eq!(shown["api_calls"], calls.to_string());
    assert_eq!(shown["tool_calls"], tools.to_string());
    // And the log under the pane lists those api calls, one row each.
    assert_eq!(markup.values("data-child").len() as i64, calls);
}

#[tokio::test]
async fn a_slash_turn_leads_with_the_command_it_ran() {
    // A turn typed as a slash command shows the command, not the block it was expanded into.
    //
    // Claude Code stores such a turn's prompt as the `<command-name>`/`<command-args>` wrapper it
    // built, and the extractor pulls the two halves into columns of their own. The pane reads
    // those columns — the command on a line of its own, and what followed it as a value of the
    // turn — and drops the wrapper from the prompt beside them, which otherwise printed the
    // command and its arguments a second time in their tags. What was sent stays whole in the
    // thread's transcript, which is where the pane links for the record.
    let served = Served::corpus();
    let row = rows::one(
        &served.db(),
        "SELECT id, command_name, command_args FROM live_turns \
         WHERE session_id = $session_id AND source = $source AND command_name IS NOT NULL \
         AND length(command_args) > 0 ORDER BY \"index\" LIMIT 1",
        &[("session_id", SPINE.into()), ("source", MAIN.into())],
    );
    let turn_id = row.str("id").expect("a turn id").to_owned();
    let name = row.str("command_name").expect("a command name").to_owned();
    let args = row
        .str("command_args")
        .expect("command arguments")
        .to_owned();
    let at = format!("/session/{SPINE}/thread/{MAIN}/turn/{turn_id}");
    let (status, page) = served.page(&at).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // The command, off the store's own column and on the command line the pane leads with rather
    // than among the counts the header rows.
    assert_eq!(markup.field("data-command", &turn_id, "command_name"), name);
    // What followed it is a value of the turn like the prompt is, so it is previewed under its own
    // heading with the way to the rest of it — arguments run to thousands of characters.
    assert_eq!(
        markup.field("data-detail", "command_args", "command_args"),
        args
    );
    // The rest of it comes off a route of its own, rendered as the prose a person typed — like the
    // prompt beside it, and unlike a tool's arguments, which are JSON and are marked up as JSON. A
    // fetch that read the arguments as code would print them in a `<pre>`.
    let (status, args_fragment) = served
        .page(&format!(
            "/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"
        ))
        .await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        args_fragment.contains("<p>"),
        "the arguments render as prose"
    );
    assert!(!args_fragment.contains("<pre"), "and not as code");
    // The wrapper itself is gone from the pane: everything inside it is already on the page under
    // the two headings above, and this turn's prompt is nothing else.
    assert!(!markup.values("data-detail").contains(&"prompt".to_owned()));
    // Gone from the value route under that heading too, and not as an empty page: the column the
    // fragment reads is NULL for this turn, so the URL a reader kept answers nothing.
    let (status, _) = served
        .page(&format!(
            "/fragment/prompt/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"
        ))
        .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    // It is still what was sent, though, so the record the pane opens beneath holds it whole.
    let lines = markup.values("data-open-record");
    assert_eq!(lines.len(), 1, "the pane opens one record");
    let (status, recorded) = served
        .page(&format!(
            "/fragment/record/session/{SPINE}/thread/{MAIN}/line/{}",
            lines[0]
        ))
        .await;
    assert_eq!(status, StatusCode::OK);
    assert!(recorded.contains("&lt;command-name&gt;"));
    // A turn nobody typed a command at has no command line at all: the pane leads with the prompt,
    // and there is no empty heading over a column the store left NULL.
    let (_, plain) = served.page(&turn_url()).await;
    assert!(Markup::of(&plain).values("data-command").is_empty());
}

#[tokio::test]
async fn a_tool_call_that_spawned_a_run_leads_with_the_way_to_it() {
    // A `Task` call's body opens with a link to the run it started.
    //
    // The tool call is where a run begins, and the run is what a reader came to the call to
    // reach — so it leads the body rather than sitting under the facts. Read out of the store's
    // own spawning edge, and followed: a link to a page that does not serve is not a way there.
    let served = Served::corpus();
    let row = rows::one(
        &served.db(),
        "SELECT tc.session_id, tc.source, tc.id AS tool_id, a.id AS run_id FROM live_tool_calls tc \
         JOIN live_agent_runs a ON a.session_id = tc.session_id AND a.tool_use_id = tc.id \
         AND tc.source <> a.id \
         ORDER BY tc.session_id, tc.id LIMIT 1",
        &[],
    );
    // A fork copies the call that spawned it into its own thread; that copy spawned nothing, and
    // `tc.source <> a.id` is the rule every other query reads the edge by.
    let session_id = row.str("session_id").expect("a session id");
    let source = row.str("source").expect("a thread");
    let tool_id = row.str("tool_id").expect("a tool call id");
    let run_id = row.str("run_id").expect("a run id");
    let (status, page) = served
        .page(&format!(
            "/session/{session_id}/thread/{source}/tool/{tool_id}"
        ))
        .await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    let spawned = markup.inside("data-spawned", run_id, "href");
    assert_eq!(spawned, [format!("/session/{session_id}/run/{run_id}")]);
    assert_eq!(served.page(&spawned[0]).await.0, StatusCode::OK);
    // It leads: the link is above the tool's own facts, not under them.
    assert!(
        page.find(&format!("data-spawned=\"{run_id}\"")) < page.find("data-field=\"tool_index\""),
        "the way to the run leads the body"
    );
    // And a call that started no run says nothing about one, rather than linking nowhere.
    let quiet = rows::one(
        &served.db(),
        "SELECT tc.session_id, tc.source, tc.id FROM live_tool_calls tc \
         LEFT JOIN live_agent_runs a ON a.session_id = tc.session_id AND a.tool_use_id = tc.id \
         AND tc.source <> a.id \
         WHERE a.id IS NULL ORDER BY tc.session_id, tc.id LIMIT 1",
        &[],
    );
    let (status, bare) = served
        .page(&format!(
            "/session/{}/thread/{}/tool/{}",
            quiet.str("session_id").expect("a session id"),
            quiet.str("source").expect("a thread"),
            quiet.str("id").expect("a tool call id"),
        ))
        .await;
    assert_eq!(status, StatusCode::OK);
    assert!(Markup::of(&bare).values("data-spawned").is_empty());
}

#[tokio::test]
async fn the_same_node_url_serves_the_same_bytes_cold_and_warm() {
    // A tree click and a pasted link produce one response, byte for byte.
    //
    // The click is an `hx-get` of the node's own URL, cut down to `#reading-pane` by the browser
    // rather than by the server, so the response cannot depend on the htmx headers that came with
    // it. That is what lets one entry in the payload sweep price both ways of arriving.
    let served = Served::corpus();
    let url = turn_url();
    let current = format!("http://testserver{url}");
    let (cold_status, cold) = served.page(&url).await;
    let (warm_status, warm) = served
        .page_sent(
            &url,
            &[
                ("HX-Request", "true"),
                ("HX-Target", "pane"),
                ("HX-Current-URL", &current),
            ],
        )
        .await;
    assert_eq!(cold_status, StatusCode::OK);
    assert_eq!(warm_status, StatusCode::OK);
    assert_eq!(warm, cold);
}

#[tokio::test]
async fn the_citation_footer_scrolls_with_the_pane_it_cites() {
    // A node page's footer sits inside the reading pane, last, rather than beside it.
    //
    // The page fills the viewport: the NavTree and the pane each carry a scrollbar and the
    // document carries none, so a footer outside both would be a strip pinned under them or a line
    // below the fold of a page that does not scroll. Inside the pane it scrolls with the node it
    // cites — and a tree click, which takes `#reading-pane` out of the response, brings that
    // node's citations along instead of leaving the last node's behind.
    //
    // Containment is what is asserted rather than a class: CSS alone could stand a sibling under
    // the pane and look right, while the swap kept serving stale provenance.
    let served = Served::corpus();
    let (status, page) = served.page(&turn_url()).await;
    assert_eq!(status, StatusCode::OK);
    let ids = Markup::of(&page).inside("id", "reading-pane", "id");
    assert_eq!(ids.first().map(String::as_str), Some("reading-pane"));
    assert_eq!(ids.last().map(String::as_str), Some("citation"));
}

#[tokio::test]
async fn every_kind_renders_a_body_and_every_shape_a_log() {
    // The match over a node's kind and the one over its log's shape each answer for every member.
    //
    // The runtime half of what the checker's exhaustiveness promises: a kind added to `Kind` with
    // no arm behind it renders no body, and a shape added to `Shape` lists no rows. Read off the
    // two tables rather than a written list, so a member added later is covered here without
    // anyone remembering to add it — and swept over every page the store can serve, so what
    // answers for a member is a real render of a real node rather than a hand-built one.
    let served = Served::corpus();
    let mut bodies: BTreeSet<String> = BTreeSet::new();
    let mut logged: BTreeSet<String> = BTreeSet::new();
    for url in pages(&served.db()) {
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "GET {url}");
        // Riding along because this is the one sweep that fetches every page: no page prints a
        // debug spelling of an absent value. A cell over a column the store left NULL says so in
        // the dash `tests/app_list.rs` pins, and `None` is what a component that formatted an
        // `Option` rather than reading it would put there instead.
        assert!(!page.contains(">None<"), "GET {url}");
        let markup = Markup::of(&page);
        bodies.extend(markup.values("data-body"));
        logged.extend(markup.values("data-log"));
    }
    for kind in Kind::ALL {
        assert!(bodies.contains(kind.word()), "no body renders a {kind:?}");
    }
    // Every shape but one. `Shape::None` is the absence itself — a node with nothing under it has
    // no log — so it is the one member whose arm renders nothing, and it is checked that way
    // rather than left out.
    for shape in Shape::ALL {
        if shape == Shape::None {
            assert!(!logged.contains(shape.word()), "the absence lists rows");
        } else {
            assert!(logged.contains(shape.word()), "no log lists {shape}");
        }
    }
}

#[tokio::test]
async fn every_session_in_the_corpus_gets_a_page() {
    // The whole fixture corpus, not one hand-picked session: a kind of session the walk handles
    // and the page does not is exactly the failure this sweep is for.
    let served = Served::corpus();
    let ids = served::session_ids(&served.db());
    assert!(
        !ids.is_empty(),
        "the fixture corpus put sessions in a store"
    );
    for id in &ids {
        let (status, page) = served.page(&format!("/session/{id}")).await;
        assert_eq!(status, StatusCode::OK, "GET /session/{id}");
        // The two halves of a node page arrive in one response, which is what a click re-fetches.
        assert!(
            page.contains("id=\"nav-tree-rows\""),
            "NavTree in /session/{id}"
        );
        assert!(
            page.contains("id=\"reading-pane\""),
            "pane in /session/{id}"
        );
        // The session's own row is the one the NavTree opens on, and the pane is reading it.
        assert!(
            page.contains("aria-current=\"true\""),
            "selection in /session/{id}"
        );
    }
}
