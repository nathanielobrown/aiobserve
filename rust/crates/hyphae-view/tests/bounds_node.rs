//! What a node page and an expansion cost, with every value on them as fat as the caps allow.
//!
//! The ceiling leaves in `bounds.rs` say the page fits; these say what it spends to fit. The page
//! is matched into the rows the arithmetic prices — a crumb, a NavTree row, a log row, a previewed
//! value — and each is weighed against what `hyphae-testsupport/src/budgets.rs` measured it at, so
//! a page that grows a field pays for it here before it reaches a ceiling.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

use duckdb::params;
use hyphae_extract::pricing::CONTEXT_WINDOWS;
use hyphae_store::{Store, queries};
use hyphae_testsupport::budgets::{
    DEAR_PANE_DETAILS, MEASURED_EXPANSION_CHROME, MEASURED_NODE_CHROME, MEASURED_PAGER_BYTES,
    PANE_DETAILS, describe_at_every_cap, exact_pins, fits, worst_crumb_bytes,
    worst_expansion_bytes, worst_log_row_bytes, worst_rendered_detail_bytes,
    worst_stored_detail_bytes,
};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::selections::pages;
use hyphae_testsupport::served::Served;
use hyphae_testsupport::{cache, rows};
use hyphae_view::format::ELLIPSIS;
use hyphae_view::knobs;
use hyphae_view::nodes::{BAR_STEPS, BODY_URL, Preset};
use regex::Regex;
use serde_json::json;

