//! The cost badge a NavTree row draws: what the node spent, and what its subtree did.
//!
//! Read back off the rendered row rather than computed beside it — a badge is a step per decade of
//! the session's spend, so what deepens a wash is the money and not arithmetic written twice. The
//! other meter a row draws is the context bar (`nav_tree_bars.rs`). What the store holds behind
//! each priced row is read in `nav_tree_spend.rs`.

use std::collections::{BTreeMap, BTreeSet};

use duckdb::params;
use hyphae_store::Store;
use hyphae_testsupport::html::{Badge, Markup, money};
use hyphae_testsupport::landmarks::{SPINE, SPINE_LEAF, SPINE_RUN};
use hyphae_testsupport::nav_trees::{self, Levels};
use hyphae_testsupport::served::{self, Served};
use hyphae_view::nodes::{Kind, Preset, STEPS, meter};
use regex::Regex;

#[tokio::test]
async fn a_row_badges_its_cost_only_where_it_has_a_share_to_draw() {
    // A badge is a wash behind a dollar value, and a row draws one of them or two.
    //
    // The second half is what the whole subtree under the row cost, so it is drawn only where a
    // run hangs below — a turn that spawned none, an api call, a session with no agent in it all
    // print the one number they always printed. Rows that cost nothing of their own — a plain tool
    // call, a compaction — carry no badge rather than an empty one, because a wash drawn at zero
    // reads as a measurement.
    let served = Served::corpus();
    let mut paired = 0;
    // Swept over every session under every preset rather than over the deepest session alone. The
    // rows that take their own share — the buckets, which are not rows of the store — are gathered
    // by a different builder under each preset, and are not all on one session's page.
    for session_id in served::session_ids(&served.db()) {
        let mut drawn: BTreeMap<String, BTreeMap<String, Badge>> = BTreeMap::new();
        for preset in Preset::ALL {
            let (_, html) = served
                .page(&format!("/session/{session_id}?nav={}", preset.word()))
                .await;
            let page = Markup::of(&html);
            for key in page.values("data-nav-tree") {
                let pair = page.badges(&key);
                // A row draws its own value, or its own and the subtree's. Never the second alone:
                // a total nothing is measured against says nothing.
                let halves: Vec<&str> = pair.keys().map(String::as_str).collect();
                assert!(
                    matches!(
                        halves.as_slice(),
                        [] | ["cost_usd"] | ["cost_usd", "total_usd"]
                    ),
                    "{key}: {halves:?}"
                );
                // The step rides on the value it washes and no longer on the row, because a row
                // draws two of them and one class cannot say two depths.
                let row_classes = page.inside("data-nav-tree", &key, "class");
                assert!(steps(&row_classes[0]).is_empty(), "{key}");
                for (name, half) in &pair {
                    // Exactly one, always: a half wearing none is drawn flat whatever it cost.
                    assert_eq!(steps(&half.step).len(), 1, "{key} {name}");
                }
                if let Some(total) = pair.get("total_usd") {
                    paired += 1;
                    // The invariant the rollup lives or dies by. A subtree holds the node itself,
                    // so a total under the node's own is a run counted somewhere it does not hang,
                    // or an own counted twice.
                    assert!(read(total) >= read(&pair["cost_usd"]), "{key}");
                }
                // And a preset decides which rows are drawn, never what one of them spent or how
                // much of the session that was: a badge that moved between presets is a share
                // taken against something other than the session.
                assert_eq!(
                    drawn.entry(key.clone()).or_insert(pair.clone()),
                    &pair,
                    "{key} {preset:?}"
                );
            }
        }
    }
    // Bounds the sweep: a corpus whose rows all drew one number would agree with a viewer that had
    // never learned the second.
    assert!(paired > 0, "some row of the corpus draws both halves");
}

