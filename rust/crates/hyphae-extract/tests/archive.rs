//! The archive: every line of every file a session wrote, and its offloaded tool outputs.
//!
//! The port of `tests/extract/test_claude_code__archive.py`. Claude Code prunes a session's
//! directory a few weeks after it ends, so what is not archived here is gone. The fixtures
//! are redacted mycelia sessions; each fixture directory's README names its source session
//! and Claude Code version.

use std::collections::HashMap;

use hyphae_testsupport::corpus;
use hyphae_testsupport::landmarks::{
    CONFIG_ONLY, DEEP_RESEARCH_SESSION, OFFLOAD_FILE, SPINE, SPINE_LEAF, SPINE_RUN, WORKFLOW_AGENT,
    WORKFLOW_RUN,
};

use hyphae_extract::ExtractError;
use hyphae_model::{MAIN_SOURCE, OffloadFile, SessionTrace};

/// How many archived lines each transcript of a session contributed.
fn lines_by_source(trace: &SessionTrace) -> HashMap<&str, usize> {
    let mut counted: HashMap<&str, usize> = HashMap::new();
    for record in &trace.raw_records {
        *counted.entry(record.source.as_str()).or_default() += 1;
    }
    counted
}

/// A session's subagent transcripts are archived beside its own, line for line.
#[test]
fn the_archive_holds_every_line_of_every_file() {
    let source = corpus::fixture_source("spine", SPINE);
    let trace = corpus::extractor().extract(&source).expect("spine parses");

    // If a session spawned a subagent, which spawned one in turn, then all three files are
    // in the archive, each line under the transcript that recorded it — the main one as
    // "main", each subagent's under its bare agentId...
    assert_eq!(
        lines_by_source(&trace),
        HashMap::from([(MAIN_SOURCE, 41), (SPINE_RUN, 10), (SPINE_LEAF, 6)])
    );
    // ...numbered from 1 within its own file, so a row points back at a line...
    let agent: Vec<i32> = trace
        .raw_records
        .iter()
        .filter(|record| record.source == SPINE_RUN)
        .map(|record| record.line_no)
        .collect();
    assert_eq!(agent, (1..=10).collect::<Vec<i32>>());
    // ...and the two `meta.json` files the walk also found became no source at all: they are
    // linkage that agent runs read, not records.
    assert_eq!(source.files.len(), 5);
}

