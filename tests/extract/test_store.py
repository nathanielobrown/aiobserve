"""Reading the trace store back as traces: the round trip, and the source filter.

The OTLP export ships the store rather than the transcripts on disk, so `StoreSource` is the
extractor that pipeline runs on (`plans/otlp-export/design.md`). Everything the exporter can
send is only as true as this rebuild — a column silently dropped here ships a corpus missing
a field nobody notices — so the round trip compares whole objects rather than fields.
"""

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import pytest

from aiobserve.export.duckdb import open_trace_store
from aiobserve.extract.store import ROW_ORDER, StoreSource, UnplaceableSessionError
from aiobserve.model import SessionTrace
from aiobserve.pipeline import SessionSource
from tests.conftest import (
    FIXTURES,
    MYCELIA,
    NO_PROJECT_SESSION,
    SIBLING_SESSION,
    SPINE,
    WORKTREE_SESSION,
    TraceFactory,
    build_store,
    corpus_transcripts,
)

# A worktree of the analyzed repository, and a repository whose path merely starts with the
# same characters. Planted onto two recorded sessions (the `worktree_db` precedent in
# `tests/conftest.py`) because the recorded corpus holds no sibling of `MYCELIA`.
UNDER_PROJECT = f"{MYCELIA}/worktrees/paging"
SIBLING_PROJECT = f"{MYCELIA}-other"


def canonical(trace: SessionTrace) -> SessionTrace:
    """The same trace with every list in `StoreSource`'s order.

    List order carries no meaning: the model's lists are keyed by natural ids, and the
    extractor emits the main transcript's rows before each subagent's while the store
    orders by primary key. Sorting both sides leaves the comparison over every row and
    every field of every row.
    """
    ordered: dict[str, Any] = {
        table: sorted(
            getattr(trace, table),
            key=lambda row: tuple(getattr(row, column) for column in columns),
        )
        for table, columns in ROW_ORDER.items()
        if table != "sessions"
    }
    return replace(trace, **ordered)


def source(session_id: str) -> SessionSource:
    """What `refresh()` hands `extract()`: an id and a fingerprint, and no files."""
    return SessionSource(id=session_id, files=(), fingerprint="fixture")


