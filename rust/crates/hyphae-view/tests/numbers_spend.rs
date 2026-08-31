//! A popover's dollars: what the node's own calls were charged, and what the runs under it spent.
//!
//! Ported from `tests/view/test_numbers__spend.py`, the other half of `numbers.rs`, reading the same
//! fetched fragment through the same helpers. A dollar is priced from tokens and then summed twice —
//! once over the node's own calls, once over every thread hanging below it — and that second sum is
//! drawn a second time by the NavTree's dual badge, so most of what these leaves do is put two
//! derivations beside each other. Where nothing hangs below a node the two breakout lines are not
//! drawn at all, and the absence is pinned here as well.

use std::collections::BTreeSet;

use hyphae_store::Param;
use hyphae_testsupport::html::{Markup, money};
use hyphae_testsupport::landmarks::{
    DENSE_TOOL, FORK_ORIGIN, FORK_ORIGIN_RUN, INVENTED_PROJECT_SESSION, MAIN, MODEL_ONLY,
    NO_TTL_SPLIT_CALL, SPINE, SPINE_LEAF, SPINE_RUN,
};
use hyphae_testsupport::nav_trees::Levels;
use hyphae_testsupport::popovers::{CHARGES, amount, charged, misread, popover, popped};
use hyphae_testsupport::served::Served;
use hyphae_testsupport::{rows, selections};
use hyphae_view::format::ABSENT;
use hyphae_view::nodes::{Kind, meter};

/// The fields of the breakout, which only a node with agent runs below it draws: the two lines and
/// the share printed on the first of them.
const BREAKOUT: [&str; 3] = ["subagent_share", "cost_subagents", "cost_total"];

#[tokio::test]
async fn a_nodes_total_spend_is_the_number_its_own_navtree_badge_already_draws() {
    // One rollup, two queries: the badge sums it in Rust and the popover sums it in SQL.
    //
    // A dual badge is summed over `view_runs`'s rows as a page is built (`view::nodes::ledger`); the
    // popover's total spend is summed inside `view_numbers` when a reader points at the row. Both
    // answer what this node and everything under it cost, and nothing but a comparison catches the
    // day they stop agreeing.
    //
    // On `spine`, where both edges the rollup walks exist: the session gathers every run, and the
    // turn that asked for the outer run gathers it and the run that one asked for in turn. Which
    // turn that is comes from the shared spawn join rather than from a pinned id.
    let served = Served::corpus();
    let hung: BTreeSet<String> = Levels::of(&served.db())
        .edges(SPINE)
        .into_iter()
        .filter(|edge| edge.spawn_source.as_deref() == Some(MAIN))
        .filter_map(|edge| edge.spawn_turn_id)
        .collect();
    assert!(
        !hung.is_empty(),
        "no run of the spine hangs on a turn of its main thread"
    );
    let (_, html) = served.page(&format!("/session/{SPINE}")).await;
    let page = Markup::of(&html);
    let mut read = vec![(
        format!("{}:{SPINE}", Kind::Session),
        format!("/session/{SPINE}"),
    )];
    read.extend(hung.iter().map(|turn_id| {
        (
            format!("{}:{turn_id}", Kind::Turn),
            format!("/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"),
        )
    }));
    for (key, path) in read {
        let printed = popover(&served, &path, &key).await;
        // Compared as the badge prints it: the popover carries four places and the badge two, and
        // the badge's is the figure a reader actually reads off the row.
        assert_eq!(
            money(amount(&printed["cost_total"])),
            page.badges(&key)["total_usd"].shown,
            "{key}"
        );
    }
}

