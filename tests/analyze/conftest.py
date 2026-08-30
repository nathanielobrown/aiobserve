"""Scaffolding for the analysis tier: the fixture corpus as one read-only trace store.

The queries are the subject here, so their evidence has to be rows the real pipeline wrote:
`corpus_db` extracts every recorded fixture transcript into one DuckDB file, once per test
session. Tests read it through `hp query` and never write to it — a test that plants
a row copies the file first, as `worktree_db` does.
"""

import csv
import datetime as dt
import io
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Self, override

import duckdb
import pytest

from hyphae import cli
from hyphae.export.duckdb import DuckDbExporter
from hyphae.extract.claude_code import ClaudeCodeExtractor
from hyphae.pipeline import SessionSource
from hyphae.sessions import SessionFiles
from tests.conftest import (
    FIXTURES,
    MYCELIA,
    RESUME,
    SIBLING_SESSION,
    WORKTREE_SESSION,
)

# Long after the last fixture was recorded — the date this whole tier runs at. A windowed
# query measures back from `--as-of`, whose default is the wall clock, so a query or a test
# that leans on today's date passes while the corpus is recent and goes red the morning it
# recedes. PR #4 found `select_sessions` days from exactly that. Run far enough into the
# future and the failure lands on the change that introduced it instead.
FAR_FUTURE = dt.date(2030, 1, 1)


class _PinnedDate(dt.date):
    """`date`, with today at `FAR_FUTURE` — everything else is the real thing."""

    @classmethod
    @override
    def today(cls) -> Self:
        return cls(FAR_FUTURE.year, FAR_FUTURE.month, FAR_FUTURE.day)


class _PinnedDateTime(dt.datetime):
    """`datetime`, with now at midnight on `FAR_FUTURE` in whatever zone the caller asks for."""

    @classmethod
    @override
    def now(cls, tz: dt.tzinfo | None = None) -> Self:
        return cls(FAR_FUTURE.year, FAR_FUTURE.month, FAR_FUTURE.day, tzinfo=tz)


@pytest.fixture(autouse=True)
def far_future(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every analysis test long after the corpus was recorded.

    Patched on `datetime` itself rather than on the one caller (`cli`'s `--as-of` default),
    so a clock read added anywhere under a test here is pinned too — both spellings of it,
    because the CLI reads the zone-aware one and a leaf may read either. The stores are built
    by session-scoped fixtures, which pytest sets up before this one — a recording keeps the
    dates it was recorded with, and only the reading of it moves.
    """
    monkeypatch.setattr(dt, "date", _PinnedDate)
    monkeypatch.setattr(dt, "datetime", _PinnedDateTime)


# Mycelia sessions `corpus_rollups` credits with no turns and no agent runs, so no stratum
# may reach them. One of them compacted, which is what makes the exclusion visible: a pool
# drawn on metrics alone would rank it.
NO_WORK_SESSIONS = (RESUME, "8ee00a94-b01a-4394-b447-b065f74b11af")

# The id the planted agent-run compaction carries, so no first-seen twin can own it.

# Measured on 2026-08-27: of the in-window sessions, the ones with any turn or agent run.
POOL_AT_WHOLE = 12
POOL_AT_PARTIAL = 6
# Distinct `agent_type`s across the corpus's 11 agent runs.
AGENT_TYPES = 7

# Measured on 2026-08-15 by building the store below: 15 mycelia sessions between
# 2026-06-30 and 2026-07-27, in five unevenly filled ISO weeks.
MYCELIA_SESSIONS = 15
WEEKS = {
    "2026-W27": 4,
    "2026-W28": 4,
    "2026-W29": 4,
    # Two, since `model_only/` — the recording that carries a turn and no api call — landed here.
    "2026-W30": 2,
    "2026-W31": 1,
}
# `$as_of` values with a window each side of the corpus: 2026-08-07 opens the trailing 28
# days at 2026-07-10 and covers 8 sessions; 2026-07-28 opens at 2026-06-30 and covers all 15.
AS_OF_PARTIAL = "2026-08-07"
IN_WINDOW_AT_PARTIAL = 8
AS_OF_WHOLE = "2026-07-28"
# A third `$as_of`, inside the corpus, so the window's far edge has something to exclude:
# 2026-07-19 opens at 2026-06-21, before the earliest session, and closes at the end of that
# day — 12 sessions, the corpus minus the three recorded after it (07-20, 07-21, 07-27).
AS_OF_MID = "2026-07-19"
IN_WINDOW_AT_MID = 12


@dataclass(frozen=True)
class Output:
    """What one `hp query` run printed, split by stream."""

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
    """Run `hp query` against one store, returning what it printed."""
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
    """Run `hp query` against the fixture corpus, returning what it printed."""

    def run(name: str, *arguments: str) -> Output:
        return query(corpus_db, capsys, name, *arguments)

    return run


@pytest.fixture
def enriched_query(enriched_db: Path, capsys: pytest.CaptureFixture[str]) -> QueryRunner:
    """Run `hp query` against the corpus an enrichment pass has written to."""

    def run(name: str, *arguments: str) -> Output:
        return query(enriched_db, capsys, name, *arguments)

    return run


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
            session = SessionFiles(id=stem, transcript=transcript)
            source = SessionSource(id=stem, files=tuple(session.files()), fingerprint="planted")
            trace = ClaudeCodeExtractor().extract(source)
            trace = replace(trace, session=replace(trace.session, project_dir=project_dir))
            exporter.export(trace, source.fingerprint)
    return path
