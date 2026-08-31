//! Three readings the fixture corpus does not hold, planted onto rows it does.
//!
//! Ported from the three planted leaves of `tests/view/test_numbers.py`. Each names what is
//! invented and what is recorded in its own comment: the arrangement is the plant's, the thread and
//! the order of its turns are the transcript's.

use duckdb::params;
use hyphae_store::{Param, Store};
use hyphae_testsupport::html::{Bar, step};
use hyphae_testsupport::landmarks::{MAIN, SPINE};
use hyphae_testsupport::popovers::{held, popover, reached, signed, tokens};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::format::ABSENT;
use hyphae_view::nodes::Kind;

#[tokio::test]
async fn a_turn_that_compacted_says_the_window_it_gave_back() {
    // A compaction inside a turn leaves the window below where the turn before it stood.
    //
    // The NavTree clamps that at nothing, because a bar has no way to draw a negative tip — so the
    // real delta is the popover's alone, and a popover that clamped too would print a turn that
    // dropped thirty thousand tokens as one that added none.
    //
    // INVENTED number, recorded shape: no session in the fixture corpus compacted mid-turn, so the
    // drop is planted onto a recorded turn by cutting the cache its calls read. Everything around
    // it — the thread, the order of its turns, the calls under them — is what the transcript
    // recorded.
    let served = Served::planted(|store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE api_calls SET cache_read_tokens = 100, cache_creation_tokens = 50, \
                 input_tokens = 10, output_tokens = 10 \
                 WHERE session_id = ? AND source = ? AND turn_id = \
                   (SELECT id FROM turns WHERE session_id = ? AND source = ? \
                    ORDER BY \"index\" LIMIT 1 OFFSET 2)",
                params![SPINE, MAIN, SPINE, MAIN],
            )
            .expect("the third turn's calls are cut");
    });
    // The spine's first turn never reached a model, so the pair to compare is the two after it.
    let turns = thread_turns(&served, SPINE, MAIN);
    let (first, second) = (&turns[1], &turns[2]);
    let before = turn_popover(&served, first).await;
    let after = turn_popover(&served, second).await;
    let (_, page) = served.page(&format!("/session/{SPINE}")).await;
    assert!(
        tokens(&after, "fill") < tokens(&before, "fill"),
        "the plant is meant to drop the window"
    );
    assert_eq!(
        after["added"],
        signed(tokens(&after, "fill") - tokens(&before, "fill"))
    );
    assert!(after["added"].starts_with('-'), "{}", after["added"]);
    // And the row the popover opened from draws that same turn with no band of its own: the edge its
    // growth begins at is held up at the fill, because a band has no way to run backwards. This is
    // the one place the two seams are meant to disagree — the tree clamps where the popover prints
    // the drop.
    let drawn =
        hyphae_testsupport::html::Markup::of(&page).bar(&format!("{}:{second}", Kind::Turn));
    assert_eq!(
        drawn.fill,
        step(Some(tokens(&after, "fill")), &after["model"])
    );
    assert_eq!(drawn.prior, drawn.fill, "{drawn:?}");
}

