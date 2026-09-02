"""Turning recorded Claude Code transcripts into a `SessionTrace`.

Fixtures are redacted excerpts of real mycelia sessions; each fixture directory's README
names the source session and the Claude Code version that wrote it. The handful of
invented fixtures live under `fixtures/invented/` and are called out at every use.
"""

import json
from datetime import UTC, datetime

import pytest

from hyphae.extract.claude_code import ClaudeCodeExtractor
from hyphae.extract.errors import TranscriptSchemaError
from hyphae.model import MAIN_SOURCE, ApiCall, PrLink, Session, Turn
from tests.conftest import SourceFactory

SPINE = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b"
ZOO = "registry-zoo-0000-0000-0000-000000000000"
# The zoo's `system/model_consent_fallback` record. Archive-only, so proving where it does
# *not* land takes its uuid.
CONSENT_FALLBACK = "8a87c47a-66fd-47c7-a643-28ebe3914883"
DUPS = "8ee00a94-b01a-4394-b447-b065f74b11af"
# The session whose replies carry server-side tool calls and a model fallback.
SERVER_TOOLS = "088d63aa-71d3-4108-965e-5147e3eaddbd"

# The slash-command records the redactor kept intact, so a test can read the parsed halves.
MODEL_COMMAND = (
    "<command-name>/model</command-name>\n"
    "            <command-message>[redacted]</command-message>\n"
    "            <command-args>[redacted]</command-args>"
)
NIGHT_RUN_COMMAND = (
    "<command-message>[redacted]</command-message>\n"
    "<command-name>/night-run</command-name>\n"
    "<command-args>[redacted]</command-args>"
)


def at(moment: str) -> datetime:
    """A transcript timestamp, as the extractor parses it."""
    return datetime.fromisoformat(moment).replace(tzinfo=UTC)


