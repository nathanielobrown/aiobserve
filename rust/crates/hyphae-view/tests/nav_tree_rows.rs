//! What one row of the NavTree shows, and how the page lays the rows out.
//!
//! A row says three things at once: how deep it stands, what node it is, and what to fetch when a
//! reader clicks it. These leaves read all three back off the rendered page — the indent, the
//! links, and the totals a bucket row carries for the rows it gathers, which are its own because a
//! bucket is not a row of the store.
//!
//! Several of them read the stylesheet instead of the markup: where a row sits is decided there,
//! the served bytes are the same either way, and nothing in this tier can see a laid-out box.
//! `hyphae-view` compiles in the same `src/hyphae/view/static/` bytes the Python viewer mounts,
//! so these read one stylesheet through two servers.

use std::collections::{BTreeMap, BTreeSet};

use axum::http::StatusCode;
use duckdb::params;
use hyphae_store::Store;
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{COMPACTED, COMPACTED_RUN, MAIN, SPINE, SPINE_RUN};
use hyphae_testsupport::nav_trees::{self, Levels};
use hyphae_testsupport::served::Served;
use hyphae_view::knobs::DEPTH;
use hyphae_view::nodes::meter;
use regex::Regex;

#[tokio::test]
async fn every_link_that_swaps_the_pane_lands_the_pane_in_the_pane() {
    // The whole of what a click does, on both the mounts that mount a node link.
    //
    // A NavTree row, a children-log row and the two walk controls are how a reader moves without
    // leaving the page, and all of them do the same thing: fetch the node's URL, take
    // `#reading-pane` out of the response, put it where the pane already is, and swap the rows out
    // of band. Read as htmx composes it, inheritance and all, because that is what the browser
    // acts on.
    //
    // `hx-target` is the half that has no default worth having: htmx aims at the clicked element,
    // so a page missing it swaps the whole pane inside the `<a>` the reader clicked and leaves the
    // pane itself showing the node they came from. `hx-swap` is `outerHTML` because `hx-select`
    // hands back the `#reading-pane` element itself, not its contents.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let (_, html) = served.page(&nav_trees::url(&levels.open_turn())).await;
    let page = Markup::of(&html);
    let swap = BTreeMap::from([
        ("hx-target", "#reading-pane"),
        ("hx-swap", "outerHTML"),
        ("hx-select", "#reading-pane"),
        ("hx-select-oob", "#nav-tree-rows"),
        ("hx-push-url", "true"),
    ]);
    for mount in ["data-nav-tree", "data-child", "data-walk"] {
        // A row's other fetch is its body toggle, which opens in place and has nowhere to go:
        // the ones that move the reader are the ones fetching a node's own URL.
        let moving: Vec<_> = page
            .wired(mount)
            .into_iter()
            .filter(|(_, wiring)| nav_trees::node_link(&wiring["hx-get"]))
            .collect();
        assert!(moving.len() > 1, "{mount}");
        for (key, wiring) in moving {
            // A link fetches what it points at: one URL, however the reader gets there. A walk
            // control has no `href` to agree with — it is a button, because what it offers is a
            // move through the pane and not a place of its own to paste.
            let fetched = &wiring["hx-get"];
            assert_eq!(
                wiring.get("href").unwrap_or(fetched),
                fetched,
                "{mount} {key}"
            );
            for (name, expected) in &swap {
                assert_eq!(
                    wiring.get(*name).map(String::as_str),
                    Some(*expected),
                    "{mount} {key} {name}"
                );
            }
        }
    }
    // The two ids the swap aims at, each written exactly once.
    assert_eq!(html.matches(r#"id="reading-pane""#).count(), 1);
    assert_eq!(html.matches(r#"id="nav-tree-rows""#).count(), 1);
}

#[tokio::test]
async fn every_level_a_nav_tree_opens_is_indented_one_step_further_than_the_one_above() {
    // A row sits one step further in than its parent, however deep the session nests.
    //
    // A subagent's own turns render four levels down and its api calls deeper still, so a
    // stylesheet with a rung for the first three levels laid them flush against the session and
    // the hierarchy vanished exactly where a reader most needs it. CSS cannot read `data-depth`
    // as a number portably and the CSP forbids the inline style that would carry one, so every
    // level a chain can open is written out — and this is what keeps that ladder as long as
    // `knobs::DEPTH` says a chain can be.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    // A turn of a subagent's own thread opens the session, the turn that spawned the run, the
    // run, the turn itself and its api calls — five levels, past the three the ladder had...
    let (turn_id, source) = deep_turn(&levels);
    let (_, html) = served
        .page(&format!("/session/{SPINE}/thread/{source}/turn/{turn_id}"))
        .await;
    let rendered: BTreeSet<usize> = Markup::of(&html)
        .rows()
        .into_iter()
        .map(|(at, _)| at)
        .collect();
    assert!(
        *rendered.iter().max().expect("the page draws rows") > 3,
        "the recorded subagent no longer nests past three levels"
    );
    // ...and the stylesheet indents each of them by its own depth, in one step a level.
    let style = stylesheet(&served).await;
    let ladder: BTreeMap<usize, usize> = captured(
        r#"li\.row\[data-depth="(\d+)"\][^{]*\{[^}]*calc\((\d+) \* var\(--nav-tree-step\)\)"#,
        &style,
    );
    // Every level a chain can open has a rung, and no rung stands for a level nothing reaches.
    assert_eq!(ladder, (1..=DEPTH).map(|at| (at, at)).collect());
    assert!(
        rendered
            .iter()
            .all(|at| *at == 0 || ladder.contains_key(at))
    );
}

#[tokio::test]
async fn the_open_path_clamps_at_the_top_while_the_rows_under_it_scroll() {
    // The steps down to the selection stay on screen, stacked under the preset control.
    //
    // A working session's NavTree is longer than the column holding it, and the rows a reader
    // scrolls past are the ones saying where they are — the session, the turn that spawned the
    // run, the run. So the open path clamps: each ancestor stands where its own depth puts it,
    // one row below the step above it and the first of them below the presets, and only the
    // siblings and children scroll past them.
    //
    // Pure CSS, so the markup's whole part is one class on the rows of the open path — which is
    // what this reads — and an offset per depth written out beside the indent ladder above,
    // because the CSP forbids the inline style a computed one would ride on.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    // The same deep selection the ladder above opens: a subagent's own turn, whose path runs
    // session -> turn -> run -> the turn itself.
    let (turn_id, source) = deep_turn(&levels);
    let (_, html) = served
        .page(&format!("/session/{SPINE}/thread/{source}/turn/{turn_id}"))
        .await;
    let page = Markup::of(&html);
    // The chain the crumbs print is the open path, and everything but its last step is an
    // ancestor — the selection is what the reader is already reading.
    let chain = page.values("data-crumb");
    assert!(
        chain.len() > 2,
        "the recorded subagent no longer opens a path worth clamping"
    );
    let clamped: BTreeSet<&String> = chain[..chain.len() - 1].iter().collect();
    // Every step of the path wears the class, the selection does not, and no other row does:
    // a NavTree that clamped a sibling would stack rows the reader never opened.
    let marked: BTreeSet<String> = page
        .rows()
        .into_iter()
        .map(|(_, key)| key)
        .filter(|key| page.marked(key, "ancestor"))
        .collect();
    assert_eq!(marked.iter().collect::<BTreeSet<_>>(), clamped);
    // And each depth clamps one row further down than the one above it, under the control the
    // presets are pinned in. Written out per level, as long as a chain can be.
    let style = stylesheet(&served).await;
    let stack: BTreeMap<usize, usize> = captured(
        concat!(
            r#"li\.row\.ancestor\[data-depth="(\d+)"\][^{]*\{[^}]*"#,
            r"calc\(var\(--nav-tree-head\) \+ (\d+) \* var\(--nav-tree-row\)\)"
        ),
        &style,
    );
    assert_eq!(stack, (0..=DEPTH).map(|at| (at, at)).collect());
    assert!(matched(
        r"li\.row\.ancestor\s*\{[^}]*position: sticky",
        &style
    ));
}

#[tokio::test]
async fn a_row_reads_from_the_left_and_only_its_cost_sits_at_the_right() {
    // The parts of a row are pushed together at the left, and the spare width goes to the title.
    //
    // A row is a flex line of four parts — the kind mark, the enrichment glyph, the title and the
    // cost — and free space in a flex line goes wherever the line says to put it. Spread between
    // the parts, a short title floats out in the middle of the column with the glyph adrift ahead
    // of it, and a column of them reads as centred text; the indent that says how deep a row sits
    // then measures from a mark nothing follows. So the free width belongs to the title: it is the
    // one part that can use it, and giving it there is what keeps every other part where the
    // reader's eye already is.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let (_, html) = served.page(&nav_trees::url(&levels.open_turn())).await;
    // The row is the flex line the rule below is about, with its parts in reading order.
    let parts = Regex::new(r#"(?s)class="icon".*data-field="title".*class="secondary""#)
        .expect("a pattern");
    let rows = Regex::new(r#"(?s)<li class="row node.*?</li>"#).expect("a pattern");
    assert!(
        rows.find_iter(&html)
            .any(|row| parts.is_match(row.as_str())),
        "no row lays its parts out in reading order"
    );
    let style = stylesheet(&served).await;
    let row_rules: Vec<String> = group(r"li\.node > a \{([^}]*)\}", &style);
    assert!(row_rules.iter().any(|rule| rule.contains("display: flex")));
    // Nothing distributes the spare width between the parts — that is the centring itself.
    assert!(
        !row_rules
            .iter()
            .any(|rule| rule.contains("justify-content"))
    );
    // The title takes it instead, so the cost is what ends up against the right edge.
    let title = group(r#"li\.node \[data-field="title"\] \{([^}]*)\}"#, &style);
    assert_eq!(title.len(), 1, "one rule sizes the title");
    assert!(title[0].contains("flex: 1"));
}

#[tokio::test]
async fn the_nav_tree_keeps_its_place_because_the_scroller_is_not_what_swaps() {
    // What holds a reader's place in a long tree when a click replaces its rows.
    //
    // Nothing in the markup says "keep the scroll offset" — the NavTree keeps it because the
    // element carrying the scrollbar is `#nav-tree`, and the swap replaces `#nav-tree-rows` inside
    // it. An untouched scroller keeps its `scrollTop`, which is why the design could drop
    // `hx-preserve`. Move `overflow` down onto the rows and every click sends the reader back to
    // the top of the session, and no assertion on served HTML would notice.
    //
    // So the structure is what gets pinned: the rows the swap replaces are nested inside the
    // element the stylesheet scrolls, and nothing scrolls below it.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let (_, html) = served.page(&nav_trees::url(&levels.open_turn())).await;
    // The element the swap replaces sits inside the one the NavTree is scrolled by.
    assert!(
        Markup::of(&html)
            .inside("id", "nav-tree", "id")
            .iter()
            .any(|at| at == "nav-tree-rows")
    );
    let style = stylesheet(&served).await;
    let scrolls: BTreeSet<String> = Regex::new(r"([^{}]+)\{([^{}]*)\}")
        .expect("a pattern")
        .captures_iter(&style)
        .filter(|found| found[2].contains("overflow:"))
        .map(|found| found[1].trim().to_owned())
        .collect();
    // One of them scrolls, and it is the one the swap leaves alone. The two selectors that could
    // take the scrollbar off it are the rows themselves, under either name.
    assert!(scrolls.contains("#nav-tree"), "{scrolls:?}");
    assert!(
        !scrolls
            .iter()
            .any(|rule| rule.contains("#nav-tree-rows") || rule.contains("#nav-tree .rows")),
        "{scrolls:?}"
    );
}

#[tokio::test]
async fn the_nav_tree_is_widened_by_a_handle_and_the_width_outlives_the_page() {
    // A handle beside the NavTree drags it wider, and the browser remembers how wide.
    //
    // Every other thing a reader sets rides the URL. A width cannot: it belongs to the screen they
    // are reading on and not to the node they linked to, so a pasted link would carry someone
    // else's column. What this pins is the chain that lets a script set it instead — a handle in
    // the markup, a grid whose NavTree column is one custom property, and a script served from
    // this app, because the CSP forbids an inline one and a page load would forget a width that
    // CSS alone had kept.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let (_, html) = served.page(&nav_trees::url(&levels.open_turn())).await;
    let page = Markup::of(&html);
    // The handle sits between the two columns it divides, and says what it is to a reader who
    // cannot see it.
    let columns: Vec<String> = page
        .values("id")
        .into_iter()
        .filter(|at| matches!(at.as_str(), "nav-tree" | "nav-tree-grip" | "reading-pane"))
        .collect();
    assert_eq!(columns, ["nav-tree", "nav-tree-grip", "reading-pane"]);
    let grip = group_whole(r#"<div id="nav-tree-grip"[^>]*>"#, &html);
    assert_eq!(grip.len(), 1);
    assert!(grip[0].contains(r#"role="separator""#) && grip[0].contains(r#"tabindex="0""#));
    // The NavTree's column is one custom property, which is the whole of what the script writes:
    // a width the stylesheet fixed some other way is a handle that drags nothing.
    let style = stylesheet(&served).await;
    let tracks = group(r"#browser\s*\{[^}]*grid-template-columns:([^;]*);", &style);
    assert_eq!(tracks.len(), 1, "one grid lays the browser out");
    assert!(tracks[0].contains("var(--nav-tree-width"));
    // And the script that writes it is a file this app serves, keeping the width where a page
    // load cannot reach it.
    let sources: Vec<String> = page
        .values("src")
        .into_iter()
        .filter(|asset| asset.contains("tree-width"))
        .collect();
    assert_eq!(sources.len(), 1, "one script sizes the column");
    let (status, script) = served.page(&sources[0]).await;
    assert_eq!(status, StatusCode::OK);
    assert!(script.contains("--nav-tree-width") && script.contains("localStorage"));
    // And where the width starts when this browser remembers none: the column the stylesheet lays
    // out, read off the grid's own first track. Not the NavTree's laid-out box — under the narrow
    // layout, `#browser` is a block and the NavTree is the whole page, so a width seeded from it
    // survives into the wide layout as a column twice the one above. Witnessed in Chromium on
    // 2026-08-25: loaded at 800 px and widened to 1600, the NavTree held 768 px against the
    // stylesheet's 384 and left the pane narrower than the NavTree.
    //
    // And it reads the *first* track of that grid, which is the NavTree's: `parseFloat` takes the
    // leading number of `"384px 8px 1fr"` and stops there. A read that walked to another track
    // would seed the gap or the pane — and where the walk misses, `apply()` clamps the `NaN` it
    // yields to `NaN` and the column comes out broken. Pinned as one expression, which is as far
    // as a server-side test can follow a script this app only serves.
    assert!(matched(
        r"parseFloat\(getComputedStyle\(\w+\)\.gridTemplateColumns\)",
        &script
    ));
    assert!(!script.contains("getBoundingClientRect"));
}

#[tokio::test]
async fn a_run_row_says_how_often_its_own_thread_compacted() {
    // A run that ran its window out says so on its row; one that never did says nothing.
    //
    // The badge is the only thing on a run's row that comes off another table entirely — every
    // other value a row carries is the run's own column — so it is read back against
    // `live_compactions` in the test's own SQL rather than against what the run view computed.
    //
    // One run in the corpus is in this shape and one is the whole of the reading: `compaction/`'s
    // `general-purpose` run is the only recorded `compact_boundary` outside a `main` thread. The
    // absent half is what makes it a badge and not a field — a row with nothing to say draws no
    // pill at all — and `spine/`'s run is read for it.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let compacted = format!("run:{COMPACTED_RUN}");
    let count = compactions(&levels, COMPACTED, COMPACTED_RUN);
    assert!(count > 0, "the fixture run's own thread compacted");
    let (_, html) = served
        .page(&format!("/session/{COMPACTED}/run/{COMPACTED_RUN}"))
        .await;
    let page = Markup::of(&html);
    assert!(page.values("data-nav-tree").contains(&compacted));
    // The count alone in the labelled span, the way every other number on a row is carried: the
    // word beside it is prose the markup around the value owns.
    assert_eq!(
        page.fields("data-nav-tree", &compacted)["compactions"],
        count.to_string()
    );
    assert!(
        page.reads("data-nav-tree", &compacted)
            .contains(&format!("{count} compaction"))
    );
    // And the run whose thread never compacted carries no such field — not a zero, which would
    // draw a pill saying nothing happened.
    let spine = format!("run:{SPINE_RUN}");
    assert_eq!(compactions(&levels, SPINE, SPINE_RUN), 0);
    let (_, quiet) = served
        .page(&format!("/session/{SPINE}/run/{SPINE_RUN}"))
        .await;
    let quiet = Markup::of(&quiet);
    assert!(quiet.values("data-nav-tree").contains(&spine));
    assert!(
        !quiet
            .fields("data-nav-tree", &spine)
            .contains_key("compactions")
    );
}

#[tokio::test]
async fn a_bucket_row_carries_the_totals_of_what_it_gathers() {
    // Neither bucket is a row of the store: its numbers are sums over what it holds.
    //
    // The rest of the NavTree hands a row the store's own numbers, so a bucket is the one place
    // the viewer adds up. What it adds up is read back here — the spend, the bar that spend takes
    // against the session, and the mark saying some of the calls under it went unpriced — for
    // every bucket the corpus records, on the page the bucket hangs on.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    // The session's threads whose calls answer no turn of them, and the runs nothing placed.
    let standing = unattributed_threads(&levels);
    for (session_id, source) in &standing {
        let at = bucket_page(session_id, source);
        let (cost, unpriced) = levels.standing(session_id, source);
        let (_, html) = served.page(&at).await;
        nav_trees::weighed(
            &Markup::of(&html),
            &format!("unattributed:{source}"),
            &levels,
            session_id,
            cost,
            unpriced,
        );
    }
    let mut gathered: Option<(String, Vec<String>)> = None;
    for session_id in hyphae_testsupport::served::session_ids(&served.db()) {
        let loose: Vec<String> = levels
            .edges(&session_id)
            .into_iter()
            .filter(|edge| edge.spawn_source.is_none())
            .map(|edge| edge.run_id)
            .collect();
        if loose.is_empty() {
            continue;
        }
        // The bucket's own row is every loose run's thread at once, which is the sum the session's
        // page shows against a row that has no children of the store's to point at.
        let totals: Vec<(f64, i64)> = loose
            .iter()
            .map(|run_id| levels.thread_spend(&session_id, run_id))
            .collect();
        let (cost, unpriced) = summed(&totals);
        let (_, html) = served.page(&format!("/session/{session_id}")).await;
        nav_trees::weighed(
            &Markup::of(&html),
            &format!("unattached:{session_id}"),
            &levels,
            &session_id,
            cost,
            unpriced,
        );
        // Opening it hands its children the same basis: a run under the bucket draws its share of
        // the session, like every other run, and not a share of the bucket that gathered it.
        let whole = levels.session_spend(&session_id);
        let (_, opened) = served
            .page(&format!("/session/{session_id}/unattached"))
            .await;
        let opened = Markup::of(&opened);
        for (run_id, (spent, _)) in loose.iter().zip(&totals) {
            let drawn = &opened.badges(&format!("run:{run_id}"))["cost_usd"];
            let share = (whole != 0.0).then_some(spent / whole);
            assert!(
                drawn
                    .step
                    .split_whitespace()
                    .any(|step| step == meter(share)),
                "{run_id}: {}",
                drawn.step
            );
        }
        gathered = gathered.or(Some((session_id, loose)));
    }
    // Both buckets are read above rather than one of them: they are built by different code over
    // different rows, and only one of them can span threads.
    let (loose_at, loose_runs) = gathered.expect("the corpus records an unattached run");
    assert!(!standing.is_empty());
    // No recorded bucket holds a call our price table could not price, so the mark that says one
    // does is planted: a thread under each bucket loses its costs, and the bucket has to both
    // count what went unpriced and total what is left. The expectations read the planted store
    // through the same sums, so the plant moves the page and the oracle together.
    let (thread, source) = standing[0].clone();
    let (blank, blank_source) = (thread.clone(), source.clone());
    let (blank_at, blank_run) = (loose_at.clone(), loose_runs[0].clone());
    let marked = Served::planted(move |store: &Store| {
        let unprice = "UPDATE api_calls SET cost_usd = NULL WHERE session_id = ? AND source = ?";
        let connection = store.connection();
        connection
            .execute(unprice, params![blank, blank_source])
            .expect("the unattributed bucket's calls lose their price");
        connection
            .execute(unprice, params![blank_at, blank_run])
            .expect("the unattached run's calls lose theirs");
    });
    let planted = Levels::of(&marked.db());
    let (cost, unpriced) = planted.standing(&thread, &source);
    assert!(
        unpriced > 0,
        "the plant leaves the unattributed bucket calls to mark"
    );
    let (_, html) = marked.page(&bucket_page(&thread, &source)).await;
    nav_trees::weighed(
        &Markup::of(&html),
        &format!("unattributed:{source}"),
        &planted,
        &thread,
        cost,
        unpriced,
    );
    let totals: Vec<(f64, i64)> = loose_runs
        .iter()
        .map(|run_id| planted.thread_spend(&loose_at, run_id))
        .collect();
    let (cost, unpriced) = summed(&totals);
    assert!(
        unpriced > 0,
        "and leaves the unattached bucket calls to mark"
    );
    let (_, html) = marked.page(&format!("/session/{loose_at}")).await;
    nav_trees::weighed(
        &Markup::of(&html),
        &format!("unattached:{loose_at}"),
        &planted,
        &loose_at,
        cost,
        unpriced,
    );
    // A child of the bucket says the same thing for itself: the run whose calls the plant left
    // unpriced carries its own count, and the runs beside it carry no mark at all.
    let (_, opened) = marked
        .page(&format!("/session/{loose_at}/unattached"))
        .await;
    let opened = Markup::of(&opened);
    for (run_id, (_, missing)) in loose_runs.iter().zip(&totals) {
        let marks = opened.inside("data-nav-tree", &format!("run:{run_id}"), "title");
        assert_eq!(!marks.is_empty(), *missing != 0, "{run_id}");
        assert!(
            *missing == 0 || marks[0].contains(&missing.to_string()),
            "{run_id}: {marks:?}"
        );
    }
}

/// The first turn of a subagent's own thread in `spine/`: the deepest selection the corpus opens.
fn deep_turn(levels: &Levels) -> (String, String) {
    let rows = levels
        .store()
        .fetch(
            "SELECT id, source FROM live_turns WHERE session_id = $session_id \
             AND source <> $main ORDER BY \"index\" LIMIT 1",
            &[("session_id", SPINE.into()), ("main", MAIN.into())],
        )
        .expect("the store answers");
    let row = rows.first().expect("the recorded subagent has a turn");
    (
        row.str("id").expect("a turn id").to_owned(),
        row.str("source").expect("a thread").to_owned(),
    )
}

/// How often one thread of one session compacted, off the compaction table itself.
fn compactions(levels: &Levels, session_id: &str, source: &str) -> i64 {
    levels
        .store()
        .fetch(
            "SELECT count(*) AS n FROM live_compactions \
             WHERE session_id = $session_id AND source = $source",
            &[("session_id", session_id.into()), ("source", source.into())],
        )
        .expect("the store answers")
        .first()
        .expect("a count answers one row")
        .i64("n")
        .expect("a count")
}

/// Every thread of the corpus holding an api call that answers no turn of it.
fn unattributed_threads(levels: &Levels) -> Vec<(String, String)> {
    levels
        .store()
        .fetch(
            "SELECT DISTINCT c.session_id, c.source FROM live_api_calls c \
             LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
              AND t.id = c.turn_id \
             WHERE t.id IS NULL ORDER BY 1, 2",
            &[],
        )
        .expect("the store answers")
        .iter()
        .map(|row| {
            (
                row.str("session_id").expect("a session").to_owned(),
                row.str("source").expect("a thread").to_owned(),
            )
        })
        .collect()
}

/// Where a thread's unattributed bucket hangs: the session's page, or the run's own.
fn bucket_page(session_id: &str, source: &str) -> String {
    if source == MAIN {
        format!("/session/{session_id}")
    } else {
        format!("/session/{session_id}/run/{source}")
    }
}

fn summed(totals: &[(f64, i64)]) -> (f64, i64) {
    (
        totals.iter().map(|(cost, _)| cost).sum(),
        totals.iter().map(|(_, unpriced)| unpriced).sum(),
    )
}

/// The served stylesheet with its comments taken out, which is what these leaves read.
async fn stylesheet(served: &Served) -> String {
    let (status, style) = served.page("/static/style.css").await;
    assert_eq!(status, StatusCode::OK);
    Regex::new(r"(?s)/\*.*?\*/")
        .expect("a pattern")
        .replace_all(&style, "")
        .into_owned()
}

/// A two-group pattern read as a table of numbers.
fn captured(pattern: &str, text: &str) -> BTreeMap<usize, usize> {
    Regex::new(pattern)
        .expect("a pattern")
        .captures_iter(text)
        .map(|found| {
            (
                found[1].parse().expect("a level"),
                found[2].parse().expect("a step"),
            )
        })
        .collect()
}

/// Every first group of a one-group pattern.
fn group(pattern: &str, text: &str) -> Vec<String> {
    Regex::new(pattern)
        .expect("a pattern")
        .captures_iter(text)
        .map(|found| found[1].to_owned())
        .collect()
}

/// Every whole match of a pattern that captures nothing.
fn group_whole(pattern: &str, text: &str) -> Vec<String> {
    Regex::new(pattern)
        .expect("a pattern")
        .find_iter(text)
        .map(|found| found.as_str().to_owned())
        .collect()
}

fn matched(pattern: &str, text: &str) -> bool {
    Regex::new(pattern).expect("a pattern").is_match(text)
}
