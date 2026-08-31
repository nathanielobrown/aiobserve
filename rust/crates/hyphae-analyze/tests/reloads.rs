//! What a thread paid to rebuild a context it already had, and which kinds of thread run out
//! of one.
//!
//! The `agent_compactions`, `context_reloads`, `idle_gaps` and `reload_cost_split` half of
//! `tests/analyze/test_counts.py`. The leaves are about which thread an event is counted
//! under, what a period's roll-up totals, and where a caller's bound cuts. Nothing here is
//! planted: `compaction/`'s agent run compacted, and three recorded fixture threads rebuilt
//! their whole context mid-run. The two counts that do need a plant are `failures.rs`.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use duckdb::types::Value;
use hyphae_store::{Param, Row};
use hyphae_testsupport::{cache, landmarks, windows};

mod common;

use common::{corpus, count, of_period, probe};

/// One thread's row, keyed as `context_reloads` grains them: session and source.
type Threads = BTreeMap<(String, String), Row>;

/// The row `agent_compactions` gives a session's own thread, so a definition's rate has the
/// thing it has to beat beside it. The query writes the sentinel; nothing in Rust reads it.
const MAIN_THREAD: &str = "(main thread)";
/// The sentinel a rolled-up row carries where a thread kind would sit, `context_reloads`'s and
/// `reload_cost_split`'s alike.
const ALL_THREAD_KINDS: &str = "(all)";
/// The definition of the one recorded run that compacted, and how many threads it has: the
/// ratio a reader ranks definitions by needs a denominator bigger than the compaction.
const COMPACTED_DEFINITION: &str = "general-purpose";
const COMPACTED_RUN_THREADS: i64 = 3;

// The three threads of the recorded corpus that rebuilt their context mid-run, and what each
// rebuilt. `TEAMMATE_RUN` is the sharper case: both of its calls read nothing back, so its
// opening load is a rebuild in every respect except being the one the thread started with.
const ARCHITECT_DEFINITION: &str = "architect";
const ARCHITECT_OPENING_TOKENS: i64 = 23_444;
const ARCHITECT_RELOAD_TOKENS: i64 = 89_383;
/// The silence its rebuild followed: 6,035 seconds, an hour and forty minutes — shorter than
/// the two main-thread waits below, which is what puts the corpus's idle reloads either side
/// of a bound.
const ARCHITECT_IDLE_SECONDS: i64 = 6_035;
/// `SPINE`'s main thread went 23,276 seconds — 6h27m — between two calls and rebuilt 94,194
/// tokens on the far side, so its gap is what a rebound `$idle_seconds` can be walked past.
const SPINE_RELOAD_TOKENS: i64 = 94_194;
const SPINE_IDLE_SECONDS: i64 = 23_276;
/// `COMPACTED`'s main thread is the third, and the only one whose rebuild followed a
/// compaction: 21,648 seconds of silence over a boundary, 36,465 tokens on the far side.
const COMPACTED_RELOAD_TOKENS: i64 = 36_465;
const COMPACTED_IDLE_SECONDS: i64 = 21_648;
/// The shortest silence the recorded corpus has over the five-minute floor: the 302 seconds
/// `COMPACTED`'s agent run spent compacting and rebuilding. The silence that pins the measure
/// is `ANCESTOR`'s — 319 seconds between two requests, 281 from the first one's reply. A cache
/// entry ages from the request that wrote it, so it clears the 300-second floor; measured end
/// to start it would fall out of the table.
const SHORTEST_IDLE_SECONDS: i64 = 302;
const REQUEST_MEASURED_IDLE_SECONDS: i64 = 319;
/// How many silences over that floor the recorded corpus holds: nine in main threads, two in
/// agent runs. The raw table holds two more — `corpus_api_calls` hides a resumed thread's
/// replayed rows, and a gap between two of them is not the corpus's to count.
const RECORDED_IDLE_GAPS: usize = 11;
/// The two shares `idle_gaps` and `context_reloads` have to agree at: the production one, and
/// a looser one that admits more rebuilds.
const REBUILT_SHARES: &[&str] = &["90", "50"];
/// Both periods a corpus query answers in.
const PERIODS: &[&str] = &["corpus", "trailing_window"];

// ---------------------------------------------------------------------------
// agent_compactions

