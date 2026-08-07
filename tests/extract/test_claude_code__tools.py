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
# A message that issued a single call, and whose session ended before the result arrived.
LONE = "toolu_01B6iTUMs3YrNvULzgRkwuar"
# The `offload/` session, and the one Bash call whose output Claude Code moved to a file.
OFFLOAD = "7e37bb35-4dcb-4e16-85be-55ac510c168e"
OFFLOADED = "toolu_01JXs55LXLHvzWt8KczuYfyD"


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
    # If the transcript holds a `tool_use` with no answering record...
    call = calls(fixture_source, "spine", SPINE)[LONE]

    # ...then the call is there, marked incomplete, with no result and no end.
    assert (call.incomplete, call.result, call.ended_at) == (True, None, None)
    assert (call.name, call.api_call_id) == ("Read", "msg_011Cdmz3NQtuzwN3cqYvvkuN")


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
