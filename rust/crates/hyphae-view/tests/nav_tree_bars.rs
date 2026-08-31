//! The context bar a NavTree row draws: how full the model's window was when the node ended.
//!
//! Read back off the rendered row rather than computed beside it — a bar is a set of nested edges
//! over the window the model that answered works in, so what moves one is the data and not
//! arithmetic written twice. Each leaf plants or scales what the store holds and reads back the
//! step the row landed on. The boundaries' own bars are in `nav_tree_bars_compaction.rs` and what
//! the stylesheet draws them as is in `nav_tree_bars_style.rs`; the other meter a row draws is the
//! cost badge (`nav_tree_badges.rs`).

use std::collections::BTreeMap;

use duckdb::params;
use hyphae_extract::pricing::{CONTEXT_WINDOWS, SYNTHETIC_MODEL};
use hyphae_store::Store;
use hyphae_testsupport::html::{Bar, Markup, bands, step};
use hyphae_testsupport::landmarks::{ANCESTOR, MAIN, SPINE, SPINE_LEAF, SPINE_RUN};
use hyphae_testsupport::nav_trees::{self, Levels};
use hyphae_testsupport::served::{self, Served};
use hyphae_view::nodes::{BAR_STEPS, Kind};

/// One recorded api call, as the context oracle below reads it out of the store.
struct Call {
    api_call_id: String,
    source: String,
    turn_id: Option<String>,
    model: String,
    synthetic: bool,
    /// Where the call left the model's window: everything it was billed for.
    fill: i64,
    /// How much of that the call itself put there: what it was billed for less the cache it read.
    added: i64,
    /// What the call sent before it answered: the cache it read and the input it wrote. The first
    /// main-thread call's is the context the session opened on, which is the ground every turn's
    /// growth is drawn over.
    sent: i64,
}

/// Every api call one session recorded, on every thread, in the order each thread made them.
///
/// The three sums are restated in the test's own SQL rather than read off `analyze/macros.py`, so
/// the two can disagree.
fn calls(store: &Store, session_id: &str) -> Vec<Call> {
    store
        .fetch(
            "SELECT id, source, turn_id, model, synthetic, \
               cache_read_tokens + cache_creation_tokens + input_tokens + output_tokens AS fill, \
               cache_creation_tokens + input_tokens + output_tokens AS added, \
               cache_read_tokens + cache_creation_tokens + input_tokens AS sent \
             FROM live_api_calls WHERE session_id = $session_id ORDER BY source, \"index\"",
            &[("session_id", session_id.into())],
        )
        .expect("the store answers")
        .iter()
        .map(|row| Call {
            api_call_id: row.str("id").expect("a call id").to_owned(),
            source: row.str("source").expect("a thread").to_owned(),
            turn_id: row
                .opt_str("turn_id")
                .expect("a turn or none")
                .map(str::to_owned),
            model: row.str("model").expect("a model").to_owned(),
            synthetic: row.bool("synthetic").expect("a flag"),
            fill: row.i64("fill").expect("a token count"),
            added: row.i64("added").expect("a token count"),
            sent: row.i64("sent").expect("a token count"),
        })
        .collect()
}

