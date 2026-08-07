"""Discovery, skip, and replace — the loop that keeps a DuckDB store level with disk.

Each test builds a projects root the way Claude Code lays one out, filled with copies of
the recorded fixtures, so `refresh()` walks a real directory rather than a stub.
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from aiobserve import cli
from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.extract import claude_code
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.model import SessionTrace
from aiobserve.pipeline import Extractor, SessionSource, refresh
from aiobserve.sessions import encode_project_path
from tests.conftest import FIXTURES

SPINE = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b"
DUPS = "8ee00a94-b01a-4394-b447-b065f74b11af"
OFFLOAD = "7e37bb35-4dcb-4e16-85be-55ac510c168e"


class Corpus:
    """A Claude Code projects root on disk, and the project path that addresses it."""

    def __init__(self, root: Path, project: Path) -> None:
        self.root = root
        self.project = project
        self.session_dir = root / encode_project_path(project)
        self.session_dir.mkdir(parents=True)

    def add(self, directory: str, stem: str, *, lines: int | None = None) -> Path:
        """Copy a fixture session in, optionally truncating its transcript to `lines` records.

        The session's own directory comes too, where the fixture has one, so the corpus
        holds subagent transcripts and offloaded results as Claude Code wrote them.
        """
        source = FIXTURES / directory / f"{stem}.jsonl"
        destination = self.session_dir / f"{stem}.jsonl"
        if lines is None:
            shutil.copy(source, destination)
        else:
            kept = source.read_text().split("\n")[:lines]
            destination.write_text("\n".join(kept) + "\n")
        if (FIXTURES / directory / stem).is_dir():
            shutil.copytree(
                FIXTURES / directory / stem, self.session_dir / stem, dirs_exist_ok=True
            )
        return destination

    def extractor(self) -> ClaudeCodeExtractor:
        return ClaudeCodeExtractor(projects_root=self.root)


class CountingExtractor:
    """Wraps an extractor to record which sessions it was asked to parse."""

    def __init__(self, wrapped: Extractor) -> None:
        self.wrapped = wrapped
        self.extracted: list[str] = []

    def sessions(self, project: Path) -> list[SessionSource]:
        return self.wrapped.sessions(project)

    def extract(self, source: SessionSource) -> SessionTrace:
        self.extracted.append(source.id)
        return self.wrapped.extract(source)


@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    project = tmp_path / "repo"
    project.mkdir()
    return Corpus(tmp_path / "projects", project)


@pytest.fixture
def exporter(tmp_path: Path) -> Iterator[DuckDbExporter]:
    with DuckDbExporter(tmp_path / "traces.duckdb") as open_exporter:
        yield open_exporter


def table(exporter: DuckDbExporter, name: str, session: str) -> list[tuple[object, ...]]:
    key = "id" if name == "sessions" else "session_id"
    return exporter.connection.execute(
        f"SELECT * FROM {name} WHERE {key} = ? ORDER BY 1, 2, 3", [session]
    ).fetchall()


def test_a_refresh_ingests_every_session_it_finds(corpus: Corpus, exporter: DuckDbExporter):
    """`refresh()` walks a project's sessions and writes each one into the store."""
    # If a project has two recorded sessions...
    corpus.add("spine", SPINE)
    corpus.add("dup_uuid", DUPS)
    extractor = corpus.extractor()

    result = refresh(corpus.project, extractor=extractor, exporter=exporter)

    # ...then both are extracted, and each table holds what `extract()` produced for them.
    assert sorted(result.extracted) == sorted([DUPS, SPINE])
    assert result.skipped == []
    for source in extractor.sessions(corpus.project):
        trace = extractor.extract(source)
        assert len(table(exporter, "turns", source.id)) == len(trace.turns)
        assert len(table(exporter, "api_calls", source.id)) == len(trace.api_calls)
        assert len(table(exporter, "raw_records", source.id)) == len(trace.raw_records)


