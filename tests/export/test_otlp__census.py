"""The dry run: what a send would ship, counted by shaping every session and sending nothing.

The count is the one number an operator sees before spending an hour and a backend's ingest
quota, so it has to be the mapper's own answer rather than a convenient approximation of it.
"""

import datetime as dt
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from aiobserve.export.duckdb import open_trace_store
from aiobserve.export.otlp import AmbiguousCompactionError, census, session_spans
from aiobserve.extract.store import StoreSource
from aiobserve.model import SessionTrace
from tests.conftest import FORK_ORIGIN, FORK_RUN, MYCELIA

# The store a dry run reads when one is named, mirroring the pipeline plan's census pattern:
# the leaf skips rather than inventing a corpus, since no fixture set is the real one.
CORPUS_ENV = "AIOBSERVE_CENSUS_STORE"
# The project whose sessions that store holds; the canonical corpus is mycelia's.
CORPUS_PROJECT_ENV = "AIOBSERVE_CENSUS_PROJECT"

# What the mapper emits per session, spelled independently in SQL. Kept as the formula rather
# than today's total so the leaf does not rot as fixtures land: one root per shipped session,
# every live turn, api call and tool call, every run whose spawning tool call this trace does
# not hold live (a matched pair collapses into the run's own span), and every compaction that
# survives the copied-prefix replay rule — a fork-source compaction at or before its run's
# `started_at`, or in a fork run that started at no recorded time, is a copy.
MAPPING = """
SELECT
    (SELECT count(*) FROM extract_state WHERE session_id IN $ids)
  + (SELECT count(*) FROM turns WHERE session_id IN $ids AND NOT replayed)
  + (SELECT count(*) FROM api_calls WHERE session_id IN $ids AND NOT replayed)
  + (SELECT count(*) FROM tool_calls WHERE session_id IN $ids AND NOT replayed)
  + (SELECT count(*) FROM agent_runs run WHERE run.session_id IN $ids AND NOT EXISTS (
        SELECT 1 FROM tool_calls call
        WHERE call.session_id = run.session_id
          AND call.id = run.tool_use_id
          AND NOT call.replayed))
  + (SELECT count(*) FROM compactions compaction
     LEFT JOIN agent_runs run
       ON run.session_id = compaction.session_id AND run.id = compaction.source
     WHERE compaction.session_id IN $ids
       AND (run.id IS NULL OR NOT run.is_fork
            OR (run.started_at IS NOT NULL AND compaction.timestamp > run.started_at)))
"""

# Planted, synthetic: no recorded fixture holds a fork-source compaction, and the shape this
# tier is about is a fork that copied one out of its parent's transcript. `FORK_RUN` is the
# corpus's one fork run, and it started at this recorded instant.
FORK_STARTED_AT = dt.datetime(2026, 7, 21, 22, 5, 3, 221000, tzinfo=dt.UTC)
PLANTED_COMPACTION = "planted-compaction-0000-0000-000000000000"
PLANT = """
INSERT INTO compactions VALUES
    (?, ?, 'main', ?, 'auto', 100, 10, 5),
    (?, ?, ?, ?, 'auto', 100, 10, 5)
"""


def traces(
    connection: duckdb.DuckDBPyConnection, project: Path = Path(MYCELIA)
) -> list[SessionTrace]:
    """Every session a run would ship, shaped the way `export()` receives it."""
    source = StoreSource(connection)
    return [source.extract(session) for session in source.sessions(project)]


def scalar(connection: duckdb.DuckDBPyConnection, query: str, parameters: object = None) -> int:
    """One number out of the store. A query that returns no row is a broken query, so it
    crashes rather than comparing `None` against a count."""
    row = connection.execute(query, parameters) if parameters else connection.execute(query)
    answer = row.fetchone()
    assert answer is not None, f"{query} returned no row"
    return int(answer[0])


def mapping_true(connection: duckdb.DuckDBPyConnection, shipped: list[SessionTrace]) -> int:
    """The span total the store's own rows say the mapper owes, over the shipped sessions."""
    return scalar(connection, MAPPING, {"ids": [trace.session.id for trace in shipped]})


