//! The line above the reading pane: the way out of the session, and how much of a title fits.
//!
//! A crumb is a place to click rather than a place to read, so it carries less of a title than
//! every other surface that names the same node. The two steps above the session — home, and the
//! project — are what make a node three spawns deep reachable from anywhere else.

use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{FORK_ORIGIN, HOME, MAIN, MYCELIA, NO_PROJECT_SESSION, SPINE};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;

use axum::http::StatusCode;
use hyphae_store::Store;
use hyphae_store::queries::{CRUMB_CHARS, NAV_CHARS};
use hyphae_view::format::ELLIPSIS;

/// Who is reading, pinned: the fold is against the reader's own home, and the corpus was recorded
/// under one that only exists on the machine that recorded it. `format::home` reads the
/// environment per request, and nextest runs a process per test, so this is the whole of the
/// monkeypatch `tests/view/test_node.py` makes.
fn read_as_the_recorder() {
    // SAFETY: nextest gives each test its own process, so nothing else is reading the environment
    // while this runs.
    unsafe { std::env::set_var("HOME", HOME) };
}

#[tokio::test]
async fn the_crumb_chain_leads_with_the_way_back_out_of_the_session() {
    // Above the session crumb sit the two places a reader came from: home, and the project.
    //
    // Every node page hangs under one project's session list, and until now the chain started at
    // the session — a reader three spawns deep had a way up to the session and no way out of it.
    // The two steps are links rather than text: the first is the whole list, the second is that
    // list narrowed to this project.
    //
    // The project's parameter is read off the served filter form rather than written out here. The
    // form is what the list itself binds, so a link minted against a name this file made up would
    // open a list filtered by nothing and still read as a link.
    read_as_the_recorder();
    let served = Served::corpus();
    let (status, page) = served.page(&format!("/session/{SPINE}")).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // The first step is the list itself, under the house that stands for it.
    assert_eq!(
        markup.inside("data-crumb-head", "home", "href"),
        ["/sessions"]
    );
    assert_eq!(markup.icons("data-crumb-head", "home"), ["🏠"]);
    // The second is the project, printed the way every other surface prints a path: folded against
    // the reader's own home directory, so the part that varies leads.
    assert_eq!(
        markup.field("data-crumb-head", "project", "project_dir"),
        "~/repos/mycelia"
    );
    let links = markup.inside("data-crumb-head", "project", "href");
    assert_eq!(links.len(), 1, "one way to the project's list");
    // And it opens the session list narrowed to this project, by the name the form declares.
    let (_, form) = served.page("/sessions").await;
    let named: Vec<String> = Markup::of(&form)
        .values("data-filter")
        .into_iter()
        .filter(|name| name == "project" || name == "project_dir")
        .collect();
    assert_eq!(
        named.len(),
        1,
        "the form binds one project filter: {named:?}"
    );
    let (path, query) = links[0]
        .split_once('?')
        .expect("a narrowed list is a query");
    assert_eq!(path, "/sessions");
    let asked: Vec<&str> = query
        .split('&')
        .filter_map(|pair| pair.strip_prefix(&format!("{}=", named[0])))
        .collect();
    assert_eq!(asked.len(), 1, "the project is named once: {query}");
    assert_eq!(decoded(asked[0]), MYCELIA);
    // The session the page is about still leads the chain of nodes under those two.
    assert_eq!(markup.values("data-crumb")[0], format!("session:{SPINE}"));
}

/// One query-string value, its percent escapes undone — a project path is full of `/`.
fn decoded(value: &str) -> String {
    let bytes = value.replace('+', " ").into_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut at = 0;
    while at < bytes.len() {
        if bytes[at] == b'%' && at + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[at + 1..at + 3]).expect("two hex digits");
            out.push(u8::from_str_radix(hex, 16).expect("an escape is hexadecimal"));
            at += 3;
        } else {
            out.push(bytes[at]);
            at += 1;
        }
    }
    String::from_utf8(out).expect("a decoded path is UTF-8")
}

#[tokio::test]
async fn a_session_with_no_project_still_leads_with_the_way_home() {
    // A session the store recorded no directory for has no project list to open. So the project
    // step is absent rather than a link to a list filtered by nothing — which would open the whole
    // corpus under a crumb claiming to narrow it.
    let served = Served::corpus();
    let (status, page) = served.page(&format!("/session/{NO_PROJECT_SESSION}")).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    assert_eq!(
        markup.inside("data-crumb-head", "home", "href"),
        ["/sessions"]
    );
    assert!(
        !markup
            .values("data-crumb-head")
            .contains(&"project".to_owned())
    );
}

