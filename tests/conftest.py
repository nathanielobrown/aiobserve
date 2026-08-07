"""Scaffolding shared by every tier: the recorded fixtures, and traces built from them.

The fixtures sit at `tests/fixtures/` rather than beside the extractor tests because the
exporter and pipeline tests want the same recorded sessions — a trace built from a real
transcript is better evidence than one assembled by hand. Each fixture directory's README
names its source session and Claude Code version.
"""

import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.model import SessionTrace
from aiobserve.pipeline import SessionSource
from aiobserve.sessions import Session

FIXTURES = Path(__file__).parent / "fixtures"

SourceFactory = Callable[[str, str], SessionSource]
TraceFactory = Callable[[str, str], SessionTrace]
PlantedFactory = Callable[[str, str, dict[str, str]], SessionSource]


def fixture_transcripts(*directories: str) -> tuple[Path, ...]:
    """Every recorded transcript under the named fixture directories, in a stable order."""
    return tuple(
        transcript
        for directory in directories
        for transcript in sorted((FIXTURES / directory).glob("*.jsonl"))
    )


def build_store(path: Path, transcripts: Iterable[Path]) -> None:
    """Extract each transcript into a store at `path`, as `refresh()` would.

    Tiers that query the store want their evidence to be rows the real pipeline wrote, so
    they build one from recorded transcripts rather than inserting rows by hand. Building
    costs an extraction per transcript — build once per test session and copy the file for
    any test that plants or deletes rows.
    """
    with DuckDbExporter(path) as exporter:
        for transcript in transcripts:
            session = Session(id=transcript.stem, transcript=transcript)
            source = SessionSource(
                id=transcript.stem, files=tuple(session.files()), fingerprint="fixture"
            )
            exporter.export(ClaudeCodeExtractor().extract(source), source.fingerprint)


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
def planted_source(tmp_path: Path) -> PlantedFactory:
    """A fixture session copied into `tmp_path`, with extra files in its directory.

    The transcript is the recorded one; only the planted file *names* are invented, which is
    the point — they stand for layouts Claude Code writes, or might write next.
    """

    def build(directory: str, stem: str, files: dict[str, str]) -> SessionSource:
        transcript = tmp_path / f"{stem}.jsonl"
        shutil.copy(FIXTURES / directory / transcript.name, transcript)
        for relative, content in files.items():
            path = tmp_path / stem / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        session = Session(id=stem, transcript=transcript)
        return SessionSource(id=stem, files=tuple(session.files()), fingerprint="planted")

    return build


@pytest.fixture
def fixture_trace(fixture_source: SourceFactory) -> TraceFactory:
    """Extract one fixture transcript, for tests that need a trace but not the parsing."""

    def build(directory: str, stem: str) -> SessionTrace:
        return ClaudeCodeExtractor().extract(fixture_source(directory, stem))

    return build
