//! The popover behind a NavTree row: the exact numbers its bar and its badge stand for.
//!
//! Ported from `tests/view/test_numbers.py`. A row draws two summaries and can print neither — a
//! bar is twenty steps of a window, and a badge is a dollar figure at cent precision. The popover
//! is the numbers themselves, fetched when a reader points at a row or tabs to it
//! (`docs/viewer.md`).
//!
//! The helpers every leaf here reads through are `hyphae_testsupport::popovers`, which says how the
//! oracle prices what the page groups. The planted readings are `numbers_planted.rs`, the wiring
//! that fetches a popover is `numbers_wiring.rs`, and the dollars that cross a thread boundary are
//! `numbers_spend.rs`.

use hyphae_store::Param;
use hyphae_testsupport::html::counted;
use hyphae_testsupport::landmarks::{
    DENSE_CALL, DENSE_TOOL, FORK_ORIGIN, FORK_ORIGIN_RUN, MAIN, SEARCH_BASH_TOOL, SEARCH_TOOL,
    SPINE, SPINE_LEAF, SPINE_RUN,
};
use hyphae_testsupport::popovers::{
    amount, assert_holds, charged, held, misread, popover, reached, signed, tokens, total,
};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::format::ABSENT;
use hyphae_view::nodes::Kind;

#[tokio::test]
async fn a_session_reads_its_window_and_its_dollars_off_the_thread_a_reader_is_on() {
    // A session popover is the main thread's, and the runs it spawned stand under it.
    //
    // Both summaries are one thread's: the window because that is the thread a reader of the
    // session is in — a run holds one of its own — and now the dollars too. They used to be every
    // thread's, which made the session the one node whose three charges answered a different
    // question from the three on the row under it. What the subagents cost is the line below
    // instead, where a reader can see it is a different set of calls.
    let served = Served::corpus();
    let db = served.db();
    let printed = popover(
        &served,
        &format!("/session/{SPINE}"),
        &format!("{}:{SPINE}", Kind::Session),
    )
    .await;
    assert_holds(&printed, &held(&db, SPINE, MAIN, ""));
    // Nothing came before a session for it to have added to, so the figure is the dash a missing
    // number prints rather than the whole of the fill dressed as a delta.
    assert_eq!(printed["added"], ABSENT);
    // The three charges price the main thread's calls and nothing else, which on a session that
    // ran subagents is strictly less than what the session spent.
    let (split, main) = charged(&db, SPINE, &format!("AND source = '{MAIN}'"));
    let whole = rows::one(
        &db,
        "SELECT cost_usd FROM session_rollups WHERE session_id = $session",
        &[("session", Param::from(SPINE))],
    )
    .f64("cost_usd")
    .expect("a cost");
    assert!(
        main < whole,
        "the reversal is only visible on a session with subagents"
    );
    assert!(misread(&printed, &split).is_empty(), "{printed:?}");
    // And they come to the total under them, which is now the main thread's own. Printed to the
    // place a cost is stored at rather than to the badge's cents: the popover is where a reader
    // adds the column up, and a column of cents would not come to a total in cents.
    assert_eq!(printed["cost_usd"], format!("${main:.4}"));
    assert_eq!(round(total(&split), 4), round(main, 4));
    // What left the column is the breakout line under it — every thread but the main one — and the
    // two of them come back to what the store says the session spent.
    let under = rows::one(
        &db,
        "SELECT round(sum(cost_usd), 4) AS under FROM live_api_calls \
         WHERE session_id = $session AND source <> $source",
        &[
            ("session", Param::from(SPINE)),
            ("source", Param::from(MAIN)),
        ],
    )
    .f64("under")
    .expect("a cost");
    assert_eq!(printed["cost_subagents"], format!("${under:.4}"));
    assert_eq!(amount(&printed["cost_total"]), round(main + under, 4));
    assert_eq!(round(amount(&printed["cost_total"]), 2), round(whole, 2));
}

