//! One recorded session, extracted whole.
//!
//! The first half of `tests/extract/test_claude_code.py`: the leaf that names every field of
//! every row `spine/` produces, and the two that read one field closely. Fixtures are
//! redacted excerpts of real mycelia sessions; each fixture directory's README names the
//! source session and the Claude Code version that wrote it.

use hyphae_testsupport::corpus::{self, at};
use hyphae_testsupport::landmarks::{
    MYCELIA, SERVER_TOOLS, SIBLING_SESSION as LEGACY_ENTRYPOINT, SLASH_TURN, SPINE,
    SPINE_MODEL_TURN,
};

use hyphae_model::{ApiCall, MAIN_SOURCE, PrLink, Session, Turn};

/// The slash-command records the redactor kept intact, so a leaf can read the parsed halves.
const MODEL_COMMAND: &str = "<command-name>/model</command-name>\n            \
                             <command-message>[redacted]</command-message>\n            \
                             <command-args>[redacted]</command-args>";
const NIGHT_RUN_COMMAND: &str = "<command-message>[redacted]</command-message>\n\
                                 <command-name>/night-run</command-name>\n\
                                 <command-args>[redacted]</command-args>";

/// A session's records become one `SessionTrace` — its metadata, its turns, its API calls.
#[test]
fn a_recorded_session_extracts_whole() {
    // If a real session is extracted...
    let source = corpus::fixture_source("spine", SPINE);
    let trace = corpus::extractor().extract(&source).expect("spine parses");

    // ...then its metadata comes from the records that carry it...
    assert_eq!(
        trace.session,
        Session {
            id: SPINE.to_owned(),
            // ...the first three records are bookkeeping types with no `cwd`, so the
            // project, branch, version and entrypoint all come from the fourth...
            project_dir: Some(MYCELIA.to_owned()),
            git_branch: Some("fixture-branch-1".to_owned()),
            version: Some("2.1.221".to_owned()),
            entrypoint: Some("cli".to_owned()),
            // ...the session spans its earliest and latest record (this excerpt borrows
            // records from other sessions, which is what widens the window past a day)...
            started_at: Some(at("2026-07-06T19:10:55.881")),
            ended_at: Some(at("2026-08-06T18:41:14.084")),
            // ...and active time is the sum of the two `system/turn_duration` records,
            // 206872 + 12713.
            active_ms: 219_585,
            transcript_path: source.files[0].to_string_lossy().into_owned(),
            // ...the title is the *last* `custom-title`, and a later `ai-title` does not
            // displace it: a hand-written name outranks a generated one...
            title: Some("fixture-title-2".to_owned()),
            // ...and the persona name likewise comes from the last `agent-name`.
            agent_name: Some("fixture-agent-name-2".to_owned()),
        }
    );

    // ...four of the eleven `user` records in its own transcript open a turn (its
    // subagent's rows carry that agent's source, and are asserted in `agents.rs`)...
    assert_eq!(
        trace
            .turns
            .iter()
            .filter(|turn| turn.source == MAIN_SOURCE)
            .cloned()
            .collect::<Vec<Turn>>(),
        [
            // ...a slash command leading with `<command-name>`...
            Turn {
                id: SPINE_MODEL_TURN.to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                index: 0,
                prompt: MODEL_COMMAND.to_owned(),
                command_name: Some("/model".to_owned()),
                command_args: Some("[redacted]".to_owned()),
                started_at: at("2026-08-06T10:43:50.675"),
                ended_at: at("2026-08-06T10:43:50.675"),
                // ...none of them a replay, no transcript here having copied another's
                // work...
                replayed: false,
            },
            // ...one leading with `<command-message>` instead — both orderings occur...
            Turn {
                id: SLASH_TURN.to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                index: 1,
                prompt: NIGHT_RUN_COMMAND.to_owned(),
                command_name: Some("/night-run".to_owned()),
                command_args: Some("[redacted]".to_owned()),
                started_at: at("2026-08-06T10:44:27.629"),
                ended_at: at("2026-08-06T10:50:00.205"),
                replayed: false,
            },
            // ...a plain string prompt...
            Turn {
                id: "818588ad-3849-48fe-a546-573163768e04".to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                index: 2,
                prompt: "[redacted]".to_owned(),
                command_name: None,
                command_args: None,
                started_at: at("2026-08-06T18:40:38.883"),
                ended_at: at("2026-08-06T18:41:14.084"),
                replayed: false,
            },
            // ...and one whose content is blocks rather than a string.
            Turn {
                id: "8cdceb31-385c-42d4-9dae-137958b09b88".to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                index: 3,
                prompt: "[redacted]".to_owned(),
                command_name: None,
                command_args: None,
                started_at: at("2026-07-31T19:39:58.872"),
                // ...running to the last record the excerpt holds, a `pr-link`.
                ended_at: at("2026-08-06T11:52:57.977"),
                replayed: false,
            },
        ]
    );

    // ...the thirteen assistant records collapse into the six messages they belong to...
    assert_eq!(
        trace
            .api_calls
            .iter()
            .filter(|call| call.source == MAIN_SOURCE)
            .cloned()
            .collect::<Vec<ApiCall>>(),
        [
            ApiCall {
                id: "msg_011CdmMjFXDofyYSMxYtXa5n".to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                // ...each attributed to the turn that was open when it started...
                turn_id: Some(SLASH_TURN.to_owned()),
                index: 0,
                model: "claude-fable-5".to_owned(),
                // ...answered by the model it was asked of, as all but three calls in the
                // corpus were...
                fallback_from: None,
                effort: Some("high".to_owned()),
                stop_reason: Some("tool_use".to_owned()),
                attribution_skill: Some("night-run".to_owned()),
                request_id: Some("req_011CdmMjDTCU8h7qzXd5Chuj".to_owned()),
                // ...starting when the record it answers was written, ending on its last
                // chunk...
                started_at: at("2026-08-06T10:44:27.629"),
                ended_at: at("2026-08-06T10:44:33.590"),
                input_tokens: 2,
                output_tokens: 415,
                cache_read_tokens: 9_768,
                cache_creation_tokens: 20_257,
                cache_5m_tokens: Some(0),
                cache_1h_tokens: Some(20_257),
                text: "[redacted]".to_owned(),
                thinking: "[redacted]".to_owned(),
                // ...priced from our own table, which the transcript knows nothing about —
                // the arithmetic is `pricing.rs`'s job, so these are exact...
                cost_usd: Some(0.435_678),
                synthetic: false,
                replayed: false,
            },
            // ...one that did nothing but delegate: a single `Agent` block, so no text and
            // no thinking, and its subagent's transcript holds what came of it...
            ApiCall {
                id: "msg_011CdmToQdxciYnDo9M2d7HN".to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                turn_id: Some("818588ad-3849-48fe-a546-573163768e04".to_owned()),
                index: 1,
                model: "claude-fable-5".to_owned(),
                fallback_from: None,
                effort: Some("high".to_owned()),
                stop_reason: Some("tool_use".to_owned()),
                attribution_skill: None,
                request_id: Some("req_011CdmToGAj76xW5dBRexvQm".to_owned()),
                // ...answering a record this excerpt does not carry, so it falls back to
                // its own first chunk for a start...
                started_at: at("2026-08-06T12:04:25.038"),
                ended_at: at("2026-08-06T12:04:25.038"),
                input_tokens: 2,
                output_tokens: 2_378,
                cache_read_tokens: 75_235,
                cache_creation_tokens: 917,
                cache_5m_tokens: Some(0),
                cache_1h_tokens: Some(917),
                text: String::new(),
                thinking: String::new(),
                cost_usd: Some(0.212_495),
                synthetic: false,
                replayed: false,
            },
            // ...one that asked for two tools at once, a `Bash` and a `ToolSearch`, which
            // is why this excerpt carries it: the calls log and the tool popover both need
            // a call whose tools are siblings...
            ApiCall {
                id: "msg_011CdmUSN7CEFrApaViphdwb".to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                turn_id: Some("818588ad-3849-48fe-a546-573163768e04".to_owned()),
                index: 2,
                model: "claude-fable-5".to_owned(),
                fallback_from: None,
                effort: Some("high".to_owned()),
                stop_reason: Some("tool_use".to_owned()),
                attribution_skill: None,
                request_id: Some("req_011CdmUSH9nYjBWjJMdPE2s6".to_owned()),
                started_at: at("2026-08-06T12:12:31.903"),
                ended_at: at("2026-08-06T12:12:31.946"),
                input_tokens: 2,
                output_tokens: 335,
                cache_read_tokens: 88_758,
                cache_creation_tokens: 1_101,
                cache_5m_tokens: Some(0),
                cache_1h_tokens: Some(1_101),
                text: String::new(),
                thinking: String::new(),
                cost_usd: Some(0.127_548),
                synthetic: false,
                replayed: false,
            },
            // ...one that sent a notification and nothing else...
            ApiCall {
                id: "msg_011CdmUTLXigDcVRN67fErbT".to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                turn_id: Some("818588ad-3849-48fe-a546-573163768e04".to_owned()),
                index: 3,
                model: "claude-fable-5".to_owned(),
                fallback_from: None,
                effort: Some("high".to_owned()),
                stop_reason: Some("tool_use".to_owned()),
                attribution_skill: None,
                request_id: Some("req_011CdmUTJMFEfCSxd89Q4jpL".to_owned()),
                started_at: at("2026-08-06T12:12:42.148"),
                ended_at: at("2026-08-06T12:12:42.148"),
                input_tokens: 2,
                output_tokens: 153,
                cache_read_tokens: 91_282,
                cache_creation_tokens: 667,
                cache_5m_tokens: Some(0),
                cache_1h_tokens: Some(667),
                text: String::new(),
                thinking: String::new(),
                cost_usd: Some(0.112_292),
                synthetic: false,
                replayed: false,
            },
            ApiCall {
                id: "msg_011Cdmz3NQtuzwN3cqYvvkuN".to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                turn_id: Some("818588ad-3849-48fe-a546-573163768e04".to_owned()),
                index: 4,
                model: "claude-fable-5".to_owned(),
                fallback_from: None,
                effort: Some("high".to_owned()),
                stop_reason: Some("tool_use".to_owned()),
                // ...and this one ran outside any skill, so it carries none.
                attribution_skill: None,
                request_id: Some("req_011Cdmz3L3GvhB4826jd4xYp".to_owned()),
                started_at: at("2026-08-06T18:40:38.878"),
                ended_at: at("2026-08-06T18:41:14.084"),
                input_tokens: 2,
                output_tokens: 2_062,
                cache_read_tokens: 0,
                cache_creation_tokens: 94_194,
                cache_5m_tokens: Some(0),
                cache_1h_tokens: Some(94_194),
                text: "[redacted]".to_owned(),
                thinking: "[redacted]".to_owned(),
                cost_usd: Some(1.987),
                synthetic: false,
                replayed: false,
            },
            // ...and one Claude Code wrote itself rather than asking a model for: no
            // request id, no effort, no tokens, and a stated cost of zero rather than an
            // unpriced null.
            ApiCall {
                id: "03b918cc-8a2a-4891-9385-39caceac50ac".to_owned(),
                session_id: SPINE.to_owned(),
                source: MAIN_SOURCE.to_owned(),
                turn_id: Some("8cdceb31-385c-42d4-9dae-137958b09b88".to_owned()),
                index: 5,
                model: "<synthetic>".to_owned(),
                fallback_from: None,
                effort: None,
                stop_reason: Some("stop_sequence".to_owned()),
                attribution_skill: None,
                request_id: None,
                started_at: at("2026-07-06T19:10:55.881"),
                ended_at: at("2026-07-06T19:10:55.881"),
                input_tokens: 0,
                output_tokens: 0,
                cache_read_tokens: 0,
                cache_creation_tokens: 0,
                cache_5m_tokens: Some(0),
                cache_1h_tokens: Some(0),
                text: "[redacted]".to_owned(),
                thinking: String::new(),
                cost_usd: Some(0.0),
                synthetic: true,
                replayed: false,
            },
        ]
    );

    // ...the two `pr-link` records become two rows even though both name the same PR, since
    // a session that pushes twice links it twice and the records carry no uuid...
    assert_eq!(
        trace.pr_links,
        [
            PrLink {
                session_id: SPINE.to_owned(),
                line_no: 39,
                pr_number: 656,
                pr_url: "fixture-pr-url-1".to_owned(),
                pr_repository: "fixture-pr-repo-1".to_owned(),
                timestamp: at("2026-08-06T11:48:48.477"),
            },
            PrLink {
                session_id: SPINE.to_owned(),
                line_no: 40,
                pr_number: 656,
                pr_url: "fixture-pr-url-1".to_owned(),
                pr_repository: "fixture-pr-repo-1".to_owned(),
                timestamp: at("2026-08-06T11:52:57.977"),
            },
        ]
    );

    // ...while every line of the transcript survives in the archive, whatever it was —
    // beside the lines of the subagent it spawned, which carry their own source.
    assert_eq!(
        trace
            .raw_records
            .iter()
            .filter(|record| record.source == MAIN_SOURCE)
            .count(),
        41
    );
    assert_eq!(trace.extractor, "claude_code");
}