def test_an_unchanged_corpus_is_not_re_extracted(corpus: Corpus, exporter: DuckDbExporter):
    """A second refresh over untouched files parses nothing and rewrites nothing.

    This is what lets the pipeline run on a timer: the cost of a no-op pass is a stat per
    file, not a reparse of the corpus.
    """
    corpus.add("spine", SPINE)
    extractor = CountingExtractor(corpus.extractor())
    refresh(corpus.project, extractor=extractor, exporter=exporter)
    stamped = exporter.connection.execute("SELECT extracted_at FROM extract_state").fetchall()

    # If nothing on disk changed...
    result = refresh(corpus.project, extractor=extractor, exporter=exporter)

    # ...then the session is skipped, `extract()` ran only on the first pass, and even the
    # row saying when it ran is untouched.
    assert (result.extracted, result.skipped) == ([], [SPINE])
    assert extractor.extracted == [SPINE]
    assert exporter.connection.execute("SELECT extracted_at FROM extract_state").fetchall() == (
        stamped
    )


def test_a_grown_session_is_replaced_rather_than_appended(
    corpus: Corpus, exporter: DuckDbExporter, tmp_path: Path
):
    """Resuming a session and refreshing gives the same rows as extracting it fresh.

    A session grows by appending, and the naive fix — insert only the new lines — leaves
    the session's own metadata frozen at its first extract. Comparing against a store
    built from scratch over the grown file catches that, where a row count would not.
    """
    # If a session was extracted while it was still short...
    corpus.add("spine", SPINE, lines=18)
    extractor = corpus.extractor()
    refresh(corpus.project, extractor=extractor, exporter=exporter)
    # Three turns of its own, plus the two its subagent's transcript holds.
    assert len(table(exporter, "turns", SPINE)) == 5

    # ...and then it resumed, growing by seven more records...
    corpus.add("spine", SPINE)
    refresh(corpus.project, extractor=extractor, exporter=exporter)

    # ...then the store matches one built from scratch over the grown file, table for table.
    with DuckDbExporter(tmp_path / "fresh.duckdb") as fresh:
        refresh(corpus.project, extractor=extractor, exporter=fresh)
        for name in ("sessions", "turns", "api_calls", "raw_records"):
            assert table(exporter, name, SPINE) == table(fresh, name, SPINE)
    assert len(table(exporter, "turns", SPINE)) == 6


def test_a_new_subagent_file_re_extracts_its_session(corpus: Corpus, exporter: DuckDbExporter):
    """A session whose subagent wrote a transcript is stale, though its own file never changed.

    The fingerprint covers every file under the session directory for exactly this case:
    a subagent, a workflow journal, or an offloaded tool result can all change while the
    main transcript's size and mtime stand still.
    """
    transcript = corpus.add("spine", SPINE)
    extractor = CountingExtractor(corpus.extractor())
    refresh(corpus.project, extractor=extractor, exporter=exporter)
    before = exporter.fingerprints()
    unchanged = (transcript.stat().st_size, transcript.stat().st_mtime_ns)

    # If another subagent transcript appears beside an untouched main transcript — with the
    # `meta.json` Claude Code always writes with it, here a recorded one under the new name...
    subagents = corpus.session_dir / SPINE / "subagents"
    shutil.copy(
        FIXTURES / "dup_uuid" / f"{DUPS}.jsonl", subagents / "agent-a1d0bc50fe316ed8e.jsonl"
    )
    shutil.copy(
        subagents / "agent-af6473ae437c9608d.meta.json",
        subagents / "agent-a1d0bc50fe316ed8e.meta.json",
    )
    result = refresh(corpus.project, extractor=extractor, exporter=exporter)

    # ...then the session is re-extracted under a new fingerprint...
    assert (transcript.stat().st_size, transcript.stat().st_mtime_ns) == unchanged
    assert result.extracted == [SPINE]
    assert extractor.extracted == [SPINE, SPINE]
    assert exporter.fingerprints() != before


