//! What the per-level cap cuts, what the tail row it leaves behind stands for, and how deep a
//! chain the NavTree will open at all.
//!
//! The second half of `tests/view/test_nav_tree.py`, split off because the Rust port of that one
//! file runs past the repo's length budget. Where a node hangs is `nav_tree.rs`.

use std::collections::BTreeMap;

use hyphae_store::{Row, Value};

use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{MAIN, SPINE, SPINE_RUN};
use hyphae_testsupport::nav_trees::{Levels, mounts, spilled, url};
use hyphae_testsupport::served::Served;
use hyphae_view::enrichment::Descriptions;
use hyphae_view::knobs;
use hyphae_view::nav_tree::{self, Corpus};
use hyphae_view::nodes::{KIN_URL, Kind, Ledger, Ref};

#[tokio::test]
async fn the_kin_cap_cuts_the_children_but_never_the_open_path() {
    // Children are capped per level, with a row saying how many the cap left out. Driven below
    // the fixture corpus's fan-out rather than planted up to the production window
    // (`knobs::KIN`), which no recorded session comes near: the knob exists for exactly this. The
    // cap bites twice here — once on the level beside the selection, once on the calls under it —
    // and the selection survives it either way. A cut that hid the open path would leave the pane
    // describing a node the NavTree does not show.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let selection = levels.open_turn();
    let (_, page) = served.page(&format!("{}?kin=1", url(&selection))).await;
    let markup = Markup::of(&page);
    let level = levels.thread_level(SPINE, MAIN);
    let beneath = levels.turn_level(SPINE, MAIN, Some(&selection));
    assert!(
        level.len() > 2 && beneath.len() > 1,
        "the cap has to have something to cut"
    );
    // The cap admits one child, and the path through the selection takes that slot rather than
    // being kept past it: the rescue rides inside the cap. A level of `kin + 1` children is a
    // page the byte arithmetic never priced, and the sibling the reader loses is one the tail row
    // still counts and the parent's own page still lists.
    let drawn = markup.values("data-nav-tree");
    let shown: Vec<&String> = drawn.iter().filter(|key| level.contains(key)).collect();
    assert_eq!(shown, [&format!("turn:{selection}")]);
    // ...with a tail saying how many rows are off the NavTree, and no way off the page at all:
    // what it offers is a fetch for the rest of its own level, which the leaf below follows.
    let session_key = format!("session:{SPINE}");
    assert_eq!(
        markup.field("data-more", &session_key, "cut"),
        (level.len() - shown.len()).to_string()
    );
    assert_eq!(
        markup.inside("data-more", &session_key, "href"),
        [] as [String; 0]
    );
    // And the level under the selection takes the same cap, where no rescue is owed: one child of
    // the several the turn has, and a tail for the rest.
    let below: Vec<&String> = drawn.iter().filter(|key| beneath.contains(key)).collect();
    assert_eq!(below, [&beneath[0]]);
    let turn_key = format!("turn:{selection}");
    assert_eq!(
        markup.field("data-more", &turn_key, "cut"),
        (beneath.len() - 1).to_string()
    );
    // And no level on the page exceeds the cap, anywhere. The worst-page arithmetic prices
    // `DEPTH * (KIN + 1)` rows on exactly this, so it is pinned here rather than left to a
    // reading of the windowing code.
    let mut per_depth: BTreeMap<usize, usize> = BTreeMap::new();
    for (depth, _) in markup.rows() {
        *per_depth.entry(depth).or_default() += 1;
    }
    assert_eq!(per_depth.values().max(), Some(&1), "{per_depth:?}");
}

