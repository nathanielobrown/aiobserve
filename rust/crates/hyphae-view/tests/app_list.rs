//! One row of the session list: what it holds, and how each cell is printed.
//!
//! Every expectation is derived from the store the app is serving rather than written down. The
//! numbers are re-counted through the corpus views and the order is re-derived in the test's own
//! SQL, so a fixture added to the corpus joins the expectation instead of falling out of it.

use chrono::Duration;
use duckdb::params;
use regex::Regex;

use hyphae_store::{Param, Store, queries};
use hyphae_testsupport::html::{Markup, counted, money};
use hyphae_testsupport::landmarks::{NO_PROJECT_SESSION, SPINE, SPINE_LEAF};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::{Served, listed_sessions};
use hyphae_view::format as fmt;

/// The list page, read.
async fn listing(served: &Served) -> Markup {
    let (_, page) = served.page("/sessions").await;
    Markup::of(&page)
}

#[tokio::test]
async fn the_list_holds_every_session_with_its_own_numbers() {
    // The list is one row per session, its counts stacked two to a cell over that session's
    // rollup — the primary someone scans for, and the texture under it.
    let served = Served::corpus();
    let db = served.db();
    let page = listing(&served).await;
    // Every session gets a row, and the default order is newest first...
    assert_eq!(page.values("data-session-id"), listed_sessions(&db));
    // ...whose cells are that session's rollup, not a number computed anywhere else.
    let row = page.fields("data-session-id", SPINE);
    let rollup = rows::one(
        &db,
        "SELECT turns, api_calls, tool_calls, compactions, cost_usd, output_tokens, \
         wall_ms, active_ms, started_at FROM session_rollups WHERE session_id = $session",
        &[("session", Param::from(SPINE))],
    );
    let errors = rows::one(
        &db,
        "SELECT count(*) AS failed FROM live_tool_calls WHERE session_id = $session AND is_error",
        &[("session", Param::from(SPINE))],
    )
    .i64("failed")
    .expect("a count");
    let tool_calls = rollup.i64("tool_calls").expect("a count");
    // The four plain counts, each through the same formatter every count on the page uses...
    for field in ["turns", "api_calls", "tool_calls", "compactions"] {
        let held = rollup.i64(field).expect("a count");
        assert_eq!(row[field], counted(held), "{field}");
    }
    // ...the stacked cells, whose secondary is the texture the recompose demoted rather than
    // dropped: what the errors were a rate of, what the spend bought, how long of the wall clock
    // was work. `tests/format.rs` owns what each of these strings looks like; this leaf owns
    // which of the session's values reaches which cell.
    assert_eq!(
        (row["error_rate"].as_str(), row["tool_errors"].as_str()),
        (
            fmt::share(Some(errors as f64), Some(tool_calls as f64)).as_str(),
            counted(errors).as_str()
        )
    );
    assert_eq!(
        (row["cost_usd"].as_str(), row["output_tokens"].as_str()),
        (
            money(rollup.f64("cost_usd").expect("a cost")).as_str(),
            counted(rollup.i64("output_tokens").expect("a count")).as_str()
        )
    );
    assert_eq!(
        (row["wall_ms"].as_str(), row["active_ms"].as_str()),
        (
            fmt::duration(rollup.opt_i64("wall_ms").expect("a duration")).as_str(),
            fmt::duration(rollup.opt_i64("active_ms").expect("a duration")).as_str()
        )
    );
    assert_eq!(
        row["started_at"],
        fmt::when(Some(rollup.timestamp("started_at").expect("a start")))
    );
    // ...and the unit word stands off the number under it, which is the one thing on this row no
    // `data-field` carries: `0 errors`, never `0errors`.
    assert!(
        page.reads("data-session-id", SPINE)
            .contains(&format!("{} errors", counted(errors)))
    );
}

#[tokio::test]
async fn a_column_the_store_left_null_reads_as_one_dash() {
    // A cell over a column the store holds nothing in prints a dash, not a blank.
    //
    // `fork_byref`'s fork is the recorded case on the list: it carries neither a project directory
    // nor a start, so its row is the one place the list has to say "the store does not know" out
    // loud. A run is the recorded case on a node page — most spawning calls name no model — so a
    // run's own pane is checked here too, against the same convention rather than against each
    // component's own idea of a gap. The sweep over every page the store can serve rides on
    // `tests/node.rs`, which already fetches them all.
    let served = Served::corpus();
    let row = listing(&served)
        .await
        .fields("data-session-id", NO_PROJECT_SESSION);
    assert_eq!(row["project_dir"], fmt::ABSENT);
    assert_eq!(row["started_at"], fmt::ABSENT);
    let (_, body) = served
        .page(&format!("/session/{SPINE}/run/{SPINE_LEAF}"))
        .await;
    assert_eq!(
        Markup::of(&body).fields("data-body", "run")["model"],
        fmt::ABSENT
    );
}

