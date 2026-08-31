//! What a described store's pages show of the model's words.
//!
//! Ported from `tests/view/test_enrichment.py`. The described store is the cached enriched one,
//! whose four model-written fields are invented and say so: no fixture records a model's answer
//! about a private transcript. Absence — the store no pass has touched, and the one it stopped
//! part way through — is `enrichment_absence.rs`; the words as text is `enrichment_words.rs`.

use std::collections::{BTreeMap, BTreeSet};

use hyphae_store::queries::{LIST_CATEGORIES, LIST_CHARS};
use hyphae_store::{Param, Store};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{SPINE, SPINE_RUN};
use hyphae_testsupport::planting::{Said, written};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::enrichment::Level;
use hyphae_view::format::cut;
use hyphae_view::store::{Page, Query as _};

/// What the pass said about every session in one store, keyed by session — the list's own input.
fn said_of_every_session(served: &Served) -> BTreeMap<String, Said> {
    rows::all(
        &served.db(),
        "SELECT session_id, description, category, outcome FROM session_enrichments",
        &[],
    )
    .iter()
    .map(|row| {
        let text = |column: &str| row.str(column).expect("a written field").to_owned();
        (
            text("session_id"),
            Said {
                description: text("description"),
                category: text("category"),
                outcome: text("outcome"),
            },
        )
    })
    .collect()
}

/// A described session carries its own description, its category and its outcome.
#[tokio::test]
async fn a_session_page_shows_what_the_model_said_about_the_session() {
    let served = Served::enriched();
    let db = served.db();
    let (_, served_page) = served.page(&format!("/session/{SPINE}")).await;
    let page = Markup::of(&served_page);
    let said = &written(&db, Level::Session, SPINE)[SPINE];
    let shown = page.fields("data-enrichment", SPINE);
    assert_eq!(
        (&shown["description"], &shown["category"], &shown["outcome"]),
        (&said.description, &said.category, &said.outcome)
    );
    // ...and the query behind it is cited like every other query the page ran.
    assert!(
        page.fields("id", "citation")
            .contains_key(Page::Enrichment.stem())
    );
    // The pass's words head the pane once it has reached the session, and they are the only name
    // on it: the pane prints no fact row for the name the session was recorded under, so a
    // described session heads itself with what a model said it did and nothing repeats.
    let recorded = rows::one(
        &db,
        "SELECT title FROM sessions WHERE id = $session",
        &[("session", Param::from(SPINE))],
    )
    .str("title")
    .expect("the fixture session was recorded under a title")
    .to_owned();
    let pane = page.fields("data-body", "session");
    assert!(!recorded.is_empty() && recorded != said.description);
    assert_eq!(pane["title"], said.description);
    assert!(!pane.contains_key("recorded_title"));
}

/// A row of the list carries the head of its session's description and its two tags.
///
/// The list is where a reader picks what to open, so the pass's one-line answer to "what was this
/// session" belongs on it — cut to a row's head, because the row is multiplied by the page and the
/// whole description is on the session's own page.
#[tokio::test]
async fn the_session_list_shows_what_the_model_said_about_each_session() {
    let served = Served::enriched();
    let (_, served_listing) = served.page("/sessions").await;
    let listing = Markup::of(&served_listing);
    let said = said_of_every_session(&served);
    let listed = listing.values("data-session-id");
    let described: Vec<String> = listed
        .iter()
        .filter(|session_id| said.contains_key(*session_id))
        .cloned()
        .collect();
    // The store is the partly-described one, so the page has rows of both kinds on it...
    assert!(!described.is_empty() && described.len() < listed.len());
    for session_id in &described {
        let row = listing.fields("data-session-id", session_id);
        let wrote = &said[session_id];
        // ...each described row showing a head of what the pass wrote, and both its tags...
        assert_eq!(
            row["description"],
            wrote
                .description
                .chars()
                .take(LIST_CHARS)
                .collect::<String>()
        );
        assert_eq!(
            (&row["category"], &row["outcome"]),
            (&wrote.category, &wrote.outcome)
        );
        // ...with a space before the first pill. A tag carries a right margin and no left one, so
        // without it the pill's border touches the last word of the description.
        let together = format!(
            "{} {} {}",
            row["description"], row["category"], row["outcome"]
        );
        assert_eq!(
            listing.reads("data-enrichment", session_id),
            together.split_whitespace().collect::<Vec<_>>().join(" ")
        );
    }
    // ...and a session the pass never reached carrying nothing at all beside it.
    assert_eq!(listing.values("data-enrichment"), described);
    // The query behind that is cited like every other query the page ran.
    assert!(
        listing
            .fields("id", "citation")
            .contains_key(Page::DescribedSessions.stem())
    );
    // A row's head is narrower than the pane's, and a pass writes to neither: 435 of the 438
    // described sessions in the canonical store on 2026-08-25 run past the 100 characters a row
    // prints, so the cut is the ordinary case here rather than the edge. It is marked like every
    // other cut value, which is what the row's link then makes good on — the whole line is on the
    // session's own page, a click from the mark that says there is more of it.
    let sentence = "w".repeat(LIST_CHARS + 1);
    let planted = Served::enriched_planted(|store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE session_enrichments SET description = ?",
                duckdb::params![sentence],
            )
            .expect("every described session is given one long line");
    });
    let (_, listing) = planted.page("/sessions").await;
    let row = Markup::of(&listing).fields("data-session-id", &described[0]);
    assert_eq!(row["description"], cut(&sentence, LIST_CHARS));
}

