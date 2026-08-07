"""The corpus counts that promote a recurring observation to a counted finding.

`error_signatures` answers "how often did this error happen, and to which tool";
`agent_compactions` answers "which kinds of thread run out of context". The leaves here are
about what a group holds: which rows fall into one signature, which thread a compaction is
counted under, and what the trailing window leaves out.

Both need a population the recorded corpus lacks — every recorded error is a one-off redacted
down to a word, and no recorded run compacted — so each plants one onto real rows and says so.
"""

from pathlib import Path

import duckdb
import pytest

from tests.analyze.conftest import (
    AS_OF_PARTIAL,
    AS_OF_WHOLE,
    Output,
    QueryRunner,
    mappings,
    query,
    scalar,
)
from tests.conftest import FORK_ORIGIN, MYCELIA, SPINE

# The first line every planted failure shares, and the tail that differs between them. A
# recurring error is one signature over many bodies — "File has not been read yet" ahead of a
# different path each time — and no recorded fixture error survived redaction with a body.
SIGNATURE = "planted failure signature"
PLANTED_ERROR = f"{SIGNATURE}{chr(10)}tail for "
# The tool the plant marks failed, and what marking it costs: every `Read` in two sessions,
# which is 4 calls in one thread of `FORK_ORIGIN` and 3 + 1 in two threads of `SPINE`.
PLANTED_TOOL = "Read"
PLANTED_ERRORS = 8
PLANTED_SESSIONS = 2
PLANTED_THREADS = 3
# `FORK_ORIGIN` started 2026-07-21, inside either window; `SPINE` started 2026-07-06, before
# the shorter one opens, so the window count drops its 4.
PLANTED_IN_SHORT_WINDOW = 4
# The two recorded errors, each in a session of its own: an `Agent` call and a server-side
# `advisor` call, whose results redaction left as one word apiece.
RECORDED_SIGNATURES = ["[redacted]", "unavailable"]

# The row `agent_compactions` gives a session's own thread, so a definition's rate has the
# thing it has to beat beside it. The query writes the sentinel; nothing in Python reads it.
MAIN_THREAD = "(main thread)"
# The agent definition the planted compaction lands under.
PLANTED_DEFINITION = "auditor"