/// What a node page's arithmetic prices row by row, and so which chrome is the page without it: a
/// crumb of the chain down to the selection, a row of the NavTree, a row of the pane's children
/// log, and one previewed value. Each is matched rather than differenced, so what the leaf below
/// weighs is the row itself and not a difference between two pages that could differ in something
/// else.
static PRICED_ROWS: LazyLock<[(&str, Regex); 5]> = LazyLock::new(|| {
    [
        ("crumb", r"(?s)<a data-crumb=.*?</a>"),
        ("nav_tree", r#"(?s)<li class="row.*?</li>"#),
        ("log", r"(?s)<tr data-child=.*?</tr>"),
        // The control under the log, which is once a page rather than once a row — priced apart
        // from the chrome because it renders only where the level runs past one page, so a page
        // that happens to hold every child of its node would otherwise weigh it at nothing.
        ("pager", r#"(?s)<nav class="pager".*?</nav>"#),
        // The class carries the wall a quoted value wears as well as the name of the part, so the
        // match reads the whole attribute: a pattern pinned to `detail"` would stop pricing a
        // prose preview the moment one was walled.
        ("detail", r#"(?s)<section class="detail[^"]*".*?</section>"#),
    ]
    .map(|(name, pattern)| (name, Regex::new(pattern).expect("a pattern")))
});

/// One of [`PRICED_ROWS`] by name, for the leaves that match a single kind.
fn priced_row(name: &str) -> &'static Regex {
    &PRICED_ROWS
        .iter()
        .find(|(held, _)| *held == name)
        .expect("a priced row")
        .1
}

/// The sizes that make every link on a node page longest, which is what `worst_knob_bytes`
/// prices, with the log's own size named by the caller: a sweep that pages a level asks for one
/// child to a page, and an expansion asks for the whole page it is allowed.
///
/// Written as a request rather than derived from `knobs`, so the leaves below fail if the app
/// stops accepting one of them rather than quietly measuring a page with no knobs at all.
fn worst_knobs(log: i64) -> String {
    let preset = Preset::ALL
        .into_iter()
        .max_by_key(|preset| preset.word().len())
        .expect("a preset");
    format!(
        "nav={}&kin={}&log={}&detail={}",
        preset.word(),
        knobs::KIN.ceiling - 1,
        log,
        knobs::DETAIL.ceiling - 1,
    )
}

/// One node page, split into the chrome the arithmetic budgets whole and the rows it prices one
/// at a time.
struct Priced {
    chrome: String,
    rows: BTreeMap<&'static str, Vec<String>>,
}

impl Priced {
    /// The rows of one priced kind on this page.
    fn of(&self, name: &str) -> &[String] {
        &self.rows[name]
    }
}

/// A node page split into the rows the arithmetic prices and the chrome it does not.
fn priced(html: &str) -> Priced {
    let mut chrome = html.to_owned();
    let mut rows = BTreeMap::new();
    for (name, pattern) in PRICED_ROWS.iter() {
        rows.insert(
            *name,
            pattern
                .find_iter(&chrome)
                .map(|found| found.as_str().to_owned())
                .collect::<Vec<_>>(),
        );
        chrome = pattern.replace_all(&chrome, "").into_owned();
    }
    // The split is the instrument, so it is checked both ways: a row left in is a cost counted
    // twice, and a wrapper taken out hides part of the page this measures.
    let left = Markup::of(&chrome);
    for attribute in ["data-crumb", "data-nav-tree", "data-child", "data-detail"] {
        assert!(left.values(attribute).is_empty(), "{attribute}");
    }
    assert!(chrome.contains(r#"id="nav-tree-rows""#));
    assert!(chrome.contains(r#"id="reading-pane""#));
    Priced { chrome, rows }
}

/// Every row of one priced kind, across every page the sweep served.
fn found<'a>(split: &'a [Priced], name: &str) -> Vec<&'a String> {
    split.iter().flat_map(|page| page.of(name)).collect()
}

/// The widest of several strings in bytes, and the *first* of them where two tie.
///
/// ADAPTED: Python's `max` answers with the first of a tie and `max_by_key` with the last, and
/// the leaves below read what the widest row drew rather than only how wide it was — so a tie
/// resolved the other way would be a different row.
fn widest<'a>(held: impl IntoIterator<Item = &'a String>) -> &'a str {
    let mut widest: Option<&str> = None;
    for one in held {
        if widest.is_none_or(|standing| one.len() > standing.len()) {
            widest = Some(one);
        }
    }
    widest.expect("something to weigh")
}

#[tokio::test]
async fn a_node_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets() {
    // Every part of a node page weighs no more than the arithmetic in `budgets.rs` gives it. The
    // node page is the one page `worst_node_bytes` multiplies four ways — a crumb per level open,
    // a NavTree row per child of each, a log row per child of the selection, and the values the
    // pane previews — so a template that grows any of them puts the ceiling out by whatever size
    // it is multiplied by. Every cap a title, a heading or a preview reads is planted full of `&`,
    // the character that escapes to five bytes, because no recorded node is adversarial: what a
    // pass wrote, and the prompt, command, agent type, model, tool name and tool payload a page
    // falls back to. The sweep is every node of every session, not one page: the widest chrome
    // belongs to whichever pane is dearest, and that is a question about the corpus.
    let head = "&".repeat(queries::HEADER_CHARS);
    // Longer than the widest cut any query makes, so every cut bites and every preview offers the
    // rest of itself: what this weighs is the page at its caps, not at the corpus's sizes.
    let fat = "&".repeat(queries::DETAIL_CHARS + 1);
    let item = "&".repeat(queries::HEADER_ITEM_CHARS);
    // And the same width of the pair every lexer here makes two tokens of, for the two previews a
    // row can name the syntax of.
    let tokens = "&;".repeat((queries::DETAIL_CHARS + 2) / 2);
    let over = (queries::HEADER_ITEMS + 2) as i64;
    let tool_input = json!({"description": fat, "command": fat, "prompt": fat}).to_string();
    let bash_input = json!({"description": fat, "command": tokens}).to_string();
    let read_input = json!({ "file_path": format!("/{fat}/planted.sql") }).to_string();
    let fenced = format!("```sql\n{tokens}");
    let served = Served::enriched_planted(|store: &Store| {
        let connection = store.connection();
        let run = |sql: &str, bound: &[&dyn duckdb::ToSql]| {
            connection.execute(sql, bound).expect("the plant lands");
        };
        run(
            "UPDATE sessions SET title = ?, agent_name = ?, project_dir = ?, git_branch = ?,\
             version = ?, entrypoint = ?",
            params![head, head, head, head, head, head],
        );
        // A skill rides an api call, so the plant clones a live one per session rather than
        // inventing a row: `live_api_calls` is the population the header's list counts.
        run(
            "INSERT INTO api_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id, \
             ? || i AS attribution_skill) \
             FROM (SELECT DISTINCT ON (l.session_id) l.* FROM live_api_calls l) c, \
             range(1, ?) t(i))",
            params![item, over + 1],
        );
        run(
            "INSERT INTO pr_links (SELECT s.id, 900000 + i, i, ? || i, 'planted/repo', \
             '2026-01-01T00:00:00Z' FROM sessions s, range(1, ?) t(i))",
            params![item, over + 1],
        );
        // What a turn's NavTree row, log row and pane read. All three go in past every cut that
        // touches them: the timeline cuts each to a log line's width, and the prompt is the pane's
        // one preview as well as the row's title, which is the wider of the two.
        run(
            "UPDATE turns SET prompt = ?, command_name = ?, command_args = ?",
            params![fat, fat, fat],
        );
        run(
            "UPDATE agent_runs SET agent_type = ?, model = ?, brief = ?",
            params![fat, fat, fat],
        );
        run(
            "UPDATE api_calls SET model = ?, text = ?, thinking = ?",
            params![fat, fat, fat],
        );
        // The input parses and says all three of the things read out of one, because a log row
        // that could not find a description would print the raw input in its place and leave the
        // line under it empty, which is a row two columns short of the widest one there is. Every
        // call failed too, which is the dearest a tool row gets: the mark the NavTree puts on a
        // failure is markup no other kind of row carries. It does not make a tool the widest row
        // — a turn's is wider — but it is what puts the stepper on every tool page, and that is
        // the dearest the chrome under a pane gets.
        run(
            "UPDATE tool_calls SET name = ?, input = ?, result = ?, is_error = true",
            params![fat, tool_input, fat],
        );
        // One call a turn answered in a model the window table prices, at tokens a window over the
        // turn before it, so every row that draws a context bar draws one at its widest spelling:
        // three edges of two digits each. Cloned rather than flipped, because the model column
        // above is what makes an api call's the widest row of the children log, and a thread that
        // answered in a real model would print a real model there. Which model is arbitrary — the
        // bar reads the window off the table, and every window in it is spent past here — but the
        // tokens climb with the call's index, because a turn's tip is what it added over the turn
        // before: a thread of turns all left at the same fill draws a full bar with no tip in it.
        // It answers last in its turn — a thread's calls are ordered by index and a turn's fill is
        // its last call's — so the index is planted past every recorded one.
        run(
            "INSERT INTO api_calls (SELECT * EXCLUDE (rank) \
             REPLACE (id || '-filled' AS id, ? AS model, false AS synthetic, \
             1000000 + \"index\" AS \"index\", \
             300000 * rank AS input_tokens, 0 AS output_tokens, 0 AS cache_read_tokens, \
             0 AS cache_creation_tokens) \
             FROM (SELECT DISTINCT ON (l.session_id, l.source, l.turn_id) l.*, \
             l.\"index\" + 1 AS rank FROM live_api_calls l))",
            params![CONTEXT_WINDOWS[0].0],
        );
        // And the third edge, which is read off the session's opening call rather than off the
        // turn: every thread's turns stand on what `main` sent first, so the earliest call of
        // every main thread is filled to the window too. Planted after the clone above, which
        // copies live rows and would otherwise carry this width into a second call.
        run(
            "UPDATE api_calls SET input_tokens = 300000 WHERE (session_id, source, \"index\") IN \
             (SELECT session_id, source, min(\"index\") FROM api_calls \
             WHERE source = 'main' AND NOT synthetic GROUP BY session_id, source)",
            params![],
        );
        // And the two calls whose panes show a value in its own syntax, planted after the rest so
        // they keep the widths above and take the tool names that reach the lexers. `&;` is the
        // pair the shipped lexers make the most tokens of, which is what a preview budgeted at a
        // span a character has to hold.
        run(
            "UPDATE tool_calls SET name = 'Bash', input = ? \
             WHERE id = (SELECT min(id) FROM tool_calls)",
            params![bash_input],
        );
        // The path is planted past the cut like every other input here, and its suffix is what the
        // page reads the result's syntax off — a name, not a length.
        run(
            "UPDATE tool_calls SET name = 'Read', input = ?, result = ? \
             WHERE id = (SELECT max(id) FROM tool_calls)",
            params![read_input, tokens],
        );
        // And one turn asked in the dearest markdown there is: a fenced block, the one construct
        // markdown hands to a lexer. The pane cuts the head inside the fence, which commonmark
        // closes at the end of what it was given — so what it renders is `&;` at an element a
        // token, which is what a preview budgeted at `MARKED_CHAR_BYTES` has to hold.
        run(
            "UPDATE turns SET prompt = ? WHERE id = (SELECT min(id) FROM turns)",
            params![fenced],
        );
        describe_at_every_cap(store);
    });
    let db = served.db();
    let mut pages_served = Vec::new();
    // Twice over the store: once at the defaults, where the NavTree holds a row of every kind
    // there is, and once at the knobs that make every link on the page longest. A reader who
    // narrows a page pays for the query string on every row of it, and the two sweeps together
    // hold the widest row of each kind beside the dearest link.
    for marks in ["", &worst_knobs(knobs::LOG.ceiling - 1)] {
        for url in pages(&db) {
            let asked = if marks.is_empty() {
                url.clone()
            } else {
                format!("{url}?{marks}")
            };
            let (status, page) = served.page(&asked).await;
            assert_eq!(status, axum::http::StatusCode::OK, "{asked}");
            pages_served.push(page);
        }
    }
    // And once more one child to a page, at the second page of each level: no recorded node has
    // children enough to page at a size a reader would type, and the control under the log is what
    // a level running past its page costs. A level of fewer than three has no second page and no
    // middle page, and answers 404 by design.
    for url in pages(&db) {
        let (status, page) = served
            .page(&format!("{url}?{}&page=2", worst_knobs(1)))
            .await;
        if status == axum::http::StatusCode::OK {
            pages_served.push(page);
        }
    }
    // The list and the two pages that are not nodes come back too; only a node page splits.
    let split: Vec<_> = pages_served
        .iter()
        .filter(|page| page.contains(r#"id="nav-tree-rows""#))
        .map(|page| priced(page))
        .collect();
    // A crumb, a NavTree row, a log row and a preview each weigh what the arithmetic budgets...
    for (name, budget, exact) in [
        ("crumb", worst_crumb_bytes(), exact_pins()),
        ("nav_tree", knobs::NAV_TREE_ROW_BYTES, true),
        ("log", worst_log_row_bytes(), false),
        ("pager", MEASURED_PAGER_BYTES, exact_pins()),
    ] {
        let held = found(&split, name);
        assert!(!held.is_empty(), "{name}");
        let widest_row = widest(held.iter().copied());
        // A log row is arithmetic over a cap with a rounding fudge inside it, so a row that comes
        // in under is a cap with room left and the budget is only ever a ceiling. The other three
        // are measurements of the row itself, or arithmetic with nothing rounded in it: the
        // NavTree's is held from below always — the NavTree is most of the page, so a byte of
        // slack there is thousands of bytes the ceiling keeps for nothing — and the crumb's and
        // the pager's under the exact-pin mode, which is what keeps a hand-written pin from
        // outliving the measurement it stood for.
        if exact {
            assert_eq!(widest_row.len(), budget, "{name}");
        } else {
            assert!(widest_row.len() <= budget, "{name}: {}", widest_row.len());
        }
        if name != "nav_tree" {
            continue;
        }
        // And the row it priced drew a context bar at its widest spelling: three edges of two
        // digits each, which is the most a turn's row carries. A corpus that answered in models
        // the window table holds none of would price a row that draws no bar, and every barred row
        // would be twelve bytes over it.
        let bar = Regex::new(&format!(
            r#"class="[^"]* f{BAR_STEPS} p{BAR_STEPS} b{BAR_STEPS}""#
        ))
        .expect("a pattern");
        assert!(bar.is_match(widest_row), "{}", &widest_row[..200]);
        // And it drew both halves of its cost badge, which is the widest thing the row has grown:
        // a corpus whose dearest row spawned no agent run would measure under this.
        assert_eq!(widest_row.matches(r#"class="badge "#).count(), 2);
    }
    // A preview is priced by whether the page marked it up, which is the whole of the difference
    // between the two budgets: an element a token against an escape a character. Marked up two
    // ways — the syntax a record named, and the markdown a session wrote — and both are read off
    // the markup rather than off the route, because what the ceiling pays for is what came back.
    let previews = found(&split, "detail");
    let dear = |row: &str| row.contains(r#"class="code "#) || row.contains(r#"class="prose""#);
    let dearest: Vec<_> = previews.iter().copied().filter(|row| dear(row)).collect();
    assert!(!dearest.is_empty() && dearest.len() < previews.len());
    assert!(widest(dearest.iter().copied()).len() <= worst_rendered_detail_bytes());
    // And the plant reached a lexer through both of those routes, so that budget is being held
    // rather than merely not approached: the dearest preview costs more than escaping every
    // character of it would, which is the whole of the difference between the two.
    assert!(widest(dearest.iter().copied()).len() > worst_stored_detail_bytes());
    let stored: Vec<_> = previews.iter().copied().filter(|row| !dear(row)).collect();
    assert!(widest(stored).len() <= worst_stored_detail_bytes());
    // And no pane shows more previews than the arithmetic gives it, or more marked-up ones: a kind
    // that grew a third value would otherwise spend the ceiling unpriced.
    let per_page = |only_dear: bool| {
        split
            .iter()
            .map(|page| {
                page.of("detail")
                    .iter()
                    .filter(|row| !only_dear || dear(row))
                    .count()
            })
            .max()
            .expect("a page")
    };
    assert_eq!(per_page(false), PANE_DETAILS);
    assert_eq!(per_page(true), DEAR_PANE_DETAILS);
    // ...and what the page carries whatever it holds fits the allowance the ceiling gives it.
    let chrome = widest(split.iter().map(|page| &page.chrome));
    assert!(fits(chrome.len(), MEASURED_NODE_CHROME), "{}", chrome.len());
    // The plant reached the caps, which is what makes those numbers a worst case: each header
    // string cut to its head, each list cut to its first members and saying how many it left,
    // every tree title cut to a nav width, and every preview offering the rest of itself.
    let session = split
        .iter()
        .map(|page| &page.chrome)
        .find(|chrome| chrome.contains(r#"data-body="session""#))
        .expect("a session page");
    let facts = Markup::of(session).fields("data-body", "session");
    for name in ["git_branch", "version"] {
        assert_eq!(facts[name].chars().count(), queries::HEADER_CHARS, "{name}");
    }
    let title = Regex::new(r#"(?s)<span data-field="title">(.*?)</span>"#).expect("a pattern");
    let escaped: BTreeSet<usize> = found(&split, "nav_tree")
        .iter()
        .flat_map(|row| title.captures_iter(row).collect::<Vec<_>>())
        .map(|drawn| drawn[1].matches("&amp;").count())
        .collect();
    // No title got past the cut, and one reached it. Not every row's title is planted — a bucket
    // is named by the viewer and a compaction by its trigger — so the widest is what says the cut
    // bit rather than every row being the same width.
    assert_eq!(escaped.last(), Some(&queries::NAV_CHARS));
    let cuts: BTreeSet<usize> = previews
        .iter()
        .map(|row| row.matches("more character(s)").count())
        .collect();
    assert_eq!(cuts, BTreeSet::from([1]));
    // And the mark a failed call carries reached the rows the NavTree priced, so
    // `NAV_TREE_ROW_BYTES` is a price for the dearest tool row rather than for one that happened
    // to succeed.
    assert!(
        found(&split, "nav_tree")
            .iter()
            .any(|row| row.contains(r#"data-field="is_error""#))
    );
    // The enrichment sits in the chrome, stale tag and all, so it is planted with the rest.
    let markup = Markup::of(session);
    let key = markup.values("data-enrichment");
    let described = markup.fields("data-enrichment", key.first().expect("an enrichment"));
    let marked = "&".repeat(queries::ENRICHMENT_CHARS) + ELLIPSIS;
    assert_eq!(described["description"], marked);
    assert_eq!(described["friction"], marked);
    assert_eq!(described["stale"], "stale");
}

#[tokio::test]
async fn an_expansion_weighs_a_body_and_the_one_page_of_rows_it_lists() {
    // An expansion is bounded by the same cap its node's own page is, and by nothing else. An api
    // call's expansion lists the tools it called, so a call that called two hundred is where the
    // bound has to hold: the fragment reads one page of the level at the reader's `?log=`, and the
    // way past that page is the link to the call's own page rather than more rows. Planted,
    // because the densest call the corpus recorded made four tool calls — and planted at every
    // cap, with `&` in each string a row prints, so what this weighs is the fragment at its
    // ceiling rather than at the fixture's sizes.
    //
    // The body above those rows is weighed over all three kinds a log can open, not just the
    // call's: a turn's is the dearest of them, because a turn's body is the one that carries what
    // an enrichment pass wrote. So the described store, planted at the enrichment's caps as well.
    let fat = "&".repeat(queries::LOG_CHARS + 1);
    // The body's own strings are cut at the width a title is, not at the reader's `?detail=`.
    let head = "&".repeat(queries::HEADER_CHARS + 1);
    let cached = cache::enriched_store();
    let densest = rows::one(
        &cached,
        "SELECT session_id, source, api_call_id, count(*) AS held FROM live_tool_calls \
         GROUP BY 1, 2, 3 ORDER BY 4 DESC, 1, 2, 3 LIMIT 1",
        &[],
    );
    let session_id = densest.str("session_id").expect("a session id");
    let source = densest.str("source").expect("a thread");
    let api_call_id = densest.str("api_call_id").expect("a call id");
    let recorded = densest.i64("held").expect("a count");
    let under = rows::one(
        &cached,
        "SELECT c.turn_id, t.id AS tool_id FROM live_api_calls c JOIN live_tool_calls t \
         ON t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id \
         WHERE c.session_id = $session AND c.source = $source AND c.id = $call",
        &[
            ("session", session_id.into()),
            ("source", source.into()),
            ("call", api_call_id.into()),
        ],
    );
    let turn_id = under.str("turn_id").expect("a turn id");
    let tool_id = under.str("tool_id").expect("a tool id");
    let clones = knobs::LOG.ceiling * 2;
    let input = json!({"description": fat, "command": fat}).to_string();
    let served = Served::enriched_planted(|store: &Store| {
        let connection = store.connection();
        let run = |sql: &str, bound: &[&dyn duckdb::ToSql]| {
            connection.execute(sql, bound).expect("the plant lands");
        };
        // One recorded tool call, cloned past the cap: the clone keeps every column the row reads
        // except the two that have to differ, so the rows are the store's own shape.
        run(
            "INSERT INTO tool_calls (SELECT t.* REPLACE (t.id || '-planted-' || i AS id, \
             90000 + i AS \"index\") FROM (SELECT * FROM tool_calls WHERE session_id = ? \
             AND source = ? AND api_call_id = ? LIMIT 1) t, range(1, ?) g(i))",
            params![session_id, source, api_call_id, clones + 1],
        );
        // Then every string a tool row prints, planted past its cut: the name, the title the input
        // is read for, the command under it, and the failure that marks the row.
        run(
            "UPDATE tool_calls SET name = ?, input = ?, result = ?, is_error = true",
            params![fat, input, fat],
        );
        // And the facts the bodies themselves print, planted past the cut each is read at: a
        // call's model and what it fell back from, a turn's ask and the command it was typed as. A
        // body reads them at `HEADER_CHARS`, not at the reader's `?detail=`. What it said and what
        // it thought go in too: a body previews neither, but the head of what a call said is what
        // its title falls back to.
        run(
            "UPDATE api_calls SET model = ?, fallback_from = ?, text = ?, thinking = ?",
            params![head, head, head, head],
        );
        run(
            "UPDATE turns SET prompt = ?, command_name = ?",
            params![head, head],
        );
        describe_at_every_cap(store);
    });
    let at = format!("/session/{session_id}/thread/{source}");
    let asked = worst_knobs(knobs::LOG.ceiling);
    let (status, expansion) = served
        .page(&format!("{BODY_URL}{at}/call/{api_call_id}?{asked}"))
        .await;
    assert_eq!(status, axum::http::StatusCode::OK);
    let log = priced_row("log");
    let listed: Vec<&str> = log
        .find_iter(&expansion)
        .map(|found| found.as_str())
        .collect();
    // The cap bit: the level holds twice what came back, and what came back is one page of it.
    assert_eq!(listed.len() as i64, knobs::LOG.ceiling);
    assert_eq!(
        Markup::of(&expansion).field("data-log", "tools", "children"),
        (recorded + clones).to_string()
    );
    // The fragment weighs its rows and a body, and neither part is over what it is budgeted...
    assert!(
        expansion.len() <= worst_expansion_bytes(),
        "{}",
        expansion.len()
    );
    assert!(listed.iter().map(|row| row.len()).max().expect("a row") <= worst_log_row_bytes());
    let mut bodies = vec![log.replace_all(&expansion, "").into_owned()];
    // Every other kind a log opens a body for, for the widest chrome of the three.
    for (kind, node_id) in [("turn", &turn_id), ("tool", &tool_id)] {
        let (status, other) = served
            .page(&format!("{BODY_URL}{at}/{kind}/{node_id}?{asked}"))
            .await;
        assert_eq!(status, axum::http::StatusCode::OK, "{kind}");
        assert!(!log.is_match(&other), "{kind} listed a level");
        bodies.push(other);
    }
    let dearest = widest(bodies.iter());
    assert!(
        fits(dearest.len(), MEASURED_EXPANSION_CHROME),
        "{:?}",
        bodies.iter().map(String::len).collect::<Vec<_>>()
    );
    // A turn's body is the one whose title a pass can have written, so the described store is what
    // makes that title the widest it gets rather than the prompt's own head.
    assert!(
        Markup::of(&bodies[1])
            .field("data-body", "turn", "title")
            .starts_with(&"&".repeat(queries::TAG_CHARS))
    );
    // ...and an expansion opens no expansion: not one of those rows carries a button that would
    // fetch another body under it.
    assert!(!expansion.contains("data-view"));
}
