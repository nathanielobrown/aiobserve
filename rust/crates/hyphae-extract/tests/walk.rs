//! The transcript walk over the recorded corpus: what each fixture's ids and parents are.
//!
//! Two leaves that no port owns. The first is the cross-language check — the ids and parents
//! Python's extractor read from four recordings, dumped once and compared here — and the
//! second is discovery, the path `hp extract` takes to a session's files. What each field of
//! an extracted row holds is `session.rs`; what the walk refuses is `refusals.rs`.

use hyphae_testsupport::corpus;

use hyphae_extract::SessionSource;
use hyphae_extract::sessions::SessionFiles;
use hyphae_model::SessionTrace;

/// The four fixtures the Python generator dumps, in its order.
/// `tests/snapshot_from_python.py:FIXTURES` is the authority; these two lists disagreeing is
/// a snapshot mismatch, not a silent pass.
const SNAPSHOT_FIXTURES: &[(&str, &str)] = &[
    ("spine", "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b"),
    ("parallel_tools", "5f4b59fb-a9a8-4ca1-af62-a64b9d0ce515"),
    ("teammate", "10d0349d-0705-4e23-aa64-5b1b97698b2e"),
    ("compaction", "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9"),
];

/// The ids and parents of one trace, one entity per line, in extraction order.
///
/// The twin of `tests/snapshot_from_python.py:dump`. Both write the same lines from the same
/// fields, so a port that attaches a call to the wrong turn — or emits entities in another
/// order — shows up as a diff rather than as equal counts.
fn dump(trace: &SessionTrace) -> Vec<String> {
    let mut lines = vec![format!("session {}", trace.session.id)];
    for turn in &trace.turns {
        lines.push(format!(
            "  turn {} {} {} replayed={}",
            turn.index, turn.source, turn.id, turn.replayed
        ));
    }
    for call in &trace.api_calls {
        lines.push(format!(
            "  call {} {} {} turn={} replayed={}",
            call.index,
            call.source,
            call.id,
            optional(call.turn_id.as_deref()),
            call.replayed
        ));
    }
    for tool in &trace.tool_calls {
        lines.push(format!(
            "  tool {} {} {} call={} replayed={}",
            tool.index, tool.source, tool.id, tool.api_call_id, tool.replayed
        ));
    }
    for run in &trace.agent_runs {
        lines.push(format!(
            "  run {} parent={} tool={} depth={}",
            run.id,
            optional(run.parent_agent_id.as_deref()),
            optional(run.tool_use_id.as_deref()),
            run.spawn_depth
                .map_or_else(|| "None".to_owned(), |depth| depth.to_string())
        ));
    }
    for compaction in &trace.compactions {
        lines.push(format!(
            "  compaction {} {}",
            compaction.source, compaction.id
        ));
    }
    lines
}

/// A missing value printed the way Python prints `None`, since Python writes the other side.
fn optional(value: Option<&str>) -> &str {
    value.unwrap_or("None")
}

/// The parity leaf. The snapshot is not written by Rust: `snapshot_from_python.py` generated
/// it from `hyphae.extract` over these same four transcripts, so a mismatch means the two
/// extractors read one recording differently.
#[test]
fn ids_and_parents_match_the_python_extractor() {
    let dumped: Vec<String> = SNAPSHOT_FIXTURES
        .iter()
        .flat_map(|(directory, stem)| dump(&corpus::trace(directory, stem)))
        .collect();
    insta::assert_snapshot!("ids_and_parents", dumped.join("\n"));
}

/// Discovery under a projects root, the path `hp extract` takes.
#[test]
fn discovery_finds_a_projects_sessions_with_fingerprints() {
    // The fixture directories are not encoded project paths, so this points the extractor at
    // one directly: what is under test is `files()` and the digest, not the path encoding.
    let transcript = corpus::fixtures()
        .join("spine")
        .join("4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b.jsonl");
    let session = SessionFiles {
        id: "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b".to_owned(),
        transcript: transcript.clone(),
    };
    let files = session.files().expect("the session's files are readable");
    // The transcript first, then everything under its sibling directory.
    assert_eq!(files[0], transcript);
    assert!(
        files.len() > 1,
        "spine records subagent transcripts beside it"
    );
    let source: SessionSource = corpus::source(&transcript);
    assert_eq!(source.files, files);
}
