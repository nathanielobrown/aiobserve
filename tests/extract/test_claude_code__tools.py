"""Tool calls: pairing a `tool_use` block with the result that answered it.

The fixtures are the same redacted mycelia sessions the rest of the extractor tests use;
`tests/fixtures/*/README.md` names each source session and its Claude Code version.
"""

from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.model import MAIN_SOURCE, ToolCall
from tests.conftest import SourceFactory
from tests.extract.test_claude_code import SPINE, at

# The one call in `spine/` whose result record survived the trim, and the message that
# issued it alongside two others.
ANSWERED = "toolu_01GzkcnijJv7xLcXGBsKivfz"
BATCH = "msg_011CdmMjFXDofyYSMxYtXa5n"
# A message that issued a single call. The recorded session answered it; `spine/` ends at
# the call, standing in for the sessions that really do end mid-call.
LONE = "toolu_01B6iTUMs3YrNvULzgRkwuar"
# The `offload/` session, and the one Bash call whose output Claude Code moved to a file.
OFFLOAD = "7e37bb35-4dcb-4e16-85be-55ac510c168e"
OFFLOADED = "toolu_01JXs55LXLHvzWt8KczuYfyD"
# The `server_tools/` session and its three `advisor` calls: one the service refused, one
# whose answer came back encrypted, and one issued alongside two local calls and never
# answered. See `tests/fixtures/server_tools/README.md`.
SERVER_TOOLS = "088d63aa-71d3-4108-965e-5147e3eaddbd"
REFUSED = "srvtoolu_01KUMaS97sNkE7Z12UW4HMEp"
ENCRYPTED = "srvtoolu_01TK5pPoxEdDu3g975oMijMg"
UNANSWERED = "srvtoolu_01FHMDigqBGzPfr9CkXyA91v"
DELEGATION = "a3b37063695183556"


def calls(fixture_source: SourceFactory, directory: str, stem: str) -> dict[str, ToolCall]:
    trace = ClaudeCodeExtractor().extract(fixture_source(directory, stem))
    return {call.id: call for call in trace.tool_calls}


def test_a_tool_call_carries_its_result(fixture_source: SourceFactory):
    """A `tool_use` block and the `tool_result` record answering it become one row."""
    # If a session issued a tool call and recorded its result...
    call = calls(fixture_source, "spine", SPINE)[ANSWERED]

    # ...then the two halves meet in one row, keyed by the tool_use id...
    assert call == ToolCall(
        id=ANSWERED,
        session_id=SPINE,
        source=MAIN_SOURCE,
        # ...pointing back at the message that issued it...
        api_call_id=BATCH,
        index=1,
        name="Read",
        # ...run locally, as all but the `advisor` tool are...
        server_side=False,
        # ...carrying the arguments as recorded, JSON and all...
        input='{"file_path": "[redacted]"}',
        # ...the flattened result text, which this fixture's redaction replaced...
        result="[redacted]",
        offload_file=None,
        is_error=False,
        incomplete=False,
        # ...starting when its batch was issued and ending when the result landed.
        started_at=at("2026-08-06T10:44:33.136"),
        ended_at=at("2026-08-06T10:44:33.589"),
        duration_synthetic=True,
        replayed=False,
    )


def test_parallel_calls_share_a_start_and_say_so(fixture_source: SourceFactory):
    """Calls issued together in one message report a shared, synthetic start.

    Claude Code writes each `tool_use` block as its own record, in the order it got round
    to running them, so per-record timestamps rank a parallel batch by execution order
    rather than by issue time. The flag is what stops an analysis ranking on that noise.
    """
    # If one assistant message issued three calls...
    batch = [
        call for call in calls(fixture_source, "spine", SPINE).values() if call.api_call_id == BATCH
    ]

    # ...then all three share one start and are flagged as measuring from it...
    assert len(batch) == 3
    assert {call.started_at for call in batch} == {at("2026-08-06T10:44:33.136")}
    assert all(call.duration_synthetic for call in batch)

    # ...while a message that issued a single call reports its own, real start.
    lone = calls(fixture_source, "spine", SPINE)[LONE]
    assert (lone.duration_synthetic, lone.started_at) == (False, at("2026-08-06T18:41:14.084"))


