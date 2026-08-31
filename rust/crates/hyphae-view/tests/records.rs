//! The records browser: a thread's raw transcript, a page at a time and a record at a time.
//!
//! This is where a report's citation lands. An analysis finding names `(session_id, source,
//! line_no)`, so the leaves here are about the walk and the mapping: paging that neither repeats
//! nor skips a line, and a URL derived from the tuple that opens on the record it names.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use axum::http::StatusCode;
use duckdb::params;
use regex::Regex;

use hyphae_store::{Param, Store, queries};
use hyphae_testsupport::html::{Markup, counted, plain};
use hyphae_testsupport::landmarks::{
    ANCESTOR, MAIN, MISSING, RESUME, RESUME_LONG_RECORD, SPINE, SPINE_RUN,
};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::knobs;
use hyphae_view::store::{Page, Query};

/// The records a page opens with its own body already fetched, in document order.
///
/// Read off the start tag rather than through `inside`, because what says a record is open is
/// `open` itself — an attribute with no value, which nothing keyed by value can see. The tag is
/// matched whole and read inside it: the formatter lays a tag's attributes out as it likes, and
/// the one invariant is that they belong to the same tag.
fn opened(html: &str) -> Vec<String> {
    static DETAILS: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"<details class="whole"([^>]*)>"#).expect("a pattern"));
    static RECORD: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"data-open-record="(\d+)""#).expect("a pattern"));
    // `open` on its own, not the middle of `data-open-record`.
    static FLAG: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(^|[^-\w])open($|[^-\w])").expect("a pattern"));
    DETAILS
        .captures_iter(html)
        .filter_map(|tag| {
            let attributes = tag.get(1).expect("the group").as_str();
            if !FLAG.is_match(attributes) {
                return None;
            }
            Some(
                RECORD
                    .captures(attributes)
                    .unwrap_or_else(|| panic!("an open record with no line number: {attributes}"))
                    [1]
                .to_owned(),
            )
        })
        .collect()
}

/// One thread's records browser, at a cursor.
fn browsing(session_id: &str, source: &str, after: i64) -> String {
    format!("/session/{session_id}/thread/{source}/records?after={after}")
}

#[tokio::test]
async fn the_browser_pages_by_line_number_without_repeating_or_skipping() {
    // Following a thread's pages shows every record it holds once, in line order.
    //
    // Paged at 20 against the corpus's densest recorded thread, which holds 47 — so the page
    // boundary is a real overflow of recorded data rather than a staged one.
    let served = Served::corpus();
    let archived: Vec<String> = rows::all(
        &served.db(),
        "SELECT line_no FROM raw_records WHERE session_id = $session AND source = $thread \
         ORDER BY line_no",
        &[
            ("session", Param::from(ANCESTOR)),
            ("thread", Param::from(MAIN)),
        ],
    )
    .iter()
    .map(|row| row.i64("line_no").expect("a line number").to_string())
    .collect();
    assert_eq!(
        archived.len(),
        47,
        "the densest fixture thread moved: re-pick the session"
    );
    // Walking from before the first line, taking the cursor each page hands back...
    let mut seen: Vec<String> = Vec::new();
    let mut after = queries::FIRST_PAGE;
    let mut ran_out = false;
    for _ in 0..4 {
        let (status, page) = served
            .page(&format!("{}&size=20", browsing(ANCESTOR, MAIN, after)))
            .await;
        assert_eq!(status, StatusCode::OK);
        let markup = Markup::of(&page);
        let shown = markup.values("data-record");
        assert!(shown.len() <= 20);
        seen.extend(shown);
        let following = markup.values("data-more-records");
        match following.first() {
            None => {
                ran_out = true;
                break;
            }
            Some(cursor) => after = cursor.parse().expect("a line number"),
        }
    }
    assert!(ran_out, "the browser never ran out of pages");
    // ...covers the thread exactly: no line twice, none missed, and none out of order.
    assert_eq!(seen, archived);
    // Keyset, not OFFSET: a page counted off from the start re-reads rows an extract appended.
    assert!(
        !queries::load(Page::Records.stem())
            .to_uppercase()
            .contains("OFFSET")
    );
}

