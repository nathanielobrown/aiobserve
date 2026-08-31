//! What a record becomes, and what it never becomes.
//!
//! The second half of `tests/extract/test_claude_code.py`: which records open a turn, how the
//! chunks of one reply are put back together, and which records the archive keeps while no
//! parsed table names them. The refusals are `refusals.rs`.

use hyphae_testsupport::corpus;
use hyphae_testsupport::landmarks::{DUP_UUID, REGISTRY_ZOO, SPINE};

use hyphae_model::MAIN_SOURCE;

/// The zoo's `system/model_consent_fallback` record. Archive-only, so proving where it does
/// *not* land takes its uuid.
const CONSENT_FALLBACK: &str = "8a87c47a-66fd-47c7-a643-28ebe3914883";

/// One API reply written as several chained records is one API call, not several.
///
/// Claude Code writes a record per content block, so a thinking-plus-text-plus-three-tools
/// reply lands as five lines under one `message.id`. 67% of `(message.id, file)` pairs in the
/// corpus span more than one record, so a per-line parser triples the call count.
#[test]
fn a_message_split_across_records_merges_into_one_call() {
    let trace = corpus::trace("spine", SPINE);

    // If the file holds thirteen assistant records under six message ids...
    assert_eq!(
        trace
            .raw_records
            .iter()
            .filter(|record| record.r#type == "assistant" && record.source == MAIN_SOURCE)
            .count(),
        13
    );
    // ...then six API calls come back, each spanning from the record it answers to its last
    // chunk, with the thinking and the text it was split across both present.
    let main: Vec<_> = trace
        .api_calls
        .iter()
        .filter(|call| call.source == MAIN_SOURCE)
        .collect();
    assert_eq!(main.len(), 6);
    let merged = main[0];
    assert!(!merged.text.is_empty() && !merged.thinking.is_empty());
    // ...and the usage is counted once: all five chunks repeat the reply's numbers, so a
    // per-record sum would report 2,075 output tokens for a 415-token reply.
    assert_eq!(merged.output_tokens, 415);
}

/// The XML Claude Code writes to itself — notifications, shell echoes — is not a prompt.
///
/// An unfiltered turn rule counts these, which is the ~3.6x turn inflation the prior importer
/// shipped: 2,157 `<task-notification>` records against 968 real prompts.
#[test]
fn machine_records_are_archived_but_never_turns() {
    let trace = corpus::trace("spine", SPINE);

    // If the fixture holds a notification, a shell echo, a bash prompt and its output...
    for tag in [
        "<task-notification>",
        "<local-command-stdout>",
        "<bash-input>",
        "<bash-stdout>",
    ] {
        assert_eq!(
            trace
                .raw_records
                .iter()
                .filter(|record| record.raw.contains(tag))
                .count(),
            1,
            "the fixture records one {tag}"
        );
    }
    // ...then each is archived, and none of them opened a turn.
    assert!(
        !trace
            .turns
            .iter()
            .any(|turn| turn.prompt.starts_with("<task"))
    );
    assert_eq!(
        trace
            .turns
            .iter()
            .filter(|turn| turn.source == MAIN_SOURCE)
            .count(),
        4
    );
}

/// A caveat Claude Code injects on the user's behalf is marked `isMeta` and never a turn.
///
/// It also carries a tag no registry lists, so the meta filter has to run first. A `user`
/// record carrying a tool result is the transcript's plumbing rather than a prompt for a
/// second reason, and neither opens one.
#[test]
fn a_meta_record_and_a_tool_result_are_not_turns() {
    let trace = corpus::trace("spine", SPINE);

    assert!(
        trace
            .raw_records
            .iter()
            .any(|record| record.raw.contains("<local-command-caveat>")),
        "the fixture records an injected caveat"
    );
    assert!(
        !trace
            .turns
            .iter()
            .any(|turn| turn.prompt.contains("caveat"))
    );
    assert!(
        !trace
            .turns
            .iter()
            .any(|turn| turn.prompt.contains("tool_result"))
    );
}

/// When a reply reports no cache-creation split, the two TTL columns are null, not zero.
///
/// INVENTED fixture: every assistant record in the mycelia corpus carries
/// `usage.cache_creation`, so nothing recorded shows the absent shape. See
/// `fixtures/invented/README.md` — this pins a behaviour we chose, not one we observed.
#[test]
fn the_cache_split_is_absent_rather_than_zero() {
    let trace = corpus::trace("invented", "invented-no-cache-creation");

    let call = &trace.api_calls[0];
    assert_eq!((call.cache_5m_tokens, call.cache_1h_tokens), (None, None));
    // ...while the total the record does report still lands.
    assert_eq!(call.cache_creation_tokens, 100);
}

/// One redacted record of every live type and system subtype extracts without a crash.
///
/// The registry's completeness is what sank the design's first revision. This fixture is its
/// only regression net in the suite — it cannot prove the live corpus grew a new type, which
/// is a gap the testing plan records rather than papers over.
#[test]
fn every_record_type_the_corpus_holds_parses() {
    // If a file holds one record of every type and subtype the census found...
    let trace = corpus::trace("registry_zoo", REGISTRY_ZOO);

    // ...then extraction returns, and every line lands in the archive with its type intact.
    assert_eq!(trace.raw_records.len(), 31);
    for kind in ["worktree-state", "fork-context-ref", "summary"] {
        assert!(
            trace.raw_records.iter().any(|record| record.r#type == kind),
            "the zoo records a {kind}"
        );
    }
    assert_eq!(
        trace
            .raw_records
            .iter()
            .filter(|record| record.r#type == "system")
            .count(),
        10
    );
}

/// When Claude Code falls back to another model for the session, it says so and nothing more.
///
/// The notice is a UI message about the harness, not about the work: it opens no turn,
/// answers no call, and has no children. The archive keeps it; no parsed table does.
#[test]
fn the_notice_that_the_harness_switched_models_is_archived_only() {
    let trace = corpus::trace("registry_zoo", REGISTRY_ZOO);

    // If a session records the harness swapping the model it was asked for...
    let archived: Vec<_> = trace
        .raw_records
        .iter()
        .filter(|record| record.uuid.as_deref() == Some(CONSENT_FALLBACK))
        .collect();
    assert_eq!(
        archived
            .iter()
            .map(|record| record.r#type.as_str())
            .collect::<Vec<&str>>(),
        ["system"]
    );
    // ...then the whole record is archived, carrying what was swapped and whether it stuck...
    let recorded: serde_json::Value =
        serde_json::from_str(&archived[0].raw).expect("an archived record is JSON");
    assert_eq!(recorded["subtype"], "model_consent_fallback");
    assert_eq!(recorded["originalModel"], "claude-fable-5");
    assert_eq!(recorded["fallbackModel"], "claude-opus-5[1m]");
    assert_eq!(recorded["persistedAsDefault"], false);
    // ...and no parsed row is keyed by it.
    let parsed = trace
        .turns
        .iter()
        .map(|row| row.id.as_str())
        .chain(trace.api_calls.iter().map(|row| row.id.as_str()))
        .chain(trace.tool_calls.iter().map(|row| row.id.as_str()))
        .chain(trace.agent_runs.iter().map(|row| row.id.as_str()))
        .chain(trace.compactions.iter().map(|row| row.id.as_str()));
    assert!(parsed.into_iter().all(|id| id != CONSENT_FALLBACK));
}

/// When a rewind rewrites a record under the same uuid, the file's final word wins.
///
/// Keep-first and keep-last give different token totals on four real sessions, so the policy
/// decides what the DB reports, not just which row it stores. The summary Claude Code writes
/// into the transcript after compacting is not a prompt, which is why this session — a rewind
/// over a compacted conversation — records no turn at all.
#[test]
fn a_duplicate_uuid_resolves_to_its_last_occurrence() {
    // If a session rewound, rewriting five records under uuids it had already used...
    let trace = corpus::trace("dup_uuid", DUP_UUID);

    // ...then each contributes one row, carrying the second occurrence's values — the
    // rewritten branch on the session...
    assert_eq!(
        trace.session.git_branch.as_deref(),
        Some("fixture-branch-3")
    );
    // ...and the rewritten usage on the API call, which the first occurrence reported as
    // 3237 cache-creation and 2629 output tokens.
    assert_eq!(trace.api_calls.len(), 1);
    let call = &trace.api_calls[0];
    assert_eq!(
        (
            call.cache_creation_tokens,
            call.output_tokens,
            call.cache_read_tokens
        ),
        (0, 0, 0)
    );
    // ...while the archive keeps both occurrences, since it is the schema-archaeology copy.
    assert_eq!(trace.raw_records.len(), 10);
    assert_eq!(trace.turns, []);
}