def test_a_call_with_no_result_is_incomplete(fixture_source: SourceFactory):
    """A session that ended before its tool returned still exports the call."""
    # If the transcript holds a `tool_use` with no answering record — here because the
    # fixture stops at the call, which is what a session killed mid-call looks like...
    call = calls(fixture_source, "spine", SPINE)[LONE]

    # ...then the call is there, marked incomplete, with no result and no end.
    assert (call.incomplete, call.result, call.ended_at) == (True, None, None)
    assert (call.name, call.api_call_id) == ("Read", "msg_011Cdmz3NQtuzwN3cqYvvkuN")


def test_a_server_side_tool_call_is_a_call_like_any_other(fixture_source: SourceFactory):
    """A tool Anthropic runs server-side lands in `tool_calls`, flagged as server-side.

    Claude Code records these as `server_tool_use`, answered by a result block inside the
    same message rather than by a user record. Left unread, an analysis would report that
    a session used no server tools at all.
    """
    # If a session called the server-side `advisor` and the service refused...
    call = calls(fixture_source, "server_tools", SERVER_TOOLS)[REFUSED]

    # ...then the call is a row like any other, marked as one we did not run ourselves...
    assert call == ToolCall(
        id=REFUSED,
        session_id=SERVER_TOOLS,
        source=MAIN_SOURCE,
        api_call_id="msg_01QippSuXCLtCz1UguYEA8tN",
        index=1,
        name="advisor",
        server_side=True,
        # ...taking no arguments, as every recorded `advisor` call does...
        input="{}",
        # ...reporting the refusal by its code, since the block carries no text...
        result="unavailable",
        offload_file=None,
        is_error=True,
        incomplete=False,
        # ...and timed from its own record to the record that answered it.
        started_at=at("2026-07-06T18:19:03.233"),
        ended_at=at("2026-07-06T18:19:12.541"),
        duration_synthetic=False,
        replayed=False,
    )

    # And when the answer came back encrypted, the row says the call succeeded and
    # carries no result: the transcript holds nothing readable to carry.
    encrypted = calls(fixture_source, "server_tools", SERVER_TOOLS)[ENCRYPTED]
    assert (encrypted.is_error, encrypted.result, encrypted.incomplete) == (False, None, False)
    assert encrypted.ended_at == at("2026-07-05T20:43:49.574")


def test_a_server_side_call_keeps_its_own_clock_beside_local_ones(fixture_source: SourceFactory):
    """A server-side call in a message full of local calls is not timed as part of their batch.

    Local calls in one message are written in execution order, so the batch shares a
    synthetic start. The server-side call's own record is the request, so it keeps its
    real start and says so.
    """
    # If one message issued two local calls and a server-side call...
    batch = [
        call
        for call in calls(fixture_source, "server_tools", SERVER_TOOLS).values()
        if call.source == DELEGATION
    ]
    local = [call for call in batch if not call.server_side]

    # ...then the two local ones share the batch's synthetic start...
    assert len(local) == 2
    assert {call.started_at for call in local} == {at("2026-07-06T20:22:36.167")}
    assert all(call.duration_synthetic for call in local)

    # ...while the server-side call reports its own, and is incomplete because this
    # message's `advisor` call was never answered — one of the 45 in the corpus is not.
    server = next(call for call in batch if call.server_side)
    assert (server.id, server.started_at, server.duration_synthetic) == (
        UNANSWERED,
        at("2026-07-06T20:22:49.761"),
        False,
    )
    assert (server.incomplete, server.result, server.ended_at) == (True, None, None)


def test_an_offloaded_result_names_the_file_holding_it(fixture_source: SourceFactory):
    """When a tool's output is too big for the transcript, the call points at the file.

    Claude Code writes the full output to `tool-results/` and leaves a preview in the
    record, so `result` alone understates what the tool returned.
    """
    # If a call's output was moved out of the transcript...
    call = calls(fixture_source, "offload", OFFLOAD)[OFFLOADED]

    # ...then the row keeps the preview and names the file, by name and not by the
    # recording machine's absolute path...
    assert (call.result, call.offload_file) == ("[redacted]", "bosvr1kjx.txt")
    # ...and the call is otherwise ordinary: complete, timed, and its own batch.
    assert (call.name, call.is_error, call.incomplete) == ("Bash", False, False)
    assert (call.started_at, call.ended_at, call.duration_synthetic) == (
        at("2026-07-27T14:59:42.004"),
        at("2026-07-27T14:59:45.116"),
        False,
    )
