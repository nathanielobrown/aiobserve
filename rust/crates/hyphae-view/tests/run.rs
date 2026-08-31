//! The run node: one agent run's own thread, and where the store places it.
//!
//! A run is the one node whose id is also a `source` — its turns, its calls and its compactions
//! are written to a transcript of its own. What makes its page more than a session page at another
//! thread is placement: a run hangs where its *spawning call* sits, and the corpus records the two
//! ways that resolves to nothing. The `nav_tree_*` files own the NavTree's ordering; these leaves
//! own what is true of a run whichever tree it appears in.

use axum::http::StatusCode;

use hyphae_store::{Param, queries};
use hyphae_testsupport::html::{Markup, money};
use hyphae_testsupport::landmarks::{
    BYREF_FORK, FORK_ORIGIN, FORK_ORIGIN_RUN, FORK_RUN, NO_PROJECT_SESSION, SPINE, SPINE_LEAF,
    SPINE_RUN, TEAMMATE, TEAMMATE_RUN,
};
use hyphae_testsupport::nav_trees::{Edge, Levels};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;

/// Where one run hangs, out of the join `view_runs` makes: the api call that spawned it and the
/// turn that call answers *on the call's own thread*.
fn spawn_of(levels: &Levels, session_id: &str, run_id: &str) -> Edge {
    levels
        .edges(session_id)
        .into_iter()
        .find(|edge| edge.run_id == run_id)
        .unwrap_or_else(|| panic!("{run_id} is not a run of {session_id}"))
}

#[tokio::test]
async fn a_run_page_is_that_runs_own_thread() {
    // The children log holds the turns written to this run's transcript, and nothing else.
    //
    // The run id is substituted for the thread everywhere the session page reads `main`, so a page
    // that leaked the session's turns or the session's spend would look identical but for these
    // two numbers.
    let served = Served::corpus();
    let bound = &[
        ("session", Param::from(SPINE)),
        ("thread", Param::from(SPINE_RUN)),
    ];
    let turns: Vec<String> = rows::all(
        &served.db(),
        "SELECT id FROM live_turns WHERE session_id = $session AND source = $thread \
         ORDER BY \"index\"",
        bound,
    )
    .iter()
    .map(|row| format!("turn:{}", row.str("id").expect("a turn id")))
    .collect();
    assert!(
        !turns.is_empty(),
        "this run recorded no turns of its own: it no longer proves the case"
    );
    let (_, page) = served
        .page(&format!("/session/{SPINE}/run/{SPINE_RUN}"))
        .await;
    let markup = Markup::of(&page);
    assert_eq!(markup.values("data-child"), turns);
    // And the pane is the run's own spend, not the session's.
    let spend = rows::one(
        &served.db(),
        "SELECT count(*) AS api_calls, round(coalesce(sum(cost_usd), 0), 4) AS cost \
         FROM live_api_calls WHERE session_id = $session AND source = $thread",
        bound,
    );
    let pane = markup.fields("data-body", "run");
    assert_eq!(
        pane["api_calls"],
        spend.i64("api_calls").expect("a count").to_string()
    );
    assert_eq!(pane["cost_usd"], money(spend.f64("cost").expect("a total")));
}

#[tokio::test]
async fn a_nested_run_breadcrumbs_through_every_run_above_it() {
    // A run two levels down names the whole chain of rows that reached it.
    //
    // `SPINE_LEAF` was spawned from a turn of `SPINE_RUN`'s thread, which was itself spawned from
    // a turn of `main` — so the trail repeats, and each step is the rows a run hangs under: the
    // turn on the *spawning call's own thread*, that api call, and the tool call that asked for
    // the run. The expectation reads that join out of the store rather than pinning the ids, so a
    // re-recorded fixture moves it.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let mut trail = vec![format!("session:{SPINE}")];
    for run_id in [SPINE_RUN, SPINE_LEAF] {
        let edge = spawn_of(&levels, SPINE, run_id);
        let turn_id = edge
            .spawn_turn_id
            .unwrap_or_else(|| panic!("{run_id} no longer resolves a spawning turn"));
        let call_id = edge.spawn_call_id.expect("a spawning call");
        let tool_id = edge.spawn_tool_id.expect("a spawning tool call");
        trail.extend([
            format!("turn:{turn_id}"),
            format!("call:{call_id}"),
            format!("tool:{tool_id}"),
            format!("run:{run_id}"),
        ]);
    }
    let (_, page) = served
        .page(&format!("/session/{SPINE}/run/{SPINE_LEAF}"))
        .await;
    let markup = Markup::of(&page);
    assert_eq!(markup.values("data-crumb"), trail);
    // Every step of the trail is a link a reader can follow back up.
    assert_eq!(
        markup.inside("data-crumb", &format!("run:{SPINE_RUN}"), "href"),
        vec![format!("/session/{SPINE}/run/{SPINE_RUN}")]
    );
}