@pytest.fixture
def counted(exportable_db: Path, tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """The exportable corpus, writable, so a leaf can plant the shape no fixture records."""
    path = tmp_path / "traces.duckdb"
    shutil.copyfile(exportable_db, path)
    connection = open_trace_store(path, read_only=False)
    yield connection
    connection.close()


def test_the_census_counts_what_the_mapper_would_ship(
    counted: duckdb.DuckDBPyConnection,
) -> None:
    """The dry run's span total is the number a real send would put on the wire."""
    # If every shipped session is shaped...
    shipped = traces(counted)
    counts = census(shipped)
    # ...then the census agrees with the shapes themselves, session for session and span for
    # span...
    assert counts.sessions == len(shipped)
    assert counts.spans == sum(len(session_spans(trace)) for trace in shipped)
    # ...and with the store's own rows read through the mapping formula, which is the check
    # that catches a mapper counting a matched run/tool pair twice.
    assert counts.spans == mapping_true(counted, shipped)


def test_the_compaction_term_follows_the_mapper_not_the_rollup_view(
    counted: duckdb.DuckDBPyConnection,
) -> None:
    """A compaction a fork copied is counted by neither the census nor a send."""
    # If a session's compaction also appears under its fork's source, timestamped inside the
    # prefix that fork copied — planted, since no recorded fixture holds one...
    before = census(traces(counted)).compactions
    counted.execute(
        PLANT,
        [
            PLANTED_COMPACTION,
            FORK_ORIGIN,
            FORK_STARTED_AT - dt.timedelta(hours=1),
            PLANTED_COMPACTION,
            FORK_ORIGIN,
            FORK_RUN,
            FORK_STARTED_AT - dt.timedelta(minutes=1),
        ],
    )
    # ...then `live_compactions` returns both copies. Its `_COUNTED` comment claims the table
    # is replay-free, and a compaction carries no `replayed` flag to make that true...
    view = scalar(counted, "SELECT count(*) FROM live_compactions")
    assert view == scalar(counted, "SELECT count(*) FROM compactions")
    # ...while the census counts the original and drops the copy, because that is what the
    # send does. Reading the view here would over-report by every fork copy in the corpus.
    assert census(traces(counted)).compactions == before + 1
    assert view == before + 2


def test_a_duplicated_compaction_with_two_live_copies_crashes_the_census(
    counted: duckdb.DuckDBPyConnection,
) -> None:
    """A compaction the copied-prefix rule cannot separate stops the run rather than guessing."""
    # If the same planted copy is timestamped *after* the fork's first own record, the rule
    # reads both copies as live and the session would ship one compaction as two spans...
    counted.execute(
        PLANT,
        [
            PLANTED_COMPACTION,
            FORK_ORIGIN,
            FORK_STARTED_AT - dt.timedelta(hours=1),
            PLANTED_COMPACTION,
            FORK_ORIGIN,
            FORK_RUN,
            FORK_STARTED_AT + dt.timedelta(minutes=1),
        ],
    )
    # ...so the census crashes, naming the session and the id an operator has to look at.
    # Every duplicated group in the canonical corpus keeps exactly one live copy today; this
    # is the guard for the day a fork shape lands that the rule cannot separate.
    with pytest.raises(AmbiguousCompactionError) as raised:
        census(traces(counted))
    assert FORK_ORIGIN in str(raised.value)
    assert PLANTED_COMPACTION in str(raised.value)


@pytest.mark.slow  # Shapes a whole real corpus — hundreds of sessions, read from disk.
def test_the_census_holds_over_a_real_corpus() -> None:
    """Against a store an operator names, the formula and the one-live-copy invariant hold."""
    # Only the corpus a run would really ship can answer this, and no fixture set is it, so
    # the leaf skips rather than inventing one. It reads nothing and sends nothing: the store
    # opens read-only, and a crash here is the ambiguity guard, not a delivery failure.
    named = os.environ.get(CORPUS_ENV, "").strip()
    if not named:
        pytest.skip(f"{CORPUS_ENV} names no trace store to census")
    connection = open_trace_store(Path(named), read_only=True)
    try:
        shipped = traces(connection, Path(os.environ.get(CORPUS_PROJECT_ENV, MYCELIA)))
        assert census(shipped).spans == mapping_true(connection, shipped)
    finally:
        connection.close()
