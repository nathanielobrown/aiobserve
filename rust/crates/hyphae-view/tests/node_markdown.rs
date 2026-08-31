//! Markdown in a title, from what a pass wrote to what a row serves.
//!
//! A description is one line the model wrote in markdown, and every surface that prints a title
//! prints it rendered rather than typed. No fixture holds one: redaction flattened every string
//! the corpus records, so these leaves plant the markdown and read it back off a served page. The
//! renderer's own readings are `render.rs`.

use duckdb::params;
use regex::Regex;

use hyphae_store::{Param, Store, queries};
use hyphae_testsupport::html::{Markup, plain};
use hyphae_testsupport::landmarks::MAIN;
use hyphae_testsupport::rows;
use hyphae_testsupport::served::{self, Served};
use hyphae_view::format::ELLIPSIS;
use hyphae_view::nodes::LEAD_SEPARATOR;
use hyphae_view::render::escape;

/// What a pass would write in one line about a run: the two marks a row can carry, and the link
/// only the pane can.
const WRITTEN: &str = "**bold** `code` [PR #18](https://github.test/pr/18)";

/// A model asked for a sentence that answered with a document.
const DOCUMENT: &str = "# Heading\n- one\n- two\n\n```py\nx = 1\n```";

/// Where a page labels a title. A flat one holds one run of text with no element in it, so the
/// same opening tag counts the spans and captures what they hold: a count and a capture off one
/// string cannot drift apart.
const TITLE_OPENS: &str = "<span data-field=\"title\">";

/// A turn a pass described, on the main thread and with a sibling either side, so the walk
/// controls name the same description the row does.
fn a_described_turn(db: &std::path::Path) -> (String, String) {
    let row = rows::one(
        db,
        "SELECT session_id, id FROM live_turns WHERE source = $main AND \"index\" = 1 \
         AND session_id IN (SELECT session_id FROM live_turns WHERE source = $main \
         GROUP BY 1 HAVING count(*) > 2) ORDER BY session_id LIMIT 1",
        &[("main", Param::from(MAIN))],
    );
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.str("id").expect("a turn id").to_owned(),
    )
}

/// The first turn of the store, for the leaves that only need a described row.
fn a_turn(db: &std::path::Path) -> (String, String) {
    let row = rows::one(
        db,
        "SELECT session_id, id FROM live_turns WHERE source = $main ORDER BY session_id LIMIT 1",
        &[("main", Param::from(MAIN))],
    );
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.str("id").expect("a turn id").to_owned(),
    )
}

/// An enriched store with one line written over every turn's description.
fn described(line: &'static str) -> Served {
    Served::enriched_planted(move |store: &Store| {
        store
            .connection()
            .execute("UPDATE turn_enrichments SET description = ?", [line])
            .expect("a description is plantable");
    })
}

/// The session whose main thread holds the most turns, which is the one a planted prompt reaches
/// the most rows of.
fn widest_thread(db: &std::path::Path) -> String {
    rows::one(
        db,
        "SELECT session_id FROM live_turns WHERE source = $main GROUP BY 1 \
         ORDER BY count(*) DESC, 1 LIMIT 1",
        &[("main", Param::from(MAIN))],
    )
    .str("session_id")
    .expect("a session id")
    .to_owned()
}

/// One prompt written over every turn of a session, with the slash-command columns cleared so the
/// prompt itself is what names the row.
fn prompted(session_id: String, prompt: String) -> impl Fn(&Store) {
    move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE turns SET prompt = ?, command_name = NULL, command_args = NULL \
                 WHERE session_id = ?",
                params![prompt, session_id],
            )
            .expect("the turns take the planted prompt");
    }
}

#[tokio::test]
async fn the_markdown_a_pass_wrote_renders_in_a_title_and_links_only_in_the_pane() {
    // A description is written in markdown, so a title is rendered rather than printed.
    //
    // A pass writes one line about a turn and writes it the way it writes everything else: bold
    // for the thing that matters, backticks around a path, a link to the PR it opened. Printed as
    // typed, that line spends a NavTree row's width on asterisks.
    //
    // The link is the half only one surface can carry. A NavTree row, a crumb and a walk control
    // are each already a link to the node they name, and an `<a>` inside an `<a>` is markup a
    // browser takes apart into something neither element meant — so those print the link's words,
    // and the pane's heading, which nothing wraps, gets the anchor.
    let served = described(WRITTEN);
    let (session_id, turn_id) = a_described_turn(&served.db());
    let (_, page) = served
        .page(&format!(
            "/session/{session_id}/thread/{MAIN}/turn/{turn_id}"
        ))
        .await;
    let markup = Markup::of(&page);
    // The pane heads the node it is about, and nothing wraps that heading: the link is a link.
    assert_eq!(
        markup.marked_up("data-body", "turn", "title"),
        "<strong>bold</strong> <code>code</code> \
         <a href=\"https://github.test/pr/18\">PR #18</a>"
    );
    // The three surfaces that are links already render the same line without the anchor, so the
    // reader still sees the words the pass linked and nothing nests.
    let inside_a_link = "<strong>bold</strong> <code>code</code> PR #18";
    assert_eq!(
        markup.marked_up("data-nav-tree", &format!("turn:{turn_id}"), "title"),
        inside_a_link
    );
    assert_eq!(
        markup.marked_up("data-crumb", &format!("turn:{turn_id}"), "turn"),
        inside_a_link
    );
    for stepped in ["previous", "next"] {
        assert_eq!(
            markup.marked_up("data-walk", stepped, "title"),
            inside_a_link,
            "{stepped}"
        );
    }
    // One `<a>` on the page holds the URL, and it is the one in the heading: the NavTree draws
    // this description on every turn row of the session, so a nested anchor would be everywhere.
    assert_eq!(
        page.matches("href=\"https://github.test/pr/18\"").count(),
        1
    );
}

