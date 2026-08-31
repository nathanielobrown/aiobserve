//! The header above a node: the store's own facts about it, labelled in words.
//!
//! A fact is printed under the word `view/labels.rs` gives its column, so a page and a log column
//! call one store column the same thing. The lists among them — the skills a session used, the PRs
//! it touched — say when they cut what they hold, and a PR is a link only where a browser can
//! follow one.

use std::collections::BTreeSet;

use duckdb::params;
use regex::Regex;

use hyphae_store::{Param, Store, queries};
use hyphae_testsupport::corpus;
use hyphae_testsupport::html::{Markup, money};
use hyphae_testsupport::landmarks::{MAIN, SPINE, SPINE_RUN};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::columns::Shape;
use hyphae_view::format::ELLIPSIS;
use hyphae_view::labels::{label, named};

#[tokio::test]
async fn the_session_header_holds_what_the_store_says_about_it() {
    // The session page's header is that session's own rollup and identity.
    let served = Served::corpus();
    let (_, page) = served.page(&format!("/session/{SPINE}")).await;
    let pane = Markup::of(&page).fields("data-body", "session");
    let held = rows::one(
        &served.db(),
        "SELECT s.title, r.turns, r.agent_runs, r.cost_usd FROM sessions s \
         JOIN session_rollups r ON r.session_id = s.id WHERE s.id = $session",
        &[("session", Param::from(SPINE))],
    );
    // The title heads the pane and does not repeat under it: a fact row printing the same string
    // the heading already carries is a line a reader reads twice. The row the session ran in has
    // gone the same way — the crumb above the pane links the project, which is a way out of the
    // session rather than one more string in the column.
    assert_eq!(pane["title"], held.str("title").expect("a title"));
    assert!(!pane.contains_key("recorded_title"));
    assert!(!pane.contains_key("project_dir"));
    assert_eq!(
        pane["turns"],
        held.i64("turns").expect("a count").to_string()
    );
    assert_eq!(
        pane["agent_runs"],
        held.i64("agent_runs").expect("a count").to_string()
    );
    assert_eq!(
        pane["cost_usd"],
        money(held.f64("cost_usd").expect("a cost"))
    );
}