#[tokio::test]
async fn a_row_bars_the_context_it_left_against_the_window_its_model_answers_in() {
    // Where each kind of node left the model's context window, read against the store's tokens.
    //
    // One session, read whole rather than swept: the spine records the four kinds that end on a
    // window — a session, its turns, its runs, and the calls themselves — and the two that do not.
    // What each row draws is where the window stood when the node ended, and where inside that the
    // node's own share begins; a turn draws a third edge, the context the session opened on.
    //
    // The expectation is built from `live_api_calls` here rather than from the columns the page
    // reads, so a derivation that drifted in the NavTree's SQL has nothing to agree with. Built
    // from the store rather than written down, so re-recording the fixture moves the oracle.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let recorded = calls(levels.store(), SPINE);
    let answered: Vec<&Call> = recorded.iter().filter(|call| !call.synthetic).collect();
    let main: Vec<&&Call> = answered.iter().filter(|call| call.source == MAIN).collect();
    let (_, html) = served.page(&format!("/session/{SPINE}")).await;
    let page = Markup::of(&html);
    // A session reads the window its main thread was left in, and draws that alone: nothing came
    // before a session for it to have added anything to, and no prompt it stands its growth over.
    let last = main.last().expect("the main thread answered");
    assert_eq!(
        page.bar(&format!("session:{SPINE}")),
        Bar {
            fill: step(Some(last.fill), &last.model),
            prior: None,
            base: None,
        }
    );
    // And the call it reads is not the last one the thread made. The spine ends on an interrupt
    // Claude Code wrote itself, which reports no tokens at all (`docs/schema.md`) — so a
    // derivation that took the thread's last call would open the session on an empty window.
    let ended = recorded
        .iter()
        .rfind(|call| call.source == MAIN)
        .expect("the main thread recorded a call");
    assert!(ended.synthetic && ended.fill == 0);
    // Each turn draws three edges: where it left the window, where the turn before it left one —
    // which is where its own growth begins — and the context the session opened on, which is what
    // the first main-thread call sent before anything had been said.
    let opened = main.first().expect("the main thread answered").sent;
    let mut stood = 0;
    let mut walked = Vec::new();
    for call in &main {
        let turn_id = call
            .turn_id
            .clone()
            .expect("a main-thread call answers a turn");
        if !walked.contains(&turn_id) {
            walked.push(turn_id);
        }
    }
    for turn_id in &walked {
        let last = main
            .iter()
            .rfind(|call| call.turn_id.as_deref() == Some(turn_id.as_str()))
            .expect("the turn was answered");
        assert_eq!(
            page.bar(&format!("turn:{turn_id}")),
            bands(last.fill, stood, Some(opened), &last.model),
            "{turn_id}"
        );
        stood = last.fill;
    }
    // The turn the interrupt answered has no bar at all: no call under it says where the window
    // stood, and a bar drawn at nothing would say the window emptied.
    let silent: Vec<&str> = recorded
        .iter()
        .filter(|call| call.synthetic)
        .filter_map(|call| call.turn_id.as_deref())
        .filter(|turn_id| !walked.iter().any(|walked| walked == turn_id))
        .collect();
    assert!(!silent.is_empty());
    for turn_id in silent {
        assert_eq!(
            page.bar(&format!("turn:{turn_id}")),
            Bar {
                fill: None,
                prior: None,
                base: None
            },
            "{turn_id}"
        );
    }
    // A run reads the window of its own thread, and all of it is the run's own: a run starts on an
    // empty window and builds what it holds while it runs. No base band — the prompt the session
    // opened on is the main thread's, and a run's growth is measured from nothing.
    for run_id in [SPINE_RUN, SPINE_LEAF] {
        let ran = answered
            .iter()
            .rfind(|call| call.source == run_id)
            .expect("the run answered");
        let (_, html) = served.page(&format!("/session/{SPINE}/run/{run_id}")).await;
        assert_eq!(
            Markup::of(&html).bar(&format!("run:{run_id}")),
            bands(ran.fill, 0, None, &ran.model),
            "{run_id}"
        );
    }
    // A call draws its own fill and the part of it that was already there — and the interrupt,
    // which went to no model, draws nothing. Read on each call's own page, where the level of
    // calls is the one open.
    for call in recorded.iter().filter(|call| call.source == MAIN) {
        let (_, html) = served
            .page(&nav_trees::node_url(
                Kind::Call,
                SPINE,
                MAIN,
                &call.api_call_id,
            ))
            .await;
        let row = Markup::of(&html);
        let drawn = if call.synthetic {
            Bar {
                fill: None,
                prior: None,
                base: None,
            }
        } else {
            bands(call.fill, call.fill - call.added, None, &call.model)
        };
        assert_eq!(
            row.bar(&format!("call:{}", call.api_call_id)),
            drawn,
            "{}",
            call.api_call_id
        );
        // And nothing under a call is barred: a tool call's tokens are its api call's.
        for key in row.values("data-nav-tree") {
            if key.starts_with("tool:") {
                assert_eq!(
                    row.bar(&key),
                    Bar {
                        fill: None,
                        prior: None,
                        base: None
                    },
                    "{key}"
                );
            }
        }
    }
}