#[tokio::test]
async fn a_turn_is_measured_against_the_last_turn_that_answered() {
    // A turn a model never answered is stepped over, not counted as an empty window.
    //
    // What a turn added is the window it left less the window the turn before it left — and a turn
    // Claude Code answered with a placeholder left none at all (`docs/schema.md`). Neither seam may
    // read that nothing as a floor: a turn measured against it would read as having built the whole
    // window from scratch, and the thread would look like it started over every time the reader
    // interrupted it.
    //
    // INVENTED arrangement of recorded rows: the corpus's one silent turn sits at the end of its
    // thread, so the spine's last two turns trade calls — the model's answers move to the last turn,
    // and the interrupt to the turn before it.
    let corpus = Served::corpus();
    let db = corpus.db();
    let turns = thread_turns(&corpus, SPINE, MAIN);
    let [stood_at, quiet, last] = &turns[turns.len() - 3..] else {
        panic!("the spine's main thread holds three turns");
    };
    assert!(
        reached(&db, SPINE, MAIN, stood_at),
        "the turn the delta reaches back to answered"
    );
    assert!(
        !reached(&db, SPINE, MAIN, last),
        "the spine's last turn holds the interrupt"
    );
    // Where the two turns stood before the swap: the answers land on `last`, and the window they
    // left is measured against the turn two places behind it.
    let stood = tokens(
        &held(&db, SPINE, MAIN, &format!("AND turn_id = '{stood_at}'")),
        "fill",
    );
    let moved = held(&db, SPINE, MAIN, &format!("AND turn_id = '{quiet}'"));
    let (quiet, last) = (quiet.clone(), last.clone());
    let swapped = Served::planted(move |store: &Store| {
        let connection = store.connection();
        connection
            .execute(
                "UPDATE api_calls SET turn_id = ? WHERE session_id = ? AND source = ? \
                 AND turn_id = ? AND NOT synthetic",
                params![last, SPINE, MAIN, quiet],
            )
            .expect("the answers move to the last turn");
        connection
            .execute(
                "UPDATE api_calls SET turn_id = ? WHERE session_id = ? AND source = ? AND synthetic",
                params![quiet, SPINE, MAIN],
            )
            .expect("the interrupt moves back one");
    });
    let (quiet, last) = (&turns[turns.len() - 2], &turns[turns.len() - 1]);
    let printed = turn_popover(&swapped, last).await;
    let silent = turn_popover(&swapped, quiet).await;
    let (_, html) = swapped.page(&format!("/session/{SPINE}")).await;
    let page = hyphae_testsupport::html::Markup::of(&html);
    // The turn holds the window its own calls left...
    hyphae_testsupport::popovers::assert_holds(&printed, &moved);
    // ...and what it added is measured over the interrupted turn, back to the last answer.
    assert_eq!(printed["added"], signed(tokens(&printed, "fill") - stood));
    // The row says the same thing as an edge rather than as a delta: its growth begins where the
    // turn that answered left the window, and never at the base band under it.
    let drawn = page.bar(&format!("{}:{last}", Kind::Turn));
    assert_eq!(
        drawn.fill,
        step(Some(tokens(&printed, "fill")), &moved["model"])
    );
    assert_eq!(
        drawn.prior,
        Some(
            step(Some(stood), &moved["model"])
                .unwrap_or(0)
                .max(drawn.base.unwrap_or(0))
        ),
        "{drawn:?}"
    );
    // The interrupted turn itself says neither number at either seam, which is what makes the delta
    // above a step over something rather than a step from it.
    assert_eq!(silent["fill"], ABSENT);
    assert_eq!(silent["added"], ABSENT);
    assert_eq!(
        page.bar(&format!("{}:{quiet}", Kind::Turn)),
        Bar {
            fill: None,
            prior: None,
            base: None
        }
    );
}

#[tokio::test]
async fn a_model_we_hold_no_window_for_says_so_rather_than_scaling_to_a_guess() {
    // An unknown window is stated, and the token counts print beside it anyway.
    //
    // A `[1m]` session names its base model in `message.model`, so a window larger than the table's
    // is invisible to it (`hyphae_extract::pricing`). The tokens are still the store's, and a
    // popover that withheld them for want of a scale would drop the honest numbers it has.
    let served = Served::planted(|store: &Store| {
        // Cost goes with the model: `compute_cost` answers None for a model the table lacks, so the
        // exporter would have stored no cost for these calls either (`hyphae_extract::pricing`).
        store
            .connection()
            .execute(
                "UPDATE api_calls SET model = 'claude-mythos-9', cost_usd = NULL \
                 WHERE session_id = ?",
                params![SPINE],
            )
            .expect("the session's calls take a model we hold no price for");
    });
    let printed = popover(
        &served,
        &format!("/session/{SPINE}"),
        &format!("{}:{SPINE}", Kind::Session),
    )
    .await;
    assert_eq!(printed["window"], "unknown");
    assert!(tokens(&printed, "fill") > 0, "{printed:?}");
    // A model our price table lacks shows no legend rather than four zeroes, and the count of what
    // went unpriced is what says why.
    for charge in hyphae_testsupport::popovers::CHARGES {
        assert!(!printed.contains_key(charge), "{charge} in {printed:?}");
    }
    assert_eq!(printed["unpriced_api_calls"], printed["api_calls"]);
}

/// One thread's turn ids in the order they were recorded, off whichever store is being served.
fn thread_turns(served: &Served, session_id: &str, source: &str) -> Vec<String> {
    rows::all(
        &served.db(),
        "SELECT id FROM live_turns WHERE session_id = $session AND source = $source \
         ORDER BY \"index\"",
        &[
            ("session", Param::from(session_id)),
            ("source", Param::from(source)),
        ],
    )
    .iter()
    .map(|row| row.str("id").expect("a turn id").to_owned())
    .collect()
}

/// One turn's popover on the spine's main thread.
async fn turn_popover(
    served: &Served,
    turn_id: &str,
) -> std::collections::BTreeMap<String, String> {
    popover(
        served,
        &format!("/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"),
        &format!("{}:{turn_id}", Kind::Turn),
    )
    .await
}