/// A row says what kind of work its session's turns were, ranked and cut.
///
/// The one column of the list a pass writes rather than the store reads: a session's turn
/// categories say what it spent its time on, which no count of turns or tools does. It is absent
/// from a store no pass has run over — an empty column would be a claim the store cannot support.
#[tokio::test]
async fn the_work_cell_counts_the_turn_categories_a_pass_described() {
    let served = Served::enriched();
    let (_, listing) = served.page("/sessions").await;
    let row = Markup::of(&listing).fields("data-session-id", SPINE);
    let kinds = rows::all(
        &served.db(),
        "SELECT category, count(*) AS turns FROM turn_enrichments WHERE session_id = $session \
         GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT $limit",
        &[
            ("session", Param::from(SPINE)),
            ("limit", Param::from(LIST_CATEGORIES)),
        ],
    );
    assert!(
        !kinds.is_empty(),
        "the described corpus no longer describes this session's turns"
    );
    let ranked: Vec<String> = kinds
        .iter()
        .map(|kind| {
            format!(
                "{} ×{}",
                kind.str("category").expect("a category"),
                kind.i64("turns").expect("a count")
            )
        })
        .collect();
    assert_eq!(row["work"], ranked.join(", "));
    // A store with no enrichment tables at all renders the same row without the column.
    let (_, bare) = Served::corpus().page("/sessions").await;
    assert!(
        !Markup::of(&bare)
            .fields("data-session-id", SPINE)
            .contains_key("work")
    );
}

/// A run's page says what the model said the run did, not just what it was asked to do.
#[tokio::test]
async fn a_run_page_shows_the_runs_own_enrichment_beside_its_brief() {
    let served = Served::enriched();
    let db = served.db();
    let (_, served_page) = served
        .page(&format!("/session/{SPINE}/run/{SPINE_RUN}"))
        .await;
    let page = Markup::of(&served_page);
    let said = &written(&db, Level::AgentRun, SPINE)[SPINE_RUN];
    let shown = page.fields("data-enrichment", SPINE_RUN);
    assert_eq!(
        (&shown["description"], &shown["category"], &shown["outcome"]),
        (&said.description, &said.category, &said.outcome)
    );
    // The run's recorded task keeps its own place, among the pane's own values — what the run was
    // asked to do and what a pass said it did are two different sentences.
    assert_eq!(page.values("data-detail"), ["brief", "prompt", "result"]);
}

/// A run's turns are described by the run's description, not one apiece.
///
/// A pass describes the main thread's turns and leaves an agent run's to the run — so the only
/// enrichment on a run page is the run's own, and its children's tags. The page asks for its own
/// thread all the same, because the turn key is `(session, source, turn)`: a page that asked for
/// `main` would show one thread's descriptions against another's turns.
#[tokio::test]
async fn a_run_pages_turns_carry_no_description_of_their_own() {
    let served = Served::enriched();
    let elsewhere = rows::one(
        &served.db(),
        "SELECT count(*) AS described FROM turn_enrichments WHERE source <> 'main'",
        &[],
    )
    .i64("described")
    .expect("a count");
    assert_eq!(
        elsewhere, 0,
        "a pass now describes an agent run's turns: the run page can show them"
    );
    let (_, served_page) = served
        .page(&format!("/session/{SPINE}/run/{SPINE_RUN}"))
        .await;
    let page = Markup::of(&served_page);
    let turns = page.values("data-child");
    assert!(
        !turns.is_empty(),
        "the fixture run whose thread this reads no longer holds a turn"
    );
    let enriched: BTreeSet<String> = page.values("data-enrichment").into_iter().collect();
    for key in &turns {
        let turn_id = key.strip_prefix("turn:").unwrap_or(key);
        assert!(!enriched.contains(turn_id), "{turn_id}");
    }
    // The run's own description is there, which is what covers them.
    assert!(enriched.contains(SPINE_RUN));
}
