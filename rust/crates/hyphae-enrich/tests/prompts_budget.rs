//! What never reaches a prompt, what is cut to fit, and what the hash reads.
//!
//! Ported from `tests/enrich/test_prompts__budget.py`. Every fixture string outside a small
//! structural keep-list is redacted to `[redacted]`, which is why the exclusion leaves plant a
//! **labelled sentinel** into one field of a real row: a render that included `thinking` and one
//! that excluded it produce identical characters otherwise. The same redaction is why the cap
//! leaves inject a small budget — no recorded row comes near the real ones, which is what the
//! two gated corpus sweeps at the bottom are for.

use duckdb::params;
use hyphae_enrich::prompts::{
    Budgets, RUN_BUDGETS, SESSION_BUDGETS, TURN_BUDGETS, input_hash, render_run, render_session,
    render_turn, width,
};
use hyphae_enrich::{EnrichmentStore, Item};

mod common;

use common::{
    DEEP_RESEARCH_SESSION, SERVER_TOOLS, SPINE, SPINE_RUN, TEAMMATE_RUN, describe, ended,
    open_copy, run, session, turn,
};

/// A string no redacted fixture can contain, planted into one field per test.
const SENTINEL: &str = "SENTINEL-b4d1e7-content-that-must-not-travel";

/// A command that printed pages of output costs its budget and no more of the render.
///
/// The body is invented and oversized: the longest recorded one is 2,038 characters, and the
/// next `/context` can beat that. Rendered at the real `total`, so the cap is the subject and
/// not the elision.
#[test]
fn a_long_command_result_is_capped_and_still_ends_with_how_it_ended() {
    let (_scratch, store) = open_copy();
    // If a command printed 100,000 characters, against the recorded `/model` turn...
    let body = serde_json::json!({
        "parentUuid": "5b848af7-f86e-4950-b474-cd98125fad24",
        "type": "system",
        "content": format!("<local-command-stdout>{}</local-command-stdout>", "x".repeat(100_000)),
    })
    .to_string();
    store
        .connection()
        .execute(
            "UPDATE raw_records SET raw = ? WHERE session_id = ? AND line_no = 8",
            params![body, SPINE],
        )
        .expect("the oversized body plants");
    let rendered = render_turn(&turn(&store, SPINE, "5b848af7"), &TURN_BUDGETS);
    // ...then the block carries its budget's worth and counts what it dropped...
    assert!(rendered.contains("## Command result\nxxx"));
    // ...the marker comes out of the budget rather than riding on top of it: 1,985 characters
    // of body and a 15-character marker are the 2,000 the budget allows...
    assert!(rendered.contains("[+98015 chars]"));
    // ...and the render still fits and still ends by saying how the turn ended — the head is
    // protected from elision, so an unbounded body would have taken the `Ended:` line with it.
    assert!(width(&rendered) <= TURN_BUDGETS.total);
    assert_eq!(ended(&rendered), "## Ended: no model response");
}

/// Extended thinking is excluded from every prompt, whatever it holds.
#[test]
fn thinking_reaches_no_prompt() {
    // If a sentinel is planted into the thinking of a real `spine/` api call — invented
    // content in a recorded row, because redaction leaves every real string identical...
    let (_scratch, store) = open_copy();
    store
        .connection()
        .execute(
            "UPDATE api_calls SET thinking = ? WHERE session_id = ?",
            params![SENTINEL, SPINE],
        )
        .expect("the sentinel plants");
    // ...then no turn of that session carries it: 30.5 MB corpus-wide, and the cost estimate
    // assumes it is gone.
    for item in store.turn_items(None).expect("the turns read") {
        if item.session_id == SPINE {
            assert!(
                !render_turn(&item, &TURN_BUDGETS).contains(SENTINEL),
                "{}",
                item.key()
            );
        }
    }
}

/// A successful tool's output never travels — only how big it was.
#[test]
fn a_tool_result_reaches_no_prompt_but_its_size_does() {
    // If a sentinel is planted into the result of a real, non-error `spine/` tool call...
    let (_scratch, store) = open_copy();
    store
        .connection()
        .execute(
            "UPDATE tool_calls SET result = ? WHERE id = 'toolu_015dP3eMe5GZn7BzFipupZwS'",
            params![SENTINEL],
        )
        .expect("the sentinel plants");
    let rendered = render_turn(&turn(&store, SPINE, "818588ad"), &TURN_BUDGETS);
    // ...then the prompt carries none of it — results are 390 MB corpus-wide, and including
    // them would dominate every prompt...
    assert!(!rendered.contains(SENTINEL));
    // ...but it does carry the length of that same column, which is the one-number signal
    // behind every context-bloat finding.
    assert!(rendered.contains(&format!("result {} chars", width(SENTINEL))));
}