#[tokio::test]
async fn an_interrupt_and_another_threads_calls_move_no_bar_a_row_draws() {
    // A row's window is read off its own thread's answers, and off nothing else.
    //
    // Three rules meet here, one per level: a turn and a run read the last call under them that
    // went to a model, and a session reads the last call of its *main* thread. No recorded session
    // can tell any of the three from the rule that dropped its filter — no turn in the corpus
    // mixes a model's answers with an interrupt, no run thread ends on one, and the main thread
    // holds the highest call index in every session recorded. So the three shapes are planted and
    // the page is read against itself: every bar it draws over them is the bar it drew without
    // them.
    //
    // INVENTED arrangement of recorded rows: the interrupt Claude Code wrote into the spine is
    // moved into the turn a model had already answered, a second one is cloned onto a run's own
    // thread, and the other run's calls are renumbered past the main thread's last. Every token
    // count, model and cost under them is the transcript's.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let answered = levels
        .store()
        .fetch(
            "SELECT turn_id FROM live_api_calls WHERE session_id = $session_id \
               AND source = $source AND NOT synthetic ORDER BY \"index\" DESC LIMIT 1",
            &[("session_id", SPINE.into()), ("source", MAIN.into())],
        )
        .expect("the store answers")
        .first()
        .expect("the main thread answered")
        .str("turn_id")
        .expect("a turn")
        .to_owned();
    let joined = answered.clone();
    let interrupted = Served::planted(move |store: &Store| {
        let connection = store.connection();
        // The reader interrupted a turn a model had already answered twice, so the turn holds
        // both — and the interrupt is the last call in it.
        connection
            .execute(
                "UPDATE api_calls SET turn_id = ? WHERE session_id = ? AND source = ? \
                 AND synthetic",
                params![joined, SPINE, MAIN],
            )
            .expect("the interrupt joins an answered turn");
        // A run's thread ends the same way. Cloned from its own last call rather than invented, so
        // every column but the ones an interrupt reports differently is the store's shape: a
        // placeholder went to no model, so it names none and reports no tokens at all.
        connection
            .execute(
                "INSERT INTO api_calls (SELECT c.* REPLACE (c.id || '-interrupt' AS id, \
                   ? AS model, true AS synthetic, 1000000 AS \"index\", 0 AS input_tokens, \
                   0 AS output_tokens, 0 AS cache_read_tokens, 0 AS cache_creation_tokens, \
                   0 AS cache_5m_tokens, 0 AS cache_1h_tokens, 0.0 AS cost_usd) \
                 FROM (SELECT * FROM api_calls WHERE session_id = ? AND source = ? \
                   ORDER BY \"index\" DESC LIMIT 1) c)",
                params![SYNTHETIC_MODEL, SPINE, SPINE_RUN],
            )
            .expect("the run's thread ends on an interrupt");
        // And the other run outlasts the main thread: an index counts a thread's own calls, so a
        // run that answered longer than the session's own thread carries the higher ones.
        connection
            .execute(
                "UPDATE api_calls SET \"index\" = 1000000 + \"index\" \
                 WHERE session_id = ? AND source = ?",
                params![SPINE, SPINE_LEAF],
            )
            .expect("the leaf run outlasts the main thread");
    });
    let paths = [
        format!("/session/{SPINE}"),
        format!("/session/{SPINE}/run/{SPINE_RUN}"),
        format!("/session/{SPINE}/run/{SPINE_LEAF}"),
    ];
    let mut drawn: Vec<BTreeMap<String, Bar>> = Vec::new();
    for path in &paths {
        let (_, html) = served.page(path).await;
        let page = Markup::of(&html);
        let before: BTreeMap<String, Bar> = page
            .values("data-nav-tree")
            .into_iter()
            .map(|key| {
                let bar = page.bar(&key);
                (key, bar)
            })
            .collect();
        let (_, html) = interrupted.page(path).await;
        let after = Markup::of(&html);
        for (key, bar) in &before {
            assert_eq!(&after.bar(key), bar, "{path} {key}");
        }
        drawn.push(before);
    }
    // And the rows the plant reached draw a bar at all: a sweep over rows that draw nothing would
    // agree with itself whatever a filter did.
    let session = &drawn[0];
    assert!(session[&format!("session:{SPINE}")].fill.is_some());
    assert!(session[&format!("turn:{answered}")].fill.unwrap_or(0) > 0);
    for (page, run_id) in drawn[1..].iter().zip([SPINE_RUN, SPINE_LEAF]) {
        assert!(
            page[&format!("run:{run_id}")].fill.unwrap_or(0) > 0,
            "{run_id}"
        );
    }
}