#[tokio::test]
async fn a_tail_row_stands_the_rest_of_its_level_where_it_stands() {
    // A `+N more` row opens the rest of its level in place, without moving the reader. What comes
    // back is rows and not a pane, so the row overrides every part of the swap the tree writes
    // once above it: it swaps out the row it sits in — the row, not the button inside it —
    // selects nothing, sends nothing out of band, and pushes no URL, because the reader has not
    // gone anywhere. The rows arrive at the depth the row stood at and in the level's own order,
    // which is what lets them stand in its place.
    //
    // Both halves of one split are read here — the level beside the selection, where the open
    // path holds a child inside the window wherever in the level it sits, and the level under the
    // selection, where nothing is held back. The fetch names the held child so the two halves
    // cannot both send it.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let selection = levels.open_turn();
    let (_, page) = served.page(&format!("{}?kin=1", url(&selection))).await;
    let level = levels.thread_level(SPINE, MAIN);
    let beneath = levels.turn_level(SPINE, MAIN, Some(&selection));
    let tails: BTreeMap<String, BTreeMap<String, String>> =
        Markup::of(&page).wired("data-more").into_iter().collect();
    let fetch = &tails[&format!("session:{SPINE}")];
    let below = &tails[&format!("turn:{selection}")];
    // The whole of what the row does, inheritance and all...
    assert_eq!(
        fetch,
        &BTreeMap::from([
            (
                "hx-get".to_owned(),
                format!(
                    "{KIN_URL}/session/{SPINE}/session/{SPINE}\
                     ?kin=1&thread={MAIN}&depth=1&opened=turn:{selection}"
                )
            ),
            ("hx-target".to_owned(), "closest li".to_owned()),
            ("hx-swap".to_owned(), "outerHTML".to_owned()),
            ("hx-select".to_owned(), "unset".to_owned()),
            ("hx-select-oob".to_owned(), "unset".to_owned()),
            ("hx-push-url".to_owned(), "false".to_owned()),
        ])
    );
    // ...and what it fetches is the level less the window, at the depth the row sits at: the rows
    // the reader could not see, ready to stand where the row that counted them stands.
    let (status, rest) = served.page(&fetch["hx-get"]).await;
    assert_eq!(status, axum::http::StatusCode::OK);
    let rest = Markup::of(&rest);
    let expected: Vec<(usize, String)> = level
        .iter()
        .filter(|key| **key != format!("turn:{selection}"))
        .map(|key| (1, key.clone()))
        .collect();
    assert_eq!(rest.rows(), expected);
    // Each of them reads on under the sizes the reader typed, like any row the page drew, and by
    // one URL whether it is clicked or pasted. The link, not the popover trigger beside it: a row
    // fetches twice, and only one of the two is somewhere a reader can go.
    let links: Vec<(String, BTreeMap<String, String>)> = rest
        .wired("data-nav-tree")
        .into_iter()
        .filter(|(_, wiring)| wiring.contains_key("href"))
        .collect();
    assert_eq!(links.len(), level.len() - 1);
    for (key, wiring) in &links {
        assert_eq!(wiring["href"], wiring["hx-get"], "{key}");
        assert_eq!(query_of(&wiring["hx-get"]), vec![("kin", "1")], "{key}");
    }
    // The level under the selection has no open path through it, so its tail row holds nothing
    // back and asks for everything past the window.
    assert_eq!(
        below["hx-get"],
        format!(
            "{KIN_URL}/session/{SPINE}/thread/{MAIN}/turn/{selection}?kin=1&thread={MAIN}&depth=2"
        )
    );
    let (_, under) = served.page(&below["hx-get"]).await;
    let expected: Vec<(usize, String)> = beneath[1..].iter().map(|key| (2, key.clone())).collect();
    assert_eq!(Markup::of(&under).rows(), expected);
    // The depth is the one thing a level cannot say for itself, and the NavTree's arithmetic
    // prices `DEPTH` of them: rows claiming to stand outside the NavTree a page draws are rows no
    // page ever asked for.
    for (depth, answer) in [
        (0, 400),
        (1, 200),
        (knobs::DEPTH, 200),
        (knobs::DEPTH + 1, 400),
    ] {
        let (status, _) = served
            .page(&format!(
                "{KIN_URL}/session/{SPINE}/thread/{MAIN}/turn/{selection}\
                 ?kin=1&thread={MAIN}&depth={depth}"
            ))
            .await;
        assert_eq!(status.as_u16(), answer, "depth {depth}");
    }
}

