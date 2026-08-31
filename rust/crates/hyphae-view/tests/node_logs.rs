//! The children log: one page of one kind of child, and the words above its columns.
//!
//! A pane lists one kind of child at a time, `?log=` steps through the level a page at a time, and
//! the head says which of how many. These leaves walk every shape of log through every page it
//! has, and read the head back off the served table rather than off the column table behind it.

use std::collections::{BTreeMap, BTreeSet};

use axum::http::StatusCode;

use hyphae_testsupport::html::Markup;
use hyphae_testsupport::rows;
use hyphae_testsupport::selections::{LEVELS, level_url, node_url, turn_url};
use hyphae_testsupport::served::{self, Served};
use hyphae_view::columns::Shape;
use hyphae_view::knobs;
use hyphae_view::labels::label;
use hyphae_view::nodes::BODY_URL;

/// The three kinds a parent's children log counts in a column of its own, beside that column.
const SHARED: [(&str, &str); 3] = [
    ("call", "api_calls"),
    ("tool", "tool_calls"),
    ("run", "agent_type"),
];

/// The shape a log's heading word names. `Shape` has no reader from its own word, so the sweep
/// that walks the words finds it here rather than pinning a second table beside the first.
fn shaped(word: &str) -> Shape {
    Shape::ALL
        .into_iter()
        .find(|shape| shape.word() == word)
        .unwrap_or_else(|| panic!("no shape is called {word}"))
}

/// The body mount on a log row: the one of its `hx-get`s under the body route.
fn mount(markup: &Markup, key: &str) -> String {
    let found: Vec<String> = markup
        .inside("data-child", key, "hx-get")
        .into_iter()
        .filter(|at| at.starts_with(BODY_URL))
        .collect();
    assert_eq!(found.len(), 1, "one body mount on {key}: {found:?}");
    found.into_iter().next().expect("a mount")
}

/// Every child a log lists, gathered by following its pager from the page given.
///
/// Bounded by the level's own size: a pager that offered a way on from its last page would
/// otherwise walk for as long as the store answers.
async fn walked_log(served: &Served, at: &str, held: usize) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    let mut following = Some(at.to_owned());
    for _ in 0..=held {
        let Some(url) = following else {
            return found;
        };
        let (status, page) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "GET {url}");
        let markup = Markup::of(&page);
        found.extend(markup.values("data-child"));
        // A last page carries no way on, and the reader that scopes to one would panic looking.
        following = markup
            .holds("data-page", "next")
            .then(|| markup.inside("data-page", "next", "href"))
            .and_then(|onward| onward.into_iter().next());
    }
    panic!("{at}: the pager never reached a last page");
}

#[tokio::test]
async fn every_shape_of_log_serves_the_page_asked_for_and_counts_its_level() {
    // A page holds what the URL asked for, and the heading above it counts the level.
    //
    // Swept per shape at `?log=1`: the corpus's widest level is five children against a page of a
    // hundred, so at the production size every page is its whole level and both clauses read true
    // however the code got there. One row a page is what tells a page from the level it came from.
    let served = Served::corpus();
    for (parent, _, _, shape) in LEVELS {
        let (url, _) = level_url(&served.db(), parent);
        let (_, page) = served.page(&url).await;
        let children = Markup::of(&page).values("data-child");
        assert!(
            children.len() > 1,
            "{url}: the widest {parent} has to hold a level worth paging"
        );
        for (number, child) in children.iter().enumerate() {
            let number = number + 1;
            let (_, page) = served.page(&format!("{url}?log=1&page={number}")).await;
            let page = Markup::of(&page);
            // The page is the one row the URL asked for, in the level's own order...
            assert_eq!(
                page.values("data-child"),
                std::slice::from_ref(child),
                "{url} page {number}"
            );
            // ...under a heading counting the level rather than the row beneath it...
            assert_eq!(
                page.field("data-log", shape, "children"),
                children.len().to_string(),
                "{url}"
            );
            // ...and a pager placing the page in the level.
            assert_eq!(
                page.field("data-pager", shape, "place"),
                format!("Page {number} of {}", children.len()),
                "{url}"
            );
        }
    }
}

