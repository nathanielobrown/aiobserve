//! A children-log row opened in place: one body, two mounts.
//!
//! The pane's log lists a child as a row with a View button, and the button fetches the same body
//! the child's own page wraps. What the wrapper adds — the crumbs, the tree, the walk, the
//! previews — is what an expansion must not, so these leaves read the two renders against each
//! other rather than against a fixture. The mount is an htmx fetch rather than a page, so the
//! last two leaves ask for it by hand: what it refuses is as much of its contract as what it
//! serves.

use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::ANCESTOR;
use hyphae_testsupport::marks::mark;
use hyphae_testsupport::selections::{KINDS, level_url, node_url};
use hyphae_testsupport::served::{self, Served};

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use axum::http::StatusCode;
use hyphae_store::Store;
use hyphae_view::columns::Shape;
use hyphae_view::labels::label;
use hyphae_view::nodes::BODY_URL;

/// What an expansion says is under the node instead of listing it: the column of the node's own
/// facts that counts them, and the word the count is read with. A kind absent from here has no
/// level under it at all — a tool call ends the NavTree.
const CHILDREN: [(&str, &str, &str); 3] = [
    ("turn", "api_calls", "calls"),
    ("call", "tool_calls", "tools"),
    ("run", "turns", "turns"),
];

/// The mount a log row's View button fetches, which is the one of its two `hx-get`s under the
/// body route: the other rides the row itself and carries no title.
fn mount(markup: &Markup, key: &str) -> String {
    let found: Vec<String> = markup
        .inside("data-child", key, "hx-get")
        .into_iter()
        .filter(|url| url.starts_with(BODY_URL))
        .collect();
    assert_eq!(found.len(), 1, "one body mount on {key}: {found:?}");
    found.into_iter().next().expect("a mount")
}

#[tokio::test]
async fn a_log_row_expands_to_the_body_its_own_page_wraps() {
    expansions_hold(Served::corpus()).await;
}

/// Run over the described store as well as the plain one: a title is the model's words where a
/// pass reached the node, and a body that read enrichment differently from the page wrapping it
/// would tell a reader two things about one node.
#[tokio::test]
async fn a_log_row_expands_the_same_way_where_a_pass_described_the_node() {
    expansions_hold(Served::enriched()).await;
}

/// The full view wraps a child's body with the crumbs above it, the log under it and prev/next
/// beside it; the expansion adds none of them, and the child's own children are a count and a link
/// rather than a second accordion. Swept over every kind of page so every shape of log row is
/// opened, because an expansion is built from the child's kind, not the parent's.
async fn expansions_hold(served: Served) {
    let mut opened: BTreeSet<String> = BTreeSet::new();
    // Every kind's own page, plus the corpus's densest session: the first session by id holds no
    // turns of its own, so without it no turn expansion is ever opened.
    let mut urls: Vec<String> = KINDS
        .iter()
        .map(|(kind, _, _)| node_url(&served.db(), kind))
        .collect();
    urls.push(format!("/session/{ANCESTOR}"));
    for url in urls {
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "GET {url}");
        let markup = Markup::of(&page);
        for key in markup.values("data-child") {
            let child = key
                .split(':')
                .next()
                .expect("a key names a kind")
                .to_owned();
            let at = mount(&markup, &key);
            let (status, body) = served.page(&at).await;
            assert_eq!(status, StatusCode::OK, "GET {at}");
            let opened_markup = Markup::of(&body);
            // The body is the one the child's own page wraps, fact for fact.
            let own = markup.inside("data-child", &key, "href");
            assert_eq!(own.len(), 1, "one link out of {key}");
            let own = &own[0];
            let (_, whole) = served.page(own).await;
            let facts = opened_markup.fields("data-body", &child);
            assert_eq!(
                facts,
                Markup::of(&whole).fields("data-body", &child),
                "{at}"
            );
            // It leads with the kind's mark, one space, and the node's own title. Read through
            // `reads` because that is the one reader here that can see the gap: `fields` strips it,
            // and a heading that ran the mark into the title would pass every other leaf.
            let titled = facts["title"]
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ");
            assert!(
                opened_markup
                    .reads("data-body", &child)
                    .starts_with(&format!("{} {titled}", mark(&child))),
                "{at}"
            );
            // And it is only the body: everything the full view wraps it in is absent.
            for wrapper in ["data-crumb", "data-nav-tree", "data-walk", "data-detail"] {
                assert!(
                    opened_markup.values(wrapper).is_empty(),
                    "{at} carries {wrapper}"
                );
            }
            // A call's expansion is the one that lists a level under it — the tools it called,
            // which is what the leaf below reads. A call that called none stands the count instead.
            let called = child == "call" && facts["tool_calls"] != "0";
            let logs = opened_markup.values("data-log");
            assert_eq!(logs, if called { vec!["tools"] } else { vec![] }, "{at}");
            // What is under the child is the way to its own page, with the count beside it wherever
            // the expansion listed nothing — and that count is the body's own.
            let link = opened_markup.inside("data-children", &child, "href");
            assert_eq!(link, [own.as_str()], "{at}");
            let counted = opened_markup.fields("data-children", &child);
            let level = CHILDREN.iter().find(|(kind, _, _)| *kind == child);
            match level {
                Some((_, column, shaped)) if !called => {
                    assert_eq!(counted["children"], facts[*column], "{at}");
                    // And it reads as one line — the count, a space, and what is being counted.
                    assert_eq!(
                        opened_markup.reads("data-children", &child),
                        format!("{} {shaped}", counted["children"]),
                        "{at}"
                    );
                }
                _ => {
                    assert!(!counted.contains_key("children"), "{at}");
                    assert_eq!(
                        opened_markup.reads("data-children", &child),
                        "its own page",
                        "{at}"
                    );
                }
            }
            opened.insert(child);
        }
    }
    // Every kind a log lists was opened: a shape the sweep never reached is a mount nothing proved
    // serves.
    assert_eq!(
        opened,
        ["call", "run", "tool", "turn"]
            .into_iter()
            .map(str::to_owned)
            .collect::<BTreeSet<_>>()
    );
}