def test_a_recorded_session_extracts_whole(fixture_source: SourceFactory):
    """A session's records become one `SessionTrace` — its metadata, its turns, its API calls."""
    # If a real session is extracted...
    source = fixture_source("spine", SPINE)
    trace = ClaudeCodeExtractor().extract(source)

    # ...then its metadata comes from the records that carry it...
    assert trace.session == Session(
        id=SPINE,
        # ...the first three records are bookkeeping types with no `cwd`, so the project,
        # branch, version and entrypoint all come from the fourth...
        project_dir="/Users/nob/repos/mycelia",
        git_branch="fixture-branch-1",
        version="2.1.221",
        entrypoint="cli",
        # ...the session spans its earliest and latest record (this excerpt borrows records
        # from other sessions, which is what widens the window past a single day)...
        started_at=at("2026-07-06T19:10:55.881"),
        ended_at=at("2026-08-06T18:41:14.084"),
        # ...and active time is the sum of the two `system/turn_duration` records, 206872 + 12713.
        active_ms=219585,
        transcript_path=str(source.files.transcript),
        # ...the title is the *last* `custom-title`, and a later `ai-title` does not
        # displace it: a hand-written name outranks a generated one...
        title="fixture-title-2",
        # ...and the persona name likewise comes from the last `agent-name`.
        agent_name="fixture-agent-name-2",
    )

    # ...four of the eleven `user` records in its own transcript open a turn (its subagent's
    # rows carry that agent's source, and are asserted in `test_claude_code__agents.py`)...
    assert [turn for turn in trace.turns if turn.source == MAIN_SOURCE] == [
        # ...a slash command leading with `<command-name>`...
        Turn(
            id="5b848af7-f86e-4950-b474-cd98125fad24",
            session_id=SPINE,
            source="main",
            index=0,
            prompt=MODEL_COMMAND,
            command_name="/model",
            command_args="[redacted]",
            started_at=at("2026-08-06T10:43:50.675"),
            ended_at=at("2026-08-06T10:43:50.675"),
            # ...none of them a replay, no transcript here having copied another's work...
            replayed=False,
        ),
        # ...one leading with `<command-message>` instead — both orderings occur...
        Turn(
            id="30aad8e5-21f8-486d-b9d9-e118c703a5a1",
            session_id=SPINE,
            source="main",
            index=1,
            prompt=NIGHT_RUN_COMMAND,
            command_name="/night-run",
            command_args="[redacted]",
            started_at=at("2026-08-06T10:44:27.629"),
            ended_at=at("2026-08-06T10:50:00.205"),
            replayed=False,
        ),
        # ...a plain string prompt...
        Turn(
            id="818588ad-3849-48fe-a546-573163768e04",
            session_id=SPINE,
            source="main",
            index=2,
            prompt="[redacted]",
            command_name=None,
            command_args=None,
            started_at=at("2026-08-06T18:40:38.883"),
            ended_at=at("2026-08-06T18:41:14.084"),
            replayed=False,
        ),
        # ...and one whose content is blocks rather than a string.
        Turn(
            id="8cdceb31-385c-42d4-9dae-137958b09b88",
            session_id=SPINE,
            source="main",
            index=3,
            prompt="[redacted]",
            command_name=None,
            command_args=None,
            started_at=at("2026-07-31T19:39:58.872"),
            # ...running to the last record the excerpt holds, a `pr-link`.
            ended_at=at("2026-08-06T11:52:57.977"),
            replayed=False,
        ),
    ]

    # ...the thirteen assistant records collapse into the six messages they belong to...
    assert [call for call in trace.api_calls if call.source == MAIN_SOURCE] == [
        ApiCall(
            id="msg_011CdmMjFXDofyYSMxYtXa5n",
            session_id=SPINE,
            source="main",
            # ...each attributed to the turn that was open when it started...
            turn_id="30aad8e5-21f8-486d-b9d9-e118c703a5a1",
            index=0,
            model="claude-fable-5",
            # ...answered by the model it was asked of, as all but three calls in the
            # corpus were...
            fallback_from=None,
            effort="high",
            stop_reason="tool_use",
            attribution_skill="night-run",
            request_id="req_011CdmMjDTCU8h7qzXd5Chuj",
            # ...starting when the record it answers was written, ending on its last chunk...
            started_at=at("2026-08-06T10:44:27.629"),
            ended_at=at("2026-08-06T10:44:33.590"),
            input_tokens=2,
            output_tokens=415,
            cache_read_tokens=9768,
            cache_creation_tokens=20257,
            cache_5m_tokens=0,
            cache_1h_tokens=20257,
            # ...priced from our own table, which the transcript knows nothing about — the
            # arithmetic is `tests/extract/test_pricing.py`'s job, so these are exact...
            cost_usd=0.435678,
            synthetic=False,
            text="[redacted]",
            thinking="[redacted]",
            replayed=False,
        ),
        # ...one that only answered, and is here for where it left the context window: it ends
        # the turn before the last, which is what gives that turn's context bar a conversation
        # to stand on (`tests/fixtures/spine/README.md`)...
        ApiCall(
            id="msg_011CdmMz6vD6y2JsoEV6qVYL",
            session_id=SPINE,
            source="main",
            turn_id="30aad8e5-21f8-486d-b9d9-e118c703a5a1",
            index=1,
            model="claude-fable-5",
            fallback_from=None,
            effort="high",
            stop_reason="end_turn",
            attribution_skill="night-run",
            request_id="req_011CdmMz5oVCnzjRdjfSLSRX",
            started_at=at("2026-08-06T10:47:54.500"),
            ended_at=at("2026-08-06T10:47:54.500"),
            input_tokens=2,
            output_tokens=267,
            cache_read_tokens=66505,
            cache_creation_tokens=925,
            cache_5m_tokens=0,
            cache_1h_tokens=925,
            cost_usd=0.098375,
            synthetic=False,
            text="[redacted]",
            thinking="",
            replayed=False,
        ),
        # ...one that did nothing but delegate: a single `Agent` block, so no text and no
        # thinking, and its subagent's transcript holds what came of it...
        ApiCall(
            id="msg_011CdmToQdxciYnDo9M2d7HN",
            session_id=SPINE,
            source="main",
            turn_id="818588ad-3849-48fe-a546-573163768e04",
            index=2,
            model="claude-fable-5",
            fallback_from=None,
            effort="high",
            stop_reason="tool_use",
            attribution_skill=None,
            request_id="req_011CdmToGAj76xW5dBRexvQm",
            # ...answering a record this excerpt does not carry, so it falls back to its
            # own first chunk for a start...
            started_at=at("2026-08-06T12:04:25.038"),
            ended_at=at("2026-08-06T12:04:25.038"),
            input_tokens=2,
            output_tokens=2378,
            cache_read_tokens=75235,
            cache_creation_tokens=917,
            cache_5m_tokens=0,
            cache_1h_tokens=917,
            cost_usd=0.212495,
            synthetic=False,
            text="",
            thinking="",
            replayed=False,
        ),
        # ...one that asked for two tools at once, a `Bash` and a `ToolSearch`, which is why
        # this excerpt carries it: the calls log and the tool popover both need a call whose
        # tools are siblings...
        ApiCall(
            id="msg_011CdmUSN7CEFrApaViphdwb",
            session_id=SPINE,
            source="main",
            turn_id="818588ad-3849-48fe-a546-573163768e04",
            index=3,
            model="claude-fable-5",
            fallback_from=None,
            effort="high",
            stop_reason="tool_use",
            attribution_skill=None,
            request_id="req_011CdmUSH9nYjBWjJMdPE2s6",
            started_at=at("2026-08-06T12:12:31.903"),
            ended_at=at("2026-08-06T12:12:31.946"),
            input_tokens=2,
            output_tokens=335,
            cache_read_tokens=88758,
            cache_creation_tokens=1101,
            cache_5m_tokens=0,
            cache_1h_tokens=1101,
            cost_usd=0.127548,
            synthetic=False,
            text="",
            thinking="",
            replayed=False,
        ),
        # ...one that sent a notification and nothing else...
        ApiCall(
            id="msg_011CdmUTLXigDcVRN67fErbT",
            session_id=SPINE,
            source="main",
            turn_id="818588ad-3849-48fe-a546-573163768e04",
            index=4,
            model="claude-fable-5",
            fallback_from=None,
            effort="high",
            stop_reason="tool_use",
            attribution_skill=None,
            request_id="req_011CdmUTJMFEfCSxd89Q4jpL",
            started_at=at("2026-08-06T12:12:42.148"),
            ended_at=at("2026-08-06T12:12:42.148"),
            input_tokens=2,
            output_tokens=153,
            cache_read_tokens=91282,
            cache_creation_tokens=667,
            cache_5m_tokens=0,
            cache_1h_tokens=667,
            cost_usd=0.112292,
            synthetic=False,
            text="",
            thinking="",
            replayed=False,
        ),
        ApiCall(
            id="msg_011Cdmz3NQtuzwN3cqYvvkuN",
            session_id=SPINE,
            source="main",
            turn_id="818588ad-3849-48fe-a546-573163768e04",
            index=5,
            model="claude-fable-5",
            fallback_from=None,
            effort="high",
            stop_reason="tool_use",
            # ...and this one ran outside any skill, so it carries none.
            attribution_skill=None,
            request_id="req_011Cdmz3L3GvhB4826jd4xYp",
            started_at=at("2026-08-06T18:40:38.878"),
            ended_at=at("2026-08-06T18:41:14.084"),
            input_tokens=2,
            output_tokens=2062,
            cache_read_tokens=0,
            cache_creation_tokens=94194,
            cache_5m_tokens=0,
            cache_1h_tokens=94194,
            cost_usd=1.987,
            synthetic=False,
            text="[redacted]",
            thinking="[redacted]",
            replayed=False,
        ),
        # ...and one Claude Code wrote itself rather than asking a model for: no request id,
        # no effort, no tokens, and a stated cost of zero rather than an unpriced null.
        ApiCall(
            id="03b918cc-8a2a-4891-9385-39caceac50ac",
            session_id=SPINE,
            source="main",
            turn_id="8cdceb31-385c-42d4-9dae-137958b09b88",
            index=6,
            model="<synthetic>",
            fallback_from=None,
            effort=None,
            stop_reason="stop_sequence",
            attribution_skill=None,
            request_id=None,
            started_at=at("2026-07-06T19:10:55.881"),
            ended_at=at("2026-07-06T19:10:55.881"),
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cache_5m_tokens=0,
            cache_1h_tokens=0,
            cost_usd=0.0,
            synthetic=True,
            text="[redacted]",
            thinking="",
            replayed=False,
        ),
    ]

    # ...the two `pr-link` records become two rows even though both name the same PR, since
    # a session that pushes twice links it twice and the records carry no uuid...
    assert trace.pr_links == [
        PrLink(
            session_id=SPINE,
            line_no=40,
            pr_number=656,
            pr_url="fixture-pr-url-1",
            pr_repository="fixture-pr-repo-1",
            timestamp=at("2026-08-06T11:48:48.477"),
        ),
        PrLink(
            session_id=SPINE,
            line_no=41,
            pr_number=656,
            pr_url="fixture-pr-url-1",
            pr_repository="fixture-pr-repo-1",
            timestamp=at("2026-08-06T11:52:57.977"),
        ),
    ]

    # ...while every line of the transcript survives in the archive, whatever it was —
    # beside the lines of the subagent it spawned, which carry their own source.
    assert len([r for r in trace.raw_records if r.source == MAIN_SOURCE]) == 42
    assert trace.extractor == "claude_code"