/// A run that ran out of context is counted against its definition, not its session.
#[test]
fn agent_compactions_counts_a_compaction_under_the_thread_that_had_it() {
    let db = cache::corpus_store();

    // If one compaction happened inside an agent run rather than on a main thread —
    // `compaction/`'s `general-purpose` run, the only one the corpus records...
    let rows = compactions(&db);

    // ...then it is counted under that run's definition, once, and against every run the
    // definition has — which is the ratio a reader ranks definitions by...
    let definition = &rows[COMPACTED_DEFINITION];
    assert_eq!(count(definition, "compactions"), 1);
    assert_eq!(count(definition, "compacting_threads"), 1);
    assert_eq!(count(definition, "threads"), COMPACTED_RUN_THREADS);
    assert_eq!(
        definition.f64("compactions_per_thread").expect("a rate"),
        round(1.0 / COMPACTED_RUN_THREADS as f64, 2)
    );

    // ...and it is counted there instead of under the session's own thread: every compaction
    // the period holds is in exactly one row, so the column sums to the store's own total.
    let total = count(
        &probe(
            &db,
            "SELECT count(*) AS total FROM corpus_compactions k
             JOIN sessions s ON s.id = k.session_id WHERE s.project_dir = $project",
            &[("project", Param::Text(landmarks::MYCELIA.into()))],
        ),
        "total",
    );
    assert!(total > 1);
    let counted: i64 = rows.values().map(|row| count(row, "compactions")).sum();
    assert_eq!(counted, total);
}

/// The main thread's row says both how many sessions compacted and how often they did.
#[test]
fn agent_compactions_separates_how_many_threads_from_how_often() {
    let db = cache::corpus_store();

    // If one session's main thread compacted twice and others compacted once...
    let recorded = probe(
        &db,
        "SELECT count(DISTINCT k.session_id) AS threads, count(*) AS compactions
         FROM corpus_compactions k JOIN sessions s ON s.id = k.session_id
         WHERE s.project_dir = $project AND k.source = 'main'",
        &[("project", Param::Text(landmarks::MYCELIA.into()))],
    );
    let compacted = count(&recorded, "threads");
    let times = count(&recorded, "compactions");
    assert!(times > compacted);

    // ...then the main-thread row keeps the two apart, so "most sessions compact" and "a few
    // sessions compact repeatedly" cannot be read for one another...
    let rows = compactions(&db);
    let main = &rows[MAIN_THREAD];
    assert_eq!(count(main, "compacting_threads"), compacted);
    assert_eq!(count(main, "compactions"), times);

    // ...and its population is every session in the period, not only the ones that compacted,
    // so the rate underneath is a rate and not a share of the sessions that already did.
    let sessions = count(
        &probe(
            &db,
            "SELECT count(*) AS sessions FROM sessions WHERE project_dir = $project",
            &[("project", Param::Text(landmarks::MYCELIA.into()))],
        ),
        "sessions",
    );
    assert_eq!(count(main, "threads"), sessions);

    // ...while a definition that never compacted still gets a row, which is what makes the
    // absence readable: a missing row would look like a definition nobody ran.
    assert!(rows.values().any(|row| count(row, "compactions") == 0));
}

// ---------------------------------------------------------------------------
// context_reloads

/// A thread's opening load is not a reload, however cold it was.
#[test]
fn context_reloads_leaves_out_the_context_a_thread_loaded_to_start() {
    let db = cache::corpus_store();

    // If a run's first call read nothing back and wrote its whole prompt to the cache — the
    // shape of a reload, and above the floor one has to clear...
    let opening = probe(
        &db,
        r#"SELECT cache_read_tokens, cache_creation_tokens FROM api_calls
           WHERE session_id = $session AND source = $source ORDER BY "index" LIMIT 1"#,
        &[
            ("session", Param::Text(landmarks::TEAMMATE.into())),
            ("source", Param::Text(landmarks::TEAMMATE_RUN.into())),
        ],
    );
    assert_eq!(count(&opening, "cache_read_tokens"), 0);
    assert_eq!(
        count(&opening, "cache_creation_tokens"),
        ARCHITECT_OPENING_TOKENS
    );

    // ...then it is the later rebuild alone that the run's row counts, because a thread that
    // loads its context once has not started over...
    let rows = reloads(&db, &[], "corpus");
    let row = &threads(&rows)[&thread(landmarks::TEAMMATE, landmarks::TEAMMATE_RUN)];
    assert_eq!(count(row, "reloads"), 1);
    assert_eq!(count(row, "rebuilt_tokens"), ARCHITECT_RELOAD_TOKENS);

    // ...filed under the definition that ran it and the session that spawned it, which is the
    // row a report cites when it names a run...
    assert_eq!(
        row.str("agent_type").expect("a definition"),
        ARCHITECT_DEFINITION
    );

    // ...and what the whole run cost rides beside it, so the rebuild is readable as a share of
    // the spend it taxed rather than as a number with no denominator.
    let reload_cost = row.f64("reload_cost_usd").expect("a cost");
    assert!(reload_cost > 0.0);
    assert!(reload_cost < row.f64("thread_cost_usd").expect("a cost"));
}

