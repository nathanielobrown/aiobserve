"""The reading-support queries: the timelines, `view_runs`, `error_records`, `records_slice`.

A timeline is what a reader sees instead of the transcript, so the leaves here are about
agreement and containment: the timeline's cost has to equal the rollup for the scope it
claims, and one run's timeline has to hold that run's rows and no other's. Every expected
number is read back out of the store rather than pinned in the test, so a fixture change
moves both sides together.
"""

from pathlib import Path

import duckdb
import pytest

from hyphae.analyze import queries
from tests.analyze.conftest import QueryRunner, mappings, query, scalar
from tests.conftest import (
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    FORK_RUN,
    MAIN,
    RESUME,
    RESUME_LONG_RECORD,
    SERVER_TOOLS,
    SERVER_TOOLS_RUN,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
)

# The marker a timeline gives the row for api calls that sit under no turn.
UNATTRIBUTED = "(unattributed)"
# The caps the design sets: a timeline's prompt cell, a raw record slice, an error's text. The
# prompt is the one a page prints, so it comes back one character past its cut — that extra
# character is what tells whoever prints it that the prompt went on (`view/format.py:cut`).
PROMPT_CAP = queries.LOG_CHARS + 1
RAW_CAP = 2000
ERROR_CAP = 200

# A value past any of those caps, with a tail the assertions can look for. Invented: fixture
# prompts and tool results are redacted down to a few words, so nothing recorded cuts.
SENTINEL_TAIL = "TAIL"
SENTINEL = "planted text " * 40 + SENTINEL_TAIL


