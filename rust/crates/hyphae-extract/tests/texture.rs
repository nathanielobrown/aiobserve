//! Session texture: compactions, the names a session goes by, and files still being written.
//!
//! The port of `tests/extract/test_claude_code__texture.py`. Fixtures are redacted excerpts
//! of real mycelia sessions; each fixture directory's README names the source session and the
//! Claude Code version that wrote it. The two invented transcripts here carry a deliberately
//! broken line, which no recorded session survives to hold — they are called out at every
//! use. The warning the truncated one draws is asserted in `hp::cli`, stderr being the only
//! place it goes.

use hyphae_testsupport::corpus::{self, at};
use hyphae_testsupport::landmarks::{
    COMPACTED, COMPACTED_BOUNDARY, COMPACTED_RUN, SECRET, WORKTREE_SESSION as LEGACY_TITLE,
};

use hyphae_extract::ExtractError;
use hyphae_model::{Compaction, MAIN_SOURCE};

/// Each context compaction is a row saying when it ran, why, and how much it shed.
#[test]
fn a_compaction_records_what_it_dropped() {
    let trace = corpus::trace("compaction", COMPACTED);

    // If a session compacted three times — twice on its main thread, once because the
    // operator asked and once because it ran out of window, and once inside an agent run —
    // then every boundary is a row, on the thread that had it...
    assert_eq!(
        trace.compactions,
        [
            Compaction {
                id: COMPACTED_BOUNDARY.to_owned(),
                session_id: COMPACTED.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                timestamp: at("2026-07-02T10:13:08.988"),
                // ...the operator's, which shed 94% of the window in 134 seconds...
                trigger: "manual".to_owned(),
                pre_tokens: 171_313,
                post_tokens: 9_478,
                duration_ms: 133_939,
            },
            Compaction {
                id: "0710fcd7-edbe-4012-bee4-89aadf04f6f2".to_owned(),
                session_id: COMPACTED.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                timestamp: at("2026-07-02T23:45:51.303"),
                // ...and the automatic one thirteen hours later, from a fuller window.
                trigger: "auto".to_owned(),
                pre_tokens: 222_837,
                post_tokens: 13_556,
                duration_ms: 127_487,
            },
            Compaction {
                id: "1c83df25-c70c-4ef1-965d-395a34f281ef".to_owned(),
                session_id: COMPACTED.to_owned(),
                // ...and the agent run's, three days later, carrying the run's own id where
                // the main thread's carry the sentinel: a subagent runs out of window too.
                source: COMPACTED_RUN.to_owned(),
                timestamp: at("2026-07-05T18:28:15.577"),
                trigger: "auto".to_owned(),
                pre_tokens: 240_349,
                post_tokens: 16_918,
                duration_ms: 119_332,
            },
        ]
    );
}

/// Every boundary has the summary record that replaced the dropped context beside it.
///
/// The pairing holds across the whole mycelia corpus (scanned 2026-08-07), so a count that
/// drifts from it means Claude Code changed where the summary goes.
#[test]
fn a_compaction_pairs_with_the_summary_it_wrote() {
    let trace = corpus::trace("compaction", COMPACTED);

    let summaries: Vec<(&str, i32)> = trace
        .raw_records
        .iter()
        .filter(|record| {
            serde_json::from_str::<serde_json::Value>(&record.raw)
                .expect("an archived record is JSON")
                .get("isCompactSummary")
                .is_some_and(|flag| flag == true)
        })
        .map(|record| (record.source.as_str(), record.line_no))
        .collect();

    assert_eq!(summaries.len(), trace.compactions.len());
    // ...and the summary follows its boundary in its own thread's transcript, so the pair
    // reads in transcript order on the main thread and inside the run alike.
    assert_eq!(
        summaries,
        [(MAIN_SOURCE, 3), (MAIN_SOURCE, 7), (COMPACTED_RUN, 4)]
    );
}

/// A session with no operator-set title falls back to the one Claude Code wrote for it.
#[test]
fn a_session_before_custom_titles_takes_its_generated_one() {
    let trace = corpus::trace("legacy_title", LEGACY_TITLE);

    // If a session carries `ai-title` records and no `custom-title`, the generated title is
    // the session's name...
    assert_eq!(trace.session.title.as_deref(), Some("fixture-title-1"));
    // ...and nothing claims an agent name: this session ran as the operator, not as one of
    // the named agents a later Claude Code lets you switch to.
    assert_eq!(trace.session.agent_name, None);
}

/// A session extracted mid-write keeps every complete record.
///
/// Invented: the extractor has to read a file Claude Code is appending to, and a recorded
/// fixture cannot hold a half-written line and stay a recorded fixture. The warning naming
/// the dropped line is `hp::cli`'s `a_truncated_tail_is_a_warning_that_quotes_nothing`.
#[test]
fn a_transcript_still_being_written_drops_only_its_last_line() {
    // If the final line is JSON cut off partway...
    let trace = corpus::trace("invented", "invented-truncated-tail");

    // ...then the records before it are extracted as usual, and the drop is not a crash —
    // the next run will pick the record up once Claude Code has finished writing it.
    assert_eq!(
        trace
            .raw_records
            .iter()
            .map(|record| record.line_no)
            .collect::<Vec<i32>>(),
        [1, 2]
    );
}

/// Unparseable JSON with records after it is corruption, and stops the extraction.
///
/// Invented, for the same reason as the truncated tail: the shape is the whole point.
#[test]
fn a_record_broken_before_the_end_crashes() {
    // If a broken line has a complete record after it, it cannot be a half-written tail...
    let source = corpus::fixture_source("invented", "invented-corrupt-middle");

    // ...so the extractor refuses the file, and says which line to look at without quoting
    // what the line held.
    let error = corpus::extractor()
        .extract(&source)
        .expect_err("a corrupt record is refused");
    let ExtractError::Schema(message) = &error else {
        panic!("expected a schema error, got {error:?}");
    };
    assert!(message.contains("line 3"), "{message}");
    assert!(!message.contains(SECRET), "{message}");
}