/// When Claude Code retries a request on another model, the call says which one it wanted.
///
/// The reply records only the model that answered, so without the `fallback` block a forced
/// downgrade reads as a deliberate model choice.
#[test]
fn a_call_that_fell_back_names_the_model_it_asked_for() {
    let trace = corpus::trace("server_tools", SERVER_TOOLS);

    // If a reply carries a `fallback` block...
    let fell_back = trace
        .api_calls
        .iter()
        .find(|call| call.id == "msg_011Ccua7MYguu6rjoiKNhYVh")
        .expect("server_tools records the fallback");

    // ...then the call reports the model that answered and the one first asked for...
    assert_eq!(fell_back.model, "claude-opus-4-8");
    assert_eq!(fell_back.fallback_from.as_deref(), Some("claude-fable-5"));
    // ...and every ordinary call says it fell back from nothing.
    assert_eq!(
        trace
            .api_calls
            .iter()
            .filter(|call| call.id != fell_back.id)
            .map(|call| call.fallback_from.clone())
            .collect::<Vec<Option<String>>>(),
        [None, None, None]
    );
}

/// A session recorded before `entrypoint` existed extracts with that column null.
///
/// The corpus reaches back to Claude Code 1.0.128, and two of its 575 sessions predate the
/// field. Requiring it crashes the whole extract on the oldest sessions we have.
#[test]
fn a_session_older_than_a_field_reports_it_absent() {
    let trace = corpus::trace("legacy_entrypoint", LEGACY_ENTRYPOINT);

    // If the record carrying the session's context has no `entrypoint`...
    assert_eq!(trace.session.entrypoint, None);
    // ...then everything beside it still lands, from that same record...
    assert_eq!(trace.session.version.as_deref(), Some("1.0.128"));
    assert_eq!(
        trace.session.git_branch.as_deref(),
        Some("fixture-branch-1")
    );
    // ...and the reply, which carries neither `effort` nor `attributionSkill`, parses too.
    let call = &trace.api_calls[0];
    assert_eq!(
        (
            call.effort.as_deref(),
            call.attribution_skill.as_deref(),
            call.stop_reason.as_deref()
        ),
        (None, None, None)
    );
    assert_eq!(call.cache_read_tokens, 95_331);
}