#[tokio::test]
async fn the_browser_tab_and_every_attribute_carry_the_text_under_a_title() {
    // A `<title>` and an attribute have nowhere to put markup, so they take the text.
    //
    // Both print an element as characters or act on it, and neither is what the line says: a tab
    // reading `**bold**` shows the asterisks, and markup in an attribute is the escape the inline
    // renderer exists to close. So the tab takes the same cut, stripped.
    let served = described(WRITTEN);
    let (session_id, turn_id) = a_turn(&served.db());
    let (_, page) = served
        .page(&format!(
            "/session/{session_id}/thread/{MAIN}/turn/{turn_id}"
        ))
        .await;
    // The tab says what the line says, in none of the characters it was written in.
    let tab = Regex::new(r"(?s)<title>(.*?)</title>").expect("a pattern");
    let found = tab.captures(&page).expect("the page names its tab");
    assert_eq!(&found[1], "❯ bold code PR #18 · hyphae");
    // And no attribute on the page carries a tag: an escaped value cannot hold a bare `<`, so one
    // here is markup that reached an attribute rather than the text a reader sees.
    let attributes = Regex::new(r#"\s(data-[a-z-]+|title)="([^"]*)""#).expect("a pattern");
    for found in attributes.captures_iter(&page) {
        assert!(
            !found[2].contains('<') && !found[2].contains("**"),
            "{}",
            &found[1]
        );
    }
}

#[tokio::test]
async fn no_block_element_a_pass_wrote_escapes_into_a_nav_tree_row() {
    // A description written in paragraphs is still one line in a row.
    //
    // A pass is asked for a sentence and a model sometimes answers with a document. Only the
    // inline parser runs, so there is no rule that could open a `<p>` or a `<pre>` inside a row —
    // a row that held a block element would not be a row any more, and the NavTree draws
    // thousands of them.
    let served = described(DOCUMENT);
    let (session_id, turn_id) = a_turn(&served.db());
    let (_, page) = served
        .page(&format!(
            "/session/{session_id}/thread/{MAIN}/turn/{turn_id}"
        ))
        .await;
    let row = Markup::of(&page).marked_up("data-nav-tree", &format!("turn:{turn_id}"), "title");
    for element in ["<h", "<ul>", "<li>", "<p>", "<pre>", "<ol>", "<blockquote>"] {
        assert!(!row.contains(element), "{element} in {row}");
    }
    // The heading's own `#` and the list's dashes survive as the typing they are.
    let read = plain(&row);
    assert!(
        read.contains("# Heading") && read.contains("- one"),
        "{row}"
    );
}

#[tokio::test]
async fn a_row_that_spent_none_of_its_width_still_says_the_query_cut_the_line() {
    // What says a title was stopped is the width its own query cut at, not the row's.
    //
    // A width is spent on visible characters, so a prompt written in markdown reaches a row a
    // third of the length the store shipped — and the row is full of nothing. The mark has to
    // come from the cap instead: the turns query ships a prompt one character past a row's width,
    // so 111 characters back means more went in, whatever they render to. The cap is the words'
    // own — a width of its own for a description, and the lead in front of them is room the cap
    // has to allow for.
    let corpus = Served::corpus();
    let spent = widest_thread(&corpus.db());
    let served = Served::planted(prompted(spent.clone(), "**ab** ".repeat(40)));
    let (_, page) = served.page(&format!("/session/{spent}")).await;
    let markup = Markup::of(&page);
    let turn = markup
        .values("data-nav-tree")
        .into_iter()
        .find(|key| key.starts_with("turn:"))
        .expect("the session draws a turn row");
    let row = plain(&markup.marked_up("data-nav-tree", &turn, "title"));
    // Half a row wide and still marked: the syntax the rest of the prompt was written in is what
    // the query's cut spent, and only the query knows it spent it.
    assert!(row.chars().count() < queries::NAV_CHARS / 2, "{row}");
    assert!(row.ends_with(ELLIPSIS), "{row}");

    // A description is cut at a width of its own, wider than any row — so the same markup, short
    // enough that nothing cut it, carries no mark on the same surface. A cap read off the row
    // instead would stop a line nothing stopped.
    let served = described(
        "**ab** **ab** **ab** **ab** **ab** **ab** **ab** **ab** **ab** \
                            **ab** **ab** **ab** **ab** **ab** **ab** **ab** **ab** **ab** \
                            **ab** **ab** ",
    );
    let (session_id, turn_id) = a_turn(&served.db());
    let (_, read) = served
        .page(&format!(
            "/session/{session_id}/thread/{MAIN}/turn/{turn_id}"
        ))
        .await;
    let shown =
        plain(&Markup::of(&read).marked_up("data-nav-tree", &format!("turn:{turn_id}"), "title"));
    assert_eq!(shown.trim(), "ab ".repeat(20).trim());
    assert!(!shown.contains(ELLIPSIS), "{shown}");

    // And the third thing between a cap and a mark: a lead. The query cut the words alone — what
    // stands in front of them was composed at print time and is whole — so a row whose words fill
    // the cap exactly is marked the moment the lead is counted against it. A tool the registry
    // does not name leads with the tool's own name, which is what plants one here.
    let words = format!("{}cdefg", "**ab** ".repeat(15));
    assert_eq!(
        words.chars().count(),
        queries::NAV_CHARS,
        "the plant is the widest uncut string there is"
    );
    let call = rows::one(
        &corpus.db(),
        "SELECT session_id, source, id FROM live_tool_calls \
         ORDER BY session_id, source, \"index\" LIMIT 1",
        &[],
    );
    let told = call.str("session_id").expect("a session id").to_owned();
    let thread = call.str("source").expect("a thread").to_owned();
    let tool_id = call.str("id").expect("a tool id").to_owned();
    let input = serde_json::json!({ "description": words }).to_string();
    let named = tool_id.clone();
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET name = 'BashOutput', input = ? WHERE id = ?",
                params![input, named],
            )
            .expect("the call takes the planted description");
    });
    let (_, beside) = served
        .page(&format!("/session/{told}/thread/{thread}/tool/{tool_id}"))
        .await;
    let lit =
        plain(&Markup::of(&beside).marked_up("data-nav-tree", &format!("tool:{tool_id}"), "title"));
    // Sixty-three characters of a hundred-and-ten-wide row, and nothing stopped any of them: the
    // words came back at the width the query cut at, and the thirteen in front of them were added
    // here. A cap that counted only the words would mark this row.
    assert_eq!(
        lit,
        format!("BashOutput{LEAD_SEPARATOR}{}cdefg", "ab ".repeat(15))
    );
    assert!(!lit.contains(ELLIPSIS), "{lit}");
}

