"""Subagent transcripts: the work a session delegated, parsed like the work it did itself.

A subagent writes its own file under the session's `subagents/`, in the same record shapes
the main transcript uses. Every row it produces carries the agent's id as its `source`, so
one session's rows stay separable by who did the work.
"""

from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.model import MAIN_SOURCE, SessionTrace
from tests.conftest import SourceFactory
from tests.extract.test_claude_code import SPINE, at

# The subagent `spine/` spawned, and the workflow session with its one fan-out agent.
SPINE_AGENT = "ac461ef46b4bb8e32"
WORKFLOW = "8d930c77-9e60-4784-9885-6d4c226280f7"
WORKFLOW_AGENT = "a6f04bb0e6eff6013"


def trace(fixture_source: SourceFactory, directory: str, stem: str) -> SessionTrace:
    return ClaudeCodeExtractor().extract(fixture_source(directory, stem))


def test_a_subagents_rows_are_sourced_by_its_agent_id(fixture_source: SourceFactory):
    """The delegated work parses like any other, under the agent's id rather than "main"."""
    # If a session spawned a subagent that ran one tool...
    extracted = trace(fixture_source, "spine", SPINE)
    turns = [turn for turn in extracted.turns if turn.source == SPINE_AGENT]
    calls = [call for call in extracted.api_calls if call.source == SPINE_AGENT]
    tools = [tool for tool in extracted.tool_calls if tool.source == SPINE_AGENT]

    # ...then its transcript yields the same three kinds of row the main one does...
    assert (len(turns), len(calls), len(tools)) == (2, 1, 1)
    # ...its two assistant chunks merge into the one call they shared a message id for...
    assert (calls[0].id, calls[0].turn_id) == ("msg_011CdmTpsWWekY9vCQNRhnDj", turns[0].id)
    assert (tools[0].name, tools[0].api_call_id) == ("Bash", calls[0].id)
    # ...and the rows are scoped to the session that spawned it, not to a session of its own.
    assert {turns[0].session_id, calls[0].session_id, tools[0].session_id} == {SPINE}
    # The main transcript's own rows are untouched by the extra source.
    assert [turn.index for turn in extracted.turns if turn.source == MAIN_SOURCE] == [0, 1, 2, 3]


def test_a_delegated_prompt_opens_a_turn(fixture_source: SourceFactory):
    """Inside a subagent transcript the `isSidechain` exclusion does not apply.

    Every record of a subagent file is `isSidechain: true` — the flag marks delegated work
    in the *main* transcript, where the subagent's own records would double-count it. Read
    as an exclusion here, it would leave every subagent turnless.
    """
    # If the delegating prompt is the subagent transcript's first record...
    turn = next(
        turn for turn in trace(fixture_source, "spine", SPINE).turns if turn.source == SPINE_AGENT
    )

    # ...then it opens the agent's first turn, which runs to the end of its work.
    assert (turn.index, turn.prompt) == (0, "[redacted]")
    assert (turn.started_at, turn.ended_at) == (
        at("2026-08-06T12:04:25.042"),
        at("2026-08-06T12:04:28.117"),
    )


def test_a_teammate_message_opens_a_turn(fixture_source: SourceFactory):
    """An instruction from another agent drives work exactly as a user's prompt does.

    Only subagent transcripts hold these — a census of main transcripts never sees the tag,
    and the parser crashes on an unregistered one, so an unteamed corpus hides it until a
    session uses teams.
    """
    # If a teammate sent an instruction mid-run...
    turns = [
        turn for turn in trace(fixture_source, "spine", SPINE).turns if turn.source == SPINE_AGENT
    ]

    # ...then it opens a turn of its own, carrying the message as recorded — attributes on
    # the tag and all, which is how these differ from every other registered tag.
    assert len(turns) == 2
    assert turns[1].prompt.startswith('<teammate-message teammate_id="team-lead"')
    assert (turns[1].command_name, turns[1].command_args) == (None, None)


def test_a_workflow_agent_parses_like_any_other(fixture_source: SourceFactory):
    """An agent of a parallel fan-out is a subagent that sits one directory deeper."""
    # If a workflow ran an agent...
    extracted = trace(fixture_source, "workflow", WORKFLOW)
    calls = [call for call in extracted.api_calls if call.source == WORKFLOW_AGENT]

    # ...its records parse under its own agent id, the extra directory notwithstanding...
    assert len(calls) == 1
    assert calls[0].id == "msg_011CcxQueXXAiVxM6LKvY7Ya"
    # ...and the journal beside it stays an archive, contributing no calls of its own.
    assert {call.source for call in extracted.api_calls} == {WORKFLOW_AGENT}
