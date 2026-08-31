//! What the extractor refuses to parse, and what it is allowed to say about it.
//!
//! The crash leaves of `tests/extract/test_claude_code.py`. Every fixture here is invented —
//! each plants a shape no recorded session holds, which is the registry's whole claim — and
//! each carries a tripwire the crash must not repeat: a crash message is the one place a
//! private transcript could reach a log.

use hyphae_testsupport::corpus;
use hyphae_testsupport::landmarks::SECRET;

use hyphae_extract::ExtractError;

/// The schema error one invented fixture must fail with.
fn refused(stem: &str) -> String {
    let error = corpus::extractor()
        .extract(&corpus::fixture_source("invented", stem))
        .expect_err("an unregistered shape is refused");
    let ExtractError::Schema(message) = error else {
        panic!("expected a schema error, got {error:?}");
    };
    message
}

/// A type we do not handle is a schema change to surface, and the message stays clean.
#[test]
fn an_unknown_record_type_crashes_without_quoting_the_record() {
    let message = refused("invented-unknown-type");

    assert!(message.contains("telepathy"), "{message}");
    assert!(message.contains("line 2"), "{message}");
    assert!(message.contains("invented-unknown-type"), "{message}");
    assert!(!message.contains(SECRET), "{message}");
}

/// A `system` record whose subtype is new is as much a schema change as a new type.
///
/// All nine live subtypes are registered.
#[test]
fn an_unknown_system_subtype_crashes() {
    let message = refused("invented-unknown-subtype");

    assert!(message.contains("quantum_flux"), "{message}");
    assert!(message.contains("line 2"), "{message}");
    assert!(!message.contains(SECRET), "{message}");
}

/// A message content block of a kind we do not read stops the run.
///
/// The eight block kinds the corpus holds are registered. Without the crash a new kind is
/// invisible: that is how 45 `server_tool_use` calls sat unread, and an analysis would have
/// reported the sessions used no server-side tools.
#[test]
fn an_unknown_content_block_crashes() {
    let message = refused("invented-unknown-block");

    assert!(message.contains("clairvoyance"), "{message}");
    assert!(message.contains("line 2"), "{message}");
    assert!(!message.contains(SECRET), "{message}");
}

/// A prompt leading with an unregistered tag stops the run rather than being guessed at.
///
/// The tag census closed over every main and subagent transcript. Without the crash, the next
/// notification type silently re-inflates the turn counts.
#[test]
fn a_novel_prompt_tag_crashes() {
    let message = refused("invented-novel-tag");

    assert!(message.contains("sparkle-notice"), "{message}");
    assert!(!message.contains(SECRET), "{message}");
}

/// Two records under one uuid may differ in their envelope, never in what was said.
///
/// 995 duplicate pairs exist in the corpus and none differs in content. A difference would
/// mean the conversation itself was rewritten, which last-occurrence-wins would quietly
/// accept.
#[test]
fn a_duplicate_uuid_whose_content_differs_crashes() {
    let message = refused("invented-dup-content-diff");

    assert!(
        message.contains("33333333-3333-4333-8333-333333333333"),
        "{message}"
    );
}
