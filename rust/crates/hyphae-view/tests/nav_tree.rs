//! The NavTree beside a node page: one open path through the session, and nothing else open.
//!
//! The port of `tests/view/test_nav_tree.py`. The NavTree is served with the pane in one
//! response, so these leaves fetch the same node URLs the node-page leaves do and read the rows
//! instead, through `hyphae_testsupport::nav_trees`. What they hold to is where a node hangs:
//! which level a page opens, which turn a compaction lands under, where a run stands.
//!
//! What a cap cuts is `nav_tree_cuts.rs`; what a row draws is `nav_tree_rows.rs`.

use std::collections::{BTreeMap, BTreeSet};

use axum::http::StatusCode;
use duckdb::params;
use hyphae_store::Store;
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{MAIN, SPINE};
use hyphae_testsupport::nav_trees::{Levels, url};
use hyphae_testsupport::served::{Served, session_ids};
use hyphae_view::nodes::{Kind, Preset};

#[tokio::test]
async fn every_sessions_own_page_opens_the_level_its_thread_holds() {
    // A session page shows the session and its main thread's children, in the design's order.
    // Swept over every session the corpus holds rather than over one, because the order has
    // four rules and no single recorded session exercises them all: turns interleaved with
    // compactions, then the thread's unattributed bucket, then the session's unattached runs.
    // Comparing the whole list in order is what catches a rule applied in the wrong place.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let mut seen = BTreeSet::new();
    for session_id in session_ids(&served.db()) {
        let (_, page) = served.page(&format!("/session/{session_id}")).await;
        let level = levels.thread_level(&session_id, MAIN);
        let mut expected = vec![format!("session:{session_id}")];
        expected.extend(levels.shut(&session_id, MAIN, &level));
        assert_eq!(
            Markup::of(&page).values("data-nav-tree"),
            expected,
            "{session_id}"
        );
        seen.extend(
            expected
                .iter()
                .map(|key| key.split(':').next().expect("a key has a kind").to_owned()),
        );
    }
    // And the sweep really did reach every rule above, so a corpus that lost its compactions or
    // its buckets would redden here rather than passing on the turns alone.
    for kind in ["compaction", "unattributed", "unattached"] {
        assert!(seen.contains(kind), "the sweep never reached a {kind} row");
    }
}