#[tokio::test]
async fn a_turn_says_what_it_put_into_the_window_since_the_turn_before_it() {
    // Every turn of one thread, each measured against the turn that answered before it.
    //
    // The spine's main thread covers both readings its four turns hold: two that reached a model,
    // and two that did not. A turn Claude Code answered with a placeholder has no window at all
    // (`docs/schema.md`), and a popover that stood it where the turn before it stood would invent a
    // reading — so its fill and its delta are the dash a NULL prints.
    let served = Served::corpus();
    let db = served.db();
    let turns = thread_turns(&served, SPINE, MAIN);
    assert!(turns.len() > 1, "a delta needs a turn before it");
    let mut stood = 0;
    let mut silent = 0;
    for turn_id in &turns {
        let printed = popover(
            &served,
            &format!("/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"),
            &format!("{}:{turn_id}", Kind::Turn),
        )
        .await;
        let where_turn = format!("AND turn_id = '{turn_id}'");
        if !reached(&db, SPINE, MAIN, turn_id) {
            assert_eq!(printed["fill"], ABSENT, "{turn_id}");
            assert_eq!(printed["added"], ABSENT, "{turn_id}");
            silent += 1;
            continue;
        }
        assert_holds(&printed, &held(&db, SPINE, MAIN, &where_turn));
        // Signed, always: what a turn added is a change, and a change that prints bare reads as a
        // total.
        assert_eq!(
            printed["added"],
            signed(tokens(&printed, "fill") - stood),
            "{turn_id}"
        );
        stood = tokens(&printed, "fill");
        let (split, _) = charged(&db, SPINE, &format!("AND source = '{MAIN}' {where_turn}"));
        assert!(
            misread(&printed, &split).is_empty(),
            "{turn_id}: {printed:?}"
        );
    }
    assert!(
        silent > 0,
        "the spine is meant to hold a turn that never reached a model"
    );
}

#[tokio::test]
async fn a_run_reads_the_window_it_built_on_its_own_thread() {
    // A run starts on an empty window, so what it added is the whole of what it holds.
    let served = Served::corpus();
    let db = served.db();
    for run_id in [SPINE_RUN, SPINE_LEAF] {
        let printed = popover(
            &served,
            &format!("/session/{SPINE}/run/{run_id}"),
            &format!("{}:{run_id}", Kind::Run),
        )
        .await;
        assert_holds(&printed, &held(&db, SPINE, run_id, ""));
        assert_eq!(
            printed["added"],
            format!("+{}", printed["fill"]),
            "{run_id}"
        );
        let (split, _) = charged(&db, SPINE, &format!("AND source = '{run_id}'"));
        assert!(
            misread(&printed, &split).is_empty(),
            "{run_id}: {printed:?}"
        );
    }
}

#[tokio::test]
async fn a_call_says_the_cache_it_read_apart_from_the_context_it_sent() {
    // One api call's numbers are its own: what it added is its fill less the cache it read.
    let served = Served::corpus();
    let db = served.db();
    let narrowed = format!("AND source = '{FORK_ORIGIN_RUN}' AND id = '{DENSE_CALL}'");
    let printed = popover(
        &served,
        &format!("/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}"),
        &format!("{}:{DENSE_CALL}", Kind::Call),
    )
    .await;
    assert_holds(
        &printed,
        &held(&db, FORK_ORIGIN, FORK_ORIGIN_RUN, &narrowed),
    );
    assert_eq!(
        printed["added"],
        signed(tokens(&printed, "fill") - tokens(&printed, "cached"))
    );
    // One call is one model, so the dollars beside the counts are that call's own.
    let (split, stored) = charged(&db, FORK_ORIGIN, &narrowed);
    assert!(misread(&printed, &split).is_empty(), "{printed:?}");
    assert_eq!(printed["cost_usd"], format!("${stored:.4}"));
    // One call answered, so the line saying how many did is absent: `over 1 api call` is a sentence
    // that says nothing, and the popover is already the node's own numbers.
    assert!(!printed.contains_key("api_calls"), "{printed:?}");
}

#[tokio::test]
async fn the_popovers_two_columns_come_to_the_totals_under_them() {
    // Both columns add up, which is what makes the block one reading rather than five numbers.
    //
    // The counts are the node's last answering call and come to the window it left; the dollars are
    // every call the node made and come to the total under them. That is why the cache a call wrote
    // is charged on the new-input line rather than on one of its own: its tokens are counted there,
    // and a fourth dollar would leave a column that sums to nothing a reader can see.
    //
    // Over a turn that answered more than once, so the two columns are read over different sets of
    // calls — which is what the line under them says out loud.
    let served = Served::corpus();
    let db = served.db();
    let busiest = rows::one(
        &db,
        "SELECT turn_id, count(*) AS answered FROM live_api_calls \
         WHERE session_id = $session AND source = $source AND NOT synthetic \
         GROUP BY turn_id ORDER BY count(*) DESC, min(\"index\") LIMIT 1",
        &[
            ("session", Param::from(SPINE)),
            ("source", Param::from(MAIN)),
        ],
    );
    let turn_id = busiest.str("turn_id").expect("a turn").to_owned();
    assert!(
        busiest.i64("answered").expect("a count") > 1,
        "the columns are read over different sets only where several answered"
    );
    let printed = popover(
        &served,
        &format!("/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"),
        &format!("{}:{turn_id}", Kind::Turn),
    )
    .await;
    // The counts: the cache the last call read, what it sent, and what it said back.
    let counts: i64 = ["cached", "new_input", "output"]
        .iter()
        .map(|name| tokens(&printed, name))
        .sum();
    assert_eq!(counts, tokens(&printed, "fill"));
    // The dollars: to the cent, because each is rounded before it is printed and the total is
    // rounded off the store's own sum rather than off these three.
    let dollars: f64 = hyphae_testsupport::popovers::CHARGES
        .iter()
        .map(|name| amount(&printed[*name]))
        .sum();
    assert_eq!(round(dollars, 2), round(amount(&printed["cost_usd"]), 2));
    // And the line that says the dollars cover more calls than the counts do.
    let made = rows::one(
        &db,
        "SELECT count(*) AS made FROM live_api_calls \
         WHERE session_id = $session AND source = $source AND turn_id = $turn",
        &[
            ("session", Param::from(SPINE)),
            ("source", Param::from(MAIN)),
            ("turn", Param::from(turn_id.as_str())),
        ],
    )
    .i64("made")
    .expect("a count");
    assert_eq!(printed["api_calls"], counted(made));
}