def test_a_message_split_across_records_merges_into_one_call(fixture_source: SourceFactory):
    """One API reply written as several chained records is one API call, not several.

    Claude Code writes a record per content block, so a thinking-plus-text-plus-three-tools
    reply lands as five lines under one `message.id`. 67% of `(message.id, file)` pairs in
    the corpus span more than one record, so a per-line parser triples the call count.
    """
    trace = ClaudeCodeExtractor().extract(fixture_source("spine", SPINE))

    # If the file holds fourteen assistant records under seven message ids...
    assert (
        len([r for r in trace.raw_records if r.type == "assistant" and r.source == MAIN_SOURCE])
        == 14
    )
    # ...then seven API calls come back, each spanning from the record it answers to its
    # last chunk, with the thinking and the text it was split across both present.
    main = [call for call in trace.api_calls if call.source == MAIN_SOURCE]
    assert len(main) == 7
    merged = main[0]
    assert (merged.started_at, merged.ended_at) == (
        at("2026-08-06T10:44:27.629"),
        at("2026-08-06T10:44:33.590"),
    )
    assert merged.text and merged.thinking
    # ...and the usage is counted once: all five chunks repeat the reply's numbers, so a
    # per-record sum would report 2,075 output tokens for a 415-token reply.
    assert merged.output_tokens == 415


