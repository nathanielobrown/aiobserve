//! The landing page: one row per project the store holds sessions for.
//!
//! The expectations are derived from the store the app is serving rather than listed here — the
//! fold is re-run through `sessions::project_predicate`, the same shape the query file and the
//! CLI's `--project` use, so a page that folded some other way reds even though both sides move
//! together. Two things the fixture corpus cannot show are planted and labelled: no recorded
//! session ran in a worktree, and every recorded timestamp recedes from the wall clock, so the
//! trailing windows would go quietly empty as the corpus ages.

use std::collections::{BTreeMap, BTreeSet};

use axum::http::StatusCode;
use chrono::{Duration, Utc};
use duckdb::params;
use regex::Regex;

use hyphae_extract::sessions::project_predicate;
use hyphae_store::{Param, Store};
use hyphae_testsupport::html::{Markup, money};
use hyphae_testsupport::landmarks::{HOME, MYCELIA, NO_PROJECT_SESSION, SPINE};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::format as fmt;
use hyphae_view::knobs;
use hyphae_view::store::{Page, Query};
use hyphae_view::urls::quoted;

/// What the two trailing windows are called, read off the citation: the page labels them from the
/// same parameters, so a window renamed here is a window the page stopped counting.
const RECENT_DAYS: &str = "recent_days";
const WINDOW_DAYS: &str = "window_days";

/// Every session in the store beside the project it folds onto: the shortest stored directory it
/// sits in, by the predicate the CLI filters with. Written here rather than imported from the
/// query so the page is checked against a second statement of the rule.
fn fold() -> String {
    format!(
        "SELECT r.session_id, r.started_at, r.cost_usd, \
         (SELECT min_by(a.project_dir, length(a.project_dir)) \
          FROM (SELECT DISTINCT project_dir FROM corpus_rollups WHERE project_dir IS NOT NULL) a \
          WHERE {}) AS root \
         FROM corpus_rollups r",
        project_predicate("r.project_dir", "a.project_dir")
    )
}

/// The sessions of each project the store holds, keyed by the root they fold onto.
fn folded(db: &std::path::Path) -> BTreeMap<Option<String>, Vec<String>> {
    let mut grouped: BTreeMap<Option<String>, Vec<String>> = BTreeMap::new();
    for row in rows::all(
        db,
        &format!("SELECT session_id, root FROM ({})", fold()),
        &[],
    ) {
        grouped
            .entry(row.opt_str("root").expect("a root").map(str::to_owned))
            .or_default()
            .push(row.str("session_id").expect("a session id").to_owned());
    }
    grouped
}

/// The bindings a page's citation carries for one query, keyed by parameter.
fn cited(page: &str, query: impl Query) -> BTreeMap<String, String> {
    Markup::of(page).fields("id", "citation")[query.stem()]
        .split_whitespace()
        .skip(2)
        .map(|binding| {
            let (name, value) = binding.split_once('=').expect("a binding is `name=value`");
            (name.to_owned(), value.to_owned())
        })
        .collect()
}

/// Which of a project's sessions fall inside a trailing window, and what they cost.
///
/// Bound with the values the page cited, so the expectation is the window the reader sees rather
/// than one this test computed from a clock of its own — and closed at both ends the way the
/// runner's window is, so the cited line answers the same tomorrow.
fn window(db: &std::path::Path, root: &str, as_of: &str, days: &str) -> (BTreeSet<String>, f64) {
    let held = rows::all(
        db,
        &format!(
            "SELECT session_id, cost_usd FROM ({}) WHERE root = $root \
             AND started_at >= CAST($as_of AS DATE) - to_days(CAST($days AS INTEGER)) \
             AND started_at < CAST($as_of AS DATE) + INTERVAL 1 DAY",
            fold()
        ),
        &[
            ("root", Param::from(root)),
            ("as_of", Param::from(as_of)),
            ("days", Param::from(days)),
        ],
    );
    (
        held.iter()
            .map(|row| row.str("session_id").expect("a session id").to_owned())
            .collect(),
        held.iter()
            .map(|row| row.f64("cost_usd").unwrap_or(0.0))
            .sum(),
    )
}

/// The landing page, read.
async fn landing(served: &Served) -> String {
    let (status, page) = served.page("/").await;
    assert_eq!(status, StatusCode::OK);
    page
}