#[tokio::test]
async fn a_children_log_pages_by_number_and_counts_the_whole_level() {
    // The log is one numbered page of a level, and the heading counts the level.
    //
    // Driven below the corpus's fan-out with `?log=`, because no recorded turn has more children
    // than the production page. What is read is that the pages concatenate to the level exactly
    // once, that each says which of how many it is, and that the count above them is the level's
    // own — a heading counting the rows in front of the reader says a turn of four calls has one.
    let served = Served::corpus();
    let turn = turn_url();
    let (_, whole) = served.page(&turn).await;
    let children = Markup::of(&whole).values("data-child");
    assert!(children.len() > 2, "the log has to have something to page");
    // One child to a page: the first page holds the first child...
    let (_, first) = served.page(&format!("{turn}?log=1")).await;
    let first = Markup::of(&first);
    assert_eq!(first.values("data-child"), children[..1]);
    // ...under a heading counting the whole level rather than the row beneath it...
    assert_eq!(
        first.field("data-log", "calls", "children"),
        children.len().to_string()
    );
    // ...and a pager saying which page of how many this is.
    assert_eq!(
        first.field("data-pager", "calls", "place"),
        format!("Page 1 of {}", children.len())
    );
    // The first page offers no way back, and its way on is numbered rather than a cursor.
    assert!(!first.holds("data-page", "previous"));
    let onward = first.inside("data-page", "next", "href");
    assert_eq!(onward.len(), 1);
    let onward = &onward[0];
    assert!(onward.contains("page=2") && !onward.contains("after="));
    let (_, second) = served.page(onward).await;
    let second = Markup::of(&second);
    assert_eq!(second.values("data-child"), children[1..2]);
    assert_eq!(
        second.field("data-pager", "calls", "place"),
        format!("Page 2 of {}", children.len())
    );
    // The way back from the second page lands on the first, which is the page with no number.
    let back = second.inside("data-page", "previous", "href");
    assert_eq!(back, [format!("{turn}?log=1")]);
    let (_, landed) = served.page(&back[0]).await;
    assert_eq!(Markup::of(&landed).values("data-child"), children[..1]);
    // Walking forward lands on every child exactly once, in the level's own order.
    assert_eq!(
        walked_log(&served, &format!("{turn}?log=1"), children.len()).await,
        children
    );
}

#[tokio::test]
async fn a_level_divides_into_the_pages_it_has_and_no_empty_one() {
    // The page count is the level's own arithmetic, at any size a URL asks for.
    //
    // Read at three sizes against one recorded level: one that divides it, one that leaves a
    // remainder, and one that holds the whole thing. The arithmetic is where a paginator goes
    // wrong, and the failure is quiet — an off-by-one mints a last page with nothing on it.
    let served = Served::corpus();
    let turn = turn_url();
    let (_, whole) = served.page(&turn).await;
    let children = Markup::of(&whole).values("data-child");
    let held = children.len();
    for (size, count) in [(1, held), (held - 1, 2), (held, 1)] {
        for number in 1..=count {
            let (_, page) = served
                .page(&format!("{turn}?log={size}&page={number}"))
                .await;
            let page = Markup::of(&page);
            assert_eq!(
                page.values("data-child"),
                children[(number - 1) * size..(number * size).min(held)]
            );
            // Every page of the level says the same total, and its own place in it...
            assert_eq!(
                page.field("data-log", "calls", "children"),
                held.to_string()
            );
            if count > 1 {
                assert_eq!(
                    page.field("data-pager", "calls", "place"),
                    format!("Page {number} of {count}")
                );
            }
        }
        // ...and one page past the last is nothing at all, rather than an empty log that reads
        // as a node with no children.
        let (status, _) = served
            .page(&format!("{turn}?log={size}&page={}", count + 1))
            .await;
        assert_eq!(status, StatusCode::NOT_FOUND);
    }
    // A level that fits on one page carries no pager: there is no page to go to.
    let (_, one_page) = served.page(&format!("{turn}?log={held}")).await;
    assert!(!one_page.contains("data-pager"));
    // And a page number below the first is a bad ask rather than a miss: no level has one, so
    // it is the number that is wrong and not the node — the answer every other size a URL
    // carries gives.
    let (status, _) = served.page(&format!("{turn}?page=0")).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    // A level with nothing in it counts nothing. The count comes off the page's own rows, so an
    // empty page is the one place it has no row to read it from.
    let empty = rows::one(
        &served.db(),
        "SELECT c.session_id, c.source, c.id FROM live_api_calls c \
         LEFT JOIN live_tool_calls t \
         ON t.session_id = c.session_id AND t.api_call_id = c.id \
         GROUP BY ALL HAVING count(t.id) = 0 ORDER BY 1, 2, 3 LIMIT 1",
        &[],
    );
    let at = format!(
        "/session/{}/thread/{}/call/{}",
        empty.str("session_id").expect("a session id"),
        empty.str("source").expect("a thread"),
        empty.str("id").expect("a call id"),
    );
    let (_, childless) = served.page(&at).await;
    assert_eq!(
        Markup::of(&childless).field("data-log", "tools", "children"),
        "0"
    );
    assert!(!childless.contains("data-pager"));
}