#[tokio::test]
async fn a_call_opened_in_its_turn_lists_the_tools_it_called() {
    // An api call opened in a turn's log lists its tool calls, the way the call's own page does.
    //
    // The expansion used to say `4 tools` and stop, which is a number the row above it already
    // printed — a reader who wanted to know what the call did had to leave the turn. It now mounts
    // the log the call's own page carries, through the same builder rather than a second shape, so
    // a tool reads the same in both places.
    //
    // One level and no further. The rows carry no opener and the table drops the column that holds
    // one: an expansion that opened an expansion is the accordion of accordions the rule forbids,
    // and the way past this level is the link to the call's own page under it.
    let served = Served::corpus();
    let (url, _) = level_url(&served.db(), "turn");
    let (status, page) = served.page(&url).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // The first call on the turn that called any tools, so the expansion has rows to list.
    let key = markup
        .values("data-child")
        .into_iter()
        .find(|key| markup.fields("data-child", key)["tool_calls"] != "0")
        .expect("the turn's calls made a tool call, so an expansion can list one");
    let at = mount(&markup, &key);
    let own = markup.inside("data-child", &key, "href");
    let (status, body) = served.page(&at).await;
    assert_eq!(status, StatusCode::OK);
    let (_, whole) = served.page(&own[0]).await;
    let opened = Markup::of(&body);
    let listed = Markup::of(&whole);
    // The same tool calls the call's own page lists, in the same order, printing the same values —
    // one derivation, one shape, two mounts.
    let listed_rows = opened.values("data-child");
    assert_eq!(listed_rows, listed.values("data-child"));
    assert!(!listed_rows.is_empty(), "{at}");
    for row in &listed_rows {
        assert_eq!(
            opened.fields("data-child", row),
            listed.fields("data-child", row),
            "{row}"
        );
    }
    // Headed like the log on the page, less the column the opener lives in...
    let named: Vec<&str> = Shape::Tools
        .columns()
        .iter()
        .map(|column| column.field)
        .filter(|field| *field != "body")
        .collect();
    assert_eq!(opened.inside("data-columns", "tools", "data-column"), named);
    // ...mark, space and word alike: the gap between a column's mark and its heading is a `" "`
    // somebody wrote, so it is read back here rather than assumed.
    let headed: BTreeMap<String, String> = Shape::Tools
        .columns()
        .iter()
        .filter(|column| column.field != "body")
        .map(|column| {
            (
                column.field.to_owned(),
                format!("{} {}", column.icon, label(column.field)),
            )
        })
        .collect();
    assert_eq!(opened.headings(), headed);
    for row in &listed_rows {
        assert_eq!(
            opened.inside("data-child", row, "data-column"),
            named,
            "{row}"
        );
    }
    // ...because no row in an expansion opens another one.
    assert!(!body.contains("data-view"));
    // And the count of the level stands in the log's own heading, with the link under it left to
    // say the one thing the heading does not: where the rest of this call is.
    let count = opened.field("data-log", "tools", "children");
    assert_eq!(count, markup.fields("data-child", &key)["tool_calls"]);
    // Which reads as one line above the table: the count, a space, and the level it counts.
    assert!(
        opened
            .reads("data-log", "tools")
            .starts_with(&format!("{count} tools"))
    );
}