def test_a_session_timeline_accounts_for_api_calls_that_sit_under_no_turn(
    corpus_db: Path, run_query: QueryRunner
) -> None:
    """Calls belonging to no turn get their own timeline row, so the cost still adds up."""
    # If a resumed session's api calls all carry a NULL `turn_id` — no turn of its own owns
    # them, because the turns they answered live in the session it resumed...
    unattributed = scalar(
        corpus_db,
        "SELECT count(*) FROM live_api_calls WHERE session_id = ? AND turn_id IS NULL",
        RESUME,
    )
    assert unattributed > 0
    # ...then the timeline still lists them, in one row that names itself...
    rows = mappings(run_query("session_timeline", "--param", f"session_id={RESUME}", "--csv"))
    orphans = [row for row in rows if row["turn_id"] == UNATTRIBUTED]
    assert len(orphans) == 1
    assert int(orphans[0]["api_calls"]) == unattributed
    # ...and the timeline's total is the session's rollup cost, not the $0 a plain turn join
    # would report against a front matter quoting the real number.
    rollup = scalar(corpus_db, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", RESUME)
    assert rollup > 0
    assert _total(rows, "cost_usd") == pytest.approx(rollup, abs=1e-4)


def test_a_session_timeline_totals_only_the_thread_it_lists(
    corpus_db: Path, run_query: QueryRunner
) -> None:
    """A timeline of the main thread reports the main thread's cost, not the session's."""
    # If a session spends part of its cost inside an agent run — here on a call under no turn
    # at all, the shape most likely to be swept into the wrong scope...
    scoped, elsewhere = scalar(
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
    # ...then the main-thread timeline totals the main thread and stops there: a timeline that
    # lists one scope and advertises another's total is a number no reader can reconcile.
    rows = mappings(run_query("session_timeline", "--param", f"session_id={SERVER_TOOLS}", "--csv"))
    assert _total(rows, "cost_usd") == pytest.approx(scoped, abs=1e-4)


def test_a_run_timeline_holds_one_run_and_no_other(corpus_db: Path, run_query: QueryRunner) -> None:
    """A run's timeline counts that run's own turns and calls, not its children's."""
    # If a run spawned a leaf run of its own...
    rows = mappings(
        run_query(
            "run_timeline",
            "--param",
            f"session_id={SPINE}",
            "--param",
            f"source={SPINE_RUN}",
            "--csv",
        )
    )
    # ...then its timeline lists exactly its own turns...
    turns = scalar(
        corpus_db,
        "SELECT count(*) FROM live_turns WHERE session_id = ? AND source = ?",
        SPINE,
        SPINE_RUN,
    )
    assert len(rows) == turns
    # ...and its own api and tool calls, counted once each: a join that fans out over the
    # tree inflates every number a reader copies, and the totals still look plausible.
    for column, table in (("api_calls", "live_api_calls"), ("tool_calls", "live_tool_calls")):
        expected = scalar(
            corpus_db,
            f"SELECT count(*) FROM {table} WHERE session_id = ? AND source = ?",
            SPINE,
            SPINE_RUN,
        )
        assert _total(rows, column) == expected
    # ...and the leaf's own rows are absent, since they answer to the leaf's timeline.
    leaf = mappings(
        run_query(
            "run_timeline",
            "--param",
            f"session_id={SPINE}",
            "--param",
            f"source={SPINE_LEAF}",
            "--csv",
        )
    )
    assert {row["turn_id"] for row in rows}.isdisjoint({row["turn_id"] for row in leaf})


def test_a_timeline_truncates_a_long_prompt(
    planted_prompt_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A timeline's prompt cell is bounded, so reading a session cannot flood the reader."""
    # If a turn's prompt runs past the cap (planted: the longest recorded prompt is 145 chars
    # after redaction, so no fixture can carry this)...
    rows = mappings(
        query(
            planted_prompt_db, capsys, "session_timeline", "--param", f"session_id={SPINE}", "--csv"
        )
    )
    prompts = [row["prompt"] for row in rows if len(row["prompt"]) >= PROMPT_CAP]
    # ...then the timeline cuts it at the cap and the tail never reaches the reader.
    assert len(prompts) == 1
    assert len(prompts[0]) == PROMPT_CAP
    assert SENTINEL_TAIL not in prompts[0]


def test_error_records_finds_a_run_s_errors_without_being_told_the_thread(
    corpus_db: Path, run_query: QueryRunner
) -> None:
    """A session's failed tool calls come back whatever thread they happened in."""
    # If a session's only error happened inside an agent run rather than on the main thread —
    # the shape a reader cannot search for, because finding it means knowing the run first...
    source, tool_call_id = scalar(
        corpus_db,
        "SELECT source, id FROM live_tool_calls WHERE session_id = ? AND is_error",
        FORK_ORIGIN,
        columns=2,
    )
    assert source != MAIN
    # ...then a query keyed on the session alone lists it, naming the thread it belongs to...
    rows = mappings(run_query("error_records", "--param", f"session_id={FORK_ORIGIN}", "--csv"))
    assert [(row["source"], row["tool_call_id"]) for row in rows] == [(source, tool_call_id)]
    # ...and the line it gives is the record a reader can go read: `records_slice` at that
    # line comes back holding the call. Locating errors by scanning raw records at a thousand
    # lines a session is what this query exists to replace.
    line_no = rows[0]["line_no"]
    sliced = mappings(
        run_query(
            "records_slice",
            "--param",
            f"session_id={FORK_ORIGIN}",
            "--param",
            f"source={source}",
            "--param",
            f"first_line={line_no}",
            "--param",
            f"last_line={line_no}",
            "--csv",
        )
    )
    assert tool_call_id in sliced[0]["raw"]
    # ...while binding the source narrows to that one thread, so the main thread holds none.
    main_only = mappings(
        run_query(
            "error_records",
            "--param",
            f"session_id={FORK_ORIGIN}",
            "--param",
            f"source={MAIN}",
            "--csv",
        )
    )
    assert main_only == []


def test_error_records_lists_the_failures_and_nothing_else(
    corpus_db: Path, run_query: QueryRunner
) -> None:
    """Only the calls that came back an error are listed, each of them once."""
    # If a session made both failing and succeeding tool calls, one of them server-side —
    # whose result rides an assistant record rather than a user one...
    failed, succeeded = scalar(
        corpus_db,
        """SELECT count(*) FILTER (is_error), count(*) FILTER (NOT is_error)
           FROM live_tool_calls WHERE session_id = ?""",
        SERVER_TOOLS,
        columns=2,
    )
    assert failed == 1
    assert succeeded > 0
    # ...then the errors come back one row apiece, with what the tool was and how long its
    # result ran, whether or not a raw record could be found to cite.
    rows = mappings(run_query("error_records", "--param", f"session_id={SERVER_TOOLS}", "--csv"))
    assert len(rows) == failed
    assert rows[0]["tool"] == "advisor"
    assert int(rows[0]["error_chars"]) > 0


def test_error_records_bounds_the_error_text_it_returns(
    planted_error_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An error's text comes back cut, so listing a session's errors cannot flood a reader."""
    # If a tool returned an error longer than the cap (planted: fixture results are redacted
    # down to a word, so nothing recorded exercises the cut)...
    rows = mappings(
        query(
            planted_error_db,
            capsys,
            "error_records",
            "--param",
            f"session_id={SERVER_TOOLS}",
            "--csv",
        )
    )
    # ...then the cell stops at the cap and reports the length it cut from, and the tail of a
    # private result never reaches the reader's context.
    assert len(rows) == 1
    assert len(rows[0]["error"]) == ERROR_CAP
    assert int(rows[0]["error_chars"]) == len(SENTINEL)
    assert SENTINEL_TAIL not in rows[0]["error"]


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
    length = scalar(
        corpus_db,
        "SELECT length(raw) FROM raw_records WHERE session_id = ? AND source = ? AND line_no = ?",
        RESUME,
        MAIN,
        RESUME_LONG_RECORD,
    )
    assert length > RAW_CAP
    # ...then the slice that names it returns the record cut to the cap.
    rows = mappings(
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


def test_view_runs_carries_what_ranking_a_session_s_runs_takes(
    corpus_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session's runs come back with each one's cost, failed tool calls and compactions."""
    # If a session's two runs differ on every measure a reader ranks by — one spent four
    # times the other, one failed a tool call, one compacted while the fork under it only
    # inherited that compaction...
    rows = {
        row["run_id"]: row
        for row in mappings(
            query(
                corpus_db,
                capsys,
                "view_runs",
                "--param",
                f"session_id={FORK_ORIGIN}",
                # The query declares no width — a viewer size is the surface's
                # (`view/manifest.py`) — and a caller from the command line states one. What
                # this leaf reads back is the numbers beside the strings, at any width.
                "--param",
                f"chip_chars={queries.CHIP_CHARS}",
                "--csv",
            )
        )
    }
    # ...then one query ranks them: each row carries its own numbers, read back from the
    # store rather than pinned here...
    assert set(rows) == {FORK_ORIGIN_RUN, FORK_RUN}
    for run_id, row in rows.items():
        cost, unpriced, errors, compactions = scalar(
            corpus_db,
            """SELECT
                 (SELECT coalesce(round(sum(c.cost_usd), 4), 0) FROM live_api_calls c
                    WHERE c.session_id = ? AND c.source = ?),
                 (SELECT count(*) FILTER (c.cost_usd IS NULL) FROM live_api_calls c
                    WHERE c.session_id = ? AND c.source = ?),
                 (SELECT count(*) FILTER (t.is_error) FROM live_tool_calls t
                    WHERE t.session_id = ? AND t.source = ?),
                 (SELECT count(*) FROM live_compactions k
                    WHERE k.session_id = ? AND k.source = ?)""",
            *(FORK_ORIGIN, run_id) * 4,
            columns=4,
        )
        assert float(row["cost_usd"]) == cost
        assert int(row["unpriced_api_calls"]) == unpriced
        assert int(row["tool_errors"]) == errors
        assert int(row["compactions"]) == compactions
    # ...and the numbers are the run's own: the failure sits on the run that made it and the
    # compaction on the other, where a session-wide total would put both on both.
    assert (int(rows[FORK_RUN]["tool_errors"]), int(rows[FORK_RUN]["compactions"])) == (1, 0)
    assert (
        int(rows[FORK_ORIGIN_RUN]["tool_errors"]),
        int(rows[FORK_ORIGIN_RUN]["compactions"]),
    ) == (
        0,
        1,
    )
    assert float(rows[FORK_RUN]["cost_usd"]) > float(rows[FORK_ORIGIN_RUN]["cost_usd"])


@pytest.fixture(scope="session")
def planted_error_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with one real failed tool call's result replaced by an over-long sentinel."""
    path = tmp_path_factory.mktemp("error") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            "UPDATE tool_calls SET result = ? WHERE session_id = ? AND is_error",
            [SENTINEL, SERVER_TOOLS],
        )
    finally:
        connection.close()
    return path


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


def _total(rows: list[dict[str, str]], column: str) -> float:
    return sum(float(row[column]) for row in rows)
