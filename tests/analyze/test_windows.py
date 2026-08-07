"""The dual window and the ISO-week trend, against a frozen store with `$as_of` bound.

Every number a report quotes is quoted in these two windows, so the leaves here check the
arithmetic that relates them: the trailing window is the corpus restricted, and the weeks
partition the corpus. Nothing reads the clock — the smoke tier bans the mechanism, and the
last leaf proves the replacement decides the window on its own.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.pipeline import SessionSource
from aiobserve.sessions import Session
from tests.analyze.conftest import (
    AS_OF_PARTIAL,
    AS_OF_WHOLE,
    IN_WINDOW_AT_PARTIAL,
    MYCELIA,
    MYCELIA_SESSIONS,
    NO_PROJECT_SESSION,
    WEEKS,
    Output,
    QueryRunner,
    query,
)
from tests.conftest import FIXTURES

CORPUS = "corpus"
TRAILING = "trailing_window"
UNDATED = "undated"

Rows = dict[str, dict[str, str]]


def test_the_trailing_window_is_the_corpus_restricted(run_query: QueryRunner) -> None:
    """The window's count is the full count cut to the window, over the same sessions."""
    # If the corpus holds 13 mycelia sessions and the 28 days back from 2026-08-07 hold 6...
    counts = _periods(run_query, "--as-of", AS_OF_PARTIAL)
    assert int(counts[CORPUS]["sessions"]) == MYCELIA_SESSIONS
    assert int(counts[TRAILING]["sessions"]) == IN_WINDOW_AT_PARTIAL
    # ...then the sessions query, written separately, marks exactly those 6 in window...
    listing = run_query("sessions", "--project", MYCELIA, "--as-of", AS_OF_PARTIAL, "--csv")
    rows = list(zip(listing.column("session_id"), listing.column("in_window"), strict=True))
    windowed = {session for session, in_window in rows if in_window == "True"}
    assert len(windowed) == IN_WINDOW_AT_PARTIAL
    # ...and they are a subset of the corpus, not a differently drawn set: two queries that
    # disagree here put two numbers in one report that cannot both be true.
    assert windowed < {session for session, _ in rows}
    # ...and every count the window reports is bounded by the corpus count it restricts.
    for column, whole in counts[CORPUS].items():
        assert float(counts[TRAILING][column]) <= float(whole)


def test_iso_weeks_partition_the_corpus(run_query: QueryRunner) -> None:
    """Each session lands in exactly one ISO week, and the weeks sum to the corpus count."""
    # If the corpus spans five unevenly filled weeks, 2026-W27 through W31...
    weeks = _weeks(run_query)
    assert {week: int(row["sessions"]) for week, row in weeks.items()} == WEEKS
    # ...then their sessions sum to the corpus total, which is what makes each week a share
    # of one whole rather than an independently filtered count.
    assert sum(WEEKS.values()) == MYCELIA_SESSIONS


def test_a_session_with_no_start_time_lands_in_a_bucket_that_names_itself(
    run_query: QueryRunner, undated_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An undated session is counted in a bucket of its own, never dropped into a week."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(undated_db, capsys, name, *arguments)

    # If the one recorded session with no `started_at` also has no `project_dir`, the corpus
    # predicate excludes it and no bucket mentions it...
    assert UNDATED not in _weeks(run_query)
    assert NO_PROJECT_SESSION not in run_query("sessions", "--project", MYCELIA, "--csv").stdout
    # ...but with a `project_dir` planted on it, the predicate does place it, and the trend
    # names it `undated` rather than silently dropping it into a NULL week...
    planted = _weeks(planted_query)
    assert int(planted[UNDATED]["sessions"]) == 1
    # ...so the partition still holds: the buckets sum to the corpus count, one higher than
    # before. A session the trend cannot date is a session the reader can still see.
    total = sum(int(row["sessions"]) for row in planted.values())
    assert total == int(_periods(planted_query)[CORPUS]["sessions"]) == MYCELIA_SESSIONS + 1


def test_as_of_alone_decides_the_window(run_query: QueryRunner) -> None:
    """The same query on the same store reports a different window for a different `$as_of`."""
    # If `$as_of` moves from 2026-07-28, which opens the window before the earliest session,
    # to 2026-08-07, which opens it mid-corpus...
    whole = _periods(run_query, "--as-of", AS_OF_WHOLE)
    partial = _periods(run_query, "--as-of", AS_OF_PARTIAL)
    # ...then the window covers 13 sessions and then 6, off one frozen store...
    assert int(whole[TRAILING]["sessions"]) == MYCELIA_SESSIONS
    assert int(partial[TRAILING]["sessions"]) == IN_WINDOW_AT_PARTIAL
    # ...while the corpus row, which no window touches, does not move.
    assert whole[CORPUS] == partial[CORPUS]


@pytest.fixture(scope="session")
def undated_db(analyze_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with the undated session's `project_dir` planted, so the predicate places it.

    `fork_byref`'s fork is the recorded session with no `started_at`, and its `project_dir` is
    NULL too, so nothing in the corpus can show what the trend does with an undated session.
    The planted value is invented — the rest of the session is the recorded trace.
    """
    path = tmp_path_factory.mktemp("undated") / "traces.duckdb"
    path.write_bytes(analyze_db.read_bytes())
    transcript = FIXTURES / "fork_byref" / f"{NO_PROJECT_SESSION}.jsonl"
    session = Session(id=NO_PROJECT_SESSION, transcript=transcript)
    source = SessionSource(
        id=NO_PROJECT_SESSION, files=tuple(session.files()), fingerprint="planted"
    )
    with DuckDbExporter(path) as exporter:
        trace = ClaudeCodeExtractor().extract(source)
        exporter.export(replace(trace, session=replace(trace.session, project_dir=MYCELIA)), "p")
    return path


def _periods(run: QueryRunner, *arguments: str) -> Rows:
    """`session_counts` rows keyed by period: the corpus row and the trailing-window row."""
    return _keyed(run("session_counts", "--project", MYCELIA, "--csv", *arguments), "period")


def _weeks(run: QueryRunner, *arguments: str) -> Rows:
    """`weekly_trend` rows keyed by ISO week label."""
    return _keyed(run("weekly_trend", "--project", MYCELIA, "--csv", *arguments), "week")


def _keyed(output: Output, key: str) -> Rows:
    header, *rows = output.csv_rows()
    index = header.index(key)
    return {
        row[index]: {
            column: value for column, value in zip(header, row, strict=True) if column != key
        }
        for row in rows
    }