#[tokio::test]
async fn the_list_measures_freshness_against_the_clock_the_viewer_reads() {
    // How long ago a session ran is measured from that session's own start against the viewer's
    // clock, rather than against a value baked into the row when the store was written.
    //
    // The Python patches `fmt.utcnow` between two requests; a compiled server has no such seam, so
    // the clock is frozen by the environment before the app is built and the row is read against
    // it. Its own process, which is what nextest gives every leaf: the freeze is read once.
    let db = hyphae_testsupport::cache::corpus_store();
    let started = rows::one(
        &db,
        "SELECT started_at FROM session_rollups WHERE session_id = $session",
        &[("session", Param::from(SPINE))],
    )
    .timestamp("started_at")
    .expect("the spine session records a start");
    // SAFETY: nextest runs each test in its own process, and no thread has started here.
    unsafe {
        std::env::set_var(
            "HYPHAE_FIXED_NOW",
            (started + Duration::hours(2)).to_rfc3339(),
        )
    };
    let served = Served::corpus();
    let row = listing(&served).await.fields("data-session-id", SPINE);
    assert_eq!(row["ago"], "2h ago");
    // And the timestamp under it is the session's own start, unmoved by the clock above it.
    assert_eq!(row["started_at"], fmt::when(Some(started)));
}

#[tokio::test]
async fn the_errors_cell_shows_a_rate_over_the_count_it_sorts_by() {
    // A row's errors read as a share of the tools it ran, over the count itself.
    //
    // Both recorded failing-tool sessions, because the pair is what makes the rate worth showing:
    // one error in five calls and one in seven are the same count and different rates.
    let served = Served::corpus();
    let page = listing(&served).await;
    let failing = rows::all(
        &served.db(),
        "SELECT * FROM (SELECT r.session_id, r.tool_calls, \
         (SELECT count(*) FROM live_tool_calls t \
          WHERE t.session_id = r.session_id AND t.is_error) AS errors \
         FROM session_rollups r) WHERE errors > 0",
        &[],
    );
    assert!(
        failing.len() > 1,
        "the fixture corpus no longer records two failing sessions"
    );
    let mut rates = std::collections::BTreeSet::new();
    for row in &failing {
        let session_id = row.str("session_id").expect("a session id");
        let tool_calls = row.i64("tool_calls").expect("a count") as f64;
        let errors = row.i64("errors").expect("a count");
        let cell = page.fields("data-session-id", session_id);
        assert_eq!(
            cell["error_rate"],
            fmt::share(Some(errors as f64), Some(tool_calls)),
            "{session_id}"
        );
        assert_eq!(cell["tool_errors"], counted(errors), "{session_id}");
        rates.insert(cell["error_rate"].clone());
    }
    // The rates differ, so a cell showing the count where the rate belongs would fail above.
    assert!(rates.len() > 1);
}

#[tokio::test]
async fn every_number_a_list_row_prints_carries_its_separators() {
    // Every integer a row prints goes through the count formatter — no bare `{value}`.
    //
    // Planted, because no fixture session is large enough to tell a formatted count from an
    // unformatted one: the corpus's busiest session ran 78 turns. One session's turns and api
    // calls are cloned past a thousand, which is where the two spellings diverge.
    let over: i64 = 1_000;
    let served = Served::planted(move |store: &Store| {
        // Cloning recorded rows rather than inventing them: what a row counts is the `live_*`
        // population, and a clone of a real row is a member of it.
        for table in ["turns", "api_calls"] {
            store
                .connection()
                .execute(
                    &format!(
                        "INSERT INTO {table} (SELECT r.* REPLACE (r.id || '-planted-' || i AS id) \
                         FROM {table} r, range(1, ?) g(i) WHERE r.session_id = ? \
                         AND r.id = (SELECT min(id) FROM {table} WHERE session_id = ?))"
                    ),
                    params![over + 1, SPINE, SPINE],
                )
                .expect("the clones land");
        }
    });
    let row = listing(&served).await.fields("data-session-id", SPINE);
    // Every number the row prints is either grouped in threes or the dash a NULL prints...
    let grouped =
        Regex::new(&format!(r"^(\d{{1,3}}(,\d{{3}})*|{})$", fmt::ABSENT)).expect("a pattern");
    for field in [
        "turns",
        "api_calls",
        "tool_calls",
        "compactions",
        "tool_errors",
        "output_tokens",
    ] {
        assert!(
            grouped.is_match(&row[field]),
            "{field} prints {}",
            row[field]
        );
    }
    // ...and the plant really did push two of them past the point where that is a claim.
    assert!(row["turns"].contains(',') && row["api_calls"].contains(','));
}