/// A parallel fan-out's agents and the journal tracking them are archived too.
#[test]
fn a_workflow_run_archives_its_journal_and_its_agents() {
    let trace = corpus::trace("workflow", DEEP_RESEARCH_SESSION);
    let journal = format!("{WORKFLOW_RUN}/journal");

    // If a session fanned out into a workflow, then its agents are sourced by agentId as any
    // other subagent is, and the journal by its workflow directory...
    assert_eq!(
        lines_by_source(&trace),
        HashMap::from([(MAIN_SOURCE, 8), (WORKFLOW_AGENT, 6), (journal.as_str(), 4)])
    );
    // ...carrying the two record types only journals hold.
    let mut kinds: Vec<&str> = trace
        .raw_records
        .iter()
        .filter(|record| record.source == journal)
        .map(|record| record.r#type.as_str())
        .collect();
    kinds.sort_unstable();
    kinds.dedup();
    assert_eq!(kinds, ["result", "started"]);
}

/// Records that carry neither an id nor a timestamp still reach the archive.
#[test]
fn a_bookkeeping_record_is_archived_without_a_uuid_or_a_time() {
    let trace = corpus::trace("workflow", DEEP_RESEARCH_SESSION);

    // If a transcript opens on editor-state records — as this one does, on four of them...
    let opening: Vec<_> = trace
        .raw_records
        .iter()
        .filter(|record| record.source == MAIN_SOURCE)
        .take(4)
        .collect();

    // ...then each is archived with its type and its raw line, and nothing else to say.
    assert_eq!(
        opening
            .iter()
            .map(|record| record.r#type.as_str())
            .collect::<Vec<&str>>(),
        [
            "mode",
            "permission-mode",
            "bridge-session",
            "file-history-snapshot"
        ]
    );
    assert!(
        opening
            .iter()
            .all(|record| record.uuid.is_none() && record.timestamp.is_none())
    );
}

/// The file holding a tool's full output is stored with the session, not pointed at.
#[test]
fn an_offloaded_output_is_archived_whole() {
    let trace = corpus::trace("offload", CONFIG_ONLY);
    let recorded = corpus::fixtures()
        .join("offload")
        .join(CONFIG_ONLY)
        .join("tool-results")
        .join(OFFLOAD_FILE);

    // If a session moved a tool result out of its transcript, then the file comes along
    // whole, named as `ToolCall.offload_file` names it.
    assert_eq!(
        trace.offload_files,
        [OffloadFile {
            session_id: CONFIG_ONLY.to_owned(),
            name: OFFLOAD_FILE.to_owned(),
            // Verbose, so lifted from the fixture: the point is that it is the file's text,
            // not the transcript's preview of it.
            content: std::fs::read_to_string(&recorded).expect("the offloaded file is readable"),
            lossy_decode: false,
            size_bytes: recorded
                .metadata()
                .expect("the offloaded file is on disk")
                .len() as i64,
        }]
    );
    assert_eq!(
        trace.tool_calls[0].offload_file.as_deref(),
        Some(OFFLOAD_FILE)
    );
}

/// A binary tool output is kept, flagged as decoded lossily rather than dropped.
///
/// WebFetch persists PDFs here, and output cut mid-character lands the same way — nine files
/// of the mycelia corpus (scanned 2026-08-07). Invented bytes: the point is the decode, and
/// no recorded example is redactable.
#[test]
fn an_output_that_is_not_text_is_archived_anyway() {
    let planted = corpus::planted(
        "spine",
        SPINE,
        &[("tool-results/fetched.pdf", b"%PDF-\xff\xfe\x00")],
    );

    // If a session offloaded output that is not UTF-8...
    let trace = corpus::extractor()
        .extract(&planted.source)
        .expect("the planted session parses");
    let offload = &trace.offload_files[0];

    // ...then it is archived at its true size, with the loss declared.
    assert_eq!(offload.name, "fetched.pdf");
    assert!(offload.lossy_decode);
    assert_eq!(offload.size_bytes, 8);
    assert!(offload.content.starts_with("%PDF-"));
}

/// The workflow scripts a session stores beside its runs are not parsed as records.
#[test]
fn a_workflow_definition_is_not_a_transcript() {
    // If a session ran a workflow, it keeps the definition and the script that drove it...
    let planted = corpus::planted(
        "spine",
        SPINE,
        &[
            ("workflows/wf_c30cc877-997.json", b"{}"),
            ("workflows/scripts/deep-research-wf_c30cc877-997.js", b"//"),
        ],
    );

    // ...and neither reaches the archive, which would choke on them as JSON lines.
    let trace = corpus::extractor()
        .extract(&planted.source)
        .expect("the planted session parses");
    assert_eq!(
        lines_by_source(&trace).keys().copied().collect::<Vec<_>>(),
        [MAIN_SOURCE]
    );
}

/// A file we cannot place is a Claude Code change to look at, not a file to skip.
///
/// Skipping it would lose whatever it holds for as long as nobody noticed — and the session's
/// files are pruned within weeks.
#[test]
fn an_unknown_file_in_a_session_directory_crashes() {
    let planted = corpus::planted("spine", SPINE, &[("subagents/notes.txt", b"")]);

    let error = corpus::extractor()
        .extract(&planted.source)
        .expect_err("a file the walk cannot place is refused");
    let ExtractError::Schema(message) = &error else {
        panic!("expected a schema error, got {error:?}");
    };
    assert!(
        message.contains("unknown file"),
        "the message says what went wrong: {message}"
    );
}