#[tokio::test]
async fn a_compaction_hangs_off_the_turn_whose_span_covers_it() {
    // Where a compaction sits, read at each instant the placement rule turns on. A compaction
    // that happened while a turn was running is a child of that turn; one that happened between
    // two turns is a sibling of them, in time order. The corpus exercises both sides but neither
    // edge: no recorded compaction has a turn of its own thread starting after it, and none
    // lands on the instant a turn starts or the instant one ends, so the NavTree would read the
    // same with the rule's boundaries deleted. A compaction is where the reader sees the context
    // being dropped, so the edges are planted — the same compaction moved to each of the three
    // instants, and read off the turn's own page, where both levels are open at once.
    //
    // The pair is picked so the plant has one answer: a turn whose start no sibling shares, and
    // whose end no turn of the thread is still running through.
    let corpus = Levels::of(&Served::corpus().db());
    let rows = corpus
        .store()
        .fetch(
            "SELECT k.session_id, k.source, k.id AS mark, t.id AS turn_id, \
               epoch_us(t.started_at) AS started, epoch_us(t.ended_at) AS ended \
             FROM live_compactions k \
             JOIN live_turns t ON t.session_id = k.session_id AND t.source = k.source \
             WHERE t.started_at IS NOT NULL AND t.ended_at IS NOT NULL \
               AND NOT EXISTS (SELECT 1 FROM live_turns o WHERE o.session_id = t.session_id \
                 AND o.source = t.source AND o.id <> t.id AND o.started_at = t.started_at) \
               AND NOT EXISTS (SELECT 1 FROM live_turns o WHERE o.session_id = t.session_id \
                 AND o.source = t.source AND t.ended_at >= o.started_at \
                 AND t.ended_at < o.ended_at) \
             ORDER BY k.session_id, k.source, t.\"index\" LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows.first().expect("the corpus records a clean pair");
    let session_id = row.str("session_id").expect("a session").to_owned();
    let source = row.str("source").expect("a thread").to_owned();
    let mark = row.str("mark").expect("a compaction").to_owned();
    let turn_id = row.str("turn_id").expect("a turn").to_owned();
    let (started, ended) = (
        row.i64("started").expect("a start"),
        row.i64("ended").expect("an end"),
    );

    for (moment, hangs, above) in [
        // The instant before the turn starts is nobody's turn, so the compaction stands beside
        // the turns and above the one that started after it...
        (started - 1_000_000, false, true),
        // ...the instant the turn starts is the turn's own, which is the edge the span closes
        // on, so the same compaction hangs off it instead...
        (started, true, false),
        // ...and the instant the turn ends belongs to whatever comes next, so it drops back
        // beside the turns, below the one it just left.
        (ended, false, false),
    ] {
        let at = mark.clone();
        let moved = Served::planted(move |store: &Store| {
            store
                .connection()
                .execute(
                    "UPDATE compactions SET timestamp = make_timestamp(?) WHERE id = ?",
                    params![moment, at],
                )
                .expect("the compaction moves");
        });
        let (_, page) = moved
            .page(&format!(
                "/session/{session_id}/thread/{source}/turn/{turn_id}"
            ))
            .await;
        let placed = Markup::of(&page).rows();
        let seat = |key: &str| {
            placed
                .iter()
                .position(|(_, drawn)| drawn == key)
                .unwrap_or_else(|| panic!("no row for {key} at {moment}"))
        };
        let (mark_at, turn_at) = (
            seat(&format!("compaction:{mark}")),
            seat(&format!("turn:{turn_id}")),
        );
        // A child of the turn is one level deeper than it; a sibling shares its depth, and its
        // side of the turn says which way the time comparison went.
        assert_eq!(
            placed[mark_at].0 == placed[turn_at].0 + 1,
            hangs,
            "{moment}"
        );
        assert_eq!(mark_at < turn_at, above, "{moment}");
    }
}

#[tokio::test]
async fn a_turn_holds_its_own_compactions_and_an_overlapped_instant_goes_to_the_later_turn() {
    // Two turns running at once, one compaction inside both and one inside only the outer. A
    // turn's level holds the compactions of that turn and no other's. Where two turns cover one
    // instant, the turn that started last holds it, because that is the one still running when
    // the context was dropped.
    //
    // Both claims need a thread with two turns owning a compaction apiece, and no recorded
    // session has one: every thread holding a compaction holds a single turn. The overlap is
    // real, though, so only the compactions are planted — two of them moved onto a thread whose
    // turns already overlap, one at an instant both cover and one at an instant only the outer
    // turn does.
    let corpus = Levels::of(&Served::corpus().db());
    let rows = corpus
        .store()
        .fetch(
            "SELECT a.id AS outer_id, epoch_us(a.started_at) AS outer_started, \
               b.id AS inner_id, epoch_us(b.started_at) AS inner_started, \
               epoch_us(b.ended_at) AS inner_ended FROM live_turns a \
             JOIN live_turns b ON b.session_id = a.session_id AND b.source = a.source \
              AND b.id <> a.id AND b.started_at > a.started_at AND b.ended_at <= a.ended_at \
              AND b.started_at < b.ended_at \
             WHERE a.session_id = $session_id AND a.source = $source \
             ORDER BY a.\"index\", b.\"index\" LIMIT 1",
            &[("session_id", SPINE.into()), ("source", MAIN.into())],
        )
        .expect("the store answers");
    let row = rows
        .first()
        .expect("the spine records two overlapping turns");
    let outer = row.str("outer_id").expect("a turn").to_owned();
    let inner = row.str("inner_id").expect("a turn").to_owned();
    let outer_started = row.i64("outer_started").expect("a start");
    let inner_started = row.i64("inner_started").expect("a start");
    let inner_ended = row.i64("inner_ended").expect("an end");
    let marks = corpus
        .store()
        .fetch("SELECT id FROM live_compactions ORDER BY id LIMIT 2", &[])
        .expect("the store answers");
    let shared = marks[0].str("id").expect("a compaction").to_owned();
    let alone = marks[1].str("id").expect("a compaction").to_owned();

    let (moved_shared, moved_alone) = (shared.clone(), alone.clone());
    let served = Served::planted(move |store: &Store| {
        let move_onto_main = |mark: &str, at: i64| {
            store
                .connection()
                .execute(
                    "UPDATE compactions SET session_id = ?, source = ?, \
                     timestamp = make_timestamp(?) WHERE id = ?",
                    params![SPINE, MAIN, at, mark],
                )
                .expect("the compaction moves");
        };
        // Inside both spans: the instant belongs to the turn that started last.
        move_onto_main(
            &moved_shared,
            inner_started + (inner_ended - inner_started) / 2,
        );
        // And inside the outer turn alone, before the inner one started.
        move_onto_main(
            &moved_alone,
            outer_started + (inner_started - outer_started) / 2,
        );
    });

    let mut pages = BTreeMap::new();
    for turn_id in [&outer, &inner] {
        let (_, page) = served
            .page(&format!("/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"))
            .await;
        pages.insert(turn_id.clone(), Markup::of(&page));
    }
    for (turn_id, expected) in [(&outer, &alone), (&inner, &shared)] {
        let held: Vec<String> = pages[turn_id]
            .kin()
            .into_iter()
            .filter(|key| key.starts_with("compaction:"))
            .collect();
        assert_eq!(held, [format!("compaction:{expected}")], "{turn_id}");
    }
    // And the page says what placed them: the query that answered which turn each compaction
    // happened during is cited, at the thread both levels of this page read it on.
    let cited = pages[&inner].fields("id", "citation");
    assert_eq!(
        cited["view_compactions"],
        format!(
            "-- queries/view_compactions.sql session_id={SPINE} source={MAIN} chip_chars={}",
            hyphae_store::queries::NAV_CHARS
        )
    );
}

#[tokio::test]
async fn the_nav_tree_opens_the_selections_path_and_leaves_the_rest_shut() {
    // One open path: the chain down to the selection, expanded, and no other subtree. The chain
    // here is the session and the turn under it, so the rows are the session, its thread's whole
    // level, and — under the selected turn alone — the calls it made. A tree that expanded a
    // sibling would show that sibling's calls too, which is the difference this reads: the rows
    // are compared as a whole list, in order, not searched for. Every row that is not the
    // selection stands whatever runs it hides, which is the one thing a shut row still draws.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let selection = levels.open_turn();
    let (_, page) = served.page(&url(&selection)).await;
    let markup = Markup::of(&page);
    let mut expected = vec![format!("session:{SPINE}")];
    for key in levels.thread_level(SPINE, MAIN) {
        let selected = key == format!("turn:{selection}");
        expected.push(key.clone());
        // The selection is the one node whose children render — every other row is a row and
        // the runs standing under it.
        if selected {
            let beneath = levels.turn_level(SPINE, MAIN, Some(&selection));
            expected.extend(levels.shut(SPINE, MAIN, &beneath));
        } else {
            expected.extend(levels.hanging(SPINE, MAIN, &key));
        }
    }
    assert_eq!(markup.values("data-nav-tree"), expected);
    // And the NavTree says which row the pane is about, once.
    assert_eq!(
        markup.values("data-selected"),
        [format!("turn:{selection}")]
    );
}

#[tokio::test]
async fn a_run_renders_under_the_tool_call_that_spawned_it() {
    // A run hangs off the tool call that asked for it, wherever the tree is read. The spawning
    // tool call is where the run came from, so it is the row the run renders under — a place a
    // reader can point at, rather than a note on the row saying which call to look for. Read on
    // the spawning api call's own page, where its tool calls are the level and the run stands
    // one deeper than the one that spawned it.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let spawn = levels
        .edges(SPINE)
        .into_iter()
        .find(|edge| edge.spawn_tool_id.is_some())
        .expect("the spine records a resolved spawn");
    let source = spawn.spawn_source.clone().expect("a resolved edge has one");
    let call = spawn
        .spawn_call_id
        .clone()
        .expect("a resolved edge has one");
    let tool = spawn.spawn_tool_id.clone().expect("the filter found one");
    let (_, page) = served
        .page(&format!("/session/{SPINE}/thread/{source}/call/{call}"))
        .await;
    let markup = Markup::of(&page);
    // The call's own level is its tool calls, and none of them is the run...
    assert_eq!(markup.kin(), levels.call_tools(SPINE, &source, &call));
    // ...which stands under the one tool call that spawned it, and under nothing else.
    let run = format!("run:{}", spawn.run_id);
    assert_eq!(
        markup.under(&format!("tool:{tool}")),
        std::slice::from_ref(&run)
    );
    let drawn = markup.rows();
    assert_eq!(drawn.iter().filter(|(_, key)| *key == run).count(), 1);
    // And the row carries the node's title and its two costs — its own thread and the subtree
    // under it — and nothing naming that call: the place is the whole of what says where the run
    // came from.
    let carried: BTreeSet<String> = markup.fields("data-nav-tree", &run).into_keys().collect();
    assert_eq!(
        carried,
        BTreeSet::from([
            "title".to_owned(),
            "cost_usd".to_owned(),
            "total_usd".to_owned()
        ])
    );
}

#[tokio::test]
async fn a_run_whose_rows_above_are_shut_stands_under_the_nearest_one_showing() {
    // A run is always visible: the same run read at three shut ancestors, in three presets. A
    // run nests under its spawning tool call, and a nesting that only rendered when every row
    // above it was open would hide a run behind a row a reader has no reason to click. So each
    // shut row stands the runs it hides, and opening one moves a run's indent rather than
    // bringing it into being.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let spawn = levels
        .edges(SPINE)
        .into_iter()
        .find(|edge| edge.spawn_turn_id.is_some())
        .expect("the spine records a run spawned under a turn");
    let source = spawn.spawn_source.clone().expect("a resolved edge has one");
    let turn = spawn.spawn_turn_id.clone().expect("the filter found one");
    let call = spawn
        .spawn_call_id
        .clone()
        .expect("a resolved edge has one");
    let tool = spawn
        .spawn_tool_id
        .clone()
        .expect("a resolved edge has one");
    let run = format!("run:{}", spawn.run_id);
    // A shut api call, on the turn's own page: the run stands under the call, with no tool row
    // between the two — the row it nests under is not on the page at all.
    let (_, page) = served
        .page(&format!("/session/{SPINE}/thread/{source}/turn/{turn}"))
        .await;
    let markup = Markup::of(&page);
    assert!(markup.under(&format!("call:{call}")).contains(&run));
    assert!(
        !markup
            .rows()
            .iter()
            .any(|(_, key)| *key == format!("tool:{tool}"))
    );
    // A shut turn, under `noapi`, where neither the call nor the tool call is a row: the run
    // stands under the turn, on the session's own page.
    let (_, folded) = served
        .page(&format!("/session/{SPINE}?nav={}", Preset::NoApi.word()))
        .await;
    assert!(
        Markup::of(&folded)
            .under(&format!("turn:{turn}"))
            .contains(&run)
    );
    // And under `agents`, where the run is the session's own child.
    let (_, agents) = served
        .page(&format!("/session/{SPINE}?nav={}", Preset::Agents.word()))
        .await;
    assert!(
        Markup::of(&agents)
            .under(&format!("session:{SPINE}"))
            .contains(&run)
    );
}

#[tokio::test]
async fn every_run_of_a_session_is_on_its_page_under_every_preset() {
    // No preset loses a run, over every session the corpus holds. The NavTree is the only way to
    // a run's page, so a rule that placed one under a row the session page never draws would
    // take that run out of the viewer. Swept rather than spotted: a run is placed by an edge
    // that resolves to any of five kinds of row, and a session page is where a reader starts.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    for preset in Preset::ALL {
        for session_id in session_ids(&served.db()) {
            let (_, page) = served
                .page(&format!("/session/{session_id}?nav={}", preset.word()))
                .await;
            let drawn: Vec<String> = Markup::of(&page)
                .rows()
                .into_iter()
                .map(|(_, key)| key)
                .filter(|key| key.starts_with("run:"))
                .collect();
            let held: BTreeSet<String> = levels
                .store()
                .fetch(
                    "SELECT id FROM live_agent_runs WHERE session_id = $session_id",
                    &[("session_id", session_id.as_str().into())],
                )
                .expect("the store answers")
                .iter()
                .map(|row| format!("run:{}", row.str("id").expect("a run id")))
                .collect();
            let seen: BTreeSet<String> = drawn.iter().cloned().collect();
            assert_eq!(seen, held, "{session_id} under {}", preset.word());
            // Each of them once: a run drawn twice is one placed by two edges at the same time.
            assert_eq!(
                drawn.len(),
                held.len(),
                "{session_id} under {}",
                preset.word()
            );
        }
    }
}