def test_a_call_that_fell_back_names_the_model_it_asked_for(fixture_source: SourceFactory):
    """When Claude Code retries a request on another model, the call says which one it wanted.

    The reply records only the model that answered, so without the `fallback` block a
    forced downgrade reads as a deliberate model choice.
    """
    trace = ClaudeCodeExtractor().extract(fixture_source("server_tools", SERVER_TOOLS))

    # If a reply carries a `fallback` block...
    fell_back = next(c for c in trace.api_calls if c.id == "msg_011Ccua7MYguu6rjoiKNhYVh")

    # ...then the call reports the model that answered and the one first asked for...
    assert (fell_back.model, fell_back.fallback_from) == ("claude-opus-4-8", "claude-fable-5")
    # ...and every ordinary call says it fell back from nothing.
    assert [c.fallback_from for c in trace.api_calls if c.id != fell_back.id] == [None, None, None]


def test_a_session_older_than_a_field_reports_it_absent(fixture_source: SourceFactory):
    """A session recorded before `entrypoint` existed extracts with that column null.

    The corpus reaches back to Claude Code 1.0.128, and two of its 575 sessions predate
    the field. Requiring it crashes the whole extract on the oldest sessions we have.
    """
    trace = ClaudeCodeExtractor().extract(
        fixture_source("legacy_entrypoint", "4b443ab7-98f8-4c1d-859f-9bdcafbabdd3")
    )

    # If the record carrying the session's context has no `entrypoint`...
    assert trace.session.entrypoint is None
    # ...then everything beside it still lands, from that same record...
    assert (trace.session.version, trace.session.git_branch) == ("1.0.128", "fixture-branch-1")
    # ...and the reply, which carries neither `effort` nor `attributionSkill`, parses too.
    call = trace.api_calls[0]
    assert (call.effort, call.attribution_skill, call.stop_reason) == (None, None, None)
    assert call.cache_read_tokens == 95331


def test_machine_records_are_archived_but_never_turns(fixture_source: SourceFactory):
    """The XML Claude Code writes to itself — notifications, shell echoes — is not a prompt.

    An unfiltered turn rule counts these, which is the ~3.6x turn inflation the prior
    importer shipped: 2,157 `<task-notification>` records against 968 real prompts.
    """
    trace = ClaudeCodeExtractor().extract(fixture_source("spine", SPINE))

    # If the fixture holds a notification, a shell echo, a bash prompt and its output...
    machine = {
        "<task-notification>": 0,
        "<local-command-stdout>": 0,
        "<bash-input>": 0,
        "<bash-stdout>": 0,
    }
    for record in trace.raw_records:
        for tag in machine:
            if tag in record.raw:
                machine[tag] += 1
    assert machine == dict.fromkeys(machine, 1)
    # ...then each is archived, and none of them opened a turn.
    assert not [turn for turn in trace.turns if turn.prompt.startswith("<task")]
    assert len([turn for turn in trace.turns if turn.source == MAIN_SOURCE]) == 4