#[tokio::test]
async fn a_citation_tuple_maps_to_a_working_url() {
    // A report's `(session_id, source, line_no)` opens the page holding that record.
    //
    // The mapping is mechanical — `?after={line - 1}#L{line}` — which is why the viewer's URLs are
    // natural keys. A report keeps citing the tuple; the URL is derived from it.
    let served = Served::corpus();
    let line = RESUME_LONG_RECORD;
    let kind = rows::one(
        &served.db(),
        "SELECT type FROM raw_records \
         WHERE session_id = $session AND source = $thread AND line_no = $line",
        &[
            ("session", Param::from(RESUME)),
            ("thread", Param::from(MAIN)),
            ("line", Param::Int(line)),
        ],
    )
    .str("type")
    .expect("a record type")
    .to_owned();
    let (status, page) = served.page(&browsing(RESUME, MAIN, line - 1)).await;
    assert_eq!(status, StatusCode::OK);
    let markup = Markup::of(&page);
    // The cited record is the first row of the page, under the anchor the URL fragment names...
    assert_eq!(markup.values("data-record")[0], line.to_string());
    assert!(page.contains(&format!(r#"id="L{line}""#)));
    // ...the row says which kind of record it is, so a citation reads in place...
    assert_eq!(
        markup.fields("data-record", &line.to_string())["type"],
        kind
    );
    // ...and it is the one record on the page that arrives open, fetching its own body as the page
    // loads: a reader who followed a citation asked for that record, and a row that landed
    // collapsed made them click for what they came for. The rest of the page waits to be opened,
    // which is what keeps a page of records a page and not a transcript.
    assert_eq!(opened(&page), vec![line.to_string()]);
    assert_eq!(
        markup.inside("data-open-record", &line.to_string(), "hx-trigger"),
        vec!["load".to_owned()]
    );
    let following = markup.values("data-record")[1].clone();
    assert_eq!(
        markup.inside("data-open-record", &following, "hx-trigger"),
        vec!["toggle once".to_owned()]
    );
    // ...and the page cites the query it ran, at this request's cursor and the size it took by
    // default, so a reader can re-run what produced the rows around the cited one.
    assert_eq!(
        markup.fields("id", "citation")[Page::Records.stem()],
        format!(
            "-- queries/view_records.sql session_id={RESUME} source={MAIN} after={} \
             page_records={} preview_chars={}",
            line - 1,
            knobs::RECORDS.default,
            queries::RECORD_PREVIEW
        )
    );
}

#[tokio::test]
async fn a_record_too_wide_to_weigh_waits_for_a_click() {
    // A page opens its first record only where fetching it stays inside a page's budget.
    //
    // The open row is a fetch nobody clicked, so what it costs is what the page costs — and a
    // record is the one value the store holds no bound over: the canonical store archives one of
    // 7.6 million characters, which renders to nine megabytes. A reader who paged here rather than
    // following a citation never asked for it at all.
    //
    // So the row opens itself up to `knobs::OPENED_RECORD_CHARS` and stays a click away past it,
    // which is the same page either way — the record is a fetch in both, and the difference is who
    // triggers it. Planted at the boundary in both directions, because no recorded record sits
    // on it.
    let line = RESUME_LONG_RECORD.to_string();
    for (length, opens) in [
        (knobs::OPENED_RECORD_CHARS, true),
        (knobs::OPENED_RECORD_CHARS + 1, false),
    ] {
        let served = Served::planted(move |store: &Store| {
            store
                .connection()
                .execute(
                    "UPDATE raw_records SET raw = ? \
                     WHERE session_id = ? AND source = ? AND line_no = ?",
                    params!["&".repeat(length), RESUME, MAIN, RESUME_LONG_RECORD],
                )
                .expect("the record widens");
        });
        let (_, page) = served
            .page(&browsing(RESUME, MAIN, RESUME_LONG_RECORD - 1))
            .await;
        let markup = Markup::of(&page);
        // The cited record is the first row of the page whichever side of the line it falls...
        assert_eq!(markup.values("data-record")[0], line);
        assert_eq!(
            markup.fields("data-record", &line)["raw_chars"],
            counted(length as i64)
        );
        // ...and the row carries the fetch either way. What the width decides is whether the page
        // pulls it as it loads or waits for the reader to open the row.
        let trigger = if opens { "load" } else { "toggle once" };
        let expected: Vec<String> = if opens {
            vec![line.clone()]
        } else {
            Vec::new()
        };
        assert_eq!(opened(&page), expected, "{length}");
        assert_eq!(
            markup.inside("data-open-record", &line, "hx-trigger"),
            vec![trigger.to_owned()],
            "{length}"
        );
    }
}

#[tokio::test]
async fn a_record_row_shows_a_preview_and_the_length_it_was_cut_from() {
    // A browser row previews its record and says how much of it is not shown.
    //
    // The recorded record here is 3,054 characters against a 160-character preview, so the cut is
    // a real one — and the row carries the full length, which is what tells a reader the preview
    // is a preview.
    let served = Served::corpus();
    let stored = stored_record(&served, RESUME, MAIN, RESUME_LONG_RECORD);
    assert!(stored.chars().count() > queries::RECORD_PREVIEW * 10);
    let (_, page) = served
        .page(&browsing(RESUME, MAIN, RESUME_LONG_RECORD - 1))
        .await;
    let markup = Markup::of(&page);
    let line = RESUME_LONG_RECORD.to_string();
    let row = markup.fields("data-record", &line);
    // Through the same formatter every count on a page goes through. This record is the one
    // recorded value long enough to tell the two spellings apart: 3,054 against 3054.
    assert_eq!(row["raw_chars"], counted(stored.chars().count() as i64));
    assert!(row["raw_head"].chars().count() <= queries::RECORD_PREVIEW);
    // And the row's five values read as five, not as one long word. Only the line number carries a
    // margin here — `ol.records li` is no flex row — so the spaces between the type, the time, the
    // length and the preview are all that hold them apart.
    let said = markup.reads("data-record", &line);
    let expected = [
        line.clone(),
        row["type"].clone(),
        row["timestamp"].clone(),
        format!("{} chars", row["raw_chars"]),
        row["raw_head"]
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" "),
    ]
    .join(" ");
    assert!(said.starts_with(&expected), "{said}");
}

/// One record's stored `raw`, which several leaves read the page back against.
fn stored_record(served: &Served, session_id: &str, source: &str, line: i64) -> String {
    rows::one(
        &served.db(),
        "SELECT raw FROM raw_records \
         WHERE session_id = $session AND source = $thread AND line_no = $line",
        &[
            ("session", Param::from(session_id)),
            ("thread", Param::from(source)),
            ("line", Param::Int(line)),
        ],
    )
    .str("raw")
    .expect("a stored record")
    .to_owned()
}

#[tokio::test]
async fn every_number_the_records_browser_prints_carries_its_separators() {
    // The browser's counts go through the same formatter every count on a page does.
    //
    // Planted, because the corpus's densest recorded thread archives 47 lines: under a thousand a
    // formatted count and a bare one are the same string. The clones are of a recorded record
    // given line numbers of their own, so what the page counts stays the archived population —
    // and they carry no uuid, a shape the store records for a summary line.
    let over: i64 = 1_200;
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "INSERT INTO raw_records (SELECT r.* REPLACE (r.line_no + i * 1000 AS line_no, \
                 NULL AS uuid) FROM raw_records r, range(1, ?) t(i) \
                 WHERE r.session_id = ? AND r.source = ? AND r.line_no = \
                 (SELECT min(line_no) FROM raw_records WHERE session_id = ? AND source = ?))",
                params![over + 1, ANCESTOR, MAIN, ANCESTOR, MAIN],
            )
            .expect("the clones land");
    });
    let recorded = rows::one(
        &served.db(),
        "SELECT count(*) AS held FROM raw_records \
         WHERE session_id = $session AND source = $thread AND line_no < 1000",
        &[
            ("session", Param::from(ANCESTOR)),
            ("thread", Param::from(MAIN)),
        ],
    )
    .i64("held")
    .expect("a count");
    let (_, page) = served
        .page(&format!("/session/{ANCESTOR}/thread/{MAIN}/records"))
        .await;
    let markup = Markup::of(&page);
    let held = recorded + over;
    let on_page = markup.values("data-record");
    assert_eq!(on_page.len() as i64, knobs::RECORDS.default);
    // What the thread holds from this cursor on, and what the page left behind it, both grouped in
    // threes — and the plant pushed each past where that is a claim.
    assert_eq!(markup.fields("id", "records")["matched"], counted(held));
    let after = markup.values("data-more-records");
    assert_eq!(after.len(), 1);
    assert_eq!(
        markup.fields("data-more-records", &after[0])["count"],
        format!("+{} more", counted(held - on_page.len() as i64))
    );
    // And the reader gets there by clicking, so the next page is fetched through the link the page
    // wrote rather than one this test composed — the only way a change to that URL's shape fails
    // here rather than in a browser.
    let links = markup.inside("data-more-records", &after[0], "href");
    assert_eq!(links.len(), 1);
    let link = html_escape::decode_html_entities(&links[0]).into_owned();
    let (status, following) = served.page(&link).await;
    assert_eq!(status, StatusCode::OK, "{link}");
    // The cursor carried the reader forward: a full page again, and none of it a repeat.
    let next_page = Markup::of(&following).values("data-record");
    assert_eq!(next_page.len() as i64, knobs::RECORDS.default);
    let walked: BTreeSet<&String> = on_page.iter().collect();
    assert!(next_page.iter().all(|line| !walked.contains(line)));
}