/// The idle gap classifies a reload; it never decides whether one is counted.
#[test]
fn context_reloads_says_which_reloads_an_expired_cache_explains() {
    let db = cache::corpus_store();

    // If a thread went hours between two calls and rebuilt everything on the far side...
    let key = thread(landmarks::SPINE, landmarks::MAIN);
    let rows = reloads(&db, &[], "corpus");
    let row = &threads(&rows)[&key];
    assert_eq!(count(row, "reloads"), 1);
    assert_eq!(count(row, "rebuilt_tokens"), SPINE_RELOAD_TOKENS);

    // ...then at the five minutes a cache entry lives, the gap accounts for the miss...
    assert_eq!(count(row, "idle_reloads"), 1);

    // ...while asking for a gap longer than the thread's leaves the reload counted and no
    // longer accounted for — which is the reading the column exists to keep honest, since a
    // miss with the thread still working is a miss the transcript cannot explain.
    let beyond = (SPINE_IDLE_SECONDS + 1).to_string();
    let rows = reloads(&db, &[("idle_seconds", &beyond)], "corpus");
    let patient = &threads(&rows)[&key];
    assert_eq!(count(patient, "reloads"), 1);
    assert_eq!(count(patient, "idle_reloads"), 0);
}

/// The corpus row of a period is the sum of that period's thread rows.
#[test]
fn context_reloads_totals_the_threads_it_lists() {
    let db = cache::corpus_store();
    assert!(!PERIODS.is_empty());

    for period in PERIODS {
        // If a period holds several affected threads across several sessions...
        let rows = reloads(&db, &[], period);
        let listed = threads(&rows);
        assert!(listed.len() > 1, "{period}");

        // ...then the row above them totals the threads rather than the events, so no thread's
        // cost is counted once per reload it happened to hold...
        let totals: Vec<&Row> = rows
            .iter()
            .filter(|row| row.str("grain").expect("a grain") == "corpus")
            .collect();
        assert_eq!(totals.len(), 1, "{period}");
        let total = totals[0];
        assert_eq!(count(total, "threads"), listed.len() as i64, "{period}");
        let spent: f64 = listed
            .values()
            .map(|row| row.f64("thread_cost_usd").expect("a cost"))
            .sum();
        assert_eq!(
            total.f64("thread_cost_usd").expect("a cost"),
            spent,
            "{period}"
        );

        // ...and the counts a finding would quote add up the same way, which is what a session
        // sitting in both periods must not disturb.
        for column in ["reloads", "idle_reloads", "rebuilt_tokens"] {
            let summed: i64 = listed.values().map(|row| count(row, column)).sum();
            assert_eq!(count(total, column), summed, "{period} {column}");
        }
        let sessions: BTreeSet<&String> = listed.keys().map(|(session, _)| session).collect();
        assert_eq!(count(total, "sessions"), sessions.len() as i64, "{period}");
    }
}

/// A session that sits in both periods is measured once, not once per period.
#[test]
fn context_reloads_reads_a_call_once_however_many_periods_hold_it() {
    let db = cache::corpus_store();

    // If every session of the corpus is also inside the trailing window, so both periods hold
    // exactly the same threads...
    let whole = reloads(&db, &[], "corpus");
    let windowed = reloads(&db, &[], "trailing_window");
    let (whole, windowed) = (threads(&whole), threads(&windowed));
    assert_eq!(
        whole.keys().collect::<Vec<_>>(),
        windowed.keys().collect::<Vec<_>>()
    );

    // ...then the two periods report identical numbers for every one of them. The gap and the
    // thread's first call are read from the calls of one thread, and `session_period` carries
    // an in-window session twice — so a query that fanned the periods out before measuring
    // would sit each call next to its own copy and report gaps of zero. DuckDB is free to
    // compute the windows before the join, which hides that mistake on some runs, so read this
    // leaf as a probabilistic guard on the join order and the query's own note as the rule.
    for (key, row) in &whole {
        assert_eq!(besides_the_period(&windowed[key]), besides_the_period(row));
    }

    // ...and at least one of those threads had a gap to measure, so the agreement is evidence.
    assert!(whole.values().any(|row| count(row, "idle_reloads") > 0));
}