#[tokio::test]
async fn a_node_with_no_runs_under_it_breaks_nothing_out() {
    // The breakout is drawn where there is something to break out, and nowhere else.
    //
    // A subagent line of nothing and a total repeating the figure above it are two ways of saying
    // what the node already said — and a reader who meets them on every row stops reading them.
    // Three shapes of nothing: a run that ended the chain, an api call that asked for no run, and a
    // turn of a session that spent nothing at all.
    let served = Served::corpus();
    let db = served.db();
    // An api call of the spine that spawned no run: the tool calls under it asked for none.
    let quiet = rows::one(
        &db,
        "SELECT c.id, c.source FROM live_api_calls c \
         WHERE c.session_id = $session AND c.id NOT IN ( \
           SELECT tc.api_call_id FROM live_tool_calls tc \
           JOIN live_agent_runs a ON a.session_id = tc.session_id AND a.tool_use_id = tc.id \
           WHERE tc.session_id = $session) \
         ORDER BY c.source, c.\"index\" LIMIT 1",
        &[("session", Param::from(SPINE))],
    );
    let quiet_call = quiet.str("id").expect("a call id");
    let quiet_source = quiet.str("source").expect("a thread");
    let quiet_turn = rows::one(
        &db,
        "SELECT id FROM live_turns WHERE session_id = $session LIMIT 1",
        &[("session", Param::from(MODEL_ONLY))],
    );
    let quiet_turn = quiet_turn.str("id").expect("a turn id");
    for (path, key) in [
        (
            format!("/session/{SPINE}/run/{SPINE_LEAF}"),
            format!("{}:{SPINE_LEAF}", Kind::Run),
        ),
        (
            format!("/session/{SPINE}/thread/{quiet_source}/call/{quiet_call}"),
            format!("{}:{quiet_call}", Kind::Call),
        ),
        (
            format!("/session/{MODEL_ONLY}/thread/{MAIN}/turn/{quiet_turn}"),
            format!("{}:{quiet_turn}", Kind::Turn),
        ),
    ] {
        // By the names the fields carry rather than by a string search: a template that always
        // rendered the lines and left them empty would pass any reading of the text.
        let printed = popover(&served, &path, &key).await;
        for line in BREAKOUT {
            assert!(!printed.contains_key(line), "{path} draws {line}");
        }
    }
    // And the absence is worth something only because the same corpus draws the lines: the session
    // those three nodes sit in has runs under it, and its own popover carries all three.
    let session = popover(
        &served,
        &format!("/session/{SPINE}"),
        &format!("{}:{SPINE}", Kind::Session),
    )
    .await;
    for line in BREAKOUT {
        assert!(session.contains_key(line), "the session draws no {line}");
    }
}

#[tokio::test]
async fn own_and_subagent_spend_come_to_the_total_wherever_the_breakout_is_drawn() {
    // The one arithmetic a reader does in their head, over every node that offers it.
    //
    // Two of the three numbers are read out of different sets of calls — the node's own thread, and
    // every thread hanging below it — so a run counted in neither, or in both, shows up here and
    // nowhere else. To the cent, which is the precision the badge beside them prints at.
    //
    // Swept over every popover route the corpus reaches, narrowed to the sessions that recorded an
    // agent run: a session with none has nothing to break out, and a sweep over it would measure the
    // skip.
    let served = Served::corpus();
    let db = served.db();
    let spawned: BTreeSet<String> =
        rows::all(&db, "SELECT DISTINCT session_id FROM live_agent_runs", &[])
            .iter()
            .map(|row| row.str("session_id").expect("a session id").to_owned())
            .collect();
    let mut drawn = 0;
    for (path, key) in numbered(&selections::pages(&db)) {
        let session_id = path.split('/').nth(2).expect("a session in the path");
        if !spawned.contains(session_id) {
            continue;
        }
        let printed = popover(&served, &path, &key).await;
        if BREAKOUT.iter().all(|line| !printed.contains_key(*line)) {
            continue;
        }
        drawn += 1;
        // A node whose own calls our price table could not price still gathers what the runs below
        // it spent: its own half is the dash, and nothing, and the total is theirs.
        let own = if printed["cost_usd"] == ABSENT {
            0.0
        } else {
            amount(&printed["cost_usd"])
        };
        assert_eq!(
            cents(own + amount(&printed["cost_subagents"])),
            cents(amount(&printed["cost_total"])),
            "{path}"
        );
    }
    assert!(drawn > 0, "no node of the corpus draws the breakout");
}

/// Every node URL whose popover `view_numbers` answers, beside the key it answers under.
///
/// A tool call is left out because a fragment of its own answers for it, and a compaction because no
/// row fetches numbers for one. Both are the routes `fragments` binds, read off the same list of
/// pages the rest of the sweeps walk (`hyphae_testsupport::selections::pages`).
fn numbered(urls: &[String]) -> Vec<(String, String)> {
    let mut read = Vec::new();
    for url in urls {
        let parts: Vec<&str> = url.trim_matches('/').split('/').collect();
        match parts.as_slice() {
            ["session", session_id] => {
                read.push((url.clone(), format!("{}:{session_id}", Kind::Session)));
            }
            ["session", _, "run", run_id] => {
                read.push((url.clone(), format!("{}:{run_id}", Kind::Run)));
            }
            ["session", _, "thread", _, kind @ ("turn" | "call"), node_id] => {
                read.push((url.clone(), format!("{kind}:{node_id}")));
            }
            _ => {}
        }
    }
    read
}