/// One row per kind a children log lists: the kind, and the URL its View button fetches.
fn openable(db: &Path) -> Vec<(&'static str, String)> {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let mut found = Vec::new();
    for (kind, table) in [
        ("turn", "turns"),
        ("call", "api_calls"),
        ("tool", "tool_calls"),
    ] {
        let rows = store
            .fetch(
                &format!("SELECT session_id, source, id FROM {table} ORDER BY 1, 2, 3 LIMIT 1"),
                &[],
            )
            .expect("the store answers");
        let row = rows
            .first()
            .unwrap_or_else(|| panic!("the corpus has a {kind}"));
        found.push((
            kind,
            format!(
                "/fragment/body/session/{}/thread/{}/{kind}/{}",
                row.str("session_id").expect("a session id"),
                row.str("source").expect("a thread"),
                row.str("id").expect("a node id"),
            ),
        ));
    }
    let rows = store
        .fetch(
            "SELECT session_id, id FROM agent_runs ORDER BY 1, 2 LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows.first().expect("the corpus has an agent run");
    found.push((
        "run",
        format!(
            "/fragment/body/session/{}/run/{}",
            row.str("session_id").expect("a session id"),
            row.str("id").expect("a run id"),
        ),
    ));
    found
}

#[tokio::test]
async fn every_kind_a_log_lists_opens_a_body_that_opens_nothing_further() {
    let served = Served::corpus();
    for (kind, url) in openable(&served.db()) {
        let (status, fragment) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "GET {url}");
        // The body arrives as a row of the log it was opened in, marked with its own kind...
        assert!(
            fragment.contains(&format!("data-expansion=\"{kind}\"")),
            "{url} mounts a {kind} body"
        );
        // ...carrying the way to the node's own page, which is where a reader goes for more...
        assert!(fragment.contains("class=\"children\""), "{url} links out");
        // ...and no View button anywhere inside it: an expansion opens no expansion.
        assert!(!fragment.contains("data-view="), "{url} opens another body");
        // What it ran is cited on the fragment itself, the way a page's footer cites.
        assert!(
            fragment.contains("class=\"citations\""),
            "{url} cites its queries"
        );
    }
}

#[tokio::test]
async fn a_body_is_asked_for_by_a_kind_and_an_id_the_store_holds() {
    let served = Served::corpus();
    let (session_id, _) = served::busiest_session(&served.db());
    // A kind no children log lists — the session's own body is the page, not an expansion.
    for kind in ["session", "compaction", "nonesuch"] {
        let url = format!("/fragment/body/session/{session_id}/thread/main/{kind}/whatever");
        assert_eq!(
            served.page(&url).await.0,
            StatusCode::NOT_FOUND,
            "GET {url}"
        );
    }
    // A kind that is listed, with an id the store does not hold.
    let url = format!("/fragment/body/session/{session_id}/thread/main/turn/not-a-turn");
    assert_eq!(
        served.page(&url).await.0,
        StatusCode::NOT_FOUND,
        "GET {url}"
    );
    let url = format!("/fragment/body/session/{session_id}/run/not-a-run");
    assert_eq!(
        served.page(&url).await.0,
        StatusCode::NOT_FOUND,
        "GET {url}"
    );
}