/// The one link a project's row opens.
fn opens(page: &str, root: &str) -> String {
    let links: BTreeSet<String> = Markup::of(page)
        .inside("data-project", root, "href")
        .into_iter()
        .collect();
    assert_eq!(links.len(), 1, "{root}");
    links.into_iter().next().expect("the one link")
}

#[tokio::test]
async fn the_landing_page_lists_projects_and_the_list_moved_to_sessions() {
    // `/` answers with projects and `/sessions` with sessions — neither serves the other.
    let served = Served::corpus();
    let (landed, listed) = (landing(&served).await, served.page("/sessions").await);
    assert_eq!(listed.0, StatusCode::OK);
    let (landed, listed) = (Markup::of(&landed), Markup::of(&listed.1));
    assert!(!landed.values("data-project").is_empty());
    assert!(landed.values("data-session-id").is_empty());
    assert!(!listed.values("data-session-id").is_empty());
    assert!(listed.values("data-project").is_empty());
}

#[tokio::test]
async fn a_row_per_project_and_one_for_the_sessions_that_named_none() {
    // Every project the store holds gets a row, and what it cannot attribute gets one too.
    let served = Served::corpus();
    let page = landing(&served).await;
    let markup = Markup::of(&page);
    let expected = folded(&served.db());
    // One row per root the fold produced, the sessions with no project directory among them under
    // the empty key: a row, because the store holds their spend like any other.
    assert_eq!(
        markup
            .values("data-project")
            .into_iter()
            .collect::<BTreeSet<String>>(),
        expected
            .keys()
            .map(|root| root.clone().unwrap_or_default())
            .collect::<BTreeSet<String>>()
    );
    assert!(expected[&None].contains(&NO_PROJECT_SESSION.to_owned()));
    // The row for those sessions says so in words and links nowhere: there is no project page to
    // open, and a link to `?project=` would filter by a value no session carries.
    assert_eq!(
        markup.fields("data-project", "")["project_dir"],
        "(no project)"
    );
    assert!(markup.inside("data-project", "", "href").is_empty());
    // Every other row counts exactly the sessions that folded onto it.
    for (root, sessions) in &expected {
        if let Some(root) = root {
            assert_eq!(
                markup.fields("data-project", root)["sessions"],
                hyphae_testsupport::html::counted(sessions.len() as i64),
                "{root}"
            );
        }
    }
}

#[tokio::test]
async fn a_worktree_folds_into_its_checkout_and_a_prefix_sibling_does_not() {
    // A checkout's worktrees count as the checkout; a directory beside it stays its own row.
    //
    // Planted and labelled: no recorded session ran in a worktree, so the masquerading
    // directories the design fold exists for cannot be reproduced from the fixtures. The sibling
    // is the case that separates the predicate from `starts_with(dir, ancestor)` — one character
    // short of it and `mycelia-other` disappears into `mycelia`.
    let worktree = format!("{MYCELIA}/.claude/worktrees/wt-1");
    let sibling = format!("{MYCELIA}-other");
    let planting = (worktree.clone(), sibling.clone());
    let served = Served::planted(move |store: &Store| {
        for (directory, session) in [(&planting.0, SPINE), (&planting.1, NO_PROJECT_SESSION)] {
            store
                .connection()
                .execute(
                    "UPDATE sessions SET project_dir = ? WHERE id = ?",
                    params![directory, session],
                )
                .expect("the session moves");
        }
    });
    let page = landing(&served).await;
    let markup = Markup::of(&page);
    // The worktree is not a project of its own...
    assert!(!markup.values("data-project").contains(&worktree));
    // ...its session counts under the checkout it was cut from...
    let expected = folded(&served.db());
    let under = expected[&Some(MYCELIA.to_owned())].clone();
    assert!(under.contains(&SPINE.to_owned()));
    assert_eq!(
        markup.fields("data-project", MYCELIA)["sessions"],
        hyphae_testsupport::html::counted(under.len() as i64)
    );
    // ...and the directory that merely shares its name is a row of its own.
    assert_eq!(markup.fields("data-project", &sibling)["sessions"], "1");
    // The list the row opens folds the same way, because the row's count and the list it links to
    // would otherwise disagree by exactly the sessions a worktree recorded.
    let (status, listed) = served.page(&opens(&page, MYCELIA)).await;
    assert_eq!(status, StatusCode::OK);
    let shown = Markup::of(&listed).values("data-session-id");
    assert_eq!(
        shown.iter().cloned().collect::<BTreeSet<String>>(),
        under.into_iter().collect::<BTreeSet<String>>()
    );
    assert!(shown.contains(&SPINE.to_owned()));
    assert!(!shown.contains(&NO_PROJECT_SESSION.to_owned()));
}