#[tokio::test]
async fn a_fetched_row_is_described_by_the_thread_the_reader_stands_on() {
    // The rest of a level arrives named the way the page names it, not the way its thread does.
    // `view_enrichment` keys turns by thread, so a page reads the descriptions of the one thread
    // the reader is on and every turn of another thread falls back to its prompt. A run page is
    // one such reader: the session's own turns are drawn above it, undescribed. The rows a tail
    // row fetches have to agree — a fetch that read the level's thread instead would serve the
    // same turns under other names, which is the one thing a row standing in another's place
    // cannot do.
    let served = Served::enriched();
    let page = format!("/session/{SPINE}/run/{SPINE_RUN}");
    // The main thread's turns as this page draws them: the level the run hangs under, whole.
    let (_, whole) = served.page(&page).await;
    let whole = Markup::of(&whole);
    let drawn = titled(&whole, Some(1));
    // The same level under a window of one, and the rows its tail row stands for.
    let (_, windowed) = served.page(&format!("{page}?kin=1")).await;
    let tails: BTreeMap<String, BTreeMap<String, String>> = Markup::of(&windowed)
        .wired("data-more")
        .into_iter()
        .collect();
    let (_, rest) = served
        .page(&tails[&format!("session:{SPINE}")]["hx-get"])
        .await;
    let fetched = titled(&Markup::of(&rest), None);
    assert!(
        !fetched.is_empty(),
        "the window left nothing out: this page no longer proves the case"
    );
    for (key, title) in &fetched {
        assert_eq!(Some(title), drawn.get(key), "{key}");
    }
    // And the claim has teeth: on its own page the main thread reads by its descriptions, so a
    // fragment that read the level's thread would have served those names instead.
    let (_, home) = served.page(&format!("/session/{SPINE}")).await;
    let described = titled(&Markup::of(&home), Some(1));
    assert!(
        fetched
            .iter()
            .any(|(key, title)| described.get(key) != Some(title)),
        "no turn is named differently on its own thread's page"
    );
}

#[test]
fn a_chain_is_resolved_to_the_depth_the_page_prices_and_no_deeper() {
    // `knobs::DEPTH` is the last chain `ancestry` resolves; one level past it refuses. The
    // response's bound is arithmetic over the depth and the per-level cap, so a deeper chain is
    // not a bigger page — it is a page whose size was never computed. Read at the boundary from
    // both sides, because a bound that refused at `DEPTH` would silently cost the deepest page
    // the arithmetic paid for. The corpus's deepest nesting is three runs, so the shape is built
    // rather than recorded: a ladder of runs, each spawned from a turn of the one above it.
    //
    // A rung is four levels now that a run hangs off the tool call that spawned it — the run, and
    // the turn, api call and tool call above it — which is what sizes the ladder here.
    const RUNG: usize = 4;
    assert_eq!(
        knobs::DEPTH % RUNG,
        0,
        "the rung arithmetic below is written for a multiple of four"
    );
    // One rung short of the bound: the ladder ends where the deepest tool call the page prices
    // stands, and the run under that tool call is what falls past it.
    let ladder: Vec<Row> = (0..knobs::DEPTH / RUNG - 1).map(rung).collect();
    let corpus = built(ladder.clone());
    // A short ladder resolves, which is what says a rung is worth four levels and not some other
    // number: one run is the session, a turn, a call, a tool call and the run itself.
    let shallow = built(ladder[..2].to_vec());
    for (step, rungs) in [("a0", 1), ("a1", 2)] {
        let chain = nav_tree::ancestry(&shallow, &[Ref::new(Kind::Run, Some(step), step)])
            .expect("a ladder inside the bound resolves");
        assert_eq!(chain.len(), 1 + rungs * RUNG, "{step}");
    }
    // Exactly `DEPTH` is served — a tool call of the deepest thread, seeded by its own page the
    // way `ancestry` takes one, standing where the run it spawned would hang...
    let deepest = format!("a{}", knobs::DEPTH / RUNG - 2);
    let tool = [
        Ref::new(Kind::Turn, Some(&deepest), "t"),
        Ref::new(Kind::Call, Some(&deepest), "c"),
        Ref::new(Kind::Tool, Some(&deepest), "u"),
    ];
    let chain = nav_tree::ancestry(&corpus, &tool).expect("the deepest priced chain resolves");
    assert_eq!(chain.len(), knobs::DEPTH);
    // ...and one rung past the ladder, which is the run that tool call spawned, is refused.
    let mut past = ladder;
    past.push(run_row("past", &deepest, "t", "c", "u"));
    let refused = nav_tree::ancestry(&built(past), &[Ref::new(Kind::Run, Some("past"), "past")])
        .expect_err("a chain past the bound is refused");
    assert!(
        refused.to_string().contains(&knobs::DEPTH.to_string()),
        "{refused}"
    );
}

