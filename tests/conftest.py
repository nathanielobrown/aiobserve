"""Scaffolding shared by every tier: the recorded fixtures, and traces built from them.

The fixtures sit at `tests/fixtures/` rather than beside the extractor tests because the
exporter and pipeline tests want the same recorded sessions — a trace built from a real
transcript is better evidence than one assembled by hand. Each fixture directory's README
names its source session and Claude Code version.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.model import SessionTrace
from aiobserve.pipeline import SessionSource
from aiobserve.sessions import Session

FIXTURES = Path(__file__).parent / "fixtures"

SourceFactory = Callable[[str, str], SessionSource]
TraceFactory = Callable[[str, str], SessionTrace]


@pytest.fixture
def fixture_source() -> SourceFactory:
    """Build a `SessionSource` over one fixture session, the way `sessions()` would.

    The whole session directory, not just the transcript: subagent transcripts, workflow
    journals and offloaded tool outputs are part of the session, and a builder that
    skipped them would let a test pass on files the real pipeline never sees.

    Fingerprints belong to discovery, not parsing, so the value here is a placeholder —
    `extract()` never reads it.
    """

    def build(directory: str, stem: str) -> SessionSource:
        session = Session(id=stem, transcript=FIXTURES / directory / f"{stem}.jsonl")
        return SessionSource(
            id=stem, files=tuple(session.files()), fingerprint="fixture-fingerprint"
        )

    return build


@pytest.fixture
def fixture_trace(fixture_source: SourceFactory) -> TraceFactory:
    """Extract one fixture transcript, for tests that need a trace but not the parsing."""

    def build(directory: str, stem: str) -> SessionTrace:
        return ClaudeCodeExtractor().extract(fixture_source(directory, stem))

    return build
