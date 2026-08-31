//! The model's words as text: on the node they are about, marked, cut and escaped.
//!
//! Ported from `tests/view/test_enrichment.py`. What a page shows of a described store is
//! `enrichment_pages.rs`; what it shows of an undescribed one is `enrichment_absence.rs`.

use std::collections::BTreeMap;
use std::sync::LazyLock;

use axum::http::StatusCode;
use hyphae_store::queries::{ENRICHMENT_CHARS, NAV_CHARS};
use hyphae_store::{Param, Store};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::SPINE;
use hyphae_testsupport::planting::written;
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::enrichment::{GLYPH, GLYPH_CLASS, Level, TAXONOMY_VERSION};
use hyphae_view::format::{count, cut, when};
use regex::Regex;

/// What the pane's glyph should say about one described turn, read off the store.
///
/// The provenance a reader hovers for: which model wrote the line, when, under which two versions,
/// and whether this build has moved past them. Restated here rather than read off
/// `Enrichment::provenance`, which is the code under test.
fn wrote(served: &Served, turn_id: &str) -> String {
    let row = rows::one(
        &served.db(),
        "SELECT model, enriched_at, prompt_version, taxonomy_version FROM turn_enrichments \
         WHERE turn_id = $turn",
        &[("turn", Param::from(turn_id))],
    );
    let (prompt_version, taxonomy) = (
        row.i64("prompt_version").expect("a prompt version"),
        row.i64("taxonomy_version").expect("a taxonomy version"),
    );
    let aged = prompt_version != Level::Turn.prompt_version() || taxonomy != TAXONOMY_VERSION;
    format!(
        "{} · {} · prompt v{prompt_version} · taxonomy v{taxonomy} · {}",
        row.str("model").expect("a model"),
        when(row.opt_timestamp("enriched_at").expect("an hour")),
        if aged { "stale" } else { "fresh" },
    )
}

