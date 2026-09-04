"""The archive: every line of every file a session wrote, and its offloaded tool outputs.

Claude Code prunes a session's directory a few weeks after it ends, so what is not
archived here is gone. The fixtures are redacted mycelia sessions; each fixture
directory's README names its source session and Claude Code version.
"""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyphae.extract.claude_code import ClaudeCodeExtractor
from hyphae.extract.errors import ExtractionError, SessionLayoutError, TranscriptSchemaError
from hyphae.model import MAIN_SOURCE, OffloadFile, SessionTrace
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
# The session whose only `cwd` sits on an archived record, and nowhere else.
SYSTEM_SITED = "637fb3f1-ab2c-427e-b876-304be9f7bb8e"
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
    assert lines_by_source(trace) == Counter({MAIN_SOURCE: 42, SPINE_AGENT: 10, NESTED_AGENT: 6})
    # ...numbered from 1 within its own file, so a row points back at a line...
    agent = [r.line_no for r in trace.raw_records if r.source == SPINE_AGENT]
    assert agent == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    # ...and the two `meta.json` files the walk also found became no source at all: they
    # are linkage that agent runs read, not records.
    assert len(source.files.files()) == 5


def test_a_workflow_run_archives_its_journal_and_its_agents(fixture_source: SourceFactory):
    """A parallel fan-out's agents and the journal tracking them are archived too."""
    trace = ClaudeCodeExtractor().extract(fixture_source("workflow", WORKFLOW))

    # If a session fanned out into a workflow, then its agents are sourced by agentId as
    # any other subagent is, and the journal by its workflow directory...
    assert lines_by_source(trace) == Counter({MAIN_SOURCE: 8, WORKFLOW_AGENT: 6, JOURNAL: 4})
    # ...carrying the two record types only journals hold.
    assert {r.type for r in trace.raw_records if r.source == JOURNAL} == {"started", "result"}


def test_an_archived_record_keeps_whatever_envelope_it_carried(fixture_source: SourceFactory):
    """The archive reads an id and a time off an archived record, and reports neither when absent.

    Both halves matter to a reader of `raw_records`: a null timestamp there has to mean the
    record had none, not that the archive stopped looking once it stopped modelling the kind.
    """
    trace = ClaudeCodeExtractor().extract(fixture_source("workflow", WORKFLOW))

    # If a transcript opens on editor-state records — as this one does, on four of them...
    opening = [r for r in trace.raw_records if r.source == MAIN_SOURCE][:4]

    # ...then each is archived with its type and its raw line, and nothing else to say...
    assert [r.type for r in opening] == [
        "mode",
        "permission-mode",
        "bridge-session",
        "file-history-snapshot",
    ]
    assert all(r.uuid is None and r.timestamp is None for r in opening)

    # ...while an archived kind that does carry an envelope keeps it, so the two `attachment`
    # records of the run place themselves in the session as every modelled record does.
    attachments = [r for r in trace.raw_records if r.type == "attachment"]
    assert [(r.uuid, r.timestamp) for r in attachments] == [
        ("870b4053-f05f-4b14-9d66-8492a229bf43", datetime(2026, 7, 12, 15, 38, 13, 896000, UTC)),
        ("75a30121-dfe0-4cd4-89d4-1949a4122083", datetime(2026, 7, 12, 15, 38, 13, 897000, UTC)),
    ]


def test_a_session_sited_only_by_an_archived_record_still_reports_where_it_ran(
    fixture_source: SourceFactory,
):
    """An archived record's envelope still says where and how the session was running.

    Five of the 3,647 threads in the store take their `cwd` from a thin `system` subtype, and
    24,704 `attachment` records carry one too (scanned 2026-09-04). A reader that only looked
    at the kinds with models of their own would report those sessions as belonging to no
    project, and they would drop out of every corpus query without a word.
    """
    trace = ClaudeCodeExtractor().extract(fixture_source("system_sited", SYSTEM_SITED))

    # If the only record in the file carrying a `cwd` is an archived `system/informational`...
    assert [r.type for r in trace.raw_records] == [
        "mode",
        "permission-mode",
        "system",
        "last-prompt",
    ]
    # ...then all four of the session's context fields still come off it...
    assert (
        trace.session.project_dir,
        trace.session.git_branch,
        trace.session.version,
        trace.session.entrypoint,
    ) == ("/Users/nob/repos/mycelia", "fixture-branch-1", "2.1.205", "cli")
    # ...though nothing in the file opens a turn.
    assert trace.turns == []


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


def test_the_title_sidecar_is_not_a_transcript(planted_source: PlantedFactory):
    """The title file a session keeps beside its transcript is not parsed as records."""
    # If a session was renamed, Claude Code writes the name to its own file...
    source = planted_source(
        "spine", SPINE, {"custom-title.json": '{"customTitle": "Improve NavTree context bars"}'}
    )

    # ...and it does not reach the archive, which would choke on it as JSON lines.
    trace = ClaudeCodeExtractor().extract(source)
    assert set(lines_by_source(trace)) == {MAIN_SOURCE}


@pytest.mark.parametrize(
    ("planted", "message"),
    [
        # A file whose place in the session directory we cannot name at all...
        ({"subagents/notes.txt": ""}, "unknown file"),
        # ...and one we can place, arriving without the other half of its pair.
        ({"subagents/agent-orphan.meta.json": "{}"}, "a transcript or a meta, not both"),
    ],
)
def test_a_session_directory_we_cannot_read_crashes_as_a_layout_error(
    planted_source: PlantedFactory, planted: dict[str, str], message: str
):
    """A directory shape we cannot read is a Claude Code change to look at, not a file to skip.

    Skipping it would lose whatever it holds for as long as nobody noticed — and the
    session's files are pruned within weeks.
    """
    source = planted_source("spine", SPINE, planted)

    with pytest.raises(SessionLayoutError, match=message) as raised:
        ClaudeCodeExtractor().extract(source)

    # Nothing here read a record, so it is not a schema error: the two send a reader to
    # different places. They share a base, for a caller that does not care which it got.
    assert not isinstance(raised.value, TranscriptSchemaError)
    assert isinstance(raised.value, ExtractionError)
