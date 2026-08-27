"""Forks: agents that continue a conversation someone else started.

Claude Code writes a fork two ways. A **copied-history** fork replays its parent's records
into its own file, verbatim uuids and all, then carries on; a **by-reference** fork copies
nothing and opens mid-conversation, naming the context it inherited. Both wreck a count
that assumes a uuid belongs to one transcript, so the extractor decides who owns a record
and flags every later copy as `replayed`.
"""

import json

import pytest

from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.extract.record_types import TranscriptSchemaError
from aiobserve.model import SessionTrace
from tests.conftest import FIXTURES, PlantedFactory, SourceFactory
from tests.extract.test_claude_code import at

# The session whose fork copied a sibling's history: the auditor that did the work first,
# and the fork spawned from it. Both sit at the same first timestamp — the fork's opening
# record *is* the auditor's, so depth is what separates them.
ORIGIN = "5a88789c-1da7-4f32-b631-40a7e243334b"
AUDITOR = "acbc29008a04b9702"
FORK = "a61a059e3610e6fb4"
COPIED_MESSAGE = "msg_011CdFxfStgUUn3Q59b4RFii"
# The session whose fork opened by reference instead.
BYREF = "07a769d7-828c-4edb-b3ce-af51e2712aa3"
BYREF_FORK = "afa3946951a08a798"


def trace(fixture_source: SourceFactory, directory: str, stem: str) -> SessionTrace:
    return ClaudeCodeExtractor().extract(fixture_source(directory, stem))


def test_a_copied_record_belongs_to_the_transcript_that_ran_it(fixture_source: SourceFactory):
    """A fork's replay of its parent's work is marked as a replay, and the parent keeps it.

    Flagging both sides instead would zero-count work that really happened — the copied
    message here is the auditor's, and it must stay countable somewhere.
    """
    # If a fork replayed the transcript it was spawned from and then carried on...
    extracted = trace(fixture_source, "fork_origin", ORIGIN)
    calls = {
        (call.source, call.id): call.replayed
        for call in extracted.api_calls
        if call.source in (AUDITOR, FORK)
    }

    # ...then the copied message counts under the transcript that ran it first...
    assert calls == {
        (AUDITOR, COPIED_MESSAGE): False,
        # ...and as a replay under the fork that inherited it...
        (FORK, COPIED_MESSAGE): True,
        # ...while everything the fork went on to do is its own.
        (FORK, "msg_011CdFxjoNbXw31ASkCpKqdz"): False,
        (FORK, "msg_011CdFxq21kNYhF6hTn6oE95"): False,
    }
    # The same holds for the turn both files open on, and for the tools the copy repeats.
    turns = {(turn.source, turn.replayed) for turn in extracted.turns if turn.source != "main"}
    assert turns == {(AUDITOR, False), (FORK, True)}
    tools = {tool.id for tool in extracted.tool_calls if tool.source == FORK and tool.replayed}
    assert tools == {tool.id for tool in extracted.tool_calls if tool.source == AUDITOR}
    # No row is flagged on both sides, which is what would make the work vanish.
    assert not [call for call in extracted.api_calls if call.source == AUDITOR and call.replayed]


def test_a_forks_run_starts_when_its_own_work_does(fixture_source: SourceFactory):
    """A fork's run begins at its first fresh record, not at the copied history's start."""
    # If a fork's file opens with 18 seconds of someone else's conversation...
    extracted = trace(fixture_source, "fork_origin", ORIGIN)
    runs = {run.id: run for run in extracted.agent_runs}

    # ...then its run starts where the copying stops...
    assert runs[FORK].started_at == at("2026-07-21T22:05:03.221")
    assert runs[FORK].ended_at == at("2026-07-21T22:08:02.177")
    # ...which is after the transcript it copied from even began...
    assert runs[AUDITOR].started_at == at("2026-07-21T22:04:45.578")
    # ...and the meta says which of the two is the fork. Nothing was inherited by
    # reference, so there is no context to point at.
    assert (runs[FORK].is_fork, runs[FORK].fork_context_uuid) == (True, None)
    assert (runs[AUDITOR].is_fork, runs[AUDITOR].fork_context_uuid) == (False, None)


def test_a_by_reference_fork_opens_mid_conversation(fixture_source: SourceFactory):
    """A fork that inherits context without copying it names the record it continues from.

    Its transcript starts with an answer to a prompt that lives in another file, so the
    work before its first local prompt belongs to no turn of its own — reading a turn in
    would attribute the whole fork to a prompt it never received.
    """
    # If a fork opened by reference...
    extracted = trace(fixture_source, "fork_byref", BYREF)
    (run,) = extracted.agent_runs

    # ...then it names the conversation and the record it picked up from...
    assert (run.is_fork, run.fork_context_uuid) == (True, "97e2004c-f9f6-48ac-add8-0eef6026d3f9")
    # ...nothing is a replay, because nothing was copied...
    assert not [call for call in extracted.api_calls if call.replayed]
    # ...and its calls hang off no turn, the transcript holding no prompt to open one.
    assert [call.turn_id for call in extracted.api_calls if call.source == BYREF_FORK] == [
        None,
        None,
    ]
    assert not [turn for turn in extracted.turns if turn.source == BYREF_FORK]


def test_a_transcript_that_replays_another_must_be_a_fork(planted_source: PlantedFactory):
    """A transcript repeating another's records without being a fork stops the run.

    Copying is a fork's doing; anywhere else it means the ordering rule put the wrong
    transcript first, and every count downstream would be attributed to the wrong agent.
    The `isFork` flag is dropped from a recorded fork's meta here — a planted change, since
    all 51 overlapping pairs on this machine have a fork on one side (scanned 2026-08-07).
    """
    # If the transcript that replays another does not admit to being a fork...
    recorded = FIXTURES / "fork_origin" / ORIGIN / "subagents"
    meta = json.loads((recorded / f"agent-{FORK}.meta.json").read_text())
    del meta["isFork"]
    meta["agentType"] = "auditor"
    source = planted_source(
        "fork_origin",
        ORIGIN,
        {
            f"subagents/agent-{AUDITOR}.jsonl": (recorded / f"agent-{AUDITOR}.jsonl").read_text(),
            f"subagents/agent-{AUDITOR}.meta.json": (
                recorded / f"agent-{AUDITOR}.meta.json"
            ).read_text(),
            f"subagents/agent-{FORK}.jsonl": (recorded / f"agent-{FORK}.jsonl").read_text(),
            f"subagents/agent-{FORK}.meta.json": json.dumps(meta),
        },
    )

    # ...then extraction stops, naming the transcript and one of the records it repeated.
    with pytest.raises(TranscriptSchemaError) as raised:
        ClaudeCodeExtractor().extract(source)
    assert FORK in str(raised.value)
    assert "33438141-776f-4e1e-9bc5-e5d85df18d22" in str(raised.value)