#[tokio::test]
async fn a_title_the_corpus_records_flat_is_served_as_the_bytes_it_always_was() {
    // Rendering markdown changed nothing about a title that has none in it.
    //
    // Every string the fixture corpus records is flat — redaction saw to that — which makes the
    // whole corpus the control for the renderer standing between a value and the page. Read as
    // bytes rather than as text, because a NavTree row's width is budgeted in bytes: a renderer
    // that spelled one escape differently would move the ceiling without changing a word anyone
    // reads.
    let served = Served::corpus();
    let sessions = served::session_ids(&served.db());
    assert!(!sessions.is_empty(), "the fixture corpus records a session");
    // `[^<]*` is the assertion as much as the capture: a title that rendered an element would not
    // match, so the count says every title span on the page is one run of text.
    let flat =
        Regex::new(&format!("{}([^<]*)</span>", regex::escape(TITLE_OPENS))).expect("a pattern");
    let mut read = 0;
    for session_id in &sessions {
        let (_, page) = served.page(&format!("/session/{session_id}")).await;
        let held: Vec<String> = flat
            .captures_iter(&page)
            .map(|found| found[1].to_owned())
            .collect();
        assert_eq!(
            held.len(),
            page.matches(TITLE_OPENS).count(),
            "{session_id}"
        );
        for title in &held {
            // Escaped the way the viewer escapes it: undoing the escapes and writing the value
            // back out through the one escaper every value on a page goes through is a no-op only
            // on that escaper's own spelling.
            let decoded = html_escape::decode_html_entities(title).into_owned();
            assert_eq!(&escape(&decoded), title, "{title}");
        }
        read += held.len();
    }
    // A sweep that read nothing would pass on a viewer that served no rows at all.
    assert!(read > sessions.len(), "the sweep found no title to read");

    // None of those titles holds a character worth escaping, so the spelling is planted rather
    // than swept: five characters, on the surface the byte budget is measured on.
    let marks = r#"a & b < c > d "e" 'f'"#;
    let spent = widest_thread(&served.db());
    let planted = Served::planted(prompted(spent.clone(), marks.to_owned()));
    let (_, page) = planted.page(&format!("/session/{spent}")).await;
    let escaped = escape(marks);
    assert!(
        page.contains(&format!("{TITLE_OPENS}{escaped}</span>")),
        "{escaped}"
    );
}