@pytest.fixture(scope="module")
def store(corpus_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """The whole fixture corpus, open read-only — the tier that only reads rows back."""
    connection = open_trace_store(corpus_db, read_only=True)
    yield connection
    connection.close()


@pytest.fixture
def listable(exportable_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """The corpus the source filter can place, open read-only (`exportable_db`)."""
    connection = open_trace_store(exportable_db, read_only=True)
    yield connection
    connection.close()


def test_a_recorded_trace_round_trips_through_the_store(
    store: duckdb.DuckDBPyConnection, fixture_trace: TraceFactory
) -> None:
    """A trace read back out of the store is the trace that was written into it."""
    # If the deepest recorded session — four main turns, two nested subagent runs — was
    # extracted into the store...
    expected = fixture_trace("spine", SPINE)
    # ...when it is rebuilt from the rows rather than from the transcript...
    trace = StoreSource(store).extract(source(SPINE))
    # ...then every entity list comes back whole, down to the last field of the last row...
    assert canonical(trace) == canonical(expected)
    # ...and the columns the session left NULL come back as None rather than as a default
    # that would read as a recorded value: no api call retried a model, and two of the seven
    # carry no stop reason.
    assert {call.fallback_from for call in trace.api_calls} == {None}
    assert None in {call.stop_reason for call in trace.api_calls}


@pytest.mark.parametrize(
    "transcript", corpus_transcripts(), ids=lambda transcript: str(transcript.stem)
)
def test_every_fixture_session_round_trips(
    store: duckdb.DuckDBPyConnection, fixture_trace: TraceFactory, transcript: Path
) -> None:
    """Every recorded session survives the store, not just the one the leaves above name."""
    expected = fixture_trace(transcript.parent.name, transcript.stem)
    trace = StoreSource(store).extract(source(transcript.stem))
    assert canonical(trace) == canonical(expected)


def test_provenance_names_the_parser_not_the_reader(store: duckdb.DuckDBPyConnection) -> None:
    """A rebuilt trace credits the extractor whose rows it is, never `StoreSource` itself."""
    trace = StoreSource(store).extract(source(SPINE))
    assert (trace.extractor, trace.extractor_version) == store.execute(
        "SELECT extractor, extractor_version FROM extract_state WHERE session_id = ?", [SPINE]
    ).fetchone()
    assert "StoreSource" not in trace.extractor


def test_sessions_carry_the_fingerprint_the_store_holds(
    listable: duckdb.DuckDBPyConnection,
) -> None:
    """Discovery reports each session's recorded fingerprint and no files to read."""
    # If the store holds the sessions of the analyzed repository...
    expected = [
        SessionSource(id=session_id, files=(), fingerprint=fingerprint)
        for session_id, fingerprint in listable.execute(
            "SELECT e.session_id, e.fingerprint FROM extract_state e"
            " JOIN sessions s ON s.id = e.session_id"
            " WHERE s.project_dir = ? ORDER BY e.session_id",
            [MYCELIA],
        ).fetchall()
    ]
    assert expected, "the fixture corpus should hold sessions under MYCELIA"
    # ...then discovery lists exactly those, each with the fingerprint `extract_state`
    # recorded and an empty `files` — the store is the source, so there is nothing to stat.
    assert StoreSource(listable).sessions(Path(MYCELIA)) == expected


def test_the_filter_takes_the_project_and_what_sits_under_it(
    exportable_db: Path, tmp_path: Path
) -> None:
    """A worktree of the project is in scope; a repository whose name merely starts the
    same way is not."""
    # If one recorded session is re-placed into a worktree under the analyzed repository
    # and another into a sibling repository beside it — both planted, since the recorded
    # corpus holds neither shape...
    path = tmp_path / "planted.duckdb"
    shutil.copyfile(exportable_db, path)
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "UPDATE sessions SET project_dir = ? WHERE id = ?", [UNDER_PROJECT, SIBLING_SESSION]
        )
        connection.execute(
            "UPDATE sessions SET project_dir = ? WHERE id = ?", [SIBLING_PROJECT, WORKTREE_SESSION]
        )
    # ...then the worktree ships and the sibling does not: the filter cuts on path
    # components, so a string-prefix filter passes the first half and fails here.
    connection = open_trace_store(path, read_only=True)
    listed = {found.id for found in StoreSource(connection).sessions(Path(MYCELIA))}
    connection.close()
    assert SIBLING_SESSION in listed
    assert WORKTREE_SESSION not in listed


def test_a_childless_session_with_no_project_is_excluded(tmp_path: Path) -> None:
    """A session that recorded no working directory and no work is simply not listed."""
    # If a session's main transcript is extracted without the subagent file beside it — a
    # trim of recorded data — the store holds its bookkeeping shell: three archive lines
    # (`permission-mode`, `mode`, `bridge-session`) and not one row of work...
    transcript = tmp_path / f"{NO_PROJECT_SESSION}.jsonl"
    shutil.copy(FIXTURES / "fork_byref" / transcript.name, transcript)
    path = tmp_path / "childless.duckdb"
    build_store(path, [transcript])
    # ...then discovery leaves it out without complaint: there is nothing to lose.
    connection = open_trace_store(path, read_only=True)
    assert StoreSource(connection).sessions(Path(MYCELIA)) == []
    connection.close()


def test_a_session_with_no_project_but_rows_crashes(tmp_path: Path) -> None:
    """Excluding a session that holds records would drop them silently, so it crashes."""
    # If the same session is extracted whole — its by-reference fork wrote 2 api calls, 2
    # tool calls, 1 agent run and 10 raw records under a NULL `project_dir`...
    path = tmp_path / "contentful.duckdb"
    build_store(path, [FIXTURES / "fork_byref" / f"{NO_PROJECT_SESSION}.jsonl"])
    connection = open_trace_store(path, read_only=True)
    # ...then discovery refuses to place it rather than dropping it...
    with pytest.raises(UnplaceableSessionError) as raised:
        StoreSource(connection).sessions(Path(MYCELIA))
    connection.close()
    # ...naming the session and what would have been lost, table by table...
    message = str(raised.value)
    assert NO_PROJECT_SESSION in message
    assert "api_calls 2" in message
    assert "tool_calls 2" in message
    assert "agent_runs 1" in message
    assert "raw_records 10" in message
    # ...and quoting none of the transcript, whose every string redaction flattened to this.
    assert "[redacted]" not in message
