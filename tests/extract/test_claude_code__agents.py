"""Subagent transcripts: the work a session delegated, parsed like the work it did itself.

A subagent writes its own file under the session's `subagents/`, in the same record shapes
the main transcript uses. Every row it produces carries the agent's id as its `source`, so
one session's rows stay separable by who did the work, and an `AgentRun` beside them says
who asked for the work.
"""

import logging

import pytest

from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.model import MAIN_SOURCE, AgentRun, SessionTrace
from tests.conftest import FIXTURES, PlantedFactory, SourceFactory
from tests.extract.test_claude_code import SPINE, at

# The subagent `spine/` spawned, the subagent *it* spawned in turn, and the workflow
# session with its one fan-out agent.
SPINE_AGENT = "ac461ef46b4bb8e32"
NESTED_AGENT = "af6473ae437c9608d"
WORKFLOW = "8d930c77-9e60-4784-9885-6d4c226280f7"
WORKFLOW_RUN = "wf_c30cc877-997"
WORKFLOW_AGENT = "a6f04bb0e6eff6013"
# The teammate session, and the architect it ran as a long-lived teammate.
TEAM = "10d0349d-0705-4e23-aa64-5b1b97698b2e"
TEAM_AGENT = "aarchitect-5144001ac50718bc"


def trace(fixture_source: SourceFactory, directory: str, stem: str) -> SessionTrace:
    return ClaudeCodeExtractor().extract(fixture_source(directory, stem))


def test_a_subagents_rows_are_sourced_by_its_agent_id(fixture_source: SourceFactory):
    """The delegated work parses like any other, under the agent's id rather than "main"."""
    # If a session spawned a subagent that ran a tool and delegated further...
    extracted = trace(fixture_source, "spine", SPINE)
    turns = [turn for turn in extracted.turns if turn.source == SPINE_AGENT]
    calls = [call for call in extracted.api_calls if call.source == SPINE_AGENT]
    tools = [tool for tool in extracted.tool_calls if tool.source == SPINE_AGENT]

    # ...then its transcript yields the same three kinds of row the main one does...
    assert (len(turns), len(calls), len(tools)) == (1, 2, 3)
    # ...its two assistant chunks merge into the one call they shared a message id for...
    assert (calls[0].id, calls[0].turn_id) == ("msg_011CdmTpsWWekY9vCQNRhnDj", turns[0].id)
    assert [tool.name for tool in tools] == ["Bash", "Agent", "Agent"]
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

    # ...then it opens the agent's only turn, which runs to the end of its work.
    assert (turn.index, turn.prompt) == (0, "[redacted]")
    assert (turn.started_at, turn.ended_at) == (
        at("2026-08-06T12:04:25.042"),
        at("2026-08-06T12:09:15.651"),
    )


def test_a_teammate_message_opens_a_turn(fixture_source: SourceFactory):
    """An instruction from another agent drives work exactly as a user's prompt does.

    Only subagent transcripts hold these — a census of main transcripts never sees the tag,
    and the parser crashes on an unregistered one, so an unteamed corpus hides it until a
    session uses teams.
    """
    # If a teammate agent's transcript opens on its team lead's instruction...
    turns = [
        turn for turn in trace(fixture_source, "teammate", TEAM).turns if turn.source != MAIN_SOURCE
    ]

    # ...then that instruction is its first turn, carried as recorded — attributes on the
    # tag and all, which is how these differ from every other registered tag.
    assert len(turns) == 1
    assert turns[0].prompt.startswith('<teammate-message teammate_id="team-lead"')
    assert (turns[0].command_name, turns[0].command_args) == (None, None)


def test_a_subagent_run_names_the_call_that_spawned_it(fixture_source: SourceFactory):
    """A subagent's `meta.json` is the link from the delegated work back to the ask."""
    # If a session delegated work with the `Agent` tool...
    runs = trace(fixture_source, "spine", SPINE).agent_runs

    # ...then the run it started is a row of its own, keyed within the session...
    assert next(run for run in runs if run.id == SPINE_AGENT) == AgentRun(
        id=SPINE_AGENT,
        session_id=SPINE,
        parent_agent_id=None,
        # ...naming the `Agent` tool call that asked for it, which the session's own tool
        # calls hold under the same id...
        tool_use_id="toolu_015dP3eMe5GZn7BzFipupZwS",
        agent_type="claude",
        description="[redacted]",
        model="opus",
        workflow_id=None,
        spawn_depth=1,
        # ...continuing no one else's conversation...
        is_fork=False,
        fork_context_uuid=None,
        # ...and spanning its own transcript, which starts a beat after the call.
        started_at=at("2026-08-06T12:04:25.042"),
        ended_at=at("2026-08-06T12:09:15.651"),
    )
    spawning = next(
        call
        for call in trace(fixture_source, "spine", SPINE).tool_calls
        if call.id == "toolu_015dP3eMe5GZn7BzFipupZwS"
    )
    assert (spawning.name, spawning.source) == ("Agent", MAIN_SOURCE)