#[tokio::test]
async fn a_dual_badge_gathers_under_a_row_every_run_it_spawned() {
    // What each half is worth, on the one session whose runs nest two deep.
    //
    // `spine` spawned a run from its main thread and that run spawned another, so every edge the
    // rollup walks meets in one session: a turn gathers the runs its tool calls asked for, a run
    // gathers the runs it asked for in turn, the session gathers all of them, and the tool row that
    // did the asking is charged what the api call holding it cost. The expectation is summed per
    // thread in the test's own SQL, so a derivation that drifted in the run view has nothing here
    // to agree with.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let threads: BTreeMap<String, f64> = levels
        .store()
        .fetch(
            "SELECT source, round(sum(cost_usd), 4) AS spent FROM live_api_calls \
             WHERE session_id = $session_id GROUP BY source",
            &[("session_id", SPINE.into())],
        )
        .expect("the store answers")
        .iter()
        .map(|row| {
            (
                row.str("source").expect("a thread").to_owned(),
                row.f64("spent").expect("a total"),
            )
        })
        .collect();
    let whole = levels.session_spend(SPINE);
    // Nothing hangs under the leaf run, so it is worth its own thread and draws one number.
    let leaf = threads[SPINE_LEAF];
    // The run above it is worth its own thread and the leaf's, and the session's own half is what
    // is left when every run is taken out of it: its main thread.
    //
    // Both are put back at the four decimals the store hands a cost out at, the way the viewer
    // puts its own sums back: without it a main thread that spent nothing carries a float residue,
    // and a residue is a share, and a share is a wash.
    let spawner = rounded(threads[SPINE_RUN] + leaf);
    let main = rounded(whole - threads[SPINE_RUN] - leaf);
    // Where each run was asked for: the thread, the tool call, and the turn that call answers.
    let spawns: BTreeMap<String, (String, String, String, f64)> = levels
        .store()
        .fetch(
            "SELECT a.id AS run_id, tc.source, tc.id AS tool_id, c.turn_id, \
             round(c.cost_usd, 4) AS spent FROM live_agent_runs a \
             JOIN live_tool_calls tc ON tc.session_id = a.session_id AND tc.id = a.tool_use_id \
              AND tc.source <> a.id \
             JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source \
              AND c.id = tc.api_call_id \
             WHERE a.session_id = $session_id",
            &[("session_id", SPINE.into())],
        )
        .expect("the store answers")
        .iter()
        .map(|row| {
            (
                row.str("run_id").expect("a run").to_owned(),
                (
                    row.str("source").expect("a thread").to_owned(),
                    row.str("tool_id").expect("a tool call").to_owned(),
                    row.str("turn_id").expect("a turn").to_owned(),
                    row.f64("spent").expect("what the call cost"),
                ),
            )
        })
        .collect();
    // The session reads its main thread over the whole of what it spent.
    let (_, session) = served.page(&format!("/session/{SPINE}")).await;
    let session = Markup::of(&session);
    weighs(
        &session,
        &format!("session:{SPINE}"),
        main,
        Some(whole),
        whole,
    );
    // And the turn that asked for the outer run reads its own calls over those plus the run.
    let (source, tool_id, turn_id, call_cost) = spawns[SPINE_RUN].clone();
    let turn_own = turn_spend(&levels, &source, &turn_id);
    weighs(
        &session,
        &format!("turn:{turn_id}"),
        turn_own,
        Some(turn_own + spawner),
        whole,
    );
    // Each tool row is charged what the api call holding it cost: a tool call has no spend of its
    // own, and the call that asked for the run is the nearest thing the store prices.
    let (_, outer) = served
        .page(&nav_trees::node_url(Kind::Tool, SPINE, &source, &tool_id))
        .await;
    let outer = Markup::of(&outer);
    weighs(
        &outer,
        &format!("tool:{tool_id}"),
        call_cost,
        Some(call_cost + spawner),
        whole,
    );
    weighs(
        &outer,
        &format!("run:{SPINE_RUN}"),
        threads[SPINE_RUN],
        Some(spawner),
        whole,
    );
    // One level down, where the leaf run ends the chain with a single number.
    let (deep_source, deep_tool, _, deep_cost) = spawns[SPINE_LEAF].clone();
    let (_, inner) = served
        .page(&nav_trees::node_url(
            Kind::Tool,
            SPINE,
            &deep_source,
            &deep_tool,
        ))
        .await;
    let inner = Markup::of(&inner);
    weighs(
        &inner,
        &format!("tool:{deep_tool}"),
        deep_cost,
        Some(deep_cost + leaf),
        whole,
    );
    weighs(&inner, &format!("run:{SPINE_LEAF}"), leaf, None, whole);
    // Every other tool call on those two pages is what it always was: no spend of its own, no badge
    // at all. `Bash`, `Read`, and the tool row whose run the recording did not keep.
    let asked = BTreeSet::from([tool_id, deep_tool]);
    let mut costless = 0;
    for page in [&outer, &inner] {
        for key in page.values("data-nav-tree") {
            if let Some(id) = key.strip_prefix("tool:")
                && !asked.contains(id)
            {
                assert!(page.badges(&key).is_empty(), "{key}");
                costless += 1;
            }
        }
    }
    assert!(
        costless > 0,
        "those pages hold tool rows that asked for nothing"
    );
}

