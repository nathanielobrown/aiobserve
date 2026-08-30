"""The dry run: what a send would ship, counted by shaping every session and sending nothing.

The count is the one number an operator sees before spending an hour and a backend's ingest
quota, so it has to be the mapper's own answer rather than a convenient approximation of it.
"""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from hyphae.export.duckdb import open_trace_store
from hyphae.export.otlp import SpanKey, census, census_project, session_spans, span_id
from hyphae.extract.store import StoreSource
from hyphae.model import SessionTrace
from tests.conftest import FORK_COMPACTION, FORK_RUN, MYCELIA, NO_WAIT, SPINE, SPINE_RUN

# The store a dry run reads when one is named, mirroring the pipeline plan's census pattern:
# the leaf skips rather than inventing a corpus, since no fixture set is the real one.
CORPUS_ENV = "HYPHAE_CENSUS_STORE"
# The project whose sessions that store holds; the canonical corpus is mycelia's.
CORPUS_PROJECT_ENV = "HYPHAE_CENSUS_PROJECT"

# What the mapper emits per session, spelled independently in SQL. Kept as the formula rather
# than today's total so the leaf does not rot as fixtures land: one root per shipped session,
# every live turn and api call, every live tool call *no run named as its launch*, every agent
# run, and every compaction no fork replayed.
#
# The two middle terms are where a plausible formula goes wrong. Suppression is keyed by tool
# call *id*, so a run whose call the session records twice suppresses both rows, and a
# workflow fan-out that spawns many runs from one call suppresses that one row while emitting
# a span per run. Counting a matched pair as one call traded for one run — the shape a
# hand-written formula reaches for — undercounts a fan-out by every run past the first.
MAPPING = """
SELECT
    (SELECT count(*) FROM extract_state WHERE session_id IN $ids)
  + (SELECT count(*) FROM turns WHERE session_id IN $ids AND NOT replayed)
  + (SELECT count(*) FROM api_calls WHERE session_id IN $ids AND NOT replayed)
  + (SELECT count(*) FROM tool_calls call
     WHERE call.session_id IN $ids AND NOT call.replayed AND NOT EXISTS (
        SELECT 1 FROM agent_runs run
        WHERE run.session_id = call.session_id AND run.tool_use_id = call.id))
  + (SELECT count(*) FROM agent_runs WHERE session_id IN $ids)
  + (SELECT count(*) FROM compactions WHERE session_id IN $ids AND NOT replayed)
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
    with open_trace_store(path, read_only=False, wait=NO_WAIT) as connection:
        yield connection


def test_the_census_counts_what_the_mapper_would_ship(
    counted: duckdb.DuckDBPyConnection,
) -> None:
    """The dry run's span total is the number a real send would put on the wire."""
    # If a project's whole selection is counted the way a dry run counts it — the extractor
    # `refresh` would drive, with no fingerprint diff in front of it...
    counts = census_project(Path(MYCELIA), extractor=StoreSource(counted))
    # ...then the census agrees with the shapes themselves, session for session and span for
    # span...
    shipped = traces(counted)
    assert counts.sessions == len(shipped)
    assert counts.spans == sum(len(session_spans(trace)) for trace in shipped)
    # ...and with the store's own rows read through the mapping formula, which is the check
    # that catches a mapper counting a matched run/tool pair twice.
    assert counts.spans == mapping_true(counted, shipped)


def test_the_compaction_term_and_the_store_agree_on_a_fork_copy(
    counted: duckdb.DuckDBPyConnection,
) -> None:
    """A compaction a fork copied is counted once by the census and once by the store."""
    # If the corpus holds the session whose fork copied a compaction out of the transcript it
    # forked, so the base table holds that record twice...
    held = scalar(counted, "SELECT count(*) FROM compactions WHERE id = ?", [FORK_COMPACTION])
    assert held == 2
    # ...then the census counts the live one and drops the copy, because that is what a send
    # does...
    counts = census(traces(counted))
    assert counts.compactions == scalar(counted, "SELECT count(*) FROM live_compactions")
    # ...and the fork's copy is what each of them left out.
    assert (
        scalar(
            counted, "SELECT count(*) FROM compactions WHERE replayed AND source = ?", [FORK_RUN]
        )
        == 1
    )


@pytest.mark.slow  # Shapes a whole real corpus — hundreds of sessions, read from disk.
def test_the_census_holds_over_a_real_corpus() -> None:
    """Against a store an operator names, the formula and the one-live-copy invariant hold."""
    # Only the corpus a run would really ship can answer this, and no fixture set is it, so
    # the leaf skips rather than inventing one. It reads nothing and sends nothing: the store
    # opens read-only, and a crash here is the ambiguity guard, not a delivery failure.
    named = os.environ.get(CORPUS_ENV, "").strip()
    if not named:
        pytest.skip(f"{CORPUS_ENV} names no trace store to census")
    with open_trace_store(Path(named), read_only=True, wait=NO_WAIT) as connection:
        shipped = traces(connection, Path(os.environ.get(CORPUS_PROJECT_ENV, MYCELIA)))
        assert census(shipped).spans == mapping_true(connection, shipped)


# Planted, synthetic: no recorded fixture holds a workflow fan-out, and the canonical corpus
# holds six groups of runs sharing one spawning call — the largest 93 runs from a single
# `Workflow` call.
FANOUT_RUN = "planted-fanout-run"


def test_one_call_shared_by_many_runs_is_suppressed_once(
    counted: duckdb.DuckDBPyConnection,
) -> None:
    """A fan-out costs one span per run, and the call that launched them all costs none."""
    # If a second run names the same spawning tool call as one the corpus already records...
    counted.execute(
        "INSERT INTO agent_runs SELECT * REPLACE (? AS id) FROM agent_runs"
        " WHERE session_id = ? AND id = ?",
        [FANOUT_RUN, SPINE, SPINE_RUN],
    )
    shipped = traces(counted)
    spine = next(trace for trace in shipped if trace.session.id == SPINE)
    spawn = next(
        call
        for call in spine.tool_calls
        if call.id == next(run.tool_use_id for run in spine.agent_runs if run.id == SPINE_RUN)
    )
    spans = session_spans(spine)
    # ...then the shared call emits no `execute_tool` span at all: suppression is keyed by the
    # call's id, so one row goes however many runs named it...
    identifiers = {span.span_id for span in spans}
    assert span_id(SPINE, SpanKey.tool_call, spawn.source, spawn.id) not in identifiers
    # ...both runs emit their own `invoke_agent` span, hanging off the model call that made
    # the request...
    launched = [
        span
        for span in spans
        if span.span_id
        in {span_id(SPINE, SpanKey.agent_run, "", run) for run in (SPINE_RUN, FANOUT_RUN)}
    ]
    assert [span.name for span in launched] == ["invoke_agent claude"] * 2
    assert {span.parent_span_id for span in launched} == {
        span_id(SPINE, SpanKey.api_call, spawn.source, spawn.api_call_id)
    }
    # ...and the census still agrees with the store's own rows. A formula that trades each
    # suppressed call for one run undercounts this session by every run past the first, which
    # is a shape only a real corpus holds.
    assert census(shipped).spans == mapping_true(counted, shipped)