#[tokio::test]
async fn a_record_fragment_holds_the_one_record_it_names() {
    // Opening a row fetches that record whole, and none of its neighbours. A record is a per-value
    // fetch — the browser's rows are previews, and this is the only route that ships a whole `raw`.
    let served = Served::corpus();
    let stored = stored_record(&served, RESUME, MAIN, RESUME_LONG_RECORD);
    let line = RESUME_LONG_RECORD.to_string();
    // Through the fetch the row itself carries, opened at that record. Minting the URL here would
    // leave the component free to write any shape it liked and this test still green.
    let (_, browser) = served
        .page(&browsing(RESUME, MAIN, RESUME_LONG_RECORD - 1))
        .await;
    let fetches = Markup::of(&browser).inside("data-open-record", &line, "hx-get");
    assert_eq!(fetches.len(), 1);
    let (status, served_record) = served.page(&fetches[0]).await;
    assert_eq!(status, StatusCode::OK, "{}", fetches[0]);
    let markup = Markup::of(&served_record);
    // The whole record arrived — indented and marked up, so it is read back through the markup:
    // every field the store holds, and nothing the page invented.
    let shown: serde_json::Value =
        serde_json::from_str(&plain(&markup.block("raw"))).expect("the served record is JSON");
    let held: serde_json::Value = serde_json::from_str(&stored).expect("the stored record is JSON");
    assert_eq!(shown, held);
    // ...saying its stored length in the grouping every count on a page carries...
    assert_eq!(
        markup.fields("data-record-value", &line)["raw_chars"],
        counted(stored.chars().count() as i64)
    );
    // ...and no other line of the same thread rode along with it.
    assert_eq!(markup.values("data-record-value"), vec![line]);
}