#[tokio::test]
async fn the_bucket_that_pages_in_memory_walks_the_same_way_the_query_does() {
    // The unattached bucket's log pages by slicing, and owes what the queried log owes.
    //
    // Its runs arrive with the session's, which every level of the NavTree needs anyway, so this
    // one level cuts a list it already holds instead of asking the store for a page. Read on the
    // one recorded bucket that holds more than one run: the pages have to concatenate to the
    // level, the heading has to count the level rather than the page, and the last page has to be
    // last.
    let served = Served::corpus();
    let mut bucketed: Vec<(String, Vec<String>)> = Vec::new();
    for session_id in served::session_ids(&served.db()) {
        let at = format!("/session/{session_id}/unattached");
        let (status, page) = served.page(&at).await;
        if status != StatusCode::OK {
            continue;
        }
        let children = Markup::of(&page).values("data-child");
        if children.len() > 1 {
            bucketed.push((at, children));
        }
    }
    let (at, children) = bucketed
        .first()
        .expect("the corpus has a bucket holding more than one unattached run");
    let (_, first) = served.page(&format!("{at}?log=1")).await;
    let first = Markup::of(&first);
    assert_eq!(first.values("data-child"), children[..1]);
    assert_eq!(
        first.field("data-log", "runs", "children"),
        children.len().to_string()
    );
    assert_eq!(
        first.field("data-pager", "runs", "place"),
        format!("Page 1 of {}", children.len())
    );
    // Walking to the end lands on every run exactly once, in the level's own order...
    assert_eq!(
        walked_log(&served, &format!("{at}?log=1"), children.len()).await,
        *children
    );
    // ...and the whole level on one page ends the walk there.
    let (_, one_page) = served.page(&format!("{at}?log={}", children.len())).await;
    assert!(!one_page.contains("data-pager"));
    let (status, _) = served
        .page(&format!("{at}?log={}&page=2", children.len()))
        .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn the_page_the_log_opens_at_is_the_url_with_no_page_on_it() {
    // `?page=1` serves the same page the URL without it serves.
    //
    // The two have to agree or a reader who pages back to the start gets a different document
    // from the one they were linked, and the payload sweep prices only one of them.
    let served = Served::corpus();
    let turn = turn_url();
    let (bare_status, bare) = served.page(&turn).await;
    let (opened_status, opened) = served.page(&format!("{turn}?page=1")).await;
    assert_eq!(
        (bare_status, opened_status),
        (StatusCode::OK, StatusCode::OK)
    );
    assert_eq!(
        Markup::of(&opened).values("data-child"),
        Markup::of(&bare).values("data-child")
    );
    // Which is what the helper every pager link is minted through says: the first page is the
    // node's own URL, and a later one hangs off whatever knobs the reader is carrying. A `&`
    // where a `?` belongs is a 404, so both arms are read.
    assert_eq!(knobs::numbered(&turn, "", 1), turn);
    assert_eq!(knobs::numbered(&turn, "?log=1", 1), format!("{turn}?log=1"));
    assert_eq!(knobs::numbered(&turn, "", 3), format!("{turn}?page=3"));
    assert_eq!(
        knobs::numbered(&turn, "?log=1", 3),
        format!("{turn}?log=1&page=3")
    );
}

#[tokio::test]
async fn a_page_number_outside_the_level_is_answered_rather_than_served() {
    // The other three things a reader can type at a log, answered rather than served: a page
    // past the level's end, a page below the first, and a knob outside its bounds.
    let served = Served::corpus();
    let (id, turns) = served::busiest_session(&served.db());
    assert!(turns > 1, "the corpus has a level worth paging");
    let (first, _) = served.page(&format!("/session/{id}?log=1")).await;
    let (second, _) = served.page(&format!("/session/{id}?log=1&page=2")).await;
    assert_eq!((first, second), (StatusCode::OK, StatusCode::OK));
    let (past, _) = served.page(&format!("/session/{id}?log=1&page=9999")).await;
    assert_eq!(past, StatusCode::NOT_FOUND);
    let (below, _) = served.page(&format!("/session/{id}?page=0")).await;
    assert_eq!(below, StatusCode::BAD_REQUEST);
    let (huge, _) = served.page(&format!("/session/{id}?kin=100000")).await;
    assert_eq!(huge, StatusCode::BAD_REQUEST);
    let (unknown, _) = served.page(&format!("/session/{id}?nav=bogus")).await;
    assert_eq!(unknown, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn every_children_log_heads_the_columns_its_rows_fill() {
    // The log is a table: one head naming the columns, and every row filling all of them.
    //
    // The reason it is a table at all — a row of bare numbers is unreadable, and a reader who
    // cannot tell an api-call count from a tool-call count from a time of day is reading nothing.
    // So the contract is that head and row agree, column for column, in order: a cell rendered
    // under some other column's heading is a number attributed to the wrong question.
    //
    // Swept per shape, because the columns are the shape's own — a turn's children are counted
    // by what a call did, a call's by what a tool answered.
    let served = Served::corpus();
    for (parent, _, _, shape) in LEVELS {
        let (url, _) = level_url(&served.db(), parent);
        let (_, page) = served.page(&url).await;
        let page = Markup::of(&page);
        let columns = shaped(shape).columns();
        let named: Vec<String> = columns
            .iter()
            .map(|column| column.field.to_owned())
            .collect();
        // The head names the shape's columns, in the order the shape declares them...
        assert_eq!(
            page.inside("data-columns", shape, "data-column"),
            named,
            "{url}"
        );
        // ...each a column heading a screen reader can attribute a cell to...
        assert_eq!(
            page.inside("data-columns", shape, "scope"),
            vec!["col"; named.len()],
            "{url}"
        );
        // ...each heading an icon over a word from the registry every header on the page reads...
        let headed: BTreeMap<String, String> = columns
            .iter()
            .map(|column| {
                (
                    column.field.to_owned(),
                    format!("{} {}", column.icon, label(column.field)),
                )
            })
            .collect();
        assert_eq!(page.headings(), headed, "{url}");
        // ...and every row fills every one of them, so no cell sits under a heading not its own.
        let children = page.values("data-child");
        assert!(!children.is_empty(), "{url}");
        for key in &children {
            assert_eq!(
                page.inside("data-child", key, "data-column"),
                named,
                "{url} {key}"
            );
        }
        // And what a row opens spans exactly those columns. `nodes::LISTED` says which shape of
        // log a kind lists in, and the expansion's span is read off it — a kind mapped to the
        // wrong shape opens a row narrower or wider than the table it lands in. Checked here,
        // against the page that did the listing, because this is where the shape is known right.
        let at = mount(&page, &children[0]);
        let (status, body) = served.page(&at).await;
        assert_eq!(status, StatusCode::OK, "{at}");
        assert_eq!(
            Markup::of(&body).values("colspan"),
            [named.len().to_string()],
            "{at}"
        );
    }
}

#[tokio::test]
async fn a_kind_is_marked_the_same_in_the_nav_tree_and_in_the_column_that_counts_it() {
    // A column head and a NavTree row are one reader meeting one thing twice, so they agree.
    //
    // `⇄` over a turn's api-call count and `⇄` on an api call's own row are the same fact said in
    // two places, and a reader who learned the mark in a table head has to find it again in the
    // tree. Both sides are read off served pages rather than off the table behind them, so a
    // mapping that let the two drift would show up here as two characters.
    let served = Served::corpus();
    let mut headed: BTreeMap<String, String> = BTreeMap::new();
    for (parent, _, _, _) in LEVELS {
        let (url, _) = level_url(&served.db(), parent);
        let (_, log) = served.page(&url).await;
        let log = Markup::of(&log);
        headed.extend(log.headings());
        // The head is where a log says its kind, and the only place: a mark on every row
        // under it would be the same character down a column that already means it, at
        // 49 bytes a row on a page whose tree spends four fifths of the budget.
        for key in log.values("data-child") {
            assert!(log.icons("data-child", &key).is_empty(), "{url} {key}");
        }
    }
    for (kind, field) in SHARED {
        let (_, page) = served.page(&node_url(&served.db(), kind)).await;
        let page = Markup::of(&page);
        let selected = page.values("data-selected");
        assert_eq!(selected.len(), 1, "{kind}");
        let marks = page.icons("data-nav-tree", &selected[0]);
        assert_eq!(marks.len(), 1, "{kind}");
        // The heading is the mark and then the word for the column, which is what `headings`
        // reads back with its whitespace collapsed.
        assert!(
            headed[field].starts_with(&format!("{} ", marks[0])),
            "{kind} {field} {}",
            headed[field]
        );
    }
}

#[tokio::test]
async fn a_log_row_opens_the_body_from_a_button_that_says_so() {
    // The expansion is a labelled control, and what it opens stands in the log's own table.
    //
    // A `<details>` summary said `body` and looked like text; a reader has to be able to see
    // that a row can be opened. And what arrives is a row of the same table — the fragment is
    // swapped in after the row that asked for it, so a body wrapped in anything but a `<tr>`
    // lands outside the table the browser is drawing.
    let served = Served::corpus();
    let (url, _) = level_url(&served.db(), "call");
    let (_, page) = served.page(&url).await;
    let page = Markup::of(&page);
    let children = page.values("data-child");
    assert!(!children.is_empty(), "{url}");
    for key in &children {
        // The control names the row it opens, and it is a button rather than a disclosure.
        assert_eq!(
            page.inside("data-child", key, "data-view"),
            std::slice::from_ref(key),
            "{key}"
        );
        let at = mount(&page, key);
        let (status, body) = served.page(&at).await;
        assert_eq!(status, StatusCode::OK, "{at}");
        // The body arrives as one row spanning the table it opens under.
        assert!(body.trim_start().starts_with("<tr"), "{at}");
        assert_eq!(
            Markup::row(&body).inside("data-expansion", "tool", "colspan"),
            [Shape::Tools.columns().len().to_string()],
            "{at}"
        );
    }
    // And the disclosure the button replaced is gone from the log. Scoped to the log because
    // the page footer keeps one for the queries it ran, which no reader has to find to read
    // a row.
    let served_html = page.served();
    let opens: Vec<usize> = served_html
        .match_indices("<section class=\"log\"")
        .map(|(at, _)| at)
        .collect();
    assert_eq!(opens.len(), 1, "{url} draws one log");
    let log = &served_html[opens[0]..];
    let log = &log[..log.find("</section>").expect("the log closes")];
    assert!(!log.contains("<details"), "{url}");
}

#[tokio::test]
async fn every_page_of_a_level_lists_each_row_once_and_stops() {
    // The same walk as `a_children_log_pages_by_number_and_counts_the_whole_level`, at the size
    // production serves rather than at one row a page, and over the deepest level the corpus
    // records: what it holds that the leaf above does not is that no recorded level overflows the
    // log's own page, and that the walk off the end of one stops.
    //
    // A cursor bug in `store::window` loses rows silently rather than erroring, so the walk reads
    // every page a row at a time and holds the union against the whole level.
    let served = Served::corpus();
    let (id, turns) = served::busiest_session(&served.db());
    assert!(turns > 1, "the corpus has a level worth paging");
    let (status, whole) = served.page(&format!("/session/{id}")).await;
    assert_eq!(status, StatusCode::OK);
    let level: BTreeSet<String> = Markup::of(&whole)
        .values("data-child")
        .into_iter()
        .collect();
    assert_eq!(level.len() as i64, turns, "the unpaged log lists the level");
    let mut walked = Vec::new();
    for page in 1.. {
        let (status, markup) = served
            .page(&format!("/session/{id}?log=1&page={page}"))
            .await;
        if status == StatusCode::NOT_FOUND {
            break;
        }
        assert_eq!(status, StatusCode::OK, "page {page}");
        assert!(page < 500, "the walk terminates");
        walked.extend(Markup::of(&markup).values("data-child"));
    }
    assert_eq!(walked.len() as i64, turns, "each row on exactly one page");
    assert_eq!(walked.iter().cloned().collect::<BTreeSet<_>>(), level);
}

#[tokio::test]
async fn a_row_with_no_cursor_is_on_the_page_and_outside_the_count() {
    // The other bucket, and the other half of what a bucket owes: `the_bucket_that_pages_in_memory`
    // walks the unattached runs, and this reads the unattributed calls — rows the paging query
    // gives no cursor value, which `store::cursorless_rows` is what finds. One has to reach the
    // NavTree without joining the count the children log pages against.
    let served = Served::corpus();
    let (id, source) = unattributed(&served.db());
    let (status, page) = served.page(&format!("/session/{id}")).await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        page.contains(&format!("/session/{id}/thread/{source}/unattributed")),
        "the bucket stands in the NavTree of /session/{id}",
    );
    // The log counts the turns it pages over; the bucket is not one of them.
    let counted = Markup::of(&page).values("data-child").len();
    let turns = rows::one(
        &served.db(),
        "SELECT count(*) AS turns FROM turns WHERE session_id = $session AND source = 'main'",
        &[("session", id.as_str().into())],
    )
    .i64("turns")
    .expect("a turn count");
    assert_eq!(counted as i64, turns, "the bucket is outside the count");
}

/// The session with a thread whose calls answer no turn, and that thread.
fn unattributed(db: &std::path::Path) -> (String, String) {
    let row = rows::one(
        db,
        "SELECT session_id, source FROM api_calls WHERE turn_id IS NULL \
         AND source = 'main' ORDER BY 1, 2 LIMIT 1",
        &[],
    );
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.str("source").expect("a thread").to_owned(),
    )
}