#[tokio::test]
async fn a_model_we_hold_no_window_for_is_a_bar_the_nav_tree_does_not_draw() {
    // A window our table cannot name draws no bar, the way a price it lacks shows no cost.
    //
    // Every model the corpus records is in the table, so the gap is planted: the models Claude
    // Code sends can gain a suffix — a larger window is asked for by an alias the reply does not
    // echo — and a name we have not seen is a scale we would have to invent to draw.
    let unknown = Served::planted(|store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE api_calls SET model = 'claude-mythos-9' WHERE session_id = ?",
                params![SPINE],
            )
            .expect("the session answers on a model we hold no window for");
    });
    let (_, html) = unknown.page(&format!("/session/{SPINE}")).await;
    let page = Markup::of(&html);
    for key in page.values("data-nav-tree") {
        assert_eq!(
            page.bar(&key),
            Bar {
                fill: None,
                prior: None,
                base: None
            },
            "{key}"
        );
    }
    // The row still says what it cost: the price is what the store recorded at extraction, and
    // only the bar is the table's to answer for.
    assert!(
        !page
            .field("data-nav-tree", &format!("session:{SPINE}"), "cost_usd")
            .is_empty()
    );
}

#[tokio::test]
async fn a_context_bar_fills_linearly_and_stops_at_a_full_window() {
    // Which step a fill is drawn at, at the bottom of the window, at the top, and past it.
    //
    // A recorded call fills half a window at most, so the top of the scale and the clamp above it
    // are rules no fixture exercises — every bar could be drawn at twice its share and the corpus
    // would agree. The tokens are planted instead, on the spine's own calls: each call is left
    // with nothing but the input it sent, which is the whole of what it added — so the fill is
    // read back against a band of its own that begins at nothing.
    let corpus = Served::corpus();
    let levels = Levels::of(&corpus.db());
    let model = levels
        .store()
        .fetch(
            "SELECT model FROM live_api_calls WHERE session_id = $session_id LIMIT 1",
            &[("session_id", SPINE.into())],
        )
        .expect("the store answers")
        .first()
        .expect("the session answered")
        .str("model")
        .expect("a model")
        .to_owned();
    let window = CONTEXT_WINDOWS
        .iter()
        .find(|(name, _)| *name == model)
        .map(|(_, window)| *window)
        .expect("the model's window");
    // A twentieth of the window, half of it, and three times it — the last of which is a call the
    // store can hold and the bar cannot draw past its own end.
    let ladder = [
        (window / BAR_STEPS, 1),
        (window / 2, BAR_STEPS / 2),
        (window * 3, BAR_STEPS),
    ];
    let at: Vec<String> = levels
        .store()
        .fetch(
            "SELECT id FROM live_api_calls WHERE session_id = $session_id AND source = $source \
               AND NOT synthetic ORDER BY \"index\" LIMIT 3",
            &[("session_id", SPINE.into()), ("source", MAIN.into())],
        )
        .expect("the store answers")
        .iter()
        .map(|row| row.str("id").expect("a call id").to_owned())
        .collect();
    assert_eq!(at.len(), ladder.len(), "three calls to hang the scale on");
    let planted: Vec<(i64, String)> = ladder
        .iter()
        .map(|(fill, _)| *fill)
        .zip(at.iter().cloned())
        .collect();
    let scaled = Served::planted(move |store: &Store| {
        for (fill, call_id) in &planted {
            store
                .connection()
                .execute(
                    "UPDATE api_calls SET input_tokens = ?, cache_read_tokens = 0, \
                     cache_creation_tokens = 0, output_tokens = 0 \
                     WHERE session_id = ? AND id = ?",
                    params![fill, SPINE, call_id],
                )
                .expect("a rung of the ladder lands");
        }
    });
    for ((fill, drawn), call_id) in ladder.iter().zip(&at) {
        let (_, html) = scaled
            .page(&nav_trees::node_url(Kind::Call, SPINE, MAIN, call_id))
            .await;
        assert_eq!(
            Markup::of(&html).bar(&format!("call:{call_id}")),
            Bar {
                fill: Some(*drawn),
                prior: Some(0),
                base: None
            },
            "{fill} {call_id}"
        );
    }
}