#[tokio::test]
async fn a_run_whose_spawning_call_resolves_to_nothing_is_unattached() {
    // A run the store cannot place hangs off the unattached bucket, not off `main`.
    //
    // `BYREF_FORK` forked mid-conversation: the call that spawned it lives in another session's
    // files, so the join finds no thread at all. The page is honest about it — nothing in the
    // store says the run hangs off the session's main thread, so the trail does not claim it does.
    let served = Served::corpus();
    let edge = spawn_of(&Levels::of(&served.db()), NO_PROJECT_SESSION, BYREF_FORK);
    assert_eq!(
        (edge.spawn_source, edge.spawn_turn_id),
        (None, None),
        "this fork's spawning call now resolves"
    );
    let (_, page) = served
        .page(&format!("/session/{NO_PROJECT_SESSION}/run/{BYREF_FORK}"))
        .await;
    assert_eq!(
        Markup::of(&page).values("data-crumb"),
        vec![
            format!("session:{NO_PROJECT_SESSION}"),
            format!("unattached:{NO_PROJECT_SESSION}"),
            format!("run:{BYREF_FORK}"),
        ]
    );
}

#[tokio::test]
async fn an_agent_type_leads_a_runs_title_except_where_a_column_already_heads_it() {
    // Which agent ran is the word a reader picks a run out of a list by, so it leads the title in
    // brackets — everywhere the surface has no column to align it in.
    //
    // The tree, the crumbs, the pane's heading and the tab have no such column: the type is there
    // only if the title carries it, and a tree of six runs named by their briefs alone says
    // nothing about which agent did what. The unattached bucket's children log *does* have one,
    // headed `◎ Agent`, and it reads the way the tools log reads — the name in its own narrow
    // column, what it was asked in the wide one beside it. A row that printed the type in both
    // would be saying one word twice under two headings, the second of them "Description".
    let served = Served::corpus();
    let fork = rows::one(
        &served.db(),
        "SELECT agent_type, brief FROM live_agent_runs WHERE id = $run",
        &[("run", Param::from(BYREF_FORK))],
    );
    let agent_type = fork.str("agent_type").expect("an agent type").to_owned();
    let brief = fork.str("brief").expect("a brief").to_owned();
    assert!(
        !agent_type.is_empty() && !brief.is_empty(),
        "this fork lost the two halves this leaf reads"
    );
    // The log names the agent once, in the column headed for it...
    let (_, log) = served
        .page(&format!("/session/{NO_PROJECT_SESSION}/unattached"))
        .await;
    let row = Markup::of(&log).fields("data-child", &format!("run:{BYREF_FORK}"));
    assert_eq!(row["agent_type"], agent_type);
    // ...and the wide column beside it holds what the run was asked, not that word again.
    assert_eq!(row["title"], brief);
    // The run's own page has no column for it, so every place that names the node leads with the
    // type and then says what it did.
    let (_, page) = served
        .page(&format!("/session/{NO_PROJECT_SESSION}/run/{BYREF_FORK}"))
        .await;
    let markup = Markup::of(&page);
    // The brackets are what close the lead: a bracketed type says where it ends, so the dash a
    // composed title otherwise carries would be a second mark saying the same thing.
    let led = format!("[{agent_type}] {brief}");
    let key = format!("run:{BYREF_FORK}");
    assert_eq!(markup.fields("data-body", "run")["title"], led);
    assert_eq!(markup.fields("data-crumb", &key)["run"], led);
    assert_eq!(markup.fields("data-nav-tree", &key)["title"], led);
    assert!(page.contains(&format!("<title>◎ {led} ·")));
    // And the same shape on a run whose type a reader would recognise, read off the row rather
    // than off a field: what a NavTree row prints is `[architect]` and then what it did.
    let architect = rows::one(
        &served.db(),
        "SELECT agent_type FROM live_agent_runs WHERE id = $run",
        &[("run", Param::from(TEAMMATE_RUN))],
    )
    .str("agent_type")
    .expect("an agent type")
    .to_owned();
    let (_, tree) = served
        .page(&format!("/session/{TEAMMATE}/run/{TEAMMATE_RUN}"))
        .await;
    assert!(
        Markup::of(&tree).fields("data-nav-tree", &format!("run:{TEAMMATE_RUN}"))["title"]
            .starts_with(&format!("[{architect}] "))
    );
}

