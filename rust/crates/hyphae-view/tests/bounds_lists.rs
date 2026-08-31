//! What the list pages cost: one row of a session, a project or a failed tool call.
//!
//! Ported from `tests/view/test_bounds__lists.py`. The same arithmetic as `bounds_node.rs` over
//! the pages a corpus grows rather than a session: chrome measured once, then a row priced
//! against what `hyphae-testsupport/src/budgets.rs` says the widest one can hold, times the
//! ceiling the page admits.

use std::sync::LazyLock;

use axum::http::StatusCode;
use duckdb::params;
use hyphae_store::{Param, Store, queries};
use hyphae_testsupport::budgets::{
    MEASURED_ERRORS_CHROME, MEASURED_LIST_CHROME, MEASURED_PROJECTS_CHROME, PAGE_BYTES,
    describe_at_every_cap, fits, worst_error_row_bytes, worst_project_row_bytes,
    worst_session_row_bytes,
};
use hyphae_testsupport::cache;
use hyphae_testsupport::html::{Markup, plain};
use hyphae_testsupport::landmarks::{FORK_ORIGIN, RESUME};
use hyphae_testsupport::metadata;
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::format::ELLIPSIS;
use hyphae_view::knobs::{CURSORLESS_TURNS, ERRORS, PROJECTS};
use hyphae_view::store::{Page, TURN_CURSOR, cursorless_rows};
use regex::Regex;

/// Everything one pattern matches, and the page with all of it cut out.
fn without(html: &str, pattern: &Regex) -> (Vec<String>, String) {
    let found = pattern
        .find_iter(html)
        .map(|one| one.as_str().to_owned())
        .collect();
    (found, pattern.replace_all(html, "").into_owned())
}

/// A path as the link beside it writes it, built here rather than through the viewer's own
/// encoder: the planted paths are `&` and digits, and `&` is the one character that moves.
fn encoded(path: &str) -> String {
    path.chars()
        .map(|character| {
            if character == '&' {
                "%26".to_owned()
            } else {
                character.to_string()
            }
        })
        .collect()
}

