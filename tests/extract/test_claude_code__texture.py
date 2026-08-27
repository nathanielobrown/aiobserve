"""Session texture: compactions, the names a session goes by, and files still being written.

Fixtures are redacted excerpts of real mycelia sessions; each fixture directory's README
names the source session and the Claude Code version that wrote it. The two invented
transcripts here carry a deliberately broken line, which no recorded session survives to
hold — they are called out at every use.
"""

import json
import logging

import pytest

from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.extract.record_types import TranscriptSchemaError
from aiobserve.model import MAIN_SOURCE, Compaction
from tests.conftest import SourceFactory
from tests.extract.test_claude_code import at

COMPACTED = "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9"
LEGACY_TITLE = "0b34d1b8-ebd3-40a6-bd89-f1881e1de2ba"
# The tripwire planted in both invented transcripts' broken line, standing for whatever a
# transcript holds: no crash message and no log line may carry it.
SECRET = "SUPER-SECRET-PAYLOAD-9f2a"


def test_a_compaction_records_what_it_dropped(fixture_source: SourceFactory):
    """Each context compaction is a row saying when it ran, why, and how much it shed."""
    trace = ClaudeCodeExtractor().extract(fixture_source("compaction", COMPACTED))

    # If a session compacted twice — once because the operator asked and once because it
    # ran out of window — then both boundaries are rows...
    assert trace.compactions == [
        Compaction(
            id="459d0d29-cb67-477a-9cf1-f9bb19417c49",
            session_id=COMPACTED,
            source=MAIN_SOURCE,
            timestamp=at("2026-07-02T10:13:08.988"),
            # ...the operator's, which shed 94% of the window in 134 seconds...
            trigger="manual",
            pre_tokens=171313,
            post_tokens=9478,
            duration_ms=133939,
        ),
        Compaction(
            id="0710fcd7-edbe-4012-bee4-89aadf04f6f2",
            session_id=COMPACTED,
            source=MAIN_SOURCE,
            timestamp=at("2026-07-02T23:45:51.303"),
            # ...and the automatic one thirteen hours later, from a fuller window.
            trigger="auto",
            pre_tokens=222837,
            post_tokens=13556,
            duration_ms=127487,
        ),
    ]


def test_a_compaction_pairs_with_the_summary_it_wrote(fixture_source: SourceFactory):
    """Every boundary has the summary record that replaced the dropped context beside it.

    The pairing holds across the whole mycelia corpus (scanned 2026-08-07), so a count
    that drifts from it means Claude Code changed where the summary goes.
    """
    trace = ClaudeCodeExtractor().extract(fixture_source("compaction", COMPACTED))

    summaries = [r for r in trace.raw_records if json.loads(r.raw).get("isCompactSummary")]
    assert len(summaries) == len(trace.compactions) == 2
    # ...and the summary follows its boundary, so the pair reads in transcript order.
    assert [r.line_no for r in summaries] == [2, 4]


def test_a_session_before_custom_titles_takes_its_generated_one(fixture_source: SourceFactory):
    """A session with no operator-set title falls back to the one Claude Code wrote for it."""
    trace = ClaudeCodeExtractor().extract(fixture_source("legacy_title", LEGACY_TITLE))

    # If a session carries `ai-title` records and no `custom-title`, the generated title
    # is the session's name...
    assert trace.session.title == "fixture-title-1"
    # ...and nothing claims an agent name: this session ran as the operator, not as one
    # of the named agents a later Claude Code lets you switch to.
    assert trace.session.agent_name is None


def test_a_transcript_still_being_written_drops_only_its_last_line(
    fixture_source: SourceFactory, caplog: pytest.LogCaptureFixture
):
    """A session extracted mid-write keeps every complete record and warns about the rest.

    Invented: the extractor has to read a file Claude Code is appending to, and a recorded
    fixture cannot hold a half-written line and stay a recorded fixture.
    """
    caplog.set_level(logging.WARNING)

    # If the final line is JSON cut off partway...
    trace = ClaudeCodeExtractor().extract(fixture_source("invented", "invented-truncated-tail"))

    # ...then the records before it are extracted as usual...
    assert [r.line_no for r in trace.raw_records] == [1, 2]
    # ...and the drop is a warning naming the line, not a crash — the next run will pick
    # the record up once Claude Code has finished writing it.
    (logged,) = caplog.records
    assert "dropped an incomplete final line" in logged.getMessage()
    assert SECRET not in logged.getMessage()


def test_a_record_broken_before_the_end_crashes(fixture_source: SourceFactory):
    """Unparseable JSON with records after it is corruption, and stops the extraction.

    Invented, for the same reason as the truncated tail: the shape is the whole point.
    """
    # If a broken line has a complete record after it, it cannot be a half-written tail...
    source = fixture_source("invented", "invented-corrupt-middle")

    # ...so the extractor refuses the file, and says which line to look at without
    # quoting what the line held.
    with pytest.raises(TranscriptSchemaError, match="line 3") as caught:
        ClaudeCodeExtractor().extract(source)
    assert SECRET not in str(caught.value)
