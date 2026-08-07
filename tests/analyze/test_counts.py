"""The corpus counts that promote a recurring observation to a counted finding.

`error_signatures` answers "how often did this error happen, and to which tool". The leaves
here are about what a group holds: which rows fall into one signature, how much of the
corpus a count is evidence about, and what the trailing window leaves out.

The fixture corpus records two failed tool calls, both one-off and both redacted down to a
word, so a leaf that needs a recurring error plants one onto real rows and says so.
"""

from pathlib import Path

import duckdb
import pytest

from tests.analyze.conftest import AS_OF_PARTIAL, AS_OF_WHOLE, Output, QueryRunner, query
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
    header, *rows = output.csv_rows()
    return [
        mapping
        for mapping in (dict(zip(header, row, strict=True)) for row in rows)
        if mapping["period"] == period
    ]