// ---------------------------------------------------------------------------
// idle_gaps

/// A silence the other query marks idle arrives here as its length in seconds.
#[test]
fn idle_gaps_gives_the_wait_that_context_reloads_only_flags() {
    let db = cache::corpus_store();

    // If a thread went hours between two calls and rebuilt everything on the far side, so
    // `context_reloads` counts one idle reload against it...
    let rows = reloads(&db, &[], "corpus");
    let key = thread(landmarks::SPINE, landmarks::MAIN);
    assert_eq!(count(&threads(&rows)[&key], "idle_reloads"), 1);

    // ...then that silence has a row of its own here, carrying the wait itself — the number a
    // reader needs to ask how much of a population sits under a break-even...
    let idle: Vec<Row> = gaps(&db, &[])
        .into_iter()
        .filter(|row| on_thread(row) == key && row.bool("reloaded").expect("a flag"))
        .collect();
    assert_eq!(idle.len(), 1);
    let idle = &idle[0];
    assert_eq!(count(idle, "idle_seconds"), SPINE_IDLE_SECONDS);

    // ...beside what the call that broke it rebuilt and which kind of thread waited, so a gap
    // can be priced without going back to the query that flagged it.
    assert_eq!(count(idle, "rebuilt_tokens"), SPINE_RELOAD_TOKENS);
    assert_eq!(idle.str("agent_type").expect("a kind"), MAIN_THREAD);

    // ...and beside the lifetime the wait was racing, since the call before it had paid for
    // hour-long cache entries — a six-hour silence outlives those too, but a threshold read
    // without that column would put every gap against the five-minute default.
    assert!(idle.bool("cached_1h").expect("a flag"));
}

/// A wait nothing rebuilt is a row too: it is the denominator of the waits that did.
#[test]
fn idle_gaps_keeps_the_silences_that_ended_in_no_rebuild() {
    let db = cache::corpus_store();

    // If the corpus holds waits over the floor that cost nothing on the far side...
    let recorded = gaps(&db, &[]);
    assert!(
        recorded
            .iter()
            .any(|row| !row.bool("reloaded").expect("a flag"))
    );

    // ...then each is listed once however many periods hold its session, because a detail row
    // counted twice is a population sized twice...
    let keys: BTreeSet<(String, String, chrono::DateTime<chrono::Utc>)> = recorded
        .iter()
        .map(|row| {
            let (session, source) = on_thread(row);
            (
                session,
                source,
                row.timestamp("gap_start").expect("a start"),
            )
        })
        .collect();
    assert_eq!(keys.len(), recorded.len());
    assert_eq!(recorded.len(), RECORDED_IDLE_GAPS);

    // ...each one measured request to request, the interval a cache entry ages over: one
    // silence ran 319 seconds between requests and 281 from the first reply, and it is the
    // request pair that decides it clears the five-minute floor...
    let lengths: BTreeSet<i64> = recorded
        .iter()
        .map(|row| count(row, "idle_seconds"))
        .collect();
    assert!(lengths.contains(&REQUEST_MEASURED_IDLE_SECONDS));
    assert_eq!(lengths.first().copied(), Some(SHORTEST_IDLE_SECONDS));

    // ...and the floor is the caller's: dropped to nothing it admits the short waits no cache
    // could have expired over, and raised past the longest silence it admits none.
    assert!(gaps(&db, &[("min_idle_seconds", "0")]).len() > recorded.len());
    let beyond = (lengths.last().expect("a longest wait") + 1).to_string();
    assert!(gaps(&db, &[("min_idle_seconds", &beyond)]).is_empty());
}

/// The gaps that ended in a rebuild are exactly the idle reloads the other query counts.
#[test]
fn idle_gaps_calls_the_same_waits_idle_that_context_reloads_does() {
    let db = cache::corpus_store();
    assert!(!REBUILT_SHARES.is_empty());

    // If both queries are asked what a rebuild is on the same terms — the shared detector, at
    // its production share and at a looser one...
    for share in REBUILT_SHARES {
        let bindings = [("min_rebuilt_pct", *share)];
        let reloaded = gaps(&db, &bindings)
            .into_iter()
            .filter(|row| row.bool("reloaded").expect("a flag"))
            .count();
        let rows = reloads(&db, &bindings, "corpus");
        let totals: Vec<&Row> = rows
            .iter()
            .filter(|row| row.str("grain").expect("a grain") == "corpus")
            .collect();
        assert_eq!(totals.len(), 1, "{share}");

        // ...then the silences this one says were followed by a rebuild are, one for one, the
        // `idle_reloads` the other counts. The two answer one question at two grains, so a
        // reader who thresholds these lengths is narrowing that count rather than a different
        // one.
        let counted = count(totals[0], "idle_reloads");
        assert!(counted > 0, "{share}");
        assert_eq!(reloaded as i64, counted, "{share}");
    }
}