#[tokio::test]
async fn a_size_above_its_ceiling_is_refused() {
    // The three sizes a node URL carries only go down — the ceiling is the production default. A
    // fragment takes the same sizes, because the knobs ride the mount that opens it: a size it
    // would not serve a page under is one it must refuse rather than mint into the fragment's own
    // links.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let at = url(&levels.open_turn());
    let (_, page) = served.page(&format!("{at}?kin=1")).await;
    let markup = Markup::of(&page);
    let opening = mounts(&markup).remove(0);
    // The rest of a level takes them as well, stripped back to the one thing it cannot answer
    // without: the sizes are what this leaf turns, and a URL carrying two of one is not a case.
    let spilling = spilled(&markup).remove(0);
    for (knob, bound) in [
        ("kin", &knobs::KIN),
        ("log", &knobs::LOG),
        ("detail", &knobs::DETAIL),
    ] {
        for (asked, fixed) in [
            (&at, ""),
            (&opening, ""),
            (&spilling, &format!("thread={MAIN}&depth=1&") as &str),
        ] {
            for (size, answer) in [(bound.ceiling + 1, 400), (bound.ceiling, 200)] {
                // The query a page minted is replaced rather than added to: what this turns is
                // one size at a time, and a URL carrying two of one is not a case.
                let bare = asked.split('?').next().expect("a split yields one piece");
                let (status, _) = served.page(&format!("{bare}?{fixed}{knob}={size}")).await;
                assert_eq!(status.as_u16(), answer, "{bare} {knob}={size}");
            }
        }
    }
}

/// Every NavTree row's title, keyed by the row — at one depth, or at every depth.
fn titled(page: &Markup, depth: Option<usize>) -> BTreeMap<String, String> {
    page.rows()
        .into_iter()
        .filter(|(at, _)| depth.is_none_or(|wanted| *at == wanted))
        .map(|(_, key)| {
            let title = page.field("data-nav-tree", &key, "title");
            (key, title)
        })
        .collect()
}

/// One URL's query, in the order it was written.
fn query_of(url: &str) -> Vec<(&str, &str)> {
    url.split_once('?')
        .map(|(_, query)| {
            query
                .split('&')
                .filter_map(|pair| pair.split_once('='))
                .collect()
        })
        .unwrap_or_default()
}

/// One rung of the ladder: a run spawned from a turn of the run above it.
fn rung(step: usize) -> Row {
    let above = if step == 0 {
        MAIN.to_owned()
    } else {
        format!("a{}", step - 1)
    };
    run_row(
        &format!("a{step}"),
        &above,
        &format!("t{step}"),
        &format!("c{step}"),
        &format!("u{step}"),
    )
}

/// One run row in the shape `view_runs` ships, which is all `ancestry` reads of one.
fn run_row(run_id: &str, source: &str, turn: &str, call: &str, tool: &str) -> Row {
    Row::new(
        [
            "run_id",
            "spawn_source",
            "spawn_turn_id",
            "spawn_call_id",
            "tool_use_id",
        ]
        .map(str::to_owned)
        .to_vec(),
        [run_id, source, turn, call, tool]
            .map(|value| Value::Text(value.to_owned()))
            .to_vec(),
    )
}

/// A corpus over one ladder of runs, with nothing spent and nothing described.
fn built(runs: Vec<Row>) -> Corpus {
    Corpus {
        session_id: SPINE.to_owned(),
        held: Ledger::default(),
        runs,
        described: Descriptions::default(),
        source: MAIN.to_owned(),
    }
}