def test_a_changed_offload_file_re_extracts_its_session(corpus: Corpus, exporter: DuckDbExporter):
    """Rewriting an offloaded tool result re-extracts the session and re-archives the file."""
    corpus.add("offload", OFFLOAD)
    extractor = CountingExtractor(corpus.extractor())
    refresh(corpus.project, extractor=extractor, exporter=exporter)
    offloaded = corpus.session_dir / OFFLOAD / "tool-results" / "bosvr1kjx.txt"

    # If the file holding a tool's output changes while the transcript stands still...
    offloaded.write_text("[redacted] — a shorter output than before")
    result = refresh(corpus.project, extractor=extractor, exporter=exporter)

    # ...then the session is parsed again, and the store holds the file as it now reads.
    assert result.extracted == [OFFLOAD]
    assert exporter.connection.execute(
        "SELECT content, size_bytes FROM offload_files"
    ).fetchall() == [("[redacted] — a shorter output than before", offloaded.stat().st_size)]


def test_a_bumped_extractor_version_re_extracts_everything(
    corpus: Corpus, exporter: DuckDbExporter, monkeypatch: pytest.MonkeyPatch
):
    """Upgrading the parser re-parses the corpus rather than leaving old rows in place."""
    corpus.add("spine", SPINE)
    corpus.add("dup_uuid", DUPS)
    extractor = CountingExtractor(corpus.extractor())
    refresh(corpus.project, extractor=extractor, exporter=exporter)
    before = exporter.fingerprints()

    # If the extractor's version changes with the files untouched...
    monkeypatch.setattr(claude_code, "EXTRACTOR_VERSION", "99")
    result = refresh(corpus.project, extractor=extractor, exporter=exporter)

    # ...then every session is parsed again and every fingerprint moves.
    assert sorted(result.extracted) == sorted([DUPS, SPINE])
    assert len(extractor.extracted) == 4
    assert set(exporter.fingerprints()) == set(before)
    assert not set(exporter.fingerprints().values()) & set(before.values())


def test_a_pruned_session_keeps_its_rows(corpus: Corpus, exporter: DuckDbExporter):
    """Claude Code deletes transcripts after a few weeks; the store is the archive.

    Refresh only ever adds and replaces. A session whose file is gone stops being
    discovered, and its rows stay exactly as its last extract left them.
    """
    corpus.add("spine", SPINE)
    transcript = corpus.add("dup_uuid", DUPS)
    extractor = corpus.extractor()
    refresh(corpus.project, extractor=extractor, exporter=exporter)
    before = table(exporter, "raw_records", DUPS)

    # If one session's transcript is pruned from disk...
    transcript.unlink()
    result = refresh(corpus.project, extractor=extractor, exporter=exporter)

    # ...then discovery no longer sees it, and its rows survive untouched.
    assert [source.id for source in extractor.sessions(corpus.project)] == [SPINE]
    assert result.extracted == []
    assert table(exporter, "raw_records", DUPS) == before


def test_the_cli_extract_command_writes_the_same_store(corpus: Corpus, tmp_path: Path):
    """`aiobserve extract` drives the same pipeline the API does."""
    corpus.add("spine", SPINE)
    through_api = tmp_path / "api.duckdb"
    with DuckDbExporter(through_api) as exporter:
        refresh(corpus.project, extractor=corpus.extractor(), exporter=exporter)
        expected = table(exporter, "turns", SPINE)

    # If the CLI runs over the same corpus...
    through_cli = tmp_path / "cli.duckdb"
    cli.main(
        "extract",
        str(corpus.project),
        "--db",
        str(through_cli),
        "--projects-root",
        str(corpus.root),
    )

    # ...then it leaves the same rows behind.
    with DuckDbExporter(through_cli) as exporter:
        assert table(exporter, "turns", SPINE) == expected


def test_a_session_source_carries_every_file_it_owns(corpus: Corpus):
    """Discovery hands the extractor the whole session directory, not just the transcript."""
    corpus.add("spine", SPINE)
    offloaded = corpus.session_dir / SPINE / "tool-results" / "result.txt"
    offloaded.parent.mkdir(parents=True)
    offloaded.write_text("[redacted]")

    (source,) = corpus.extractor().sessions(corpus.project)

    # An offloaded tool result is part of the session, so it reaches the fingerprint and,
    # from slice 2 on, the parser.
    assert offloaded in source.files
