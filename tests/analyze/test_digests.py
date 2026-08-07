"""The reading-support queries: `session_digest`, `run_digest`, `records_slice`.

A digest is what a reader sees instead of the transcript, so the leaves here are about
agreement and containment: the digest's cost has to equal the rollup for the scope it
claims, and one run's digest has to hold that run's rows and no other's. Every expected
number is read back out of the store rather than pinned in the test, so a fixture change
moves both sides together.
"""

from pathlib import Path
from typing import Any

import duckdb
import pytest

from tests.analyze.conftest import Output, QueryRunner, query
from tests.conftest import (
    MAIN,
    RESUME,
    RESUME_LONG_RECORD,
    SERVER_TOOLS,
    SERVER_TOOLS_RUN,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
)

# The marker a digest gives the row for api calls that sit under no turn.
UNATTRIBUTED = "(unattributed)"
# The caps the design sets: a digest's prompt cell and a raw record slice.
PROMPT_CAP = 300
RAW_CAP = 2000

# A prompt past the cap, with a tail the assertion can look for. Invented: fixture prompts are
# redacted down to a few words, so nothing recorded exercises the cut.
SENTINEL_TAIL = "TAIL"
SENTINEL = "planted prompt " * 30 + SENTINEL_TAIL


def test_a_session_digest_accounts_for_api_calls_that_sit_under_no_turn(
    corpus_db: Path, run_query: QueryRunner
) -> None:
    """Calls belonging to no turn get their own digest row, so the cost still adds up."""
    # If a resumed session's api calls all carry a NULL `turn_id` — no turn of its own owns
    # them, because the turns they answered live in the session it resumed...
    unattributed = _scalar(
        corpus_db,
        "SELECT count(*) FROM live_api_calls WHERE session_id = ? AND turn_id IS NULL",
        RESUME,
    )
    assert unattributed > 0
    # ...then the digest still lists them, in one row that names itself...
    rows = _rows(run_query("session_digest", "--param", f"session_id={RESUME}", "--csv"))
    orphans = [row for row in rows if row["turn_id"] == UNATTRIBUTED]
    assert len(orphans) == 1
    assert int(orphans[0]["api_calls"]) == unattributed
    # ...and the digest's total is the session's rollup cost, not the $0 a plain turn join
    # would report against a front matter quoting the real number.
    rollup = _scalar(corpus_db, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", RESUME)
    assert rollup > 0
    assert _total(rows, "cost_usd") == pytest.approx(rollup, abs=1e-4)


def test_a_session_digest_totals_only_the_thread_it_lists(
    corpus_db: Path, run_query: QueryRunner
) -> None:
    """A digest of the main thread reports the main thread's cost, not the session's."""
    # If a session spends part of its cost inside an agent run — here on a call under no turn
    # at all, the shape most likely to be swept into the wrong scope...
    scoped, elsewhere = _scalar(
        corpus_db,
        """SELECT
               coalesce(sum(cost_usd) FILTER (source = ?), 0),
               coalesce(sum(cost_usd) FILTER (source = ? AND turn_id IS NULL), 0)
           FROM live_api_calls WHERE session_id = ?""",
        MAIN,
        SERVER_TOOLS_RUN,
        SERVER_TOOLS,
        columns=2,
    )
    assert elsewhere > 0
    # ...then the main-thread digest totals the main thread and stops there: a digest that
    # lists one scope and advertises another's total is a number no reader can reconcile.
    rows = _rows(run_query("session_digest", "--param", f"session_id={SERVER_TOOLS}", "--csv"))
    assert _total(rows, "cost_usd") == pytest.approx(scoped, abs=1e-4)


def test_a_run_digest_holds_one_run_and_no_other(corpus_db: Path, run_query: QueryRunner) -> None:
    """A run's digest counts that run's own turns and calls, not its children's."""
    # If a run spawned a leaf run of its own...
    rows = _rows(
        run_query(
            "run_digest",
            "--param",
            f"session_id={SPINE}",
            "--param",
            f"source={SPINE_RUN}",
            "--csv",
        )
    )
    # ...then its digest lists exactly its own turns...
    turns = _scalar(
        corpus_db,
        "SELECT count(*) FROM live_turns WHERE session_id = ? AND source = ?",
        SPINE,
        SPINE_RUN,
    )
    assert len(rows) == turns
    # ...and its own api and tool calls, counted once each: a join that fans out over the
    # tree inflates every number a reader copies, and the totals still look plausible.
    for column, table in (("api_calls", "live_api_calls"), ("tool_calls", "live_tool_calls")):
        expected = _scalar(
            corpus_db,
            f"SELECT count(*) FROM {table} WHERE session_id = ? AND source = ?",  # noqa: S608
            SPINE,
            SPINE_RUN,
        )
        assert _total(rows, column) == expected
    # ...and the leaf's own rows are absent, since they answer to the leaf's digest.
    leaf = _rows(
        run_query(
            "run_digest",
            "--param",
            f"session_id={SPINE}",
            "--param",
            f"source={SPINE_LEAF}",
            "--csv",
        )
    )
    assert {row["turn_id"] for row in rows}.isdisjoint({row["turn_id"] for row in leaf})


def test_a_digest_truncates_a_long_prompt(
    planted_prompt_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A digest's prompt cell is bounded, so reading a session cannot flood the reader."""
    # If a turn's prompt runs past the cap (planted: the longest recorded prompt is 145 chars
    # after redaction, so no fixture can carry this)...
    rows = _rows(
        query(
            planted_prompt_db, capsys, "session_digest", "--param", f"session_id={SPINE}", "--csv"
        )
    )
    prompts = [row["prompt"] for row in rows if len(row["prompt"]) >= PROMPT_CAP]
    # ...then the digest cuts it at the cap and the tail never reaches the reader.
    assert len(prompts) == 1
    assert len(prompts[0]) == PROMPT_CAP
    assert SENTINEL_TAIL not in prompts[0]


def test_records_slice_refuses_to_run_without_a_line_range(run_query: QueryRunner) -> None:
    """The raw-record query names what the caller has to decide instead of guessing it."""
    # If a reader asks for raw records without saying which lines...
    with pytest.raises(SystemExit, match="first_line"):
        run_query("records_slice", "--param", f"session_id={RESUME}", "--param", f"source={MAIN}")
    # ...it exits naming the parameter: a defaulted range would hand back a window of private
    # transcript, and the reader would see no error to tell them so.


def test_records_slice_caps_the_raw_text_it_returns(
    corpus_db: Path, run_query: QueryRunner
) -> None:
    """A raw record comes back bounded, whatever its length in the store."""
    # If the store holds a record longer than the cap...
    length = _scalar(
        corpus_db,
        "SELECT length(raw) FROM raw_records WHERE session_id = ? AND source = ? AND line_no = ?",
        RESUME,
        MAIN,
        RESUME_LONG_RECORD,
    )
    assert length > RAW_CAP
    # ...then the slice that names it returns the record cut to the cap.
    rows = _rows(
        run_query(
            "records_slice",
            "--param",
            f"session_id={RESUME}",
            "--param",
            f"source={MAIN}",
            "--param",
            f"first_line={RESUME_LONG_RECORD}",
            "--param",
            f"last_line={RESUME_LONG_RECORD}",
            "--csv",
        )
    )
    assert len(rows) == 1
    assert len(rows[0]["raw"]) == RAW_CAP


@pytest.fixture(scope="session")
def planted_prompt_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with one real turn's prompt replaced by an over-long invented sentinel."""
    path = tmp_path_factory.mktemp("prompt") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """UPDATE turns SET prompt = ?
               WHERE session_id = ? AND source = ? AND "index" = 0""",
            [SENTINEL, SPINE, MAIN],
        )
    finally:
        connection.close()
    return path


def _rows(output: Output) -> list[dict[str, str]]:
    """The CSV stdout as a list of column-name to value mappings."""
    header, *rows = output.csv_rows()
    return [dict(zip(header, row, strict=True)) for row in rows]


def _total(rows: list[dict[str, str]], column: str) -> float:
    return sum(float(row[column]) for row in rows)


def _scalar(db: Path, sql: str, *parameters: Any, columns: int = 1) -> Any:
    """One value — or one row of `columns` values — read straight from the store."""
    connection = duckdb.connect(str(db), read_only=True)
    try:
        row = connection.execute(sql, list(parameters)).fetchone()
        assert row is not None
        return row if columns > 1 else row[0]
    finally:
        connection.close()