def test_a_meta_record_is_not_a_turn(fixture_source: SourceFactory):
    """A caveat Claude Code injects on the user's behalf is marked `isMeta` and never a turn.

    It also carries a tag no registry lists, so the meta filter has to run first.
    """
    trace = ClaudeCodeExtractor().extract(fixture_source("spine", SPINE))

    assert "<local-command-caveat>" in "".join(r.raw for r in trace.raw_records)
    assert not [turn for turn in trace.turns if "caveat" in turn.prompt]


def test_a_tool_result_block_is_not_a_turn(fixture_source: SourceFactory):
    """A `user` record carrying a tool result is the transcript's plumbing, not a prompt."""
    trace = ClaudeCodeExtractor().extract(fixture_source("spine", SPINE))

    assert not [turn for turn in trace.turns if "tool_result" in turn.prompt]


def test_the_cache_split_is_absent_rather_than_zero(fixture_source: SourceFactory):
    """When a reply reports no cache-creation split, the two TTL columns are null, not zero.

    INVENTED fixture: every assistant record in the mycelia corpus carries
    `usage.cache_creation`, so nothing recorded shows the absent shape. See
    `fixtures/invented/README.md` — this pins a behaviour we chose, not one we observed.
    """
    trace = ClaudeCodeExtractor().extract(fixture_source("invented", "invented-no-cache-creation"))

    call = trace.api_calls[0]
    assert (call.cache_5m_tokens, call.cache_1h_tokens) == (None, None)
    # ...while the total the record does report still lands.
    assert call.cache_creation_tokens == 100


def test_every_record_type_the_corpus_holds_parses(fixture_source: SourceFactory):
    """One redacted record of every live type and system subtype extracts without a crash.

    The registry's completeness is what sank the design's first revision. This fixture is
    its only regression net in the suite — it cannot prove the live corpus grew a new type,
    which is a gap the testing plan records rather than papers over.
    """
    # If a file holds one record of every type and subtype the census found...
    trace = ClaudeCodeExtractor().extract(fixture_source("registry_zoo", ZOO))

    # ...then extraction returns, and every line lands in the archive with its type intact.
    assert len(trace.raw_records) == 33
    types = {record.type for record in trace.raw_records}
    assert "worktree-state" in types and "fork-context-ref" in types and "summary" in types
    assert len([r for r in trace.raw_records if r.type == "system"]) == 10


def test_the_notice_that_the_harness_switched_models_is_archived_only(
    fixture_source: SourceFactory,
):
    """When Claude Code falls back to another model for the session, it says so and nothing more.

    The notice is a UI message about the harness, not about the work: it opens no turn,
    answers no call, and has no children. The archive keeps it; no parsed table does.
    """
    trace = ClaudeCodeExtractor().extract(fixture_source("registry_zoo", ZOO))

    # If a session records the harness swapping the model it was asked for...
    archived = [record for record in trace.raw_records if record.uuid == CONSENT_FALLBACK]
    assert [record.type for record in archived] == ["system"]
    # ...then the whole record is archived, carrying what was swapped and whether it stuck...
    recorded = json.loads(archived[0].raw)
    assert recorded["subtype"] == "model_consent_fallback"
    assert (recorded["originalModel"], recorded["fallbackModel"]) == (
        "claude-fable-5",
        "claude-opus-5[1m]",
    )
    assert recorded["persistedAsDefault"] is False
    # ...and no parsed row is keyed by it.
    parsed = (
        trace.turns,
        trace.api_calls,
        trace.tool_calls,
        trace.agent_runs,
        trace.compactions,
    )
    assert CONSENT_FALLBACK not in {row.id for rows in parsed for row in rows}


def test_a_duplicate_uuid_resolves_to_its_last_occurrence(fixture_source: SourceFactory):
    """When a rewind rewrites a record under the same uuid, the file's final word wins.

    Keep-first and keep-last give different token totals on four real sessions, so the
    policy decides what the DB reports, not just which row it stores.
    """
    # If a session rewound, rewriting five records under uuids it had already used...
    trace = ClaudeCodeExtractor().extract(fixture_source("dup_uuid", DUPS))

    # ...then each contributes one row, carrying the second occurrence's values — the
    # rewritten branch on the session...
    assert trace.session.git_branch == "fixture-branch-3"
    # ...and the rewritten usage on the API call, which the first occurrence reported as
    # 3237 cache-creation and 2629 output tokens.
    assert len(trace.api_calls) == 1
    call = trace.api_calls[0]
    assert (call.cache_creation_tokens, call.output_tokens, call.cache_read_tokens) == (0, 0, 0)
    # ...while the archive keeps both occurrences, since it is the schema-archaeology copy.
    assert len(trace.raw_records) == 10