#[tokio::test]
async fn two_agent_rows_in_one_call_each_claim_the_whole_of_what_it_cost() {
    // The overcount the design accepted, pinned so a later fix has to change this test to land.
    //
    // One api call can ask for several runs at once, and nothing the transcript records splits what
    // the call cost between them — so each tool row under it is charged the whole of that cost and
    // the level sums past the call that made it. Badges are a reading aid, and a reader following
    // one row down is better served by the call's own number than by a share of it nothing
    // measured.
    //
    // INVENTED arrangement of recorded rows: `spine` records the shape one run short — the second
    // Agent tool call of one of its api calls spawned nothing the recording kept — so the leaf run
    // is cloned onto that tool call, its api calls with it. Every token count, model and cost under
    // the clone is the transcript's.
    let corpus = Served::corpus();
    let levels = Levels::of(&corpus.db());
    let spawn_tool = one_str(
        &levels,
        "SELECT tool_use_id AS at FROM live_agent_runs \
         WHERE session_id = $session_id AND id = $run_id",
        &[("session_id", SPINE.into()), ("run_id", SPINE_LEAF.into())],
    );
    let holding = levels
        .store()
        .fetch(
            "SELECT source, api_call_id FROM live_tool_calls \
             WHERE session_id = $session_id AND id = $tool_id",
            &[
                ("session_id", SPINE.into()),
                ("tool_id", spawn_tool.as_str().into()),
            ],
        )
        .expect("the store answers");
    let holding = holding.first().expect("the spawning tool call is recorded");
    let source = holding.str("source").expect("a thread").to_owned();
    let call_id = holding.str("api_call_id").expect("its api call").to_owned();
    // The sibling tool row: the same api call asked for it and the recording kept no run under it.
    let sibling = one_str(
        &levels,
        "SELECT id AS at FROM live_tool_calls WHERE session_id = $session_id \
         AND source = $source AND api_call_id = $call_id AND name = 'Agent' AND id <> $tool_id",
        &[
            ("session_id", SPINE.into()),
            ("source", source.as_str().into()),
            ("call_id", call_id.as_str().into()),
            ("tool_id", spawn_tool.as_str().into()),
        ],
    );
    const TWIN: &str = "atwin0000000000000";
    let onto = sibling.clone();
    let served = Served::planted(move |store: &Store| {
        let connection = store.connection();
        connection
            .execute(
                "INSERT INTO agent_runs (SELECT * REPLACE (? AS id, ? AS tool_use_id) \
                 FROM agent_runs WHERE session_id = ? AND id = ?)",
                params![TWIN, onto, SPINE, SPINE_LEAF],
            )
            .expect("the twin run lands");
        connection
            .execute(
                "INSERT INTO api_calls (SELECT * REPLACE (? AS source, id || '-twin' AS id) \
                 FROM api_calls WHERE session_id = ? AND source = ?)",
                params![TWIN, SPINE, SPINE_LEAF],
            )
            .expect("its api calls land with it");
    });
    let twinned = Levels::of(&served.db());
    let call_cost = one_f64(
        &twinned,
        "SELECT round(cost_usd, 4) AS at FROM live_api_calls \
         WHERE session_id = $session_id AND id = $call_id",
        &[
            ("session_id", SPINE.into()),
            ("call_id", call_id.as_str().into()),
        ],
    );
    // The clone is the leaf run's api calls under another source, so the two runs cost the same.
    let run_cost = one_f64(
        &twinned,
        "SELECT coalesce(round(sum(cost_usd), 4), 0) AS at FROM live_api_calls \
         WHERE session_id = $session_id AND source = $source",
        &[("session_id", SPINE.into()), ("source", SPINE_LEAF.into())],
    );
    let (_, page) = served
        .page(&nav_trees::node_url(Kind::Call, SPINE, &source, &call_id))
        .await;
    let page = Markup::of(&page);
    let drawn: Vec<BTreeMap<String, Badge>> = [&spawn_tool, &sibling]
        .into_iter()
        .map(|tool| page.badges(&format!("tool:{tool}")))
        .collect();
    let called = page.badges(&format!("call:{call_id}"));
    // Both tool rows claim the whole of what the call cost, each gathering its own run.
    for row in &drawn {
        assert_eq!(row["cost_usd"].shown, money(call_cost));
        assert_eq!(row["total_usd"].shown, money(call_cost + run_cost));
    }
    // So the level sums past the row it hangs under: the call was billed once, and the two halves
    // under it say it twice. The call itself stays honest — its own is what it cost and its total
    // counts each run once.
    let claimed: f64 = drawn.iter().map(|row| read(&row["cost_usd"])).sum();
    assert!(claimed > read(&called["cost_usd"]));
    assert_eq!(called["cost_usd"].shown, money(call_cost));
    assert_eq!(called["total_usd"].shown, money(call_cost + 2.0 * run_cost));
}