#[tokio::test]
async fn project_spend_is_counted_through_the_corpus_views() {
    // A project's spend counts a resume's copied calls once, not once per session file. The
    // fixture corpus holds a resume pair, so the two views disagree here: a regression to
    // `session_rollups` would print the larger number.
    let served = Served::corpus();
    let spend = rows::one(
        &served.db(),
        "SELECT (SELECT sum(cost_usd) FROM corpus_rollups WHERE project_dir = $project) AS corpus, \
         (SELECT sum(cost_usd) FROM session_rollups WHERE project_dir = $project) AS live",
        &[("project", Param::from(MYCELIA))],
    );
    let corpus = spend.f64("corpus").expect("a cost");
    assert!(
        corpus < spend.f64("live").expect("a cost"),
        "the resume pair the fixture corpus records no longer double-counts"
    );
    let page = landing(&served).await;
    assert_eq!(
        Markup::of(&page).fields("data-project", MYCELIA)["cost_usd"],
        money(corpus)
    );
}

#[tokio::test]
async fn the_windows_count_the_sessions_inside_the_window_the_page_cites() {
    // Each trailing window holds exactly the sessions the citation's `as_of` puts in it.
    //
    // The three timestamps are planted because every recorded one recedes: the fixture corpus ends
    // in 2026-08 and its windows go empty as the wall clock moves, which would leave this leaf
    // asserting zero against zero. One session inside both windows, one inside the longer only,
    // and one outside both, so each boundary is exercised from the near side and the far.
    #[expect(
        clippy::disallowed_methods,
        reason = "the real clock: the leaf plants rows relative to now and reads them back through it"
    )]
    let now = Utc::now();
    // A copy of a recorded session per offset, so a planted row carries a real session's numbers
    // and the clock is the only invented part of it.
    let served = Served::planted(move |store: &Store| {
        for days in [1_i64, 10, 40] {
            store
                .connection()
                .execute(
                    "INSERT INTO sessions (SELECT s.* REPLACE (? AS id, ? AS project_dir, \
                     CAST(? AS TIMESTAMP WITH TIME ZONE) AS started_at) FROM sessions s \
                     WHERE s.id = ?)",
                    params![
                        format!("planted-{days}d"),
                        format!("{MYCELIA}/planted"),
                        (now - Duration::days(days)).to_rfc3339(),
                        SPINE
                    ],
                )
                .expect("the clone lands");
        }
    });
    let page = landing(&served).await;
    let bindings = cited(&page, Page::ProjectRollups);
    let db = served.db();
    // The planted sessions fold onto the checkout, so the counts below are the mycelia row's — the
    // same query the page ran, bound to the same `as_of` it cited.
    let recent = window(&db, MYCELIA, &bindings["as_of"], &bindings[RECENT_DAYS]);
    let trailing = window(&db, MYCELIA, &bindings["as_of"], &bindings[WINDOW_DAYS]);
    // The plants landed where they were aimed: the day-old session inside both windows, the
    // ten-day-old one inside the longer alone, and the forty-day-old one outside both. The counts
    // themselves come from the store, because the corpus holds recorded sessions inside the
    // longer window too.
    assert!(recent.0.contains("planted-1d") && trailing.0.contains("planted-1d"));
    assert!(trailing.0.contains("planted-10d") && !recent.0.contains("planted-10d"));
    assert!(!trailing.0.contains("planted-40d"));
    // And the page counts and prices exactly the sessions each window holds.
    let row = Markup::of(&page).fields("data-project", MYCELIA);
    let counted = hyphae_testsupport::html::counted;
    assert_eq!(row["recent_sessions"], counted(recent.0.len() as i64));
    assert_eq!(row["window_sessions"], counted(trailing.0.len() as i64));
    assert_eq!(row["recent_cost"], money(recent.1));
    assert_eq!(row["window_cost"], money(trailing.1));
}