#[tokio::test]
async fn a_bucket_home_is_decided_by_the_spawning_edge() {
    // The two buckets are disjoint by one edge: which of the two is a run's home, and why. A
    // spawning call that resolves but sits under no turn of its thread puts the run in that
    // thread's *unattributed* bucket, under that call as usual. Only a run whose spawning call
    // resolves to nothing at all is *unattached*. The corpus records the second and not the
    // first, so the first is planted: one recorded run's spawning call loses its turn, and the
    // run has to move one bucket and not the other.
    let corpus = Levels::of(&Served::corpus().db());
    let spawn = corpus
        .edges(SPINE)
        .into_iter()
        .next()
        .expect("the spine records a run");
    let source = spawn.spawn_source.clone().expect("a resolved edge has one");
    let call = spawn
        .spawn_call_id
        .clone()
        .expect("a resolved edge has one");
    let run_id = spawn.run_id.clone();
    assert!(
        spawn.spawn_turn_id.is_some(),
        "the run this moves starts out under a turn"
    );
    let (loosened_source, loosened_call) = (source.clone(), call.clone());
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE api_calls SET turn_id = NULL \
                 WHERE session_id = ? AND source = ? AND id = ?",
                params![SPINE, loosened_source, loosened_call],
            )
            .expect("the call loses its turn");
    });
    let turn = spawn.spawn_turn_id.clone().expect("checked above");
    let run = format!("run:{run_id}");
    // The run is gone from the turn it used to hang under — it stands on that page under the
    // bucket instead, which is the row the moved call is now part of...
    let (_, home) = served.page(&url(&turn)).await;
    let home = Markup::of(&home);
    assert!(!home.under(&format!("turn:{turn}")).contains(&run));
    assert!(home.under(&format!("unattributed:{source}")).contains(&run));
    // ...and is in the thread's unattributed bucket, standing under its spawning call.
    let bucket_url = format!("/session/{SPINE}/thread/{source}/unattributed");
    let (status, bucket) = served.page(&bucket_url).await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        Markup::of(&bucket)
            .under(&format!("call:{call}"))
            .contains(&run)
    );
    // The unattached bucket, which is the other home, does not also hold it.
    let (_, loose) = served.page(&format!("/session/{SPINE}/unattached")).await;
    assert!(!Markup::of(&loose).values("data-nav-tree").contains(&run));
    // The run's own page agrees with the bucket that holds it. Read here because the NavTree
    // above is drawn from the bucket down while a run page is drawn from the run up: the two
    // answers come from different code, and a page that disagreed would be a crash — the trail
    // would look for the run under a bucket the session's NavTree does not hold.
    let (status, own) = served.page(&format!("/session/{SPINE}/run/{run_id}")).await;
    assert_eq!(status, StatusCode::OK);
    let planted = Levels::of(&served.db());
    let spawn_tool = planted
        .store()
        .fetch(
            "SELECT tool_use_id FROM live_agent_runs WHERE id = $run_id",
            &[("run_id", run_id.as_str().into())],
        )
        .expect("the store answers")[0]
        .str("tool_use_id")
        .expect("a spawning tool call")
        .to_owned();
    assert_eq!(
        Markup::of(&own).values("data-crumb"),
        [
            format!("session:{SPINE}"),
            format!("unattributed:{source}"),
            format!("call:{call}"),
            format!("tool:{spawn_tool}"),
            run.clone(),
        ]
    );
    // And the bucket draws it under each preset, which is the shape no recorded session has: a
    // run under a thread's bucket, with the rows between them shut or folded away.
    for preset in Preset::ALL {
        let (_, page) = served
            .page(&format!("{bucket_url}?nav={}", preset.word()))
            .await;
        let markup = Markup::of(&page);
        assert_eq!(
            markup.kin(),
            planted.cell(preset, Kind::Unattributed, SPINE, &source, &source),
            "{}",
            preset.word()
        );
        assert!(
            markup.rows().iter().any(|(_, key)| *key == run),
            "{}",
            preset.word()
        );
    }
}
