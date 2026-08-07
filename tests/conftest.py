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

FIXTURES = Path(__file__).parent / "fixtures"

SourceFactory = Callable[[str, str], SessionSource]
TraceFactory = Callable[[str, str], SessionTrace]


@pytest.fixture
def fixture_source() -> SourceFactory:
    """Build a `SessionSource` over one fixture transcript, the way `sessions()` would.

    Fingerprints belong to discovery, not parsing, so the value here is a placeholder —
    `extract()` never reads it.
    """

    def build(directory: str, stem: str) -> SessionSource:
        transcript = FIXTURES / directory / f"{stem}.jsonl"
        return SessionSource(id=stem, files=(transcript,), fingerprint="fixture-fingerprint")

    return build


@pytest.fixture
def fixture_trace(fixture_source: SourceFactory) -> TraceFactory:
    """Extract one fixture transcript, for tests that need a trace but not the parsing."""

    def build(directory: str, stem: str) -> SessionTrace:
        return ClaudeCodeExtractor().extract(fixture_source(directory, stem))

    return build