#[tokio::test]
async fn a_session_list_of_nothing_but_escapes_costs_what_the_ceiling_budgets() {
    // A list row and the chrome around it weigh no more than the arithmetic gives them.
    //
    // The list is the page a corpus grows: every string in a row is one a transcript wrote, and
    // its skills and the filter box's project suggestions both grow with what the store holds.
    // So `&` is planted at every cap — the character that escapes to five bytes — and both halves
    // of the ceiling are measured against it: one more row, and the page with no rows at all. The
    // described store rather than the bare one, because a row of a store a pass has run over
    // carries what the pass said as well, and that is the row the ceiling has to budget.
    //
    // Every string goes in one character past what a row prints, because that character is the
    // whole of how the page knows a value was stopped rather than ended: at the cap exactly,
    // nothing is marked and the row costs less than the arithmetic gives it.
    let head = "&".repeat(queries::LIST_CHARS + 1);
    // Except a project path, which the filter box offers whole or not at all: the paths that fill
    // the box sit exactly at the width, and every path past those goes one over it, where the
    // row's own mark is. Two digits tell them apart, so both halves of the page are measured
    // against the same plant.
    let root = "&".repeat(queries::LIST_CHARS - 2);
    let name = "&".repeat(queries::LIST_ITEM_CHARS);
    let kind = "&".repeat(queries::TAG_CHARS);
    let over = queries::LIST_ITEMS + 2;
    let kinds = queries::LIST_CATEGORIES + 2;
    let (skill, category) = (name.clone(), kind.clone());
    let served = Served::enriched_planted(move |store: &Store| {
        let connection = store.connection();
        // A project path per session, each one longer than the filter box offers, so the box has
        // more suggestions than it shows. The two digits that tell them apart are the only
        // characters on the page that are not an escape...
        connection
            .execute(
                "UPDATE sessions SET title = ?, project_dir = ? || printf('%02d', r.n) \
                 || CASE WHEN r.n > ? THEN '&' ELSE '' END \
                 FROM (SELECT id, row_number() OVER (ORDER BY id) AS n FROM sessions) r \
                 WHERE r.id = sessions.id",
                params![head, root, queries::LIST_PROJECTS as i64],
            )
            .expect("every session names a project");
        // ...and every session runs more skills than a row shows, cloning a live api call rather
        // than inventing one: `live_api_calls` is the population a row's skill list counts.
        connection
            .execute(
                "INSERT INTO api_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id, \
                 ? || i AS attribution_skill) \
                 FROM (SELECT DISTINCT ON (l.session_id) l.* FROM live_api_calls l) c, \
                 range(1, ?) t(i))",
                params![skill, (over + 1) as i64],
            )
            .expect("every session runs skills");
        // ...and every session spawns more kinds of subagent than a row shows. The names have to
        // differ inside the *shown* width, not merely inside the one the query cuts to: the query
        // groups the runs after its cut, and the row cuts a character off that again to make room
        // for the mark. So two digits sit at the end of what a row shows, with one more escape
        // behind them.
        connection
            .execute(
                "INSERT INTO agent_runs (SELECT r.* REPLACE (s.id AS session_id, \
                 s.id || '-planted-' || i AS id, ? || printf('%02d', i) || '&' AS agent_type) \
                 FROM (SELECT * FROM live_agent_runs ORDER BY session_id, id LIMIT 1) r, \
                 sessions s, range(1, ?) t(i))",
                params![skill[..skill.len() - 2].to_owned(), (over + 1) as i64],
            )
            .expect("every session spawns runs");
        describe_at_every_cap(store);
        // The pass described every turn as the same kind of work, so the Work cell would show one
        // name; a described turn per session per kind fills it the way a real pass would, cloning
        // a described row rather than inventing one. Categories are cut and grouped like the
        // agent types, so they are told apart the same way.
        connection
            .execute(
                "INSERT INTO turn_enrichments (SELECT e.* REPLACE (s.id AS session_id, \
                 s.id || '-planted-' || i AS turn_id, ? || printf('%02d', i) AS category) \
                 FROM (SELECT * FROM turn_enrichments ORDER BY session_id, turn_id LIMIT 1) e, \
                 sessions s, range(1, ?) t(i))",
                params![
                    category[..category.len() - 2].to_owned(),
                    (kinds + 1) as i64
                ],
            )
            .expect("every session works several ways");
    });
    let sessions = rows::one(
        &cache::corpus_store(),
        "SELECT count(*) AS n FROM sessions",
        &[],
    )
    .i64("n")
    .expect("a count") as usize;
    assert!(
        sessions > queries::LIST_PROJECTS,
        "the fixture corpus no longer fills the filter box"
    );
    let mut pages = Vec::new();
    for size in 1..=sessions {
        let (status, page) = served.page(&format!("/sessions?size={size}")).await;
        assert_eq!(status, StatusCode::OK, "size {size}: {}", &page[..200]);
        pages.push(page);
    }
    let one_row = &pages[0];
    // One more row costs its markup and every head it shows, all `&` — priced at every row the
    // list holds rather than at whichever one lands second, because the ceiling multiplies the
    // dearest row and which session that is depends only on how the list happens to be sorted.
    let grew = pages
        .windows(2)
        .map(|pair| pair[1].len() - pair[0].len())
        .max()
        .expect("the corpus holds more than one session");
    assert!(grew <= worst_session_row_bytes(), "{grew}");
    // ...and what the page carries whatever its size fits the allowance the ceiling gives it,
    // with the row the arithmetic counts separately stripped out.
    static ROW: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?s)<tr data-session-id=.*?</tr>").expect("a pattern"));
    let (_, chrome) = without(one_row, &ROW);
    assert!(Markup::of(&chrome).values("data-session-id").is_empty());
    assert!(chrome.contains(r#"id="sessions""#));
    assert!(fits(chrome.len(), MEASURED_LIST_CHROME), "{}", chrome.len());
    // The plant reached every cap, which is what makes those two numbers a worst case: each
    // string cut to its head, the skills cut to their first names and saying how many were left,
    // and the filter box offering as many projects as it has room for. Read off the row the
    // budget above is priced at — the dearest one — rather than off whichever sorted first.
    let (markup, _) = without(pages.last().expect("a full page"), &ROW);
    let dearest = markup.iter().max_by_key(|row| row.len()).expect("rows");
    // Stood back in a table before it is read: a bare `<tr>` is not a tree the HTML parser keeps,
    // so a reader handed the row alone would see an empty document rather than the row.
    let dearest = Markup::of(&format!("<table>{dearest}</table>"));
    let key = dearest.values("data-session-id")[0].clone();
    let row = dearest.fields("data-session-id", &key);
    // Each of the row's own strings cut to its head and marked there, which is what says the cut
    // bit rather than the plant happening to end at the width.
    let at_head = format!("{}{ELLIPSIS}", "&".repeat(queries::LIST_CHARS));
    assert_eq!(row["title"], at_head);
    assert_eq!(
        row["project_dir"].chars().count(),
        queries::LIST_CHARS + ELLIPSIS.chars().count()
    );
    assert!(row["project_dir"].ends_with(ELLIPSIS));
    assert_eq!(
        row["skills"].matches(&format!("{name}{ELLIPSIS}")).count(),
        queries::LIST_ITEMS
    );
    assert!(row["skills"].ends_with("more"));
    // The two counted lists reached their own caps, each name cut to the head it is grouped under
    // — the last two characters of one are the digits that tell the plants apart, and the mark
    // behind them is the escape the plant put past the cut.
    let stem = &name[..name.len() - 2];
    assert_eq!(
        row["agent_types"].matches(stem).count(),
        queries::LIST_ITEMS
    );
    assert_eq!(
        row["agent_types"].matches(ELLIPSIS).count(),
        queries::LIST_ITEMS
    );
    // The kinds of work are the one cut column with no mark, and the plant cannot show why:
    // `$kind_chars` has no character to spare, so a name arrives at the width whatever was
    // planted behind it and a mark could not fire. What holds the budget is the vocabulary itself
    // — closed, and every member of both of them short of the cut — which is the claim the row
    // above prices at no mark at all.
    let taxonomy = metadata::enrichment();
    let vocabulary = taxonomy
        .categories
        .iter()
        .chain(&taxonomy.outcomes)
        .map(String::len)
        .max()
        .expect("a closed vocabulary");
    assert!(vocabulary < queries::TAG_CHARS);
    assert_eq!(
        row["work"].matches(&kind[..kind.len() - 2]).count(),
        queries::LIST_CATEGORIES
    );
    assert!(row["agent_types"].ends_with("more") && row["work"].ends_with("more"));
    assert_eq!(
        Markup::of(one_row).suggestions().len(),
        queries::LIST_PROJECTS
    );
    // And the pass's own line reached the head the list cuts it to, with both tags beside it —
    // the whole description is on the session's page, which is a page ceiling of its own.
    assert_eq!(row["description"], at_head);
    assert_eq!(row["category"].chars().count(), queries::TAG_CHARS);
    assert_eq!(row["outcome"].chars().count(), queries::TAG_CHARS);
    assert!(!row.contains_key("stale"));
}

#[tokio::test]
async fn a_projects_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets() {
    // The landing page at its ceiling weighs no more than the arithmetic gives it.
    //
    // A project path is a directory someone named, so `&` is planted at the cap the page shows —
    // the character that escapes to five bytes in a cell and to twelve in the link beside it —
    // and the store is filled past the page's own ceiling. That is the page the arithmetic bounds
    // and the one no corpus recorded so far comes near: the fixtures hold four projects.
    let over = PROJECTS.ceiling + 20;
    // Three digits tell the paths apart inside the head the page shows, so 97 of every 100
    // characters are escapes — and no path is a prefix of another, so none folds into another's
    // row. The sessions are clones of a recorded one rather than invented rows.
    let head = "&".repeat(queries::LIST_CHARS - 3);
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "INSERT INTO sessions (SELECT s.* REPLACE (s.id || '-planted-' || i AS id, \
                 ? || printf('%03d', i) AS project_dir) FROM (SELECT * FROM sessions LIMIT 1) s, \
                 range(1, ?) t(i))",
                params![head, over + 1],
            )
            .expect("the store fills past the ceiling");
    });
    let (status, page) = served.page("/").await;
    assert_eq!(status, StatusCode::OK, "{}", &page[..200]);
    // A page a reader lands on stays under the ceiling with every path at its cap...
    assert!(page.len() < PAGE_BYTES, "{}", page.len());
    let landing = Markup::of(&page);
    let shown = landing.values("data-project");
    assert_eq!(shown.len(), PROJECTS.ceiling as usize);
    // ...the planted ones at the cap, and the corpus's own short paths beside them. The attribute
    // is read back through the escaping the page wrote it with, which is the point: every
    // character of a planted path is one of the five-byte ones.
    // Read back through the escaping the page wrote the attribute with, which is what the parser
    // under `fields` does with it.
    let widest = plain(
        shown
            .iter()
            .max_by_key(|path| path.chars().count())
            .expect("the page lists projects"),
    );
    assert_eq!(
        landing.fields("data-project", &widest)["project_dir"]
            .chars()
            .count(),
        queries::LIST_CHARS
    );
    // ...each row linking by the whole path it shows, which is what the encoded head budgets...
    assert_eq!(
        landing.inside("data-project", &widest, "href"),
        [format!(
            "/sessions?sort=started_at&direction=desc&project={}",
            encoded(&widest)
        )]
    );
    // ...and what it left out said rather than dropped. Every planted path is a root of its own,
    // so the store's distinct directories are the rows the page had to choose between.
    let projects = rows::one(
        &served.db(),
        "SELECT count(DISTINCT coalesce(project_dir, '')) AS n FROM sessions",
        &[],
    )
    .i64("n")
    .expect("a count");
    assert_eq!(
        landing.values("data-more-projects"),
        [(projects - PROJECTS.ceiling).to_string()]
    );
    // What the page carries whatever it holds fits the allowance the ceiling gives it, with the
    // rows the arithmetic counts separately stripped out...
    static ROW: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?s)<tr data-project=.*?</tr>").expect("a pattern"));
    let (_, chrome) = without(&page, &ROW);
    assert!(Markup::of(&chrome).values("data-project").is_empty());
    assert!(chrome.contains(r#"id="projects""#));
    assert!(
        fits(chrome.len(), MEASURED_PROJECTS_CHROME),
        "{}",
        chrome.len()
    );
    // ...and one row costs no more than its markup and the two copies of its path.
    let row_bytes = (page.len() - chrome.len()) / PROJECTS.ceiling as usize;
    assert!(row_bytes <= worst_project_row_bytes(), "{row_bytes}");
}

#[tokio::test]
async fn an_errors_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets() {
    // A session's errors list at its ceiling weighs no more than the arithmetic gives it.
    //
    // Nothing about a session caps how often its tools fail, so the store is filled past the
    // page's own ceiling and every title planted full of `&` — the character that escapes to five
    // bytes. The failures are clones of a recorded tool call rather than invented rows; what is
    // planted on each is the flag the store already records on two of them.
    let over = ERRORS.ceiling + 20;
    // A title longer than the width a row cuts it to, so the cut bites on every row. The index
    // differs per clone because it is half of what orders the list: a page showing the first
    // `ERRORS` of a partial order is a page that cannot say what it cut.
    let title = "&".repeat(queries::NAV_CHARS + 1);
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "INSERT INTO tool_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id, \
                 ? AS name, ? AS input, true AS is_error, 9000 + i AS \"index\") \
                 FROM (SELECT * FROM live_tool_calls WHERE session_id = ? LIMIT 1) c, \
                 range(1, ?) g(i))",
                params![title, title, FORK_ORIGIN, over + 1],
            )
            .expect("the session fails past the ceiling");
    });
    let (status, page) = served.page(&format!("/session/{FORK_ORIGIN}/errors")).await;
    assert_eq!(status, StatusCode::OK, "{}", &page[..200]);
    // A page a reader jumps to stays under the ceiling with every title at its cap...
    assert!(page.len() < PAGE_BYTES, "{}", page.len());
    let listed = Markup::of(&page);
    let shown = listed.values("data-error");
    assert_eq!(shown.len(), ERRORS.ceiling as usize);
    // ...every one of them a planted failure cut to the width a row reads it at...
    let widest = shown
        .iter()
        .map(|key| listed.fields("data-error", key)["title"].chars().count())
        .max()
        .expect("the page lists failures");
    assert_eq!(widest, queries::NAV_CHARS + ELLIPSIS.chars().count());
    // ...and what it left out said rather than dropped, against the store's own count.
    let failures = rows::one(
        &served.db(),
        "SELECT count(*) AS n FROM live_tool_calls WHERE session_id = $session AND is_error",
        &[("session", Param::from(FORK_ORIGIN))],
    )
    .i64("n")
    .expect("a count");
    assert_eq!(
        listed.values("data-more-errors"),
        [(failures - ERRORS.ceiling).to_string()]
    );
    // What the page carries whatever it holds fits the allowance the ceiling gives it, with the
    // rows the arithmetic counts separately stripped out...
    static ROW: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?s)<li data-error=.*?</li>").expect("a pattern"));
    let (_, chrome) = without(&page, &ROW);
    assert!(Markup::of(&chrome).values("data-error").is_empty());
    assert!(chrome.contains(r#"id="errors""#));
    assert!(
        fits(chrome.len(), MEASURED_ERRORS_CHROME),
        "{}",
        chrome.len()
    );
    // ...and one row costs no more than its markup and the title it carries.
    let row_bytes = (page.len() - chrome.len()) / ERRORS.ceiling as usize;
    assert!(row_bytes <= worst_error_row_bytes(), "{row_bytes}");
}

#[test]
fn the_timeline_rows_no_window_reaches_are_capped_at_what_a_page_budgets() {
    // The rows that ride the last page outside its window are bounded, not counted afterwards.
    //
    // A timeline row with no turn index cannot be windowed, so it arrives on the last page
    // whatever `turns` a reader asked for — which is why the arithmetic above budgets
    // `CURSORLESS_TURNS` turn rows on top of the size the route admits. `RESUME` answers turns
    // that live in the session it resumed, so every one of its api calls is unattributed and its
    // timeline carries exactly this row. The cap is bound down to zero to reach a boundary no
    // recorded timeline crosses: more of these rows than the ceiling budgets raises rather than
    // riding a page nothing counted them on.
    let store = Store::open_read_only(&cache::corpus_store()).expect("the corpus opens");
    let bound = [
        ("session_id", Param::from(RESUME)),
        ("log_chars", Param::from(queries::LOG_CHARS as i64)),
    ];
    let rows = cursorless_rows(
        &store,
        Page::Timeline,
        TURN_CURSOR,
        CURSORLESS_TURNS,
        &bound,
    )
    .expect("the timeline reads");
    let named: Vec<String> = rows
        .iter()
        .map(|row| row.str("turn_id").expect("a turn").to_owned())
        .collect();
    assert_eq!(named, [queries::UNATTRIBUTED]);
    let refused = cursorless_rows(&store, Page::Timeline, TURN_CURSOR, 0, &bound)
        .expect_err("a row nothing counted is a raise");
    assert!(refused.to_string().contains("more than 0"), "{refused}");
}
