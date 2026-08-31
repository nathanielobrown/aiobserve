//! The offload page: a tool result Claude Code wrote to a file instead of the transcript.
//!
//! Two things make this page different from the rest of the viewer. The content has no ceiling —
//! the canonical store holds one over 50 MB — so it is served in chunks rather than whole. And the
//! file's *name* comes from the transcript, so it is a value the page carries, never a path the
//! server follows.

use axum::http::StatusCode;
use duckdb::params;

use hyphae_store::{Param, Store};
use hyphae_testsupport::html::{Markup, counted};
use hyphae_testsupport::landmarks::{
    CONFIG_ONLY, FORK_ORIGIN, MISSING, OFFLOAD_CHARS, OFFLOAD_FILE, OFFLOAD_TOOL,
};
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_view::knobs;

/// The thread the recorded offloading tool call ran on.
fn offloading_thread(db: &std::path::Path) -> String {
    rows::one(
        db,
        "SELECT source FROM tool_calls WHERE id = $tool",
        &[("tool", Param::from(OFFLOAD_TOOL))],
    )
    .str("source")
    .expect("a thread")
    .to_owned()
}

#[tokio::test]
async fn an_offloaded_result_is_served_in_chunks_that_reassemble_it() {
    // Following an offload's chunks hands back the file, once and in order.
    //
    // Chunked at 64 characters over the corpus's recorded 159-character file, so the boundary is a
    // real overflow of a recorded value rather than a staged one — three chunks, the last a short
    // one.
    let served = Served::corpus();
    let stored = rows::one(
        &served.db(),
        "SELECT content FROM offload_files WHERE session_id = $session AND name = $name",
        &[
            ("session", Param::from(CONFIG_ONLY)),
            ("name", Param::from(OFFLOAD_FILE)),
        ],
    )
    .str("content")
    .expect("the stored content")
    .to_owned();
    assert_eq!(
        stored.chars().count() as i64,
        OFFLOAD_CHARS,
        "the recorded offload moved: re-pick the file"
    );
    let url = format!("/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}");
    // Walking from the start, taking the offset each page hands back...
    let mut read = String::new();
    let mut after = 0;
    let mut cited = Vec::new();
    let mut ran_out = false;
    for _ in 0..4 {
        let (status, page) = served.page(&format!("{url}?after={after}&size=64")).await;
        assert_eq!(status, StatusCode::OK);
        let markup = Markup::of(&page);
        read.push_str(&markup.fields("data-offload", OFFLOAD_FILE)["content"]);
        cited.push(markup.fields("id", "citation"));
        let following = markup.values("data-more-offload");
        match following.first() {
            None => {
                ran_out = true;
                break;
            }
            Some(offset) => after = offset.parse().expect("an offset"),
        }
    }
    assert!(ran_out, "the offload never ran out of chunks");
    // ...cites one query per chunk, each at the offset that chunk was cut at rather than at the
    // file's start — a citation that named only the file would reproduce the wrong chunk.
    let expected: Vec<_> = [0, 64, 128]
        .iter()
        .map(|offset| {
            std::collections::BTreeMap::from([(
                "view_offload".to_owned(),
                format!(
                    "-- queries/view_offload.sql session_id={CONFIG_ONLY} name={OFFLOAD_FILE} \
                     after_chars={offset} chunk_chars=64"
                ),
            )])
        })
        .collect();
    assert_eq!(cited, expected);
    // ...reassembles the file. Compared with whitespace collapsed, because the HTML reader strips
    // each chunk it lifts and a chunk boundary can land inside a run of spaces — what this leaf is
    // about is the partition, not the `pre` the browser renders.
    let squashed = |text: &str| text.split_whitespace().collect::<String>();
    assert_eq!(squashed(&read), squashed(&stored));
    // Three chunks over 159 characters, so a boundary was really crossed twice.
    assert!(read.chars().count() > 2 * 64);
}

#[tokio::test]
async fn the_page_says_what_the_store_holds_and_how_it_decoded() {
    // An offload page carries the file's real size and whether the decode lost anything.
    //
    // `size_bytes` is what was on disk; the chunks are characters. A page that showed only the
    // chunk would leave a reader unable to tell a truncated read from a small file.
    let served = Served::corpus();
    let held = rows::one(
        &served.db(),
        "SELECT size_bytes, lossy_decode FROM offload_files \
         WHERE session_id = $session AND name = $name",
        &[
            ("session", Param::from(CONFIG_ONLY)),
            ("name", Param::from(OFFLOAD_FILE)),
        ],
    );
    let (_, page) = served
        .page(&format!("/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}"))
        .await;
    let shown = Markup::of(&page).fields("data-offload", OFFLOAD_FILE);
    assert_eq!(
        shown["size_bytes"],
        held.i64("size_bytes").expect("a size").to_string()
    );
    assert_eq!(shown["content_chars"], OFFLOAD_CHARS.to_string());
    // The recorded file decoded cleanly, so the page says nothing about a lossy one.
    assert!(!held.bool("lossy_decode").expect("a decode flag"));
    assert!(!shown.contains_key("lossy_decode"));
}