// ---------------------------------------------------------------------------
// reload_cost_split

/// The tokens rebuilt after short silences, as a share of everything idle waits rebuilt.
#[test]
fn reload_cost_split_says_what_share_of_a_rebuild_bill_short_waits_ran_up() {
    let db = cache::corpus_store();

    // If the corpus's three idle reloads sit either side of a bound — `SPINE`'s six-hour main
    // thread wait over it, `COMPACTED`'s six-hour one and an agent run's hour and forty
    // minutes under it. Keyed by thread, because two of the three waited on a main thread...
    let waits: BTreeMap<(String, String), i64> = gaps(&db, &[])
        .iter()
        .filter(|row| row.bool("reloaded").expect("a flag"))
        .map(|row| (on_thread(row), count(row, "idle_seconds")))
        .collect();
    assert_eq!(
        waits,
        BTreeMap::from([
            (
                thread(landmarks::SPINE, landmarks::MAIN),
                SPINE_IDLE_SECONDS
            ),
            (
                thread(landmarks::COMPACTED, landmarks::MAIN),
                COMPACTED_IDLE_SECONDS
            ),
            (
                thread(landmarks::TEAMMATE, landmarks::TEAMMATE_RUN),
                ARCHITECT_IDLE_SECONDS
            ),
        ])
    );

    // ...then splitting at the longest puts two reloads on the short side and one above...
    let rows = split(&db, SPINE_IDLE_SECONDS);
    let whole = &rows[ALL_THREAD_KINDS];
    assert_eq!(count(whole, "reloads"), 3);
    assert_eq!(count(whole, "short_reloads"), 2);

    // ...and the query's two shares come out as different numbers, which is the whole reason
    // it exists: two thirds of the events are not two thirds of the bill.
    let short_tokens = ARCHITECT_RELOAD_TOKENS + COMPACTED_RELOAD_TOKENS;
    let every_token = short_tokens + SPINE_RELOAD_TOKENS;
    let two_thirds = round(100.0 * 2.0 / 3.0, 1);
    assert_eq!(whole.f64("short_reload_pct").expect("a share"), two_thirds);
    assert_eq!(count(whole, "rebuilt_tokens"), every_token);
    assert_eq!(count(whole, "short_rebuilt_tokens"), short_tokens);
    let share = round(100.0 * short_tokens as f64 / every_token as f64, 1);
    assert_eq!(whole.f64("short_token_pct").expect("a share"), share);
    assert_ne!(share, two_thirds);

    // ...filed under the kind of thread that waited, so a recommendation scoped to short gaps
    // can say which threads it would apply to instead of inferring it from a corpus total.
    assert_eq!(
        count(&rows[ARCHITECT_DEFINITION], "short_rebuilt_tokens"),
        ARCHITECT_RELOAD_TOKENS
    );
    assert_eq!(
        count(&rows[MAIN_THREAD], "short_rebuilt_tokens"),
        COMPACTED_RELOAD_TOKENS
    );
}

/// Every wait is in the split, not only the ones that ended in a rebuild.
#[test]
fn reload_cost_split_counts_the_silences_that_rebuilt_nothing() {
    let db = cache::corpus_store();

    // If the corpus holds more silences than reloads — the denominator a keep-warm heartbeat
    // would fire over, since it pays on the waits that would have cost nothing too...
    let recorded = gaps(&db, &[]);
    let rows = split(&db, SPINE_IDLE_SECONDS);
    let whole = &rows[ALL_THREAD_KINDS];
    assert_eq!(recorded.len(), RECORDED_IDLE_GAPS);
    assert_eq!(count(whole, "gaps"), RECORDED_IDLE_GAPS as i64);
    assert!(count(whole, "gaps") > count(whole, "reloads"));
    let short = recorded
        .iter()
        .filter(|row| count(row, "idle_seconds") < SPINE_IDLE_SECONDS)
        .count();
    assert_eq!(count(whole, "short_gaps"), short as i64);

    // ...and the thread kinds under it partition that population, so no wait is counted twice
    // or dropped between them.
    let kinds: i64 = rows
        .iter()
        .filter(|(kind, _)| kind.as_str() != ALL_THREAD_KINDS)
        .map(|(_, row)| count(row, "gaps"))
        .sum();
    assert_eq!(kinds, RECORDED_IDLE_GAPS as i64);
}