#[tokio::test]
async fn a_record_shows_its_uuid_only_when_it_has_one() {
    // A record's uuid is shown when Claude Code wrote one, and omitted when it did not.
    //
    // The column is nullable — a summary record carries no uuid — so an unguarded component would
    // print a debug spelling where an id someone could search for belongs.
    let served = Served::corpus();
    for held in [true, false] {
        let row = rows::one(
            &served.db(),
            &format!(
                "SELECT session_id, source, line_no, uuid FROM raw_records \
                 WHERE uuid IS {} LIMIT 1",
                if held { "NOT NULL" } else { "NULL" }
            ),
            &[],
        );
        let uuid = row
            .opt_str("uuid")
            .expect("a uuid column")
            .map(str::to_owned);
        assert_eq!(
            uuid.is_some(),
            held,
            "the corpus lost one of the two shapes"
        );
        let line = row.i64("line_no").expect("a line number").to_string();
        let (_, page) = served
            .page(&format!(
                "/fragment/record/session/{}/thread/{}/line/{line}",
                row.str("session_id").expect("a session id"),
                row.str("source").expect("a thread"),
            ))
            .await;
        let shown = Markup::of(&page).fields("data-record-value", &line);
        assert_eq!(shown.get("uuid").cloned(), uuid);
        assert!(!shown.values().any(|value| value == "None"));
    }
}

#[tokio::test]
async fn a_thread_page_links_to_the_transcript_behind_it() {
    // A session page and a run page each reach their own thread's records in one click.
    let served = Served::corpus();
    for (path, source) in [
        (format!("/session/{SPINE}"), MAIN),
        (format!("/session/{SPINE}/run/{SPINE_RUN}"), SPINE_RUN),
    ] {
        let (_, page) = served.page(&path).await;
        let links = Markup::of(&page).inside("data-field", "records", "href");
        let records = format!("/session/{SPINE}/thread/{source}/records");
        assert_eq!(links, vec![records.clone()], "{path}");
        assert_eq!(served.page(&records).await.0, StatusCode::OK, "{path}");
    }
}