#[tokio::test]
async fn the_subagents_cell_counts_the_runs_of_each_agent_type() {
    // A row says which agent types the session spawned and how many runs of each. The count is
    // what the recompose bought: `agent_runs` alone said a session spawned six subagents and not
    // what any of them were.
    let served = Served::corpus();
    let row = listing(&served).await.fields("data-session-id", SPINE);
    let kinds = rows::all(
        &served.db(),
        "SELECT agent_type, count(*) AS runs FROM live_agent_runs WHERE session_id = $session \
         GROUP BY 1 ORDER BY 2 DESC, 1",
        &[("session", Param::from(SPINE))],
    );
    assert!(
        !kinds.is_empty(),
        "the fixture session no longer spawns any agent runs"
    );
    let named: Vec<String> = kinds
        .iter()
        .map(|kind| {
            format!(
                "{} ×{}",
                kind.str("agent_type").expect("an agent type"),
                counted(kind.i64("runs").expect("a count"))
            )
        })
        .collect();
    assert_eq!(row["agent_types"], named.join(", "));
}

#[tokio::test]
async fn the_subagents_cell_ranks_by_count_and_says_what_it_cut() {
    // The list is ordered by runs descending and cut like the skills beside it.
    //
    // Planted twice over: no fixture session runs one agent type twice, and none spawns more types
    // than the cell shows. Both are properties of a redacted corpus rather than of the store the
    // viewer serves, so the row is built to have them.
    let over = (queries::LIST_ITEMS + 2) as i64;
    let served = Served::planted(move |store: &Store| {
        // One recorded run cloned into `over` types of its own, the kth spawned k times: more
        // types than the cell shows, no two of them tied, so the order it shows them in is a claim
        // rather than an accident.
        store
            .connection()
            .execute(
                "INSERT INTO agent_runs (SELECT a.* REPLACE (\
                 a.id || '-planted-' || i || '-' || j AS id, 'planted-' || i AS agent_type) \
                 FROM agent_runs a, range(1, ?) r(i), range(1, ?) s(j) \
                 WHERE j <= i AND a.session_id = ? \
                 AND a.id = (SELECT min(id) FROM agent_runs WHERE session_id = ?))",
                params![over + 1, over + 1, SPINE, SPINE],
            )
            .expect("the clones land");
    });
    let cell = listing(&served).await.fields("data-session-id", SPINE)["agent_types"].clone();
    let shown: Vec<&str> = cell
        .split(" and ")
        .next()
        .expect("the head of the cell")
        .split(", ")
        .collect();
    let counts: Vec<i64> = shown
        .iter()
        .map(|entry| {
            entry
                .rsplit_once(" ×")
                .expect("each entry carries its count")
                .1
                .parse()
                .expect("a count")
        })
        .collect();
    // As many types as the cell shows, no more, ranked by the runs each stood for...
    assert_eq!(shown.len(), queries::LIST_ITEMS);
    let mut ranked = counts.clone();
    ranked.sort_unstable_by(|left, right| right.cmp(left));
    assert_eq!(counts, ranked);
    assert_eq!(
        counts
            .iter()
            .collect::<std::collections::BTreeSet<_>>()
            .len(),
        counts.len()
    );
    // ...and a tail counting the types it left out rather than dropping them silently. Two
    // recorded types sit under the planted ones, which is what the cut has to reach past.
    let left_out = over + 2 - queries::LIST_ITEMS as i64;
    assert!(
        cell.ends_with(&format!("and {} more", counted(left_out))),
        "{cell}"
    );
}

#[tokio::test]
async fn a_list_row_links_to_the_session_it_names() {
    // The link on a row opens that session's page — the list's whole purpose.
    let served = Served::corpus();
    let session_id = listed_sessions(&served.db())
        .first()
        .expect("the corpus holds a session")
        .clone();
    let opens = format!("/session/{session_id}");
    assert!(
        listing(&served)
            .await
            .inside("data-session-id", &session_id, "href")
            .iter()
            .any(|href| href.contains(&opens))
    );
    assert_eq!(served.page(&opens).await.0, axum::http::StatusCode::OK);
}