#[tokio::test]
async fn a_cost_badge_steps_by_decade_so_three_orders_of_magnitude_deepen_it() {
    // Which step a share is drawn at, read at the top of the scale, the bottom, and between.
    //
    // A recorded session's rows do not span the scale, so the ladder itself — ten steps over three
    // decades of share — is a rule no fixture exercises: every badge could be drawn a step too deep
    // and the corpus would agree. The shares are planted instead, on the calls of one session,
    // whose spend is the whole every share on its pages is taken against.
    let corpus = Served::corpus();
    let levels = Levels::of(&corpus.db());
    let calls: Vec<(String, String)> = levels
        .store()
        .fetch(
            "SELECT source, id FROM live_api_calls WHERE session_id = $session_id \
             ORDER BY id LIMIT 3",
            &[("session_id", SPINE.into())],
        )
        .expect("the store answers")
        .iter()
        .map(|row| {
            (
                row.str("source").expect("a thread").to_owned(),
                row.str("id").expect("a call").to_owned(),
            )
        })
        .collect();
    assert_eq!(calls.len(), 3, "three calls to hang the scale on");
    // A decade apart each time, against a whole of 1101: the dearest call takes almost all of it,
    // the next a tenth of that, and the last a thousandth — which is where the scale runs out, and
    // the step is held at its first rather than going below it.
    let ladder = [(1000.0, "s10"), (100.0, "s7"), (1.0, "s1")];
    let planted: Vec<(f64, String)> = ladder
        .iter()
        .zip(&calls)
        .map(|((cost, _), (_, at))| (*cost, at.clone()))
        .collect();
    let served = Served::planted(move |store: &Store| {
        let connection = store.connection();
        connection
            .execute(
                "UPDATE api_calls SET cost_usd = 0 WHERE session_id = ?",
                params![SPINE],
            )
            .expect("the session's spend is cleared");
        for (cost, at) in &planted {
            connection
                .execute(
                    "UPDATE api_calls SET cost_usd = ? WHERE session_id = ? AND id = ?",
                    params![cost, SPINE, at],
                )
                .expect("a rung of the ladder lands");
        }
    });
    for ((cost, step), (source, call_id)) in ladder.iter().zip(&calls) {
        let (_, page) = served
            .page(&nav_trees::node_url(Kind::Call, SPINE, source, call_id))
            .await;
        let drawn = &Markup::of(&page).badges(&format!("call:{call_id}"))["cost_usd"];
        assert_eq!(steps(&drawn.step), [*step], "{cost} {drawn:?}");
    }
}

