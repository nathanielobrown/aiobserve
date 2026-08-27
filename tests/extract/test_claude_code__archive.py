"""The archive: every line of every file a session wrote, and its offloaded tool outputs.

Claude Code prunes a session's directory a few weeks after it ends, so what is not
archived here is gone. The fixtures are redacted mycelia sessions; each fixture
directory's README names its source session and Claude Code version.
"""

from collections import Counter
from pathlib import Path

import pytest

from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.extract.record_types import TranscriptSchemaError
from aiobserve.model import MAIN_SOURCE, OffloadFile, SessionTrace
from tests.conftest import FIXTURES, PlantedFactory, SourceFactory
from tests.extract.test_claude_code import SPINE
from tests.extract.test_claude_code__agents import (
    NESTED_AGENT,
    SPINE_AGENT,
    WORKFLOW,
    WORKFLOW_AGENT,
    WORKFLOW_RUN,
)

# The fan-out's journal, sourced by the directory it sits in rather than by an agentId.
JOURNAL = f"{WORKFLOW_RUN}/journal"
# The session that offloaded a tool result, and the file holding it.
OFFLOAD = "7e37bb35-4dcb-4e16-85be-55ac510c168e"
OFFLOADED_FILE = "bosvr1kjx.txt"


def lines_by_source(trace: SessionTrace) -> Counter[str]:
    return Counter(record.source for record in trace.raw_records)


def test_the_archive_holds_every_line_of_every_file(fixture_source: SourceFactory):
    """A session's subagent transcripts are archived beside its own, line for line."""
    source = fixture_source("spine", SPINE)
    trace = ClaudeCodeExtractor().extract(source)

    # If a session spawned a subagent, which spawned one in turn, then all three files
    # are in the archive, each line under the transcript that recorded it — the main one
    # as "main", each subagent's under its bare agentId...
    assert lines_by_source(trace) == Counter({MAIN_SOURCE: 35, SPINE_AGENT: 10, NESTED_AGENT: 6})
    # ...numbered from 1 within its own file, so a row points back at a line...
    agent = [r.line_no for r in trace.raw_records if r.source == SPINE_AGENT]
    assert agent == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    # ...and the two `meta.json` files the walk also found became no source at all: they
    # are linkage that agent runs read, not records.
    assert len(source.files) == 5


def test_a_workflow_run_archives_its_journal_and_its_agents(fixture_source: SourceFactory):
    """A parallel fan-out's agents and the journal tracking them are archived too."""
    trace = ClaudeCodeExtractor().extract(fixture_source("workflow", WORKFLOW))

    # If a session fanned out into a workflow, then its agents are sourced by agentId as
    # any other subagent is, and the journal by its workflow directory...
    assert lines_by_source(trace) == Counter({MAIN_SOURCE: 8, WORKFLOW_AGENT: 6, JOURNAL: 4})
    # ...carrying the two record types only journals hold.
    assert {r.type for r in trace.raw_records if r.source == JOURNAL} == {"started", "result"}


def test_a_bookkeeping_record_is_archived_without_a_uuid_or_a_time(fixture_source: SourceFactory):
    """Records that carry neither an id nor a timestamp still reach the archive."""
    trace = ClaudeCodeExtractor().extract(fixture_source("workflow", WORKFLOW))

    # If a transcript opens on editor-state records — as this one does, on four of them...
    opening = [r for r in trace.raw_records if r.source == MAIN_SOURCE][:4]

    # ...then each is archived with its type and its raw line, and nothing else to say.
    assert [r.type for r in opening] == [
        "mode",
        "permission-mode",
        "bridge-session",
        "file-history-snapshot",
    ]
    assert all(r.uuid is None and r.timestamp is None for r in opening)


def test_an_offloaded_output_is_archived_whole(fixture_source: SourceFactory):
    """The file holding a tool's full output is stored with the session, not pointed at."""
    trace = ClaudeCodeExtractor().extract(fixture_source("offload", OFFLOAD))
    recorded = FIXTURES / "offload" / OFFLOAD / "tool-results" / OFFLOADED_FILE

    # If a session moved a tool result out of its transcript, then the file comes along
    # whole, named as `ToolCall.offload_file` names it.
    assert trace.offload_files == [
        OffloadFile(
            session_id=OFFLOAD,
            name=OFFLOADED_FILE,
            # Verbose, so lifted from the fixture: the point is that it is the file's
            # text, not the transcript's preview of it.
            content=recorded.read_text(),
            lossy_decode=False,
            size_bytes=recorded.stat().st_size,
        )
    ]
    assert trace.tool_calls[0].offload_file == OFFLOADED_FILE


def test_an_output_that_is_not_text_is_archived_anyway(
    tmp_path: Path, planted_source: PlantedFactory
):
    """A binary tool output is kept, flagged as decoded lossily rather than dropped.

    WebFetch persists PDFs here, and output cut mid-character lands the same way — nine
    files of the mycelia corpus (scanned 2026-08-07). Invented bytes: the point is the
    decode, and no recorded example is redactable.
    """
    offloaded = tmp_path / SPINE / "tool-results" / "fetched.pdf"
    offloaded.parent.mkdir(parents=True)
    offloaded.write_bytes(b"%PDF-\xff\xfe\x00")
    source = planted_source("spine", SPINE, {})

    # If a session offloaded output that is not UTF-8...
    offload = ClaudeCodeExtractor().extract(source).offload_files[0]

    # ...then it is archived at its true size, with the loss declared.
    assert (offload.name, offload.lossy_decode, offload.size_bytes) == ("fetched.pdf", True, 8)
    assert offload.content.startswith("%PDF-")


def test_a_workflow_definition_is_not_a_transcript(planted_source: PlantedFactory):
    """The workflow scripts a session stores beside its runs are not parsed as records."""
    # If a session ran a workflow, it keeps the definition and the script that drove it...
    source = planted_source(
        "spine",
        SPINE,
        {
            "workflows/wf_c30cc877-997.json": "{}",
            "workflows/scripts/deep-research-wf_c30cc877-997.js": "//",
        },
    )

    # ...and neither reaches the archive, which would choke on them as JSON lines.
    trace = ClaudeCodeExtractor().extract(source)
    assert set(lines_by_source(trace)) == {MAIN_SOURCE}


def test_an_unknown_file_in_a_session_directory_crashes(planted_source: PlantedFactory):
    """A file we cannot place is a Claude Code change to look at, not a file to skip.

    Skipping it would lose whatever it holds for as long as nobody noticed — and the
    session's files are pruned within weeks.
    """
    source = planted_source("spine", SPINE, {"subagents/notes.txt": ""})

    with pytest.raises(TranscriptSchemaError, match="unknown file"):
        ClaudeCodeExtractor().extract(source)