#[tokio::test]
async fn a_header_labels_its_facts_in_words() {
    // A header names each fact the way a reader says it, with the store's column beside it.
    //
    // Both halves, because they answer to different readers: the `<dt>` is what a person reads and
    // the `data-field` is what the rest of this suite reads a header by, so neither can drift into
    // the other. `wall_ms` is the case that forces the split — the value under it already prints
    // as `24h 25m`, and a label ending in `_ms` contradicts the cell it stands over.
    let served = Served::corpus();
    let (_, page) = served.page(&format!("/session/{SPINE}")).await;
    // The formatter is free to put the two tags on lines of their own, so the pattern reads across
    // whatever it left between them; what it may not do is pair a label with the value of some
    // other fact, which is why nothing but whitespace is allowed there.
    let paired =
        Regex::new(r#"(?s)<dt>([^<]*)</dt>\s*<dd data-field="([^"]+)""#).expect("a pattern");
    let labelled: std::collections::BTreeMap<String, String> = paired
        .captures_iter(&page)
        .map(|found| (found[1].to_owned(), found[2].to_owned()))
        .collect();
    assert_eq!(labelled["Wall time"], "wall_ms");
    assert_eq!(labelled["Session"], "session_id");
    assert_eq!(labelled["Cost"], "cost_usd");
    // And a label reads off its own value with a space between the two — `Cost $1.48`, never
    // `Cost$1.48`. The formatter stands the two tags on lines of their own, where before the
    // stylesheet was the only thing holding them apart.
    let markup = Markup::of(&page);
    let shown = markup.fields("data-body", "session");
    let said = markup.reads("data-body", "session");
    assert!(
        said.contains(&format!("Cost {}", shown["cost_usd"])),
        "{said}"
    );
    assert!(
        said.contains(&format!("Wall time {}", shown["wall_ms"])),
        "{said}"
    );
}

/// Every string literal the pattern's one group catches across a directory of Rust sources.
fn scanned(under: &str, pattern: &str) -> BTreeSet<String> {
    let named = Regex::new(pattern).expect("a pattern");
    let mut found = BTreeSet::new();
    let mut stack = vec![
        corpus::repo()
            .join("rust/crates/hyphae-view/src")
            .join(under),
    ];
    while let Some(path) = stack.pop() {
        if path.is_dir() {
            stack.extend(
                std::fs::read_dir(&path)
                    .expect("the package is readable")
                    .map(|entry| entry.expect("a directory entry").path()),
            );
        } else if path.extension().is_some_and(|kind| kind == "rs") {
            let source = std::fs::read_to_string(&path).expect("a source file is readable");
            found.extend(named.captures_iter(&source).map(|hit| hit[1].to_owned()));
        }
    }
    found
}

#[test]
fn every_fact_a_header_asks_for_has_a_label() {
    // The label registry is closed over the components: no extra entries, and no missing ones.
    //
    // A header field with no label would reach a reader as a column name, which is the thing the
    // registry exists to stop, and an entry nothing asks for is a word nobody sees. Read off the
    // components, the panes and the log's column table rather than listed here, so a fact added to
    // any of them lands in this check. The panes are a source because a previewed value is
    // labelled by the name the route passed it under, which no component names; the column table
    // is one because a children log heads itself from a variable, which no scan over a source file
    // can see. Every module of the view crate is read rather than one, so a pane that moves to a
    // module of its own keeps its previews in the check.
    //
    // A source scan and not a render, unlike its neighbours: a label a component asks for and no
    // page reaches would go unseen either way, but a missing one panics on `label`'s own refusal
    // the moment a page does reach it. What this adds is the other half — a word in the registry
    // that nothing asks for.
    // `counted` is the one wrapper around `fact`, and it takes the same first argument: a scan
    // that read only the two primitives would miss every count a header prints.
    let asked = scanned(
        "components",
        r#"(?s)(?:fact|labelled|counted)\(\s*"([a-z_]+)""#,
    );
    let previewed = scanned("", r#"(?s)detail_of\(\s*"([a-z_]+)""#);
    // Both scans walk the crate rather than one directory of it, and both have to find something:
    // a scan that matched nothing would agree with the registry by saying nothing, so a
    // `detail_of` call that moved under `components/` would drop out of the check instead of
    // reding it.
    assert!(!asked.is_empty(), "no component asks for a label");
    assert!(!previewed.is_empty(), "no pane previews a value");
    let headed: BTreeSet<String> = Shape::ALL
        .iter()
        .flat_map(|shape| shape.columns())
        .map(|column| column.field.to_owned())
        .collect();
    let wanted: BTreeSet<String> = asked
        .union(&previewed)
        .cloned()
        .collect::<BTreeSet<String>>()
        .union(&headed)
        .cloned()
        .collect();
    let registered: BTreeSet<String> = named().map(str::to_owned).collect();
    assert_eq!(wanted, registered);
}

#[test]
fn a_column_that_prints_a_length_says_so_in_its_heading() {
    // A column of bare numbers has to name its unit, or the number is unreadable.
    //
    // A children log prints lengths where the page under it prints the values — `text_chars` is
    // how much the model said, `result_chars` how much a tool answered. Heading either with the
    // word the pane gives the value itself leaves a reader deciding whether the column counts
    // characters, calls or answers. Read off the column table, so a length column added to any
    // shape lands in this check.
    let lengths: BTreeSet<&str> = Shape::ALL
        .iter()
        .flat_map(|shape| shape.columns())
        .map(|column| column.field)
        .filter(|field| field.ends_with("_chars"))
        .collect();
    assert!(!lengths.is_empty(), "the log heads no length column");
    for field in lengths {
        assert!(label(field).to_lowercase().contains("chars"), "{field}");
    }
}

#[tokio::test]
async fn every_number_a_header_prints_carries_its_separators() {
    // A header's counts go through the same formatter every count on a page does.
    //
    // Both panes, because they show the same rollup of two different threads: a session's, and one
    // run's. Planted, because the busiest thread the corpus records made a handful of calls —
    // under a thousand a formatted count and a bare one are the same string. The clones are of
    // recorded rows, so what a header counts stays the `live_*` population it counts today.
    let over = 1_000;
    let served = Served::planted(move |store: &Store| {
        for table in ["turns", "api_calls", "tool_calls"] {
            for source in [MAIN, SPINE_RUN] {
                // One recorded row of that thread, cloned past the point the two spellings
                // diverge.
                store
                    .connection()
                    .execute(
                        &format!(
                            "INSERT INTO {table} (SELECT t.* REPLACE (t.id || '-planted-' || i \
                             AS id) FROM {table} t, range(1, ?) r(i) WHERE t.session_id = ? \
                             AND t.id = (SELECT min(id) FROM {table} WHERE session_id = ? \
                             AND source = ?))"
                        ),
                        params![over + 1, SPINE, SPINE, source],
                    )
                    .expect("the clones insert");
            }
        }
    });
    let (_, page) = served.page(&format!("/session/{SPINE}")).await;
    let session = Markup::of(&page).fields("data-body", "session");
    let (_, page) = served
        .page(&format!("/session/{SPINE}/run/{SPINE_RUN}"))
        .await;
    let run = Markup::of(&page).fields("data-body", "run");
    // Every number either header prints is grouped in threes or the dash a NULL prints...
    let counted = [
        "turns",
        "api_calls",
        "tool_calls",
        "tool_errors",
        "compactions",
        "output_tokens",
    ];
    let grouped = Regex::new(r"^(\d{1,3}(,\d{3})*|—)$").expect("a pattern");
    for (header, name) in [(&session, "session"), (&run, "run")] {
        for field in counted.iter().chain(["unpriced_api_calls"].iter()) {
            assert!(
                grouped.is_match(&header[*field]),
                "{name} {field} {}",
                header[*field]
            );
        }
        // ...and the plant pushed three of them past the point where that is a claim.
        for field in &counted[..3] {
            assert!(header[*field].contains(','), "{name} {field}");
        }
    }
}

#[tokio::test]
async fn a_headers_list_marks_a_member_it_cut_and_links_only_a_whole_url() {
    // The pane's two lists cut every member, and a member cut in silence is a value misread.
    //
    // A skill name is prose a reader compares; a PR URL is the one transcript value that reaches
    // an `href`, and half a URL in an `href` is a link somewhere else — so a cut one is shown for
    // what it is and followed by nothing. Both values are planted and invented: redaction
    // flattened the recorded PR links, and no recorded skill has a name near the width.
    let width = queries::HEADER_ITEM_CHARS;
    let skill = format!("planted-skill-{}", "s".repeat(width));
    let fits = "https://example.test/org/repo/pull/1";
    let over = format!("{fits}?planted={}", "q".repeat(width));
    let (named, linked) = (skill.clone(), over.clone());
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE api_calls SET attribution_skill = ? WHERE session_id = ?",
                params![named, SPINE],
            )
            .expect("the skill plants");
        store
            .connection()
            .execute(
                "INSERT INTO pr_links VALUES \
                 (?, 900003, 3, ?, 'planted/repo', '2026-01-01T00:00:00Z'), \
                 (?, 900004, 4, ?, 'planted/repo', '2026-01-01T00:00:00Z')",
                params![SPINE, fits, SPINE, linked],
            )
            .expect("the planted links insert");
    });
    let (_, page) = served.page(&format!("/session/{SPINE}")).await;
    let markup = Markup::of(&page);
    // The skill's name ends at the width with the mark that says it went on...
    let cut: String = skill.chars().take(width).collect();
    assert_eq!(
        markup.fields("data-body", "session")["skills"],
        format!("{cut}{ELLIPSIS}")
    );
    // ...the URL that fit is a link the reader can follow...
    assert_eq!(
        markup.inside("data-pr", fits, "href"),
        vec![fits.to_owned()]
    );
    // ...and the one that did not is marked the same way and reaches no href at all.
    let stopped: String = over.chars().take(width).collect();
    assert!(
        markup
            .inside("data-pr", &format!("{stopped}{ELLIPSIS}"), "href")
            .is_empty()
    );
}
