"""Scaffolding for the analysis tier: the fixture corpus as one read-only trace store.

The queries are the subject here, so their evidence has to be rows the real pipeline wrote:
`corpus_db` extracts every recorded fixture transcript into one DuckDB file, once per test
session. Tests read it through `aiobserve query` and never write to it — a test that plants
a row copies the file first, as `worktree_db` does.
"""

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import duckdb
import pytest

from aiobserve import cli
from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.pipeline import SessionSource
from aiobserve.sessions import Session
from tests.conftest import (
    ANCESTOR,
    FIXTURES,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    MYCELIA,
    RESUME,
    SIBLING_SESSION,
    WORKTREE_SESSION,
)

# Mycelia sessions `corpus_rollups` credits with no turns and no agent runs, so no stratum
# may reach them. Two of them compacted, which is what makes the exclusion visible: a pool
# drawn on metrics alone would rank them.
NO_WORK_SESSIONS = (
    RESUME,
    "8ee00a94-b01a-4394-b447-b065f74b11af",
    "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9",
)

# The id the planted agent-run compaction carries, so no first-seen twin can own it.
PLANTED_COMPACTION = "planted-compaction"

# Measured on 2026-08-08: of the in-window sessions, the ones with any turn or agent run.
POOL_AT_WHOLE = 10
POOL_AT_PARTIAL = 5
# Distinct `agent_type`s across the corpus's 7 agent runs — one run each.
AGENT_TYPES = 7

# Measured on 2026-08-08 by building the store below: 13 mycelia sessions between
# 2026-06-30 and 2026-07-27, in five unevenly filled ISO weeks.
MYCELIA_SESSIONS = 13
WEEKS = {"2026-W27": 4, "2026-W28": 4, "2026-W29": 3, "2026-W30": 1, "2026-W31": 1}
# `$as_of` values with a window each side of the corpus: 2026-08-07 opens the trailing 28
# days at 2026-07-10 and covers 6 sessions; 2026-07-28 opens at 2026-06-30 and covers all 13.
AS_OF_PARTIAL = "2026-08-07"
IN_WINDOW_AT_PARTIAL = 6
AS_OF_WHOLE = "2026-07-28"
# A third `$as_of`, inside the corpus, so the window's far edge has something to exclude:
# 2026-07-19 opens at 2026-06-21, before the earliest session, and closes at the end of that
# day — 11 sessions, the corpus minus the two recorded after it.
AS_OF_MID = "2026-07-19"
IN_WINDOW_AT_MID = 11


@dataclass(frozen=True)
class Output:
    """What one `aiobserve query` run printed, split by stream."""

    stdout: str
    stderr: str

    def csv_rows(self) -> list[list[str]]:
        """The `--csv` stdout as rows, header included."""
        return list(csv.reader(io.StringIO(self.stdout)))

    def column(self, name: str) -> list[str]:
        """One column of the `--csv` stdout, by header name."""
        header, *rows = self.csv_rows()
        index = header.index(name)
        return [row[index] for row in rows]


QueryRunner = Callable[..., Output]


def query(db: Path, capsys: pytest.CaptureFixture[str], name: str, *arguments: str) -> Output:
    """Run `aiobserve query` against one store, returning what it printed."""
    cli.main("query", name, "--db", str(db), *arguments)
    captured = capsys.readouterr()
    return Output(captured.out, captured.err)


def mappings(output: Output) -> list[dict[str, str]]:
    """One `--csv` result as a list of column-name to value mappings."""
    header, *rows = output.csv_rows()
    return [dict(zip(header, row, strict=True)) for row in rows]


def scalar(db: Path, sql: str, *parameters: Any, columns: int = 1) -> Any:
    """One value — or one row of `columns` values — read straight from the store."""
    connection = duckdb.connect(str(db), read_only=True)
    try:
        row = connection.execute(sql, list(parameters)).fetchone()
        assert row is not None
        return row if columns > 1 else row[0]
    finally:
        connection.close()


@pytest.fixture
def run_query(corpus_db: Path, capsys: pytest.CaptureFixture[str]) -> QueryRunner:
    """Run `aiobserve query` against the fixture corpus, returning what it printed."""

    def run(name: str, *arguments: str) -> Output:
        return query(corpus_db, capsys, name, *arguments)

    return run


@pytest.fixture(scope="session")
def planted_run_compaction_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus plus a copy of a recorded main-thread compaction, moved onto an agent run.

    Invented placement, and it has to be: a run that compacts is what iteration 1 kept seeing
    and no fixture session recorded, so nothing would exercise the per-run counts otherwise.
    A copy under a new id rather than a move, so the recorded compaction stays where it was
    recorded and the `corpus_*` first-seen rule has no twin to prefer.
    """
    path = tmp_path_factory.mktemp("run_compaction") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """INSERT INTO compactions
               SELECT ?, ?, ?, timestamp, trigger, pre_tokens, post_tokens, duration_ms
               FROM compactions WHERE session_id = ? LIMIT 1""",
            [PLANTED_COMPACTION, FORK_ORIGIN, FORK_ORIGIN_RUN, ANCESTOR],
        )
    finally:
        connection.close()
    return path


@pytest.fixture(scope="session")
def worktree_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus plus two sessions whose `project_dir` is planted, not recorded.

    No recorded fixture sits under `<project>/.claude/worktrees/`, and none sits under a
    checkout that merely shares mycelia's path prefix. Both are re-exports of real traces
    with the one column replaced — the value is invented, the rest of the session is not.
    """
    path = tmp_path_factory.mktemp("worktree") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    with DuckDbExporter(path) as exporter:
        for directory, stem, project_dir in (
            ("legacy_title", WORKTREE_SESSION, f"{MYCELIA}/.claude/worktrees/planted"),
            ("legacy_entrypoint", SIBLING_SESSION, f"{MYCELIA}-old"),
        ):
            transcript = FIXTURES / directory / f"{stem}.jsonl"
            session = Session(id=stem, transcript=transcript)
            source = SessionSource(id=stem, files=tuple(session.files()), fingerprint="planted")
            trace = ClaudeCodeExtractor().extract(source)
            trace = replace(trace, session=replace(trace.session, project_dir=project_dir))
            exporter.export(trace, source.fingerprint)
    return path