#[tokio::test]
async fn a_crumb_is_cut_narrower_than_every_other_place_a_title_is_read() {
    // The chain is up to sixteen links on one line above the pane; the node it ends at is open
    // underneath. Everything else that names a node on this page — the walk controls stepping
    // along its level, the stepper to the next failure, the browser tab — is a line of its own and
    // keeps a NavTree row's width.
    let corpus = Served::corpus();
    // A turn with a sibling on either side, so the walk has two controls to name and neither of
    // them climbs out of the level to a node the plant did not reach.
    let row = rows::one(
        &corpus.db(),
        "SELECT session_id, id FROM live_turns WHERE source = $source AND \"index\" = 1 \
         AND session_id IN (SELECT session_id FROM live_turns WHERE source = $source \
         GROUP BY 1 HAVING count(*) > 2) ORDER BY session_id LIMIT 1",
        &[("source", MAIN.into())],
    );
    let session_id = row.str("session_id").expect("a session id").to_owned();
    let turn_id = row.str("id").expect("a turn id").to_owned();
    // The whole thread's turns, so the two the walk names are as long as the one it is about. The
    // slash columns go with them: a turn that ran a command is titled by the command.
    let long = "x".repeat(NAV_CHARS + 60);
    let at = format!("/session/{session_id}/thread/{MAIN}/turn/{turn_id}");
    let planted = {
        let (long, session) = (long.clone(), session_id.clone());
        Served::planted(move |store: &Store| {
            store
                .connection()
                .execute(
                    "UPDATE turns SET prompt = ?, command_name = NULL, command_args = NULL \
                     WHERE session_id = ?",
                    duckdb::params![long, session],
                )
                .expect("the prompt is plantable");
        })
    };
    let (status, page) = planted.page(&at).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // The crumb stops at the crumb's own width, marked where it stopped.
    let ended = markup
        .values("data-crumb")
        .pop()
        .expect("a chain ends somewhere");
    let crumb = markup.field("data-crumb", &ended, "turn");
    assert_eq!(crumb, format!("{}{ELLIPSIS}", "x".repeat(CRUMB_CHARS)));
    // While the walk along the turn's own level, and the tab, still carry a row's width.
    let walked: Vec<String> = markup
        .values("data-walk")
        .iter()
        .map(|where_| markup.field("data-walk", where_, "title"))
        .collect();
    let row_wide = format!("{}{ELLIPSIS}", "x".repeat(NAV_CHARS));
    assert_eq!(walked.len(), 2, "a turn between two names both");
    assert!(walked.iter().all(|title| *title == row_wide), "{walked:?}");
    assert!(page.contains(&format!("<title>❯ {row_wide} · hyphae</title>")));

    // The error stepper is the fourth of them, and it steps between failures rather than along a
    // level — so it is read on a page that stands on one. Every tool call of a recorded session is
    // failed and given a path too long for any width, which puts a failure on either side of the
    // one the pane reads. The glyph the registry leads a `Read` with is spent out of both widths,
    // so neither string is a bare run of `x`.
    let input = serde_json::json!({ "file_path": long }).to_string();
    let stepped = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET is_error = true, name = 'Read', input = ? \
                 WHERE session_id = ?",
                duckdb::params![input, FORK_ORIGIN],
            )
            .expect("the tool calls are plantable");
    });
    let failures = rows::all(
        &corpus.db(),
        "SELECT source, id FROM live_tool_calls WHERE session_id = $session_id \
         ORDER BY source, \"index\"",
        &[("session_id", FORK_ORIGIN.into())],
    );
    let mut middle = None;
    for failure in &failures {
        let (_, page) = stepped
            .page(&format!(
                "/session/{FORK_ORIGIN}/thread/{}/tool/{}",
                failure.str("source").expect("a thread"),
                failure.str("id").expect("a tool call id"),
            ))
            .await;
        let steps = Markup::of(&page).values("data-step");
        // A failure with one on either side of it, so the stepper names two.
        if steps.iter().any(|at| at == "previous") && steps.iter().any(|at| at == "next") {
            middle = Some(page);
            break;
        }
    }
    let middle = Markup::of(middle.as_deref().expect("a failure between two failures"));
    let read = format!("📖 {}{ELLIPSIS}", "x".repeat(NAV_CHARS - 2));
    assert_eq!(
        ["previous", "next"].map(|at| middle.field("data-step", at, "title")),
        [read.clone(), read]
    );
    // While the crumb naming the same node on the same page stops seventy characters earlier.
    let ended = middle
        .values("data-crumb")
        .pop()
        .expect("a chain ends somewhere");
    assert_eq!(
        middle.field("data-crumb", &ended, "tool"),
        format!("📖 {}{ELLIPSIS}", "x".repeat(CRUMB_CHARS - 2))
    );

    // Every one of those widths is spent on what a reader sees rather than on what the line was
    // written in. The same turns once more, with a bolded run three crumbs wide leading a tail of
    // plain text: the crumb stops on the character it stopped on above, four asterisks earlier
    // than a width counting them would have — and closes what it cut inside, because an unclosed
    // `<strong>` bolds the rest of the page.
    let bold = "x".repeat(CRUMB_CHARS + 20);
    let styled = {
        let (prompt, session) = (
            format!("**{bold}** {}", "y".repeat(NAV_CHARS)),
            session_id.clone(),
        );
        Served::planted(move |store: &Store| {
            store
                .connection()
                .execute(
                    "UPDATE turns SET prompt = ?, command_name = NULL, command_args = NULL \
                     WHERE session_id = ?",
                    duckdb::params![prompt, session],
                )
                .expect("the prompt is plantable");
        })
    };
    let (_, bolded) = styled.page(&at).await;
    let bolded = Markup::of(&bolded);
    let ended = bolded
        .values("data-crumb")
        .pop()
        .expect("a chain ends somewhere");
    assert_eq!(bolded.field("data-crumb", &ended, "turn"), crumb);
    assert_eq!(
        bolded.marked_up("data-crumb", &ended, "turn"),
        format!("<strong>{}</strong>{ELLIPSIS}", "x".repeat(CRUMB_CHARS))
    );
    // And the tab, which has nowhere to put markup, says the same words without any of it.
    assert!(bolded.served().contains(&format!("<title>❯ {bold} y")));
}