#[tokio::test]
async fn a_window_holding_no_session_is_a_gap_rather_than_a_crash() {
    // A project with nothing inside a window renders the dash, not a zero total or an error.
    //
    // The corpus's one-session projects are the case: their sessions were recorded in 2026-08 and
    // the short window has receded past them, so what the store holds for that window is nothing.
    let served = Served::corpus();
    let db = served.db();
    let page = landing(&served).await;
    let recent_days = cited(&page, Page::ProjectRollups)[RECENT_DAYS].clone();
    let markup = Markup::of(&page);
    let mut quiet = 0;
    for root in folded(&db).keys().flatten() {
        let held = rows::one(
            &db,
            &format!(
                "SELECT count(*) AS held FROM ({}) WHERE root = $root \
                 AND started_at >= current_date - to_days(CAST($days AS INTEGER))",
                fold()
            ),
            &[
                ("root", Param::from(root.as_str())),
                ("days", Param::from(recent_days.as_str())),
            ],
        )
        .i64("held")
        .expect("a count");
        if held != 0 {
            continue;
        }
        quiet += 1;
        let row = markup.fields("data-project", root);
        // No sessions is a count of zero — the store knows that — and no spend at all.
        assert_eq!(row["recent_sessions"], "0", "{root}");
        assert_eq!(row["recent_cost"], fmt::ABSENT, "{root}");
    }
    assert!(
        quiet > 0,
        "every project has run recently: the empty-window case needs a plant now"
    );
}

#[tokio::test]
async fn a_project_row_links_to_the_sessions_it_counts() {
    // Following a row's link lands on the list holding exactly that project's sessions.
    let served = Served::corpus();
    let page = landing(&served).await;
    let (status, listed) = served.page(&opens(&page, MYCELIA)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        Markup::of(&listed)
            .values("data-session-id")
            .into_iter()
            .collect::<BTreeSet<String>>(),
        folded(&served.db())[&Some(MYCELIA.to_owned())]
            .iter()
            .cloned()
            .collect::<BTreeSet<String>>()
    );
}

#[tokio::test]
async fn the_page_cites_the_query_and_the_window_it_ran() {
    // The footer carries the clock the windows were computed from, so they reproduce.
    //
    // A page whose windows came from SQL's own `now()` would cite a line that answers something
    // different every time it is re-run, which is the reason the route binds the clock.
    let served = Served::corpus();
    let bindings = cited(&landing(&served).await, Page::ProjectRollups);
    let as_of: chrono::NaiveDate = bindings["as_of"].parse().expect("an ISO day");
    #[expect(
        clippy::disallowed_methods,
        reason = "the real clock is the oracle: the page's `as_of` must not be in the future"
    )]
    let today = Utc::now().date_naive();
    assert!(as_of <= today);
    assert_eq!(bindings["projects"], knobs::PROJECTS.default.to_string());
    // And the two windows the columns are headed with, which are bindings like the rest.
    assert_eq!(bindings[RECENT_DAYS], "7");
    assert_eq!(bindings[WINDOW_DAYS], "30");
}

#[tokio::test]
async fn the_page_is_ordered_by_what_ran_most_recently() {
    // Projects arrive newest first, with the sessions that named no directory last.
    //
    // The store holds no timestamp for those, so they sort where every NULL the viewer prints
    // sorts: at the end, rather than at the top of a page ranked by recency.
    let served = Served::corpus();
    let mut ordered: Vec<String> = rows::all(
        &served.db(),
        &format!(
            "SELECT root, max(started_at) AS last FROM ({}) WHERE root IS NOT NULL \
             GROUP BY root ORDER BY last DESC",
            fold()
        ),
        &[],
    )
    .iter()
    .map(|row| row.str("root").expect("a root").to_owned())
    .collect();
    ordered.push(String::new());
    assert_eq!(
        Markup::of(&landing(&served).await).values("data-project"),
        ordered
    );
}

#[tokio::test]
async fn the_filter_box_suggests_the_projects_the_landing_page_lists() {
    // The box offers roots, so filling one in finds the sessions the row it came from counts.
    //
    // Planted for the same reason as the fold above: without a worktree in the store, a box that
    // offered every recorded directory and one that offered only roots look identical.
    let worktree = format!("{MYCELIA}/.claude/wt-1");
    let planted = worktree.clone();
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE sessions SET project_dir = ? WHERE id = ?",
                params![planted, SPINE],
            )
            .expect("the session moves");
    });
    let (_, listing) = served.page("/sessions").await;
    let offered = Markup::of(&listing).suggestions();
    let listed = Markup::of(&landing(&served).await).values("data-project");
    // Every suggestion is a project the landing page counts...
    assert!(!offered.is_empty());
    let shown: BTreeSet<&String> = listed.iter().collect();
    assert!(offered.iter().all(|option| shown.contains(option)));
    // ...the worktree is not one of them, and the checkout it folds into is...
    assert!(!offered.contains(&worktree));
    assert!(offered.contains(&MYCELIA.to_owned()));
    // ...and each one finds sessions rather than filling the box in with a dead value.
    for option in &offered {
        let (_, found) = served
            .page(&format!("/sessions?project={}", quoted(option)))
            .await;
        assert!(
            !Markup::of(&found).values("data-session-id").is_empty(),
            "{option}"
        );
    }
}