/// A failed tool call carries the tail of its error, which is where friction shows.
#[test]
fn an_error_result_tail_is_the_one_exception() {
    // If `server_tools/`'s one recorded failing call is rendered...
    let (_scratch, store) = open_copy();
    let item = turn(&store, SERVER_TOOLS, "9ae45aaa");
    // ...then its line is flagged and carries the error text...
    assert!(render_turn(&item, &TURN_BUDGETS).contains(
        "- advisor (input 2 chars, result 11 chars, ERROR) {} | error tail: unavailable"
    ));
    // ...and the tail is a tail: capped at the budget's size, the *end* of the message
    // survives. Injected small, since no recorded error runs to the real 300 chars. Four
    // characters is all four characters of error text: the count of what was dropped comes out
    // of the budget too, and here there is no room for it.
    let capped = render_turn(
        &item,
        &Budgets {
            error_tail: 4,
            ..TURN_BUDGETS
        },
    );
    assert!(capped.contains("| error tail: able"));
}

/// A tool line names what the tool was called on, by carrying the head of its input.
#[test]
fn the_tool_input_head_is_the_head() {
    // If `workflow/`'s `Workflow` call is rendered...
    let (_scratch, store) = open_copy();
    let item = turn(&store, DEEP_RESEARCH_SESSION, "cd7adeae");
    // ...then the line carries the input's own first characters — the workflow's name here, a
    // file path or a command elsewhere — not a hash and not the tool name again.
    assert!(render_turn(&item, &TURN_BUDGETS).contains("{\"name\": \"deep-research\""));
    // ...and past the budget's head size it stops, saying how much it left behind. The marker
    // comes out of the budget rather than riding on top of it: nine characters of input and an
    // eleven-character marker are the twenty the budget allows.
    let capped = render_turn(
        &item,
        &Budgets {
            input_head: 20,
            ..TURN_BUDGETS
        },
    );
    assert!(capped.contains("- Workflow (input 47 chars, result 10 chars) {\"name\": [+38 chars]"));
}

/// The staleness hash moves when the prompt does, and only then.
#[test]
fn input_hash_reads_the_rendered_content_and_nothing_else() {
    let (_scratch, store) = open_copy();
    let rendered = |store: &EnrichmentStore| {
        input_hash(&render_turn(&turn(store, SPINE, "818588ad"), &TURN_BUDGETS))
    };
    // If the same turn is rendered twice, the hash is the same...
    let before = rendered(&store);
    assert_eq!(before, rendered(&store));
    // ...if a field the render reads changes — a tool call's name...
    store
        .connection()
        .execute(
            "UPDATE tool_calls SET name = 'Grep' WHERE id = 'toolu_015dP3eMe5GZn7BzFipupZwS'",
            [],
        )
        .expect("the rename lands");
    let renamed = rendered(&store);
    // ...then the hash moves, so the turn re-enriches...
    assert_ne!(renamed, before);
    // ...and if a field the render does not read changes, it does not, so a re-extract that
    // changed no text re-buys nothing.
    store
        .connection()
        .execute(
            "UPDATE api_calls SET request_id = 'req_rewritten' WHERE session_id = ?",
            params![SPINE],
        )
        .expect("the rewrite lands");
    assert_eq!(rendered(&store), renamed);
}