#[tokio::test]
async fn a_forks_calls_under_no_turn_are_its_own_bucket() {
    // A run's api calls that answer no turn of its own thread get a node, not silence.
    //
    // `BYREF_FORK`'s first calls answer turns that live in the transcript it forked from, so they
    // belong to the run's thread and to no turn in it. That is the unattributed bucket, and it
    // hangs under the run rather than under the session.
    let served = Served::corpus();
    let calls: Vec<String> = rows::all(
        &served.db(),
        "SELECT id FROM live_api_calls WHERE session_id = $session AND source = $thread \
         AND turn_id IS NULL ORDER BY \"index\"",
        &[
            ("session", Param::from(NO_PROJECT_SESSION)),
            ("thread", Param::from(BYREF_FORK)),
        ],
    )
    .iter()
    .map(|row| format!("call:{}", row.str("id").expect("an api call id")))
    .collect();
    assert_eq!(
        calls.len(),
        2,
        "this fork's unattributed calls moved: re-pick the fixture"
    );
    let (status, page) = served
        .page(&format!(
            "/session/{NO_PROJECT_SESSION}/thread/{BYREF_FORK}/unattributed"
        ))
        .await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    assert_eq!(markup.values("data-body"), vec!["unattributed"]);
    assert_eq!(markup.values("data-child"), calls);
    // The bucket's own crumb sits under the run whose thread it stands for.
    let crumbs = markup.values("data-crumb");
    assert_eq!(
        crumbs[crumbs.len() - 2..],
        [
            format!("run:{BYREF_FORK}"),
            format!("unattributed:{BYREF_FORK}"),
        ]
    );
}

#[tokio::test]
async fn a_fork_is_never_its_own_child() {
    // A fork's transcript replays the call that spawned it, and the NavTree ignores that copy.
    //
    // `view_runs` excludes a spawning call recorded on the run's own thread (`tc.source <> a.id`).
    // Drop the exclusion and the fork resolves to a turn of its own timeline: it becomes its own
    // child, which is a tree with a cycle in it.
    let served = Served::corpus();
    let (_, page) = served
        .page(&format!("/session/{FORK_ORIGIN}/run/{FORK_RUN}"))
        .await;
    let markup = Markup::of(&page);
    // Its own page lists no child, and no row of the open tree repeats it.
    assert_eq!(markup.values("data-child"), Vec::<String>::new());
    let key = format!("run:{FORK_RUN}");
    assert_eq!(
        markup
            .values("data-nav-tree")
            .iter()
            .filter(|row| **row == key)
            .count(),
        1
    );
    // Nor does the run it forked from claim it — the exclusion leaves the edge unresolved, which
    // is what puts both in the unattached bucket.
    let crumbs = markup.values("data-crumb");
    assert_eq!(
        crumbs[crumbs.len() - 2..],
        [format!("unattached:{FORK_ORIGIN}"), key]
    );
    assert!(!crumbs.contains(&format!("run:{FORK_ORIGIN_RUN}")));
}

#[tokio::test]
async fn the_run_page_cites_the_two_queries_that_read_its_thread() {
    // The run's header and its thread are read at the run id rather than at `main`.
    //
    // That substitution is the whole difference between this page and the session page, so the
    // citations are where it has to show. The rest of the footer is the frame every node page
    // carries, which `app.rs` pins on the session.
    let served = Served::corpus();
    let (_, page) = served
        .page(&format!("/session/{SPINE}/run/{SPINE_RUN}"))
        .await;
    let citations = Markup::of(&page).fields("id", "citation");
    assert_eq!(
        citations["view_run_header"],
        format!(
            "-- queries/view_run_header.sql session_id={SPINE} run_id={SPINE_RUN} \
             head_chars={} detail_chars={}",
            queries::HEADER_CHARS,
            queries::DETAIL_CHARS
        )
    );
    assert_eq!(
        citations["run_timeline"],
        format!(
            "-- queries/run_timeline.sql session_id={SPINE} log_chars={} source={SPINE_RUN}",
            queries::LOG_CHARS
        )
    );
}