#[tokio::test]
async fn a_column_is_headed_the_way_its_cells_are_set() {
    // A count is read down its column, so the heading over it sits where the digits do.
    //
    // The page has one alignment vocabulary — the class the stylesheet sets flush right — and the
    // claim is that the head and the first body row agree on it, column by column. Positional,
    // because that is what a reader sees: a heading is over whatever cell shares its index.
    let served = Served::corpus();
    let page = landing(&served).await;
    let section = |pattern: &str| {
        Regex::new(pattern)
            .expect("a pattern")
            .captures(&page)
            .expect("the page has the section")[1]
            .to_owned()
    };
    let head = section(r"(?s)<thead>(.*?)</thead>");
    let body = section(r"(?s)<tbody>\s*<tr[^>]*>(.*?)</tr>");
    let aligned = |section: &str, tag: &str| -> Vec<bool> {
        Regex::new(&format!("<{tag}[^>]*>"))
            .expect("a pattern")
            .find_iter(section)
            .map(|cell| hyphae_testsupport::html::classed(cell.as_str()).contains("number"))
            .collect()
    };
    // The project, three windows and the last-active column: five of each, and the three windows
    // are the ones set right.
    let set = vec![false, true, true, true, false];
    assert_eq!(aligned(&head, "th"), set);
    assert_eq!(aligned(&body, "td"), set);
}

#[tokio::test]
async fn a_project_directory_folds_the_readers_home_and_still_links_whole() {
    // A project cell prints `~` for the home of whoever is reading, and nothing else does.
    //
    // Every row of one person's corpus repeats the same home directory, which is column width
    // spent on a constant — and the project column is the one that squeezes the lists beside it.
    // The fold is display alone: the row's own attribute, the link the landing page mints and the
    // box that suggests a filter all carry the path the store holds, because a filter matches
    // that path and not a reader's shorthand for it.
    //
    // The Python patches `fmt.home`; here the reader's home is the environment the process runs
    // in, which `fmt::home` reads afresh on every call. Its own process, which is what nextest
    // gives every leaf.
    let served = Served::corpus();
    // SAFETY: nextest runs each test in its own process, and no thread has started here.
    unsafe { std::env::set_var("HOME", HOME) };
    let (_, listing) = served.page("/sessions").await;
    let page = landing(&served).await;
    let folded = "~/repos/mycelia";
    assert_eq!(
        Markup::of(&listing).fields("data-session-id", SPINE)["project_dir"],
        folded
    );
    assert_eq!(
        Markup::of(&page).fields("data-project", MYCELIA)["project_dir"],
        folded
    );
    // The session's own page says where it ran in the same words its row does — in the crumb above
    // the pane, which is the way out of the session and the one place the page names the directory
    // now that the pane's fact row is gone.
    let (_, session) = served.page(&format!("/session/{SPINE}")).await;
    assert_eq!(
        Markup::of(&session).fields("data-crumb-head", "project")["project_dir"],
        folded
    );
    // What a reader clicks or types is untouched: the row is keyed by the stored path, the link
    // filters on it, and the box offers it.
    assert!(opens(&page, MYCELIA).contains(&format!("project={}", quoted(MYCELIA))));
    assert!(
        Markup::of(&listing)
            .suggestions()
            .contains(&MYCELIA.to_owned())
    );
    // Read from anywhere else, the same cell prints the path whole. The fold is this reader's own
    // home and not a rule about any directory two levels under `/Users`.
    //
    // A directory that exists, because DuckDB reads the same variable: it looks under `$HOME` for
    // the extensions it autoloads, and a home nothing is at turns every query on the page into a
    // 500 that has nothing to do with the fold.
    // SAFETY: as above.
    unsafe { std::env::set_var("HOME", std::env::temp_dir()) };
    let (_, elsewhere) = served.page("/sessions").await;
    assert_eq!(
        Markup::of(&elsewhere).fields("data-session-id", SPINE)["project_dir"],
        MYCELIA
    );
}