def test_a_compact_summary_is_not_a_turn(fixture_source: SourceFactory):
    """The summary Claude Code writes into the transcript after compacting is not a prompt."""
    trace = ClaudeCodeExtractor().extract(fixture_source("dup_uuid", DUPS))

    assert trace.turns == []


def test_an_unknown_record_type_crashes_without_quoting_the_record(
    fixture_source: SourceFactory,
):
    """A type we do not handle is a schema change to surface, and the message stays clean.

    INVENTED fixture — every type in the corpus is registered, which is the registry's
    whole claim. The message is the one place a private transcript could reach a log, so
    the fixture plants a payload the crash must not repeat.
    """
    with pytest.raises(TranscriptSchemaError) as excinfo:
        ClaudeCodeExtractor().extract(fixture_source("invented", "invented-unknown-type"))

    message = str(excinfo.value)
    assert "telepathy" in message and "line 2" in message
    assert "invented-unknown-type" in message
    assert "SUPER-SECRET-PAYLOAD-9f2a" not in message


def test_an_unknown_system_subtype_crashes(fixture_source: SourceFactory):
    """A `system` record whose subtype is new is as much a schema change as a new type.

    INVENTED fixture — all nine live subtypes are registered.
    """
    with pytest.raises(TranscriptSchemaError) as excinfo:
        ClaudeCodeExtractor().extract(fixture_source("invented", "invented-unknown-subtype"))

    message = str(excinfo.value)
    assert "quantum_flux" in message and "line 2" in message
    assert "SUPER-SECRET-PAYLOAD-9f2a" not in message


def test_an_unknown_content_block_crashes(fixture_source: SourceFactory):
    """A message content block of a kind we do not read stops the run.

    INVENTED fixture — the eight block kinds the corpus holds are registered. Without the
    crash a new kind is invisible: that is how 45 `server_tool_use` calls sat unread, and
    an analysis would have reported the sessions used no server-side tools.
    """
    with pytest.raises(TranscriptSchemaError) as excinfo:
        ClaudeCodeExtractor().extract(fixture_source("invented", "invented-unknown-block"))

    message = str(excinfo.value)
    assert "clairvoyance" in message and "line 2" in message
    assert "SUPER-SECRET-PAYLOAD-9f2a" not in message


def test_a_novel_prompt_tag_crashes(fixture_source: SourceFactory):
    """A prompt leading with an unregistered tag stops the run rather than being guessed at.

    INVENTED fixture — the tag census closed over every main and subagent transcript.
    Without the crash, the next notification type silently re-inflates the turn counts.
    """
    with pytest.raises(TranscriptSchemaError) as excinfo:
        ClaudeCodeExtractor().extract(fixture_source("invented", "invented-novel-tag"))

    message = str(excinfo.value)
    assert "sparkle-notice" in message
    assert "SUPER-SECRET-PAYLOAD-9f2a" not in message


def test_a_record_with_no_timestamp_crashes_naming_the_kind_it_was(
    fixture_source: SourceFactory,
):
    """The crash names the record kind that was missing a timestamp, not a guess at it.

    INVENTED fixture — of the 678,793 records on the recording machine that reach this raise,
    none is missing a timestamp, so it has no recorded example (`fixtures/invented/README.md`).
    The message is the whole value of a fail-fast crash, and eight parse paths reach it:
    compactions, pr links, api calls and tool calls among them.
    """
    with pytest.raises(TranscriptSchemaError) as excinfo:
        ClaudeCodeExtractor().extract(fixture_source("invented", "invented-no-timestamp"))

    message = str(excinfo.value)
    assert "pr-link" in message and "line 2" in message
    # The old message called every one of them a prompt, and this record is not one.
    assert "prompt" not in message
    assert "SUPER-SECRET-PAYLOAD-9f2a" not in message


def test_a_duplicate_uuid_whose_content_differs_crashes(fixture_source: SourceFactory):
    """Two records under one uuid may differ in their envelope, never in what was said.

    INVENTED fixture — 995 duplicate pairs exist in the corpus and none differs in
    content. A difference would mean the conversation itself was rewritten, which
    last-occurrence-wins would quietly accept.
    """
    with pytest.raises(TranscriptSchemaError) as excinfo:
        ClaudeCodeExtractor().extract(fixture_source("invented", "invented-dup-content-diff"))

    assert "33333333-3333-4333-8333-333333333333" in str(excinfo.value)