def test_error_signatures_counts_one_signature_over_many_bodies(
    planted_failures_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Errors that differ only after their first line are counted as one recurring error."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_failures_db, capsys, name, *arguments)

    # If eight tool calls failed with the same opening line and a different body each — the
    # shape of a recurring error, planted because the recorded ones are one-offs — spread
    # over two sessions and three threads...
    rows = _signatures(planted_query, {"min_occurrences": 2})
    # ...then they come back as one row. The signature is the first line, so the bodies do
    # not split the count, and the spread says how much of the corpus it is evidence about.
    assert len(rows) == 1
    assert rows[0]["tool"] == PLANTED_TOOL
    assert rows[0]["signature"] == SIGNATURE
    assert int(rows[0]["errors"]) == PLANTED_ERRORS
    assert int(rows[0]["sessions"]) == PLANTED_SESSIONS
    assert int(rows[0]["threads"]) == PLANTED_THREADS


def test_error_signatures_counts_the_window_beside_the_corpus(
    planted_failures_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each signature is counted twice: over the trailing window, and over the whole corpus."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_failures_db, capsys, name, *arguments)

    # If the as-of moves forward until one of the two erring sessions falls out of the
    # trailing window...
    bindings: dict[str, int | str] = {"min_occurrences": 2}
    window = _signatures(planted_query, bindings, as_of=AS_OF_PARTIAL)
    corpus = _signatures(planted_query, bindings, as_of=AS_OF_PARTIAL, period="corpus")
    # ...then the window count drops that session's four errors, so a report quoting it is
    # quoting a number its citation's `as_of` can be re-run for...
    assert int(window[0]["errors"]) == PLANTED_IN_SHORT_WINDOW
    assert int(window[0]["sessions"]) == 1
    # ...and the corpus count still holds all eight, which is the baseline that says whether
    # a window number is a spike or the way this tool always behaves.
    assert int(corpus[0]["errors"]) == PLANTED_ERRORS


def test_error_signatures_narrows_to_a_bound_phrase_and_a_floor(
    planted_failures_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reader can count one phrase's occurrences, and one-off errors stay out of the way."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_failures_db, capsys, name, *arguments)

    # If the corpus holds the planted signature and the two recorded one-off errors...
    every = _signatures(planted_query, {"min_occurrences": 1}, period="corpus")
    assert sorted(row["signature"] for row in every) == sorted([SIGNATURE, *RECORDED_SIGNATURES])
    # ...then the floor keeps the singletons out, which is what bounds a listing on a corpus
    # where most error text is unique...
    kept = _signatures(planted_query, {"min_occurrences": 2}, period="corpus")
    assert [row["signature"] for row in kept] == [SIGNATURE]
    # ...and binding a phrase counts just the error holding it, matched anywhere in the text
    # rather than only in the line the signature is cut from — a tail is where the path sits.
    bound = _signatures(planted_query, {"min_occurrences": 1, "signature": "tail for "})
    assert [row["signature"] for row in bound] == [SIGNATURE]
    assert int(bound[0]["errors"]) == PLANTED_ERRORS


def test_agent_compactions_counts_a_compaction_under_the_thread_that_had_it(
    planted_run_compaction_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run that ran out of context is counted against its definition, not its session."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_run_compaction_db, capsys, name, *arguments)

    # If one compaction happened inside an agent run rather than on a main thread (planted:
    # no recorded fixture run compacted, which is why iteration 1 could not count this)...
    rows = _compactions(planted_query)
    # ...then it is counted under that run's definition, once, and against the one run the
    # definition has — which is the ratio a reader ranks definitions by...
    definition = rows[PLANTED_DEFINITION]
    assert (int(definition["compactions"]), int(definition["compacting_threads"])) == (1, 1)
    assert int(definition["threads"]) == 1
    assert float(definition["compactions_per_thread"]) == 1.0
    # ...and it is counted there instead of under the session's own thread: every compaction
    # the period holds is in exactly one row, so the column sums to the store's own total.
    total = scalar(
        planted_run_compaction_db,
        """SELECT count(*) FROM corpus_compactions k JOIN sessions s ON s.id = k.session_id
           WHERE s.project_dir = ?""",
        MYCELIA,
    )
    assert total > 1
    assert sum(int(row["compactions"]) for row in rows.values()) == total


def test_agent_compactions_separates_how_many_threads_from_how_often(
    planted_run_compaction_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The main thread's row says both how many sessions compacted and how often they did."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_run_compaction_db, capsys, name, *arguments)

    # If one session's main thread compacted twice and others compacted once...
    threads, compactions = scalar(
        planted_run_compaction_db,
        """SELECT count(DISTINCT k.session_id), count(*)
           FROM corpus_compactions k JOIN sessions s ON s.id = k.session_id
           WHERE s.project_dir = ? AND k.source = 'main'""",
        MYCELIA,
        columns=2,
    )
    assert compactions > threads
    # ...then the main-thread row keeps the two apart, so "most sessions compact" and "a few
    # sessions compact repeatedly" cannot be read for one another...
    rows = _compactions(planted_query)
    assert int(rows[MAIN_THREAD]["compacting_threads"]) == threads
    assert int(rows[MAIN_THREAD]["compactions"]) == compactions
    # ...and its population is every session in the period, not only the ones that compacted,
    # so the rate underneath is a rate and not a share of the sessions that already did.
    sessions = scalar(
        planted_run_compaction_db, "SELECT count(*) FROM sessions WHERE project_dir = ?", MYCELIA
    )
    assert int(rows[MAIN_THREAD]["threads"]) == sessions
    # ...while a definition that never compacted still gets a row, which is what makes the
    # absence readable: a missing row would look like a definition nobody ran.
    quiet = [name for name, row in rows.items() if int(row["compactions"]) == 0]
    assert quiet


@pytest.fixture(scope="session")
def planted_failures_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with one tool's calls in two sessions marked failed, sharing a first line.

    Invented data, and deliberately so: the recorded errors are one-offs whose text redaction
    cut to a word, and a recurring error is precisely what this query counts.
    """
    path = tmp_path_factory.mktemp("failures") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """UPDATE tool_calls SET is_error = true, result = ? || id
               WHERE name = ? AND session_id IN (?, ?)""",
            [PLANTED_ERROR, PLANTED_TOOL, SPINE, FORK_ORIGIN],
        )
    finally:
        connection.close()
    return path


def _signatures(
    run: QueryRunner,
    bindings: dict[str, int | str],
    *,
    as_of: str = AS_OF_WHOLE,
    period: str = "trailing_window",
) -> list[dict[str, str]]:
    """`error_signatures` over the fixture project, as one column mapping per row of a period."""
    arguments = [
        part for name, value in bindings.items() for part in ("--param", f"{name}={value}")
    ]
    output = run("error_signatures", "--project", MYCELIA, "--as-of", as_of, "--csv", *arguments)
    return [row for row in mappings(output) if row["period"] == period]


def _compactions(run: QueryRunner, *, period: str = "corpus") -> dict[str, dict[str, str]]:
    """`agent_compactions` over the fixture project, by `agent_type`, for one period."""
    output = run("agent_compactions", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv")
    return {row["agent_type"]: row for row in mappings(output) if row["period"] == period}