#[tokio::test]
async fn a_cost_badge_deepens_at_every_step_and_washes_nothing_but_the_cost() {
    // What a step is drawn as: a warm wash behind the dollar value, deeper the dearer the node.
    //
    // The ladder itself is unchanged — the same ten classes `nodes::meter` has always minted — so
    // what this reads is only what a step paints, and which element wears it: the class sits on the
    // badge and not on the row, because a row draws two badges at two depths. Off the served
    // stylesheet, because that is the one place it is decided: the markup carries the class
    // whatever the wash does, and nothing in this tier can see a painted box.
    let served = Served::corpus();
    let (_, style) = served.page("/static/style.css").await;
    let style = Regex::new(r"(?s)/\*.*?\*/")
        .expect("a pattern")
        .replace_all(&style, "")
        .into_owned();
    let washes: BTreeMap<i32, i32> =
        Regex::new(r"li\.node \.badge\.s(\d+) \{[^}]*--cost-wash: (\d+)%")
            .expect("a pattern")
            .captures_iter(&style)
            .map(|found| {
                (
                    found[1].parse().expect("a step"),
                    found[2].parse().expect("a share"),
                )
            })
            .collect();
    // One rule spends them, so the warm token is named once and every step is a share of it.
    assert!(
        Regex::new(r"color-mix\(in srgb, var\(--hot\) var\(--cost-wash[^)]*\)")
            .expect("a pattern")
            .is_match(&style)
    );
    // Every step the ladder can hand a row is drawn, and `s0` — a row that spent nothing at all —
    // is not: a wash at the bottom of the scale would read as a measurement of nothing.
    assert_eq!(
        washes.keys().copied().collect::<Vec<_>>(),
        (1..=STEPS).collect::<Vec<_>>()
    );
    // Deeper at every step, and never twice the same depth: two steps drawn alike are one step.
    let painted: Vec<i32> = washes.values().copied().collect();
    let mut climbing: Vec<i32> = painted
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    climbing.sort_unstable();
    assert_eq!(painted, climbing, "{washes:?}");
    // And the wash lands on the badge alone. Neither the row nor the link inside it is painted by a
    // step, which is what would tie a row's two halves to one depth.
    assert!(
        !Regex::new(r"li\.node\.s\d+[ ,{>]")
            .expect("a pattern")
            .is_match(&style)
    );
}

/// Both halves of one row: what each printed, and the step each is washed at.
fn weighs(page: &Markup, key: &str, own: f64, total: Option<f64>, whole: f64) {
    let read = page.badges(key);
    assert_eq!(read["cost_usd"].shown, money(own), "{key}");
    assert_eq!(
        steps(&read["cost_usd"].step),
        [meter(Some(own / whole))],
        "{key}"
    );
    let Some(total) = total else {
        assert!(!read.contains_key("total_usd"), "{key}");
        return;
    };
    assert_eq!(read["total_usd"].shown, money(total), "{key}");
    // Its own step, taken against the session the same way — the halves of one badge are two
    // shares of one number, not one share drawn twice.
    assert_eq!(
        steps(&read["total_usd"].step),
        [meter(Some(total / whole))],
        "{key}"
    );
}

/// The badge steps among one element's classes — `s0` through `s10`, and nothing else.
fn steps(classes: &str) -> Vec<String> {
    let step = Regex::new(r"^s\d+$").expect("a pattern");
    classes
        .split_whitespace()
        .filter(|name| step.is_match(name))
        .map(str::to_owned)
        .collect()
}

/// What one half of a badge printed, read back as a number the way a reader reads it.
fn read(half: &Badge) -> f64 {
    half.shown
        .trim_start_matches('$')
        .replace(',', "")
        .parse()
        .expect("a badge prints a number")
}
/// A cost at the four decimals the store hands one out at.
fn rounded(amount: f64) -> f64 {
    (amount * 10_000.0).round() / 10_000.0
}

/// What one turn's own thread spent, which is the first half of its badge.
fn turn_spend(levels: &Levels, source: &str, turn_id: &str) -> f64 {
    one_f64(
        levels,
        "SELECT coalesce(round(sum(cost_usd), 4), 0) AS at FROM live_api_calls \
         WHERE session_id = $session_id AND source = $source AND turn_id = $turn_id",
        &[
            ("session_id", SPINE.into()),
            ("source", source.into()),
            ("turn_id", turn_id.into()),
        ],
    )
}

fn one_str(levels: &Levels, sql: &str, bound: &[(&str, hyphae_store::Param)]) -> String {
    levels
        .store()
        .fetch(sql, bound)
        .expect("the store answers")
        .first()
        .expect("the query answers one row")
        .str("at")
        .expect("a value")
        .to_owned()
}

fn one_f64(levels: &Levels, sql: &str, bound: &[(&str, hyphae_store::Param)]) -> f64 {
    levels
        .store()
        .fetch(sql, bound)
        .expect("the store answers")
        .first()
        .expect("the query answers one row")
        .f64("at")
        .expect("a number")
}