def test_a_nested_run_names_its_parent(fixture_source: SourceFactory):
    """A subagent's own subagent records the agent above it, not just the session."""
    # If a subagent delegated in turn...
    run = next(
        run for run in trace(fixture_source, "spine", SPINE).agent_runs if run.id == NESTED_AGENT
    )

    # ...then its run hangs off the agent that spawned it, one level deeper...
    assert (run.parent_agent_id, run.spawn_depth) == (SPINE_AGENT, 2)
    # ...and the spawning call is one of that agent's, not the session's.
    assert run.tool_use_id == "toolu_01SpzLooq2oJd72pCg4Jmq6v"
    # `model` is absent from this run's meta — the caller named none — and absence is a
    # state, not a parse failure.
    assert run.model is None


def test_a_workflow_agent_joins_by_its_run_directory(fixture_source: SourceFactory):
    """A fan-out's agents link to the `Workflow` call through the run id it reported.

    Their own metas carry no `toolUseId` — nothing spawned them one by one. What names them
    is the `wf_<id>` directory they sit in, which the launching call's result reports as
    its `runId`.
    """
    # If a session launched a workflow that ran an agent...
    extracted = trace(fixture_source, "workflow", WORKFLOW)
    run = next(run for run in extracted.agent_runs if run.id == WORKFLOW_AGENT)

    # ...then the run carries the fan-out it belonged to...
    assert (run.workflow_id, run.agent_type, run.spawn_depth) == (
        WORKFLOW_RUN,
        "workflow-subagent",
        1,
    )
    # ...and the `Workflow` call that launched the fan-out stands as its spawning call.
    launch = next(call for call in extracted.tool_calls if call.id == run.tool_use_id)
    assert (launch.name, launch.source) == ("Workflow", MAIN_SOURCE)


def test_a_workflow_agent_parses_like_any_other(fixture_source: SourceFactory):
    """An agent of a parallel fan-out is a subagent that sits one directory deeper."""
    # If a workflow ran an agent...
    extracted = trace(fixture_source, "workflow", WORKFLOW)
    calls = [call for call in extracted.api_calls if call.source == WORKFLOW_AGENT]

    # ...its records parse under its own agent id, the extra directory notwithstanding...
    assert len(calls) == 1
    assert calls[0].id == "msg_011CcxQueXXAiVxM6LKvY7Ya"
    # ...and the journal beside it stays an archive, contributing no calls of its own.
    assert {call.source for call in extracted.api_calls} == {MAIN_SOURCE, WORKFLOW_AGENT}


def test_a_meta_that_names_no_depth_says_so_rather_than_guessing(
    planted_source: PlantedFactory,
):
    """A run whose meta omits `spawnDepth` has no depth, not depth zero.

    One meta of the 2764 on this machine leaves the key out (scanned 2026-08-07):
    `-Users-nob-repos-mac-settings/c31ecec9-.../subagents/agent-a20276f6d8a4e5309.meta.json`,
    **Claude Code 2.1.186**. Its shape is planted here rather than added as a fixture tree —
    the missing key is the whole record. `description` is redacted as every fixture's is.
    """
    # If a session holds a subagent whose meta names its type and its spawning call, and
    # nothing else...
    recorded = FIXTURES / "spine" / SPINE / "subagents" / f"agent-{NESTED_AGENT}.jsonl"
    source = planted_source(
        "spine",
        SPINE,
        {
            f"subagents/agent-{NESTED_AGENT}.jsonl": recorded.read_text(),
            f"subagents/agent-{NESTED_AGENT}.meta.json": (
                '{"agentType": "claude-code-guide", "description": "[redacted]", '
                '"toolUseId": "toolu_01Wibfj3Q3njBXyH76pSf1hk"}'
            ),
        },
    )

    # ...then the run reports the depth as unknown, rather than reading absence as the top.
    (run,) = ClaudeCodeExtractor().extract(source).agent_runs
    assert (run.id, run.agent_type, run.spawn_depth) == (
        NESTED_AGENT,
        "claude-code-guide",
        None,
    )


def test_a_teammate_run_is_an_orphan_and_says_so(
    fixture_source: SourceFactory, caplog: pytest.LogCaptureFixture
):
    """A run with no spawning call is exported anyway, loudly.

    A teammate is started by the team mechanism rather than by a tool call, so its meta
    carries no `toolUseId` and nothing in the transcript points at it. Dropping such a run
    would hide a whole delegated workload: the prior importer reported 100% direct tool
    calls that way.
    """
    # If a session ran a long-lived teammate...
    with caplog.at_level(logging.WARNING):
        run = next(run for run in trace(fixture_source, "teammate", TEAM).agent_runs)

    # ...then the run is recorded with no call behind it, at the depth teams start at...
    assert (run.id, run.tool_use_id, run.spawn_depth) == (TEAM_AGENT, None, 0)
    assert (run.agent_type, run.model) == ("architect", "fable")
    # ...and the gap is announced, naming the run so it can be looked up.
    assert TEAM_AGENT in caplog.text
    assert TEAM in caplog.text