#[tokio::test]
async fn a_tool_call_says_what_it_gave_back_and_what_was_asked_beside_it() {
    // A tool call carries no usage, so its popover is a size and the company it kept.
    //
    // Its tokens are its api call's (`docs/schema.md`), which is why there is no window and no
    // price here: either one would charge everything a call did to one of the things it did.
    let served = Served::corpus();
    let db = served.db();
    let printed = popover(
        &served,
        &format!("/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"),
        &format!("{}:{DENSE_TOOL}", Kind::Tool),
    )
    .await;
    let call = rows::one(
        &db,
        "SELECT length(result) AS result_chars, api_call_id FROM live_tool_calls \
         WHERE session_id = $session AND source = $source AND id = $tool",
        &[
            ("session", Param::from(FORK_ORIGIN)),
            ("source", Param::from(FORK_ORIGIN_RUN)),
            ("tool", Param::from(DENSE_TOOL)),
        ],
    );
    assert_eq!(
        printed["result_chars"],
        counted(call.i64("result_chars").expect("a length"))
    );
    // And none of the numbers a tool call has no business printing.
    for absent in ["fill", "window", "cost_usd"] {
        assert!(!printed.contains_key(absent), "{absent} in {printed:?}");
    }
    // The other tool calls the same api call made, named the way every other surface names one: the
    // glyph that stands for the tool, then the field that tells two of its calls apart. Restated
    // here rather than read through the registry — an oracle that imported it would agree with
    // whatever it said. Every sibling of this one is a `Read`.
    let beside: Vec<String> = rows::all(
        &db,
        "SELECT json_extract_string(t.input, '$.file_path') AS path FROM live_tool_calls t \
         WHERE t.session_id = $session AND t.source = $source AND t.api_call_id = $call \
           AND t.id <> $tool ORDER BY t.\"index\"",
        &[
            ("session", Param::from(FORK_ORIGIN)),
            ("source", Param::from(FORK_ORIGIN_RUN)),
            (
                "call",
                Param::from(call.str("api_call_id").expect("a call")),
            ),
            ("tool", Param::from(DENSE_TOOL)),
        ],
    )
    .iter()
    .map(|row| format!("📖 {}", row.str("path").expect("a path")))
    .collect();
    assert!(
        !beside.is_empty(),
        "the fixture's dense call is meant to have made more than one tool call"
    );
    // Named exactly, and in the order the api call asked for them: redaction flattens most of these
    // titles to one word, so anything less than an exact match would pass on a popover that named
    // the wrong calls.
    assert_eq!(
        printed["siblings"],
        beside[..beside.len().min(5)].join(", ")
    );

    // And the one recorded api call that asked for two different tools at once, which is what says
    // the list is named per row rather than by whatever the first row was: `SPINE`'s tool search was
    // made beside a `Bash` call, so each of the two popovers names the other under its own glyph
    // (`tests/fixtures/spine/README.md`).
    let searched = popover(
        &served,
        &format!("/session/{SPINE}/thread/{MAIN}/tool/{SEARCH_TOOL}"),
        &format!("{}:{SEARCH_TOOL}", Kind::Tool),
    )
    .await;
    // A long command arrives at the width a header's list is read at, so it is a head.
    assert!(
        searched["siblings"].starts_with("⚡ ls -la "),
        "{}",
        searched["siblings"]
    );
    let ran = popover(
        &served,
        &format!("/session/{SPINE}/thread/{MAIN}/tool/{SEARCH_BASH_TOOL}"),
        &format!("{}:{SEARCH_BASH_TOOL}", Kind::Tool),
    )
    .await;
    assert_eq!(ran["siblings"], "🧰 select:PushNotification");
}

/// One thread's turn ids in the order they were recorded.
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

/// Python's `round(value, places)`, which is how every figure above is compared.
fn round(value: f64, places: i32) -> f64 {
    let scale = 10f64.powi(places);
    (value * scale).round() / scale
}