#[tokio::test]
async fn every_band_a_row_draws_nests_inside_the_one_that_holds_it() {
    // No band runs past its holder, on any row of any session the corpus records.
    //
    // The bar's whole grammar in one sweep: three edges drawn as prefixes of one another, so a
    // pair out of order is a band drawn backwards — the base prompt reaching past the conversation
    // that holds it, or a node's own share starting after the window ended. A sweep rather than a
    // spot check, because the arithmetic that orders them is three clamps and each one is a rule
    // some row of some session is the only witness to.
    let served = Served::corpus();
    let mut banded = 0;
    for session_id in served::session_ids(&served.db()) {
        let (_, html) = served.page(&format!("/session/{session_id}")).await;
        let page = Markup::of(&html);
        for key in page.values("data-nav-tree") {
            let drawn = page.bar(&key);
            let Some(fill) = drawn.fill else {
                // A row with no fill draws no bar at all, so it names no band either.
                assert_eq!(
                    drawn,
                    Bar {
                        fill: None,
                        prior: None,
                        base: None
                    },
                    "{session_id} {key}"
                );
                continue;
            };
            let edges: Vec<i64> = [drawn.fill, drawn.prior, drawn.base]
                .into_iter()
                .flatten()
                .collect();
            let mut ordered = edges.clone();
            ordered.sort_unstable_by(|left, right| right.cmp(left));
            assert_eq!(edges, ordered, "{session_id} {key} {drawn:?}");
            assert!(fill <= BAR_STEPS, "{session_id} {key} {drawn:?}");
            banded += edges.len();
        }
        // And no row carries a width of its own: the classes are the only hook there is, and a
        // `style` attribute anywhere under a row is markup the policy would refuse to paint
        // (`routes.rs`).
        assert!(
            !nav_tree_styled(&html),
            "{session_id} draws a width of its own"
        );
    }
    assert!(banded > 0, "no row in the corpus drew a band");
}