#[tokio::test]
async fn every_turn_links_to_the_record_it_was_read_from() {
    // A turn's pane reaches the transcript line the extractor read that turn from.
    //
    // `turns.id` is a `raw_records.uuid` in the same `(session_id, source)` — the store's own join,
    // not a guess about line numbers — which is what makes the link derivable at all. The line also
    // arrives whole on open, from the same route the records browser uses.
    let served = Served::corpus();
    let bound = &[
        ("session", Param::from(SPINE)),
        ("thread", Param::from(MAIN)),
    ];
    let behind: Vec<(String, i64)> = rows::all(
        &served.db(),
        "SELECT t.id, r.line_no FROM live_turns t JOIN raw_records r \
         ON r.session_id = t.session_id AND r.source = t.source AND r.uuid = t.id \
         WHERE t.session_id = $session AND t.source = $thread",
        bound,
    )
    .iter()
    .map(|row| {
        (
            row.str("id").expect("a turn id").to_owned(),
            row.i64("line_no").expect("a line number"),
        )
    })
    .collect();
    let turns = rows::one(
        &served.db(),
        "SELECT count(*) AS held FROM live_turns \
         WHERE session_id = $session AND source = $thread",
        bound,
    )
    .i64("held")
    .expect("a count");
    // Every turn of this thread was read from a record, so no turn page goes unlinked.
    assert_eq!(behind.len() as i64, turns, "the join lost a turn");
    assert!(
        turns > 0,
        "the fixture session lost its turn-to-record join"
    );
    for (turn_id, line_no) in &behind {
        let (_, page) = served
            .page(&format!("/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"))
            .await;
        let markup = Markup::of(&page);
        // The link opens the browser at that turn's own line and no other's...
        assert_eq!(
            markup.inside("class", "raw", "href"),
            vec![
                format!("/session/{SPINE}/thread/{MAIN}/records"),
                format!(
                    "/session/{SPINE}/thread/{MAIN}/records?after={}#L{line_no}",
                    line_no - 1
                ),
            ],
            "{turn_id}"
        );
        // ...and the closed block beside it fetches the same record whole, again through the URL
        // the pane wrote: this is the third place a record URL is spelled out by hand.
        assert_eq!(
            markup.values("data-open-record"),
            vec![line_no.to_string()],
            "{turn_id}"
        );
        let fetches = markup.inside("data-open-record", &line_no.to_string(), "hx-get");
        assert_eq!(fetches.len(), 1);
        let (_, opened_record) = served.page(&fetches[0]).await;
        assert_eq!(
            Markup::of(&opened_record).values("data-record-value"),
            vec![line_no.to_string()],
            "{}",
            fetches[0]
        );
    }
    // And the link lands on the record, which is the whole point of deriving it this way.
    let line = behind.first().expect("a turn").1;
    let (_, landed) = served.page(&browsing(SPINE, MAIN, line - 1)).await;
    assert_eq!(
        Markup::of(&landed).values("data-record")[0],
        line.to_string()
    );
}

#[tokio::test]
async fn a_record_the_store_does_not_hold_is_a_404() {
    // A thread or a line the store does not hold is a 404, not an empty browser — and a cursor past
    // the thread's last line is the same answer, because a walk that ran off the end asked for a
    // page of records that are not there rather than for a page that happens to be empty.
    let served = Served::corpus();
    for path in [
        format!("/session/{ANCESTOR}/thread/{MISSING}/records"),
        format!("/session/{MISSING}/thread/{MAIN}/records"),
        format!("/session/{ANCESTOR}/thread/{MAIN}/records?after=999999"),
        format!("/fragment/record/session/{ANCESTOR}/thread/{MAIN}/line/999999"),
    ] {
        assert_eq!(served.page(&path).await.0, StatusCode::NOT_FOUND, "{path}");
    }
}

#[tokio::test]
async fn a_records_page_size_outside_its_bounds_is_refused() {
    // A hand-typed page size past the ceiling is a 400, not a page nothing bounds.
    let served = Served::corpus();
    for size in [0, knobs::RECORDS.ceiling + 1] {
        let path = format!("/session/{ANCESTOR}/thread/{MAIN}/records?size={size}");
        assert_eq!(
            served.page(&path).await.0,
            StatusCode::BAD_REQUEST,
            "{size}"
        );
    }
}