#[tokio::test]
async fn every_number_the_offload_page_prints_carries_its_separators() {
    // The page's sizes go through the same formatter every count on a page does.
    //
    // Planted, because the one recorded offload is 159 characters and the sizes this page is read
    // for run to megabytes: under a thousand a formatted count and a bare one are the same string.
    // The recorded content is repeated rather than invented, so what the page reports is a length
    // of the file the transcript really wrote.
    let times: i64 = 20;
    let corpus = Served::corpus();
    let held = rows::one(
        &corpus.db(),
        "SELECT size_bytes, length(content) AS chars FROM offload_files \
         WHERE session_id = $session AND name = $name",
        &[
            ("session", Param::from(CONFIG_ONLY)),
            ("name", Param::from(OFFLOAD_FILE)),
        ],
    );
    let size = held.i64("size_bytes").expect("a size");
    let chars = held.i64("chars").expect("a length");
    let served = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE offload_files SET content = repeat(content, ?), \
                 size_bytes = size_bytes * ? WHERE session_id = ? AND name = ?",
                params![times, times, CONFIG_ONLY, OFFLOAD_FILE],
            )
            .expect("the file grows");
    });
    // A chunk under the whole file, so the page also has a next link to print a size into.
    let chunk: i64 = 1_200;
    let (_, page) = served
        .page(&format!(
            "/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}?size={chunk}"
        ))
        .await;
    let shown = Markup::of(&page).fields("data-offload", OFFLOAD_FILE);
    assert_eq!(shown["size_bytes"], counted(size * times));
    assert_eq!(shown["content_chars"], counted(chars * times));
    // ...including the size on the link to the rest, which is a number a reader typed.
    assert!(
        page.contains(&format!("next {} chars", counted(chunk))),
        "{shown:?}"
    );
}

#[tokio::test]
async fn a_tool_call_links_to_the_file_its_result_went_to() {
    // A tool call whose result was offloaded reaches the file from the call's own pane.
    //
    // The pane says where the result went instead of showing an empty one, and that line is the
    // only link a tool's facts carry.
    let served = Served::corpus();
    let source = offloading_thread(&served.db());
    let (_, pane) = served
        .page(&format!(
            "/session/{CONFIG_ONLY}/thread/{source}/tool/{OFFLOAD_TOOL}"
        ))
        .await;
    let link = Markup::of(&pane).inside("data-body", "tool", "href");
    assert_eq!(
        link,
        vec![format!(
            "/session/{CONFIG_ONLY}/offload/{}",
            hyphae_view::urls::quoted_path(OFFLOAD_FILE)
        )]
    );
    assert_eq!(served.page(&link[0]).await.0, StatusCode::OK);
}

#[tokio::test]
async fn a_name_needing_escaping_survives_the_round_trip() {
    // A file name with a space and a percent in it still reaches its own page.
    //
    // The name is Claude Code's to choose, and the two characters here are the ones that break a
    // URL built by concatenation: a space ends the attribute, a percent starts an escape. Planted
    // onto the recorded row — no fixture carries an awkward name today, and the point is that one
    // arriving tomorrow is a link that works rather than a 404.
    let awkward = "run 100% output.txt";
    let served = Served::planted(move |store: &Store| {
        let connection = store.connection();
        connection
            .execute(
                "UPDATE offload_files SET name = ? WHERE session_id = ?",
                params![awkward, CONFIG_ONLY],
            )
            .expect("the file is renamed");
        connection
            .execute(
                "UPDATE tool_calls SET offload_file = ? WHERE id = ?",
                params![awkward, OFFLOAD_TOOL],
            )
            .expect("the call points at it");
    });
    // The link the tool pane renders is the one this leaf follows — built by the app, not by the
    // test, so a component that forgot to quote fails here.
    let source = offloading_thread(&served.db());
    let (_, pane) = served
        .page(&format!(
            "/session/{CONFIG_ONLY}/thread/{source}/tool/{OFFLOAD_TOOL}"
        ))
        .await;
    let link = Markup::of(&pane).inside("data-body", "tool", "href");
    assert_eq!(link.len(), 1);
    let (status, page) = served.page(&link[0]).await;
    assert_eq!(status, StatusCode::OK, "{}", link[0]);
    assert_eq!(
        Markup::of(&page).fields("data-offload", awkward)["name"],
        awkward
    );
}

#[tokio::test]
async fn a_name_that_looks_like_a_path_is_a_404() {
    // A traversal in the name buys nothing: the name is a key, never a path to open.
    //
    // The viewer reads the store and nothing else — `offload_files` holds the content — so a name
    // shaped like a path is simply a name no row carries.
    let served = Served::corpus();
    for name in ["../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", MISSING] {
        let (status, page) = served
            .page(&format!("/session/{CONFIG_ONLY}/offload/{name}"))
            .await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{name}");
        assert!(!page.contains("root:"), "{name}");
    }
    // And a real name under a session that never offloaded anything is a 404 too, as is one under
    // a session the store has never held: the key is the pair, not either half.
    for session_id in [FORK_ORIGIN, MISSING] {
        let (status, _) = served
            .page(&format!("/session/{session_id}/offload/{OFFLOAD_FILE}"))
            .await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{session_id}");
    }
}

#[tokio::test]
async fn a_chunk_size_outside_its_bounds_is_refused() {
    // A hand-typed chunk size past the ceiling is a 400, not a whole 50 MB file.
    let served = Served::corpus();
    for size in [0, knobs::CHUNK.ceiling + 1] {
        let (status, _) = served
            .page(&format!(
                "/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}?size={size}"
            ))
            .await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{size}");
    }
}