/// The bound is the caller's, and a wait is short only when it ran strictly under it.
#[test]
fn reload_cost_split_is_bound_at_the_length_the_caller_names() {
    let db = cache::corpus_store();

    // If the bound is raised by one second past the longest reloaded wait...
    let inclusive = split(&db, SPINE_IDLE_SECONDS + 1);
    let whole = &inclusive[ALL_THREAD_KINDS];
    // ...then that wait joins the short side and the whole bill sits on it — which is what
    // pins the comparison as strict rather than inclusive, since the bound at the wait's own
    // length left it out above.
    assert_eq!(count(whole, "short_reloads"), 3);
    assert_eq!(whole.f64("short_token_pct").expect("a share"), 100.0);

    // ...while a bound under every recorded silence reports a share of zero rather than an
    // empty one: no short reload is a number, not a missing answer.
    let none = split(&db, SHORTEST_IDLE_SECONDS);
    let whole = &none[ALL_THREAD_KINDS];
    assert_eq!(count(whole, "short_gaps"), 0);
    assert_eq!(count(whole, "short_reloads"), 0);
    assert_eq!(whole.f64("short_token_pct").expect("a share"), 0.0);
    assert_eq!(whole.f64("short_reload_pct").expect("a share"), 0.0);
}

// ---------------------------------------------------------------------------
// The shapes these read their answers in

/// `context_reloads` over the fixture project, as the rows of one period.
fn reloads(db: &Path, bindings: &[(&str, &str)], period: &str) -> Vec<Row> {
    of_period(
        &corpus(db, "context_reloads", windows::AS_OF_WHOLE, bindings),
        period,
    )
}

/// `idle_gaps` over the fixture project, one row per silence. It answers at one grain rather
/// than in periods, so there is nothing to narrow.
fn gaps(db: &Path, bindings: &[(&str, &str)]) -> Vec<Row> {
    corpus(db, "idle_gaps", windows::AS_OF_WHOLE, bindings).rows
}

/// `reload_cost_split` over the fixture project, by thread kind, for the corpus period.
fn split(db: &Path, short_gap_seconds: i64) -> BTreeMap<String, Row> {
    let bound = short_gap_seconds.to_string();
    let rows = of_period(
        &corpus(
            db,
            "reload_cost_split",
            windows::AS_OF_WHOLE,
            &[("short_gap_seconds", &bound)],
        ),
        "corpus",
    );
    common::key(&rows, "agent_type")
}

/// `agent_compactions` over the fixture project, by `agent_type`, for the corpus period.
fn compactions(db: &Path) -> BTreeMap<String, Row> {
    let rows = of_period(
        &corpus(db, "agent_compactions", windows::AS_OF_WHOLE, &[]),
        "corpus",
    );
    common::key(&rows, "agent_type")
}

/// The thread-grain rows of one `context_reloads` result, by session and source.
fn threads(rows: &[Row]) -> Threads {
    rows.iter()
        .filter(|row| row.str("grain").expect("a grain") == "thread")
        .map(|row| (on_thread(row), row.clone()))
        .collect()
}

/// The thread a row is about, in the shape [`threads`] keys by.
fn on_thread(row: &Row) -> (String, String) {
    thread(
        row.str("session_id").expect("a session"),
        row.str("source").expect("a source"),
    )
}

fn thread(session: &str, source: &str) -> (String, String) {
    (session.to_owned(), source.to_owned())
}

/// Every column of a row but the period it was answered for — what two periods reporting the
/// same thread have to agree on.
fn besides_the_period(row: &Row) -> Vec<(&String, &Value)> {
    row.columns()
        .iter()
        .zip(row.values())
        .filter(|(column, _)| column.as_str() != "period")
        .collect()
}

/// DuckDB's own rounding — half away from zero, which is not what Rust's `round` on a scaled
/// float always gives and is not Python's banker's rounding either.
fn round(number: f64, places: u32) -> f64 {
    let scale = 10f64.powi(places as i32);
    (number * scale).round() / scale
}