/// A pass describes turns and runs, and each one's page shows what it said about it.
///
/// One node per response is the whole point of the browser: the pane reads the selection, so a
/// description belongs on the described node's page rather than repeated down a list of them.
/// Swept over every described turn and run of one session, because the two levels are keyed
/// differently — a turn by its thread, a run by the session.
#[tokio::test]
async fn every_described_node_carries_its_own_words_on_its_own_page() {
    let served = Served::enriched();
    let db = served.db();
    let turns = written(&db, Level::Turn, SPINE);
    let runs = written(&db, Level::AgentRun, SPINE);
    for (turn_id, said) in &turns {
        let (_, page) = served
            .page(&format!("/session/{SPINE}/thread/main/turn/{turn_id}"))
            .await;
        let page = Markup::of(&page);
        let shown = page.fields("data-enrichment", turn_id);
        assert_eq!(
            (&shown["description"], &shown["category"], &shown["outcome"]),
            (&said.description, &said.category, &said.outcome),
            "{turn_id}"
        );
        // And it is the only enrichment on the page: the NavTree rows beside it only name nodes.
        assert_eq!(
            page.values("data-enrichment"),
            std::slice::from_ref(turn_id)
        );
    }
    for (run_id, said) in &runs {
        let (_, page) = served.page(&format!("/session/{SPINE}/run/{run_id}")).await;
        let shown = Markup::of(&page).fields("data-enrichment", run_id);
        assert_eq!(
            (&shown["description"], &shown["category"], &shown["outcome"]),
            (&said.description, &said.category, &said.outcome),
            "{run_id}"
        );
    }
    assert!(
        !turns.is_empty() && !runs.is_empty(),
        "the described corpus no longer describes this session's turns or runs"
    );
    // A pass writes as much as it wants to, so the two long fields ride the same cut-and-mark
    // protocol every other head does: the query answers one character past the width and the pane
    // marks what it left. An unmarked cut would read as the whole of what the model said,
    // mid-sentence and full stop absent. Planted at every level because the query reads the three
    // tables in three arms of a UNION, and a cut is only as marked as its own arm: the dearest of
    // them by far is the run's — 2,745 of the 2,763 agent-run descriptions in the canonical store
    // on 2026-08-25 run past this width, against 959 of 1,464 turns.
    //
    // Each planted past the width by its own amount, so the number a mark offers is that field's
    // own: the two ride separate `length()` columns in each of the three arms, and one plant
    // length could tell neither a swapped pair nor a drifted cut apart. The description runs past
    // a thousand, which is the ordinary size of one — the canonical store's longest is 1,731
    // characters — and puts the separator a reader sees into the assertion.
    let rest: [(&str, i64); 2] = [("description", 1_234), ("friction", 57)];
    let words: BTreeMap<&str, String> = rest
        .iter()
        .map(|(field, over)| {
            (
                *field,
                "w".repeat(ENRICHMENT_CHARS + usize::try_from(*over).expect("a width")),
            )
        })
        .collect();
    let planted = Served::enriched_planted(|store: &Store| {
        for level in Level::ALL {
            store
                .connection()
                .execute(
                    &format!("UPDATE {} SET description = ?, friction = ?", level.table()),
                    duckdb::params![words["description"], words["friction"]],
                )
                .unwrap_or_else(|error| panic!("{} is written long: {error}", level.table()));
        }
    });
    let marked = cut(&words["description"], ENRICHMENT_CHARS);
    let swept = std::iter::once((format!("/session/{SPINE}"), SPINE.to_owned()))
        .chain(turns.keys().map(|key| {
            (
                format!("/session/{SPINE}/thread/main/turn/{key}"),
                key.clone(),
            )
        }))
        .chain(
            runs.keys()
                .map(|key| (format!("/session/{SPINE}/run/{key}"), key.clone())),
        );
    for (url, item_id) in swept {
        let (_, served_page) = planted.page(&url).await;
        let page = Markup::of(&served_page);
        let shown = page.fields("data-enrichment", &item_id);
        assert_eq!(shown["description"], marked, "{url}");
        assert_eq!(shown["friction"], marked, "{url}");
        // ...and the mark offers what it left, the way every other fat value a pane previews does.
        // A mark with no fetch behind it says there is more and gives a reader nowhere to go for
        // it, which is the one thing this page can't say.
        assert_eq!(
            page.inside("data-enrichment", &item_id, "data-whole"),
            ["description", "friction"],
            "{url}"
        );
        // ...saying how much it left, which is the whole length the query returned less the width
        // it printed. Read off the block in document order, because the two fields carry the same
        // key and the pair is what tells the arms apart.
        static BLOCK: LazyLock<Regex> = LazyLock::new(|| {
            Regex::new(r#"(?s)<section class="enrichment"[^>]*>.*?</section>"#).expect("a pattern")
        });
        static LEFT: LazyLock<Regex> =
            LazyLock::new(|| Regex::new(r#"data-field="cut">([^<]+)<"#).expect("a pattern"));
        let block = BLOCK
            .find(&served_page)
            .unwrap_or_else(|| panic!("no enrichment block on {url}"));
        let offered: Vec<String> = LEFT
            .captures_iter(block.as_str())
            .map(|found| found[1].to_owned())
            .collect();
        assert_eq!(
            offered,
            rest.iter()
                .map(|(_, over)| count(Some(*over)))
                .collect::<Vec<_>>(),
            "{url}"
        );
        let fetches = page.inside("data-enrichment", &item_id, "href");
        assert_eq!(fetches.len(), rest.len(), "{url}");
        for ((field, _), fetch) in rest.iter().zip(&fetches) {
            let (status, answered) = planted.page(fetch).await;
            assert_eq!(status, StatusCode::OK, "{fetch}");
            // And what comes back is the whole line, under the name and in the block the head
            // stood in: the fetch replaces the preview rather than sitting beside it.
            let whole = Markup::of(&answered).fields("data-enrichment-line", field);
            assert_eq!(whole.len(), 1, "{fetch}");
            assert_eq!(whole[*field], words[field], "{fetch}");
        }
    }
}

/// A description this build's prompt would no longer produce says so, quietly.
///
/// Only the versions are visible from a read: whether the rendered content moved, or which model a
/// pass would use today, is not something the store can answer. The tag says which of the two
/// states a row is in; the glyph beside the line says what it was written under, which is what a
/// reader needs to decide whether to re-run a pass.
#[tokio::test]
async fn an_item_described_under_an_older_prompt_is_marked_stale() {
    let served = Served::enriched();
    let db = served.db();
    let named = |sql: &str| {
        let row = rows::one(
            &db,
            sql,
            &[("version", Param::from(Level::Turn.prompt_version()))],
        );
        (
            row.str("session_id").expect("a session id").to_owned(),
            row.str("turn_id").expect("a turn id").to_owned(),
        )
    };
    let (stale_session, stale_turn) = named(
        "SELECT session_id, turn_id FROM turn_enrichments \
         WHERE source = 'main' AND prompt_version < $version",
    );
    let (fresh_session, fresh_turn) = named(
        "SELECT session_id, turn_id FROM turn_enrichments \
         WHERE source = 'main' AND prompt_version = $version",
    );
    // The turn described under the older prompt version is tagged...
    let (_, page) = served
        .page(&format!(
            "/session/{stale_session}/thread/main/turn/{stale_turn}"
        ))
        .await;
    let stale_page = Markup::of(&page);
    let said = stale_page.fields("data-enrichment", &stale_turn);
    assert_eq!(said.get("stale").map(String::as_str), Some("stale"));
    // Three pills read as three words. Their margins hold the boxes apart on screen; the spaces
    // are what hold them apart for a reader who hears the block instead.
    assert!(
        stale_page
            .reads("data-enrichment", &stale_turn)
            .contains(&format!("{} {} stale", said["category"], said["outcome"]))
    );
    // ...and one described under the current one is not, so the tag is telling them apart.
    let (_, page) = served
        .page(&format!(
            "/session/{fresh_session}/thread/main/turn/{fresh_turn}"
        ))
        .await;
    let fresh_page = Markup::of(&page);
    assert!(
        !fresh_page
            .fields("data-enrichment", &fresh_turn)
            .contains_key("stale")
    );
    // Both carry the same glyph, and its tooltip is where the two rows differ in full: the model,
    // the hour, the two versions, and which side of them this build is on.
    assert_eq!(said["enriched"], GLYPH);
    assert_eq!(
        stale_page.inside("data-enrichment", &stale_turn, "title"),
        [wrote(&served, &stale_turn)]
    );
    assert_eq!(
        fresh_page.inside("data-enrichment", &fresh_turn, "title"),
        [wrote(&served, &fresh_turn)]
    );
}

/// A row named by the pass says so with the glyph alone — no tooltip, no second copy.
///
/// The NavTree is the page's multiplied part: a row carries a byte budget and no more, so the
/// provenance a pane spells out is a mark here. Read against a described turn and an undescribed
/// one of the same thread, because a glyph on every row would say nothing.
#[tokio::test]
async fn a_nav_tree_row_the_model_named_carries_a_bare_glyph() {
    let served = Served::enriched();
    let db = served.db();
    let described = written(&db, Level::Turn, SPINE);
    let (turn_id, said) = described.iter().next().expect("a described turn");
    let (_, page) = served.page(&format!("/session/{SPINE}")).await;
    let page = Markup::of(&page);
    let row = format!("turn:{turn_id}");
    // The described row is titled with what the pass said, cut to the width of the NavTree...
    assert_eq!(
        page.fields("data-nav-tree", &row)["title"],
        cut(&said.description, NAV_CHARS)
    );
    // ...and marked as the model's words, with nothing hanging off the mark.
    assert!(
        page.inside("data-nav-tree", &row, "class")
            .iter()
            .any(|class| class == GLYPH_CLASS)
    );
    assert!(page.inside("data-nav-tree", &row, "title").is_empty());
    // The mark stands off the title it marks — `✨ what the pass said`, never `✨what` — and the
    // space that does it is markup no `data-field` can see.
    let titled = &page.fields("data-nav-tree", &row)["title"];
    assert!(
        page.reads("data-nav-tree", &row)
            .contains(&format!("{GLYPH} {titled}"))
    );
    // The session's own row is built by a third builder and marked the same way, for the pass that
    // named the whole session rather than one of its turns.
    let named = written(&db, Level::Session, SPINE);
    let own = format!("session:{SPINE}");
    assert_eq!(
        page.fields("data-nav-tree", &own)["title"],
        cut(&named[SPINE].description, NAV_CHARS)
    );
    assert!(
        page.inside("data-nav-tree", &own, "class")
            .iter()
            .any(|class| class == GLYPH_CLASS)
    );
    assert!(page.inside("data-nav-tree", &own, "title").is_empty());
    // The one turn of the corpus no pass reached sits on another session's NavTree, titled by what
    // the session itself recorded and carrying no mark.
    let bare = rows::one(
        &db,
        "SELECT t.session_id, t.id FROM live_turns t LEFT JOIN turn_enrichments e \
           ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id \
         WHERE t.source = 'main' AND e.turn_id IS NULL",
        &[],
    );
    let (_, undescribed) = served
        .page(&format!(
            "/session/{}",
            bare.str("session_id").expect("a session id")
        ))
        .await;
    let unmarked = format!("turn:{}", bare.str("id").expect("a turn id"));
    assert!(
        !Markup::of(&undescribed)
            .inside("data-nav-tree", &unmarked, "class")
            .iter()
            .any(|class| class == GLYPH_CLASS)
    );
    // A run reads the same way through a different builder, with one difference: its title leads
    // with the definition it ran whatever else names it, so what the pass wrote stands after the
    // agent type rather than in place of it — and carries the same bare mark.
    let ran = written(&db, Level::AgentRun, SPINE);
    let (run_id, run_said) = ran.iter().next().expect("a described run");
    let agent_type = rows::one(
        &db,
        "SELECT agent_type FROM live_agent_runs WHERE id = $run",
        &[("run", Param::from(run_id.as_str()))],
    )
    .str("agent_type")
    .expect("an agent type")
    .to_owned();
    let (_, page) = served.page(&format!("/session/{SPINE}/run/{run_id}")).await;
    let page = Markup::of(&page);
    let row = format!("run:{run_id}");
    assert_eq!(
        page.fields("data-nav-tree", &row)["title"],
        cut(
            &format!("[{agent_type}] {}", run_said.description),
            NAV_CHARS
        )
    );
    assert!(
        page.inside("data-nav-tree", &row, "class")
            .iter()
            .any(|class| class == GLYPH_CLASS)
    );
    assert!(page.inside("data-nav-tree", &row, "title").is_empty());
}

/// A description is written from a private transcript, so it reaches the page as text.
///
/// The value is invented and has to be: it is what a model would have to be talked into writing,
/// which is the case the escaping is for.
#[tokio::test]
async fn a_model_written_description_is_escaped_like_any_other_transcript_text() {
    let injected = "<script>alert('x')</script> & <b>bold</b>";
    let planted = Served::enriched_planted(|store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE session_enrichments SET description = ?, friction = ?",
                duckdb::params![injected, injected],
            )
            .expect("the session's own words are written");
        // The map names a node by what the pass said the turn did, so the same words reach a
        // second surface by a second route — as a name rather than as a paragraph.
        store
            .connection()
            .execute(
                "UPDATE turn_enrichments SET description = ?",
                duckdb::params![injected],
            )
            .expect("every turn is named the same way");
    });
    let (_, served_page) = planted.page(&format!("/session/{SPINE}")).await;
    // Nothing the model wrote opened a tag, in the pane or on the NavTree beside it...
    assert!(!served_page.contains("<script>") && !served_page.contains("<b>bold</b>"));
    let page = Markup::of(&served_page);
    // ...and the reader still sees the text it wrote, as the session's own summary...
    let shown = page.fields("data-enrichment", SPINE);
    assert_eq!(shown["description"], injected);
    assert_eq!(shown["friction"], injected);
    // ...and as the title of every turn row, which is the second surface and the second route.
    let titled: Vec<String> = page
        .values("data-nav-tree")
        .into_iter()
        .filter(|key| key.starts_with("turn:"))
        .collect();
    assert!(
        !titled.is_empty(),
        "the session that carries the fixture turn tree no longer opens one"
    );
    let head: String = injected.chars().take(NAV_CHARS).collect();
    for key in &titled {
        assert_eq!(page.fields("data-nav-tree", key)["title"], head, "{key}");
    }
}