/// Past its budget a turn drops the middle of its call sequence and says how much went.
#[test]
fn an_over_budget_turn_drops_the_middle_of_its_work() {
    // If `spine/`'s longest turn — three tool calls under one response — is rendered at a
    // budget of 300 characters, two thirds of the 463 it needs (injected, because redaction
    // leaves no fixture within two orders of magnitude of the real 30K)...
    let (_scratch, store) = open_copy();
    let elided = render_turn(
        &turn(&store, SPINE, "30aad8e5"),
        &Budgets {
            total: 300,
            ..TURN_BUDGETS
        },
    );
    // ...then the render fits, and what it kept is the prompt, the start of the work and the
    // last thing the turn did — the two ends a description is written from. The middle went,
    // and the gap counts itself rather than reading as the whole sequence. The `Ended:` line is
    // the tail of the elidable sequence, not part of the protected head, so a budget this small
    // keeps it the same way it keeps the last tool call.
    assert_eq!(
        elided,
        "\
# Main turn

## Command
/night-run [redacted]

## Command result: not recorded

## Response
[redacted]
[… 2 of 8 lines elided …]
- Read (input 58 chars, unanswered) {\"file_path\": \"/Users/nob/repos/mycelia/issues/README.md\"}

## Ended: tool_use"
    );
    assert!(width(&elided) <= 300);
}

/// Every prompt of a run gets the whole per-prompt budget, not a share of one.
#[test]
fn each_instruction_is_capped_on_its_own() {
    // If the two-instruction run is rendered at a per-prompt cap of four characters (injected:
    // redaction leaves each recorded prompt at ten, so the real 4K cannot bite)...
    let (_scratch, store) = open_copy();
    let capped = render_run(
        &run(&store, TEAMMATE_RUN),
        &Budgets {
            prompt: 4,
            ..RUN_BUDGETS
        },
    );
    // ...then both instructions are still there, and each was truncated to four characters of
    // its own rather than to four between them.
    assert!(capped.contains("## Task\n[red\n"));
    assert!(capped.contains("## Instruction\n[red\n"));
}

/// Past its budget a run drops the middle of its call sequence and says how much went.
#[test]
fn an_over_budget_run_drops_the_middle_of_its_work() {
    // If `spine/`'s subagent run is rendered at 300 characters, half what it needs (injected —
    // 209 of 2,458 real runs hit the real 30K cap, and no fixture comes near it)...
    let (_scratch, store) = open_copy();
    let elided = render_run(
        &run(&store, SPINE_RUN),
        &Budgets {
            total: 300,
            ..RUN_BUDGETS
        },
    );
    // ...then the task and the start of the work survive, the last thing the run did survives,
    // the gap between them counts itself, and the `Ended:` line rides the tail.
    assert_eq!(
        elided,
        "\
# Agent run: claude

## Task
[redacted]

## Response
[redacted]
[… 4 of 10 lines elided …]
- Agent (input 132 chars, result 10 chars) {\"description\": \"Research 0155 data-edge \
semantics\", \"subagent_type\": \"Explore\", \"run_in_background\": false,[+24 chars]

## Ended: not recorded"
    );
    assert!(width(&elided) <= 300);
}

/// Past its budget a session keeps its first and last child and says how many went.
#[test]
fn an_over_budget_session_drops_the_middle_of_its_work() {
    // If `spine/`'s four described turns are rendered at a budget that fits two of them
    // (injected: real sessions reach 92 children, and no fixture comes near the real cap)...
    let (_scratch, store) = open_copy();
    for item in store.turn_items(None).expect("the turns read") {
        if item.session_id == SPINE {
            describe(&store, &item, &format!("Did thing {}.", item.index));
        }
    }
    let elided = render_session(
        &session(&store, SPINE),
        &Budgets {
            total: 300,
            ..SESSION_BUDGETS
        },
    );
    // ...then the session keeps how it opened and how it ended, and counts what it dropped.
    assert!(elided.ends_with(
        "\
## Work
- Main turn [explore/completed] Did thing 3.
[… 2 of 4 lines elided …]
- Main turn [explore/completed] Did thing 2."
    ));
    assert!(width(&elided) <= 300);
}

/// Names a real trace store for the opt-in budget checks below. Off by default: the store holds
/// private session data, and rendering a whole real corpus takes minutes.
const LIVE_STORE: &str = "HYPHAE_LIVE_STORE";

/// A private copy of the real archive `HYPHAE_LIVE_STORE` names, or None when it names nothing.
///
/// Never the store itself: it is the archive (`docs/store.md`) and opening one runs the
/// enrichment DDL against it. The write-ahead log comes along, or the copy would be the archive
/// as of its last checkpoint.
fn live_store_copy() -> Option<(tempfile::TempDir, EnrichmentStore)> {
    let archive = std::path::PathBuf::from(std::env::var_os(LIVE_STORE)?);
    let scratch = tempfile::TempDir::new().expect("a tempdir for the copy");
    let copy = scratch
        .path()
        .join(archive.file_name().expect("the archive is a file"));
    std::fs::copy(&archive, &copy).expect("the archive copies");
    let wal = archive.with_extension("duckdb.wal");
    if wal.exists() {
        std::fs::copy(&wal, copy.with_extension("duckdb.wal")).expect("the log copies");
    }
    let store = EnrichmentStore::open(&copy).expect("the copy opens for enrichment");
    Some((scratch, store))
}

/// Every turn and run in a real store renders within the budget the enricher would send.
///
/// The fixtures cannot show this: redaction leaves them two orders of magnitude short of the
/// cap, so this is the only check that the default budgets hold on real text — including the
/// command result block, which adds up to 2,054 characters to a turn's protected head.
///
/// Counts and keys only, never a rendered prompt: a real render is transcript content, and a
/// failing assertion prints its operands.
#[test]
fn no_real_item_renders_past_its_budget() {
    let Some((_scratch, store)) = live_store_copy() else {
        return;
    };
    let turns = store.turn_items(None).expect("the turns read");
    let runs = store.run_items(None).expect("the runs read");
    assert!(
        !turns.is_empty(),
        "{LIVE_STORE} names a store with no turns in it"
    );
    assert!(
        !runs.is_empty(),
        "{LIVE_STORE} names a store with no agent runs in it"
    );
    let mut over: Vec<String> = turns
        .iter()
        .filter(|item| width(&render_turn(item, &TURN_BUDGETS)) > TURN_BUDGETS.total)
        .map(Item::key)
        .collect();
    over.extend(
        runs.iter()
            .filter(|item| width(&render_run(item, &RUN_BUDGETS)) > RUN_BUDGETS.total)
            .map(Item::key),
    );
    assert_eq!(over, Vec::<String>::new());
}

/// Over the whole corpus, every archived command output is read, and none is unclassifiable.
///
/// The one place the archive read meets all the recorded sessions. The fixtures carry one
/// example of each shape by construction; this says the corpus holds no other.
///
/// Counts only, never the items: a turn item holds transcript content, and a failing assertion
/// prints its operands.
#[test]
fn every_real_command_turn_is_classified() {
    let Some((_scratch, store)) = live_store_copy() else {
        return;
    };
    // If the real store's turns are read — which fails on any record the shape guard cannot
    // classify, so reaching the next line is the guard's verdict on the whole corpus...
    let commands: Vec<_> = store
        .turn_items(None)
        .expect("the turns read")
        .into_iter()
        .filter(|item| item.command_name.is_some())
        .collect();
    assert!(
        !commands.is_empty(),
        "{LIVE_STORE} names a store with no command turns in it"
    );
    // ...and both carriers really are in use, so the `coalesce` is load-bearing rather than a
    // branch the corpus never takes — 279 and 37 recorded instances.
    let carriers = store
        .store()
        .fetch(
            "SELECT count(*) FILTER (WHERE json_extract_string(raw, '$.message.content')
                                           LIKE '%<local-command-stdout>%') AS nested,
                    count(*) FILTER (WHERE json_extract_string(raw, '$.content')
                                           LIKE '%<local-command-stdout>%') AS flat
             FROM raw_records WHERE raw LIKE '%<local-command-stdout>%'",
            &[],
        )
        .expect("the carrier counts read");
    let row = carriers.first().expect("the count query answers");
    for column in ["nested", "flat"] {
        assert!(
            row.i64(column).expect("the count reads") > 0,
            "no turn carries `{column}`"
        );
    }
    // ...then nearly every command turn the CLI answered by itself carries what it printed:
    // 272 of 280 (measured 2026-08-13), asserted as a floor rather than the count, since the
    // corpus grows. That class is the one the read serves — a turn that drove no api call has
    // nothing else to be described from. The wider population is deliberately not the
    // invariant: 143 of the 423 command turns drove the model instead (`/manager`, `/handoff`)
    // and only 39 of those archived an output, so a threshold over all 423 would measure how
    // people use slash commands rather than whether the read works.
    let quiet: Vec<Option<String>> = commands
        .iter()
        .filter(|item| item.api_calls.is_empty())
        .map(|item| item.command_result.clone())
        .collect();
    let answered = quiet.iter().filter(|result| result.is_some()).count();
    assert!(answered as f64 > quiet.len() as f64 * 0.95);
    // ...and both recorded states are really in there: bodies, and the empty ones `/clear`
    // writes. An empty share of zero would mean the read had stopped telling them apart.
    let empty = quiet
        .iter()
        .filter(|result| result.as_deref() == Some(""))
        .count();
    assert!(0 < empty && empty < answered);
}