#[tokio::test]
async fn every_dollar_in_a_popover_is_washed_at_its_share_of_what_the_session_spent() {
    // The dollars carry the badge's own ground, so a glance reads the same scale in both places.
    //
    // `nodes::meter` by name rather than the ladder restated: the wash behind a NavTree row's badge
    // and the wash behind these four are one function of one share — what the value is of what the
    // whole session spent — and a second implementation here would agree with itself and with
    // nothing on the page.
    let served = Served::corpus();
    let whole = rows::one(
        &served.db(),
        "SELECT cost_usd FROM session_rollups WHERE session_id = $session",
        &[("session", Param::from(SPINE))],
    )
    .f64("cost_usd")
    .expect("a cost");
    let key = format!("{}:{SPINE}", Kind::Session);
    let html = popped(&served, &format!("/session/{SPINE}")).await;
    let fragment = Markup::of(&html);
    let printed = fragment.fields("data-popover", &key);
    let drawn = fragment.washes("data-popover", &key);
    // The two breakout dollars beside the four: `spine` ran subagents, so its session popover draws
    // them, and a line washed at a share of anything narrower would deepen as a reader walked down
    // the tree.
    for name in CHARGES
        .iter()
        .chain(["cost_usd", "cost_subagents", "cost_total"].iter())
    {
        assert_eq!(
            drawn[*name].split_whitespace().collect::<Vec<_>>(),
            vec!["badge", &meter(Some(amount(&printed[*name]) / whole))],
            "{name}"
        );
    }
}

#[tokio::test]
async fn the_row_that_stands_for_a_run_says_where_its_own_cost_came_from() {
    // A ⚒ row's badge is the api call that asked for the run, and its popover says so.
    //
    // A tool call is billed nothing of its own (`docs/schema.md`), so the badge on the one row that
    // draws one is an attribution rather than a measurement — and an attribution a reader cannot see
    // is a number they will read as the tool's own.
    let served = Served::corpus();
    let spawn = rows::one(
        &served.db(),
        "SELECT a.tool_use_id, t.source FROM live_agent_runs a \
         JOIN live_tool_calls t ON t.session_id = a.session_id AND t.id = a.tool_use_id \
         WHERE a.session_id = $session AND a.id = $run",
        &[
            ("session", Param::from(SPINE)),
            ("run", Param::from(SPINE_RUN)),
        ],
    );
    let spawn_tool = spawn.str("tool_use_id").expect("a tool call");
    let source = spawn.str("source").expect("a thread");
    let html = popped(
        &served,
        &format!("/session/{SPINE}/thread/{source}/tool/{spawn_tool}"),
    )
    .await;
    assert_eq!(Markup::of(&html).values("data-attribution"), ["spawn_call"]);
    // And no other tool row claims one: nothing else on the page is charged a call's cost.
    let plain = popped(
        &served,
        &format!("/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"),
    )
    .await;
    assert!(Markup::of(&plain).values("data-attribution").is_empty());
}

#[tokio::test]
async fn a_cache_write_with_no_ttl_on_it_is_charged_at_the_short_rate() {
    // A reply that reported no TTL split still pays for the cache it wrote.
    //
    // The columns say "no split reported" with NULLs rather than zeroes, so a group summing them
    // would charge that write at nothing (`tests/fixtures/invented/README.md`). The popover prices a
    // node one model-group at a time, and the group has to fall back to the whole write at the
    // 5-minute rate — the same fallback `hyphae_extract::pricing` applies to a single call.
    let served = Served::corpus();
    let narrowed = format!("AND id = '{NO_TTL_SPLIT_CALL}'");
    let call = rows::one(
        &served.db(),
        &format!(
            "SELECT cache_creation_tokens, cache_5m_tokens, cache_1h_tokens FROM live_api_calls \
             WHERE session_id = $session {narrowed}"
        ),
        &[("session", Param::from(INVENTED_PROJECT_SESSION))],
    );
    assert!(call.i64("cache_creation_tokens").expect("a count") > 0);
    for split in ["cache_5m_tokens", "cache_1h_tokens"] {
        assert!(
            call.is_null(split).expect("a column"),
            "the corpus's one untimed cache write"
        );
    }
    let printed = popover(
        &served,
        &format!("/session/{INVENTED_PROJECT_SESSION}/thread/{MAIN}/call/{NO_TTL_SPLIT_CALL}"),
        &format!("{}:{NO_TTL_SPLIT_CALL}", Kind::Call),
    )
    .await;
    let (split, _) = charged(&served.db(), INVENTED_PROJECT_SESSION, &narrowed);
    assert!(misread(&printed, &split).is_empty(), "{printed:?}");
    // And the write is a charge a reader can see rather than one that rounded away, which is what
    // makes the line above a reading of the fallback. It is charged on the new-input line, where its
    // tokens are counted, so what shows it was charged at all is that dollar standing above what the
    // call's own input came to.
    assert!(split.cache_write > 0.0);
    assert!(amount(&printed["cost_new_input"]) > split.input);
}

/// A dollar figure at the precision the badge beside it prints, which is what two sums are compared
/// at wherever the popover's own rounding stands between them.
fn cents(value: f64) -> i64 {
    (value * 100.0).round() as i64
}