/// Whether any NavTree row carries an inline `style`, read over the served bytes.
fn nav_tree_styled(html: &str) -> bool {
    regex::Regex::new(r#"data-nav-tree="[^"]*"[^>]*style=""#)
        .expect("a pattern")
        .is_match(html)
}

#[tokio::test]
async fn a_turns_bar_stands_on_the_context_the_session_opened_on() {
    // The base band: the prompt, the instructions and the tools a session begins with.
    //
    // What the first main-thread call sent before a word had been said — the ground every turn's
    // growth is drawn over, so a reader sees a conversation filling the window rather than a
    // window that was already two thirds full when it started. A session constant: every turn of
    // the session draws the same edge, whatever else its own bar says.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let (opening, model) = first_sent(&levels, SPINE);
    let (_, html) = served.page(&format!("/session/{SPINE}")).await;
    let page = Markup::of(&html);
    let drawn: Vec<Option<i64>> = page
        .values("data-nav-tree")
        .into_iter()
        .filter(|key| key.starts_with("turn:"))
        .map(|key| page.bar(&key))
        .filter(|bar| bar.fill.is_some())
        .map(|bar| bar.base)
        .collect();
    assert!(drawn.len() > 1, "one turn cannot show a constant");
    // One value across the page, and it is the opening context stepped against the window.
    for base in &drawn {
        assert_eq!(*base, step(Some(opening), &model), "{drawn:?}");
    }
    // The base is what the first call the recording holds sent, whatever was said before it.
    // `ANCESTOR` is the session `RESUME` resumed, and its recording opens partway into a
    // conversation — on more context than the window holds. Its turn is drawn base to tip: a bar
    // with no room of its own. The design accepts that reading rather than an ideal one, because
    // an inherited context is still context the turn is working inside.
    let (inherited, model) = first_sent(&levels, ANCESTOR);
    let window = CONTEXT_WINDOWS
        .iter()
        .find(|(name, _)| *name == model)
        .map(|(_, window)| *window)
        .expect("the model's window");
    assert!(inherited > window, "{inherited} {model}");
    let (_, html) = served.page(&format!("/session/{ANCESTOR}")).await;
    let resumed = Markup::of(&html);
    let turns: Vec<Bar> = resumed
        .values("data-nav-tree")
        .into_iter()
        .filter(|key| key.starts_with("turn:"))
        .map(|key| resumed.bar(&key))
        .collect();
    assert!(!turns.is_empty(), "the resumed session draws no turn");
    for band in turns {
        assert!(
            band.base == band.fill && band.fill.unwrap_or(0) > 0,
            "{band:?}"
        );
    }
}

/// What the first answered call of a session's main thread sent, and the model it went to.
fn first_sent(levels: &Levels, session_id: &str) -> (i64, String) {
    let rows = levels
        .store()
        .fetch(
            "SELECT cache_read_tokens + cache_creation_tokens + input_tokens AS sent, model \
             FROM live_api_calls WHERE session_id = $session_id AND source = $source \
               AND NOT synthetic ORDER BY \"index\" LIMIT 1",
            &[("session_id", session_id.into()), ("source", MAIN.into())],
        )
        .expect("the store answers");
    let row = rows.first().expect("the main thread answered");
    (
        row.i64("sent").expect("a token count"),
        row.str("model").expect("a model").to_owned(),
    )
}

#[tokio::test]
async fn a_turn_that_gave_the_window_back_draws_no_band_of_its_own() {
    // A turn that ends on less than the turn before it opens no band, rather than a wrapped one.
    //
    // A compaction inside a turn leaves the window below where the turn before it stood, and the
    // delta a bar would draw is negative. No recorded session holds one — the corpus's five
    // compactions all sit outside a turn — so the drop is planted, on the spine's own calls: the
    // first turn is left holding a window nothing after it reaches.
    //
    // INVENTED token counts on recorded rows. What the calls said, cost and answered on is the
    // transcript's; only the cache the first turn read is moved, to a number the turns after it
    // cannot climb back to.
    let corpus = Served::corpus();
    let levels = Levels::of(&corpus.db());
    let rows = levels
        .store()
        .fetch(
            "SELECT t.id, max(c.model) AS model FROM live_turns t JOIN live_api_calls c \
               ON c.session_id = t.session_id AND c.source = t.source AND c.turn_id = t.id \
             WHERE t.session_id = $session_id AND t.source = $source \
             GROUP BY t.id, t.\"index\" ORDER BY t.\"index\" LIMIT 1",
            &[("session_id", SPINE.into()), ("source", MAIN.into())],
        )
        .expect("the store answers");
    let row = rows.first().expect("the main thread holds a turn");
    let first = row.str("id").expect("a turn").to_owned();
    let model = row.str("model").expect("a model").to_owned();
    let window = CONTEXT_WINDOWS
        .iter()
        .find(|(name, _)| *name == model)
        .map(|(_, window)| *window)
        .expect("the model's window");
    let raised = first.clone();
    let given = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE api_calls SET cache_read_tokens = ? WHERE session_id = ? AND turn_id = ?",
                params![window, SPINE, raised],
            )
            .expect("the first turn is raised past what follows it");
    });
    let (_, html) = given.page(&format!("/session/{SPINE}")).await;
    let page = Markup::of(&html);
    // The turn that was raised is full, and every turn after it draws a bar whose own band is
    // empty: it ends where it began, because what it added was given back before it ran.
    assert_eq!(page.bar(&format!("turn:{first}")).fill, Some(BAR_STEPS));
    let after: Vec<Bar> = page
        .values("data-nav-tree")
        .into_iter()
        .filter(|key| key.starts_with("turn:") && key != &format!("turn:{first}"))
        .map(|key| page.bar(&key))
        .filter(|bar| bar.fill.is_some())
        .collect();
    assert!(!after.is_empty(), "no turn after the plant draws a bar");
    for band in after {
        assert_eq!(band.prior, band.fill, "{band:?}");
    }
}
