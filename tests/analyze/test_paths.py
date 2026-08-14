"""The counts that read a tool call's path rather than its error text.

`path_failures` answers "which directory was the failing call pointed at" — the question
`error_signatures` cannot answer, because the path is exactly what its signature drops.
`missing_file_recovery` answers what the thread did about it in the calls that followed.

Every recorded fixture input is `[redacted]`, so a path a query can group by is something no
recording carries. Both plants below say so; what stays real is the rows they land on — their
sessions, threads, tools and the order they ran in.
"""

import json
from pathlib import Path

import duckdb
import pytest

from tests.analyze.conftest import AS_OF_WHOLE, Output, QueryRunner, mappings, query
from tests.conftest import FORK_ORIGIN, FORK_ORIGIN_RUN, MAIN, MYCELIA, SPINE, SPINE_LEAF

# The scratch directory two of iteration 3's mechanisms run through, under a fake root: it is
# gitignored, so it exists in the primary checkout and in none of the worktrees cut from it.
# One reader hitting it from three checkouts is the shape the canonical store holds — 45
# window errors over 16 sessions, 40 of them inside spawned runs (`--project mycelia --as-of
# 2026-08-13`), spread over a root per worktree.
SCRATCH = "handoffs"
PRIMARY_PATH = f"/repo/{SCRATCH}/plan.md"
WORKTREE_PATH = f"/repo/.claude/worktrees/agent-1/{SCRATCH}/plan.md"
OTHER_WORKTREE_PATH = f"/repo/.claude/worktrees/agent-2/{SCRATCH}/plan.md"
# What the plant costs, following the fixture corpus's own `Read` calls: the four in
# `FORK_ORIGIN`'s run and the one in `SPINE`'s leaf run fail from a worktree, while the three
# on `SPINE`'s main thread read the same directory in the primary checkout and succeed.
SCRATCH_CALLS = 8
SCRATCH_ERRORS = 5
SCRATCH_SESSIONS = 2
SCRATCH_THREADS = 2
# The two worktree copies the failures come from, which is what has to collapse to one row.
SCRATCH_WORKTREES = 2
# The bucket a call with no directory in its path lands in — every recorded fixture input,
# since redaction leaves `file_path` as a bare `[redacted]`.
NO_DIRECTORY = "(no directory)"

# The guessed filename and the directory it sits in: the shape iteration 3 saw four readers
# describe and no query could count — a Read of a name nobody had seen, then an `ls`. Over
# mycelia's 2026-08-13 window, 24 of the 121 window failures whose text says the file does not
# exist were followed straight away by a listing of that same directory.
ADR_DIR = "/repo/docs/adrs"
ADR_PATH = f"{ADR_DIR}/0042-guessed-name.md"
PLAN_PATH = "/repo/plans/locked.md"
# The two failures, one of which no listing would have prevented. Invented text under a fake
# root, redaction having left no fixture error with a body; `$missing` is bound to the phrase
# in the first, which is what a reader narrows the population with.
MISSING_PHRASE = "does not exist"
NOT_FOUND = f"File {MISSING_PHRASE}."
DENIED = "EACCES: permission denied"
# The three dispositions the query files every failure under, and how many it has to file.
LOOKED_UP = "listed the directory"
LOOKED_ELSEWHERE = "listed elsewhere"
NEVER_LOOKED = "no listing"
PLANTED_GUESSES = 3


def test_path_failures_counts_one_directory_across_the_checkouts_it_sits_in(
    planted_paths_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory read from a worktree and from the checkout it was cut from is one row."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_paths_db, capsys, name, *arguments)

    # If five reads of one gitignored directory failed from two worktrees, and three reads of
    # that same directory in the primary checkout succeeded...
    rows = {row["directory"]: row for row in _path_failures(planted_query, {})}
    scratch = rows[SCRATCH]
    # ...then the failures are one row rather than one per worktree, which is what makes the
    # count comparable at all: a per-root split reads as a handful of one-off accidents...
    assert int(scratch["errors"]) == SCRATCH_ERRORS
    assert int(scratch["sessions"]) == SCRATCH_SESSIONS
    assert int(scratch["threads"]) == SCRATCH_THREADS
    # ...with the reads that worked beside them, since a directory nobody touches and one
    # nobody can reach are the same number of failures and different findings...
    assert int(scratch["calls"]) == SCRATCH_CALLS
    # ...and the share a spawned run hit says where the mechanism sits: the thread that
    # spawned the run could see the directory, and the run could not.
    assert int(scratch["run_errors"]) == SCRATCH_ERRORS


def test_path_failures_can_be_asked_to_tell_the_checkouts_apart(
    planted_paths_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Binding a longer tail splits one directory back into the copies of the repo it sits in."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_paths_db, capsys, name, *arguments)

    # If the same eight calls are grouped on two path segments instead of one...
    rows = _path_failures(planted_query, {"min_occurrences": 1, "tail_segments": 2})
    split = [row for row in rows if row["directory"].endswith(f"/{SCRATCH}")]
    # ...then the aggregation comes apart into a row per worktree, each naming the copy it
    # failed in — the reading a follow-up wants, and the reason the default is 1...
    assert len(split) == SCRATCH_WORKTREES
    assert sum(int(row["errors"]) for row in split) == SCRATCH_ERRORS
    # ...and no failure is lost or double-counted on the way: the split is a regrouping of the
    # same calls, so both readings total the corpus's failures the same.
    whole = _path_failures(planted_query, {"min_occurrences": 1})
    assert sum(int(row["errors"]) for row in rows) == sum(int(row["errors"]) for row in whole)


def test_path_failures_names_the_calls_that_carried_no_directory(
    planted_paths_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A path with no directory in it gets a named bucket rather than an empty key."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_paths_db, capsys, name, *arguments)

    # If some calls named a bare file — which every recorded fixture call does, redaction
    # having cut its path to one word...
    rows = {row["directory"]: row for row in _path_failures(planted_query, {"min_occurrences": 0})}
    # ...then they are counted under a bucket that says so, instead of an empty string a
    # reader would take for a query bug.
    assert int(rows[NO_DIRECTORY]["calls"]) > 0
    assert "" not in rows


@pytest.fixture(scope="session")
def planted_paths_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with one gitignored directory read from three checkouts of one repository.

    Invented paths, and they have to be: fixture redaction replaces every `file_path` with
    `[redacted]`, so the recorded corpus cannot tell one directory from another. The shape is
    the canonical store's (see `SCRATCH` above).
    """
    path = tmp_path_factory.mktemp("paths") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    connection = duckdb.connect(str(path))
    try:
        for session, source, target, fails in (
            # The spawned runs are pointed at a copy of the directory their worktree lacks...
            (FORK_ORIGIN, FORK_ORIGIN_RUN, WORKTREE_PATH, True),
            (SPINE, SPINE_LEAF, OTHER_WORKTREE_PATH, True),
            # ...while the thread that spawned one reads the primary checkout and finds it.
            (SPINE, MAIN, PRIMARY_PATH, False),
        ):
            connection.execute(
                """UPDATE tool_calls SET input = ?, is_error = ?
                   WHERE name = 'Read' AND session_id = ? AND source = ?""",
                [json.dumps({"file_path": target}), fails, session, source],
            )
    finally:
        connection.close()
    return path


def test_missing_file_recovery_counts_the_guess_the_thread_looked_up(
    planted_guesses_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A read that 404s and an `ls` of that directory in the next call is one counted pattern."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_guesses_db, capsys, name, *arguments)

    # If three reads failed on a path they named, and each thread did something different in
    # the call after — listed that directory, listed another one, listed nothing...
    rows = {row["recovery"]: row for row in _recovery(planted_query, {})}
    # ...then the recovery iteration 3 could only describe is a number, with the spread that
    # says how much of the corpus it is evidence about...
    assert int(rows[LOOKED_UP]["failures"]) == 1
    assert int(rows[LOOKED_UP]["sessions"]) == 1
    assert int(rows[LOOKED_UP]["threads"]) == 1
    # ...the listing of some other directory is kept apart from it, because a broader search
    # is not the thread finding the name it guessed at...
    assert int(rows[LOOKED_ELSEWHERE]["failures"]) == 1
    # ...and the dispositions close over the population: every failed call that named a path
    # is in exactly one of them, so the recovery rate has a denominator.
    assert sum(int(row["failures"]) for row in rows.values()) == PLANTED_GUESSES
    assert set(rows) == {LOOKED_UP, LOOKED_ELSEWHERE, NEVER_LOOKED}


def test_missing_file_recovery_moves_with_the_window_it_is_asked_for(
    planted_guesses_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """How long a thread has to look is the caller's choice, and the citation carries it."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_guesses_db, capsys, name, *arguments)

    # If one thread listed the directory it guessed in, but three calls later rather than in
    # the next one...
    default = {row["recovery"]: row for row in _recovery(planted_query, {})}
    assert int(default[NEVER_LOOKED]["failures"]) == 1
    # ...then it reads as no recovery at the production window, which is the strict claim —
    # the call after the failure is the one that answers it...
    widened = {row["recovery"]: row for row in _recovery(planted_query, {"within_calls": 3})}
    # ...and widening the window moves it, so a report that widens has to say so: the number
    # is a function of a binding its citation carries...
    assert int(widened[LOOKED_UP]["failures"]) == 2
    # ...while the disposition it left keeps its row at zero, because a disposition nothing
    # fell into is a finding and a missing row reads as a broken query.
    assert int(widened[NEVER_LOOKED]["failures"]) == 0


def test_missing_file_recovery_narrows_to_the_failures_a_listing_could_have_prevented(
    planted_guesses_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bound phrase cuts the population to missing files, leaving other failures out."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_guesses_db, capsys, name, *arguments)

    # If one of the three failures was a permission error rather than a missing file — a
    # failure no amount of listing would have prevented...
    rows = {row["recovery"]: row for row in _recovery(planted_query, {"missing": MISSING_PHRASE})}
    # ...then binding the phrase drops it, and the rate is quoted over the population it is
    # actually about — here the two reads of a name that was never there.
    assert sum(int(row["failures"]) for row in rows.values()) == PLANTED_GUESSES - 1
    assert int(rows[LOOKED_ELSEWHERE]["failures"]) == 0


@pytest.fixture(scope="session")
def planted_guesses_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with three failed reads and what each thread did in the calls after.

    Invented inputs and results, and they have to be: redaction leaves no fixture call with a
    path, a command or an error body. What the plant keeps is the recorded order — it rewrites
    calls in place, so the distance between a failure and the listing after it is the distance
    the transcript recorded.
    """
    path = tmp_path_factory.mktemp("guesses") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    connection = duckdb.connect(str(path))
    try:
        for session, source, index, name, tool_input, result in (
            # The run guesses an ADR filename and lists that directory in the very next call...
            (FORK_ORIGIN, FORK_ORIGIN_RUN, 0, "Read", {"file_path": ADR_PATH}, NOT_FOUND),
            (FORK_ORIGIN, FORK_ORIGIN_RUN, 1, "Bash", {"command": f"ls -la {ADR_DIR}"}, None),
            # ...then hits a failure listing could not have helped with, and globs elsewhere...
            (FORK_ORIGIN, FORK_ORIGIN_RUN, 2, "Read", {"file_path": PLAN_PATH}, DENIED),
            (FORK_ORIGIN, FORK_ORIGIN_RUN, 3, "Glob", {"path": "/repo/src"}, None),
            # ...while the main thread guesses, does two other things, and only then looks.
            (SPINE, MAIN, 1, "Read", {"file_path": ADR_PATH}, NOT_FOUND),
            (SPINE, MAIN, 2, "Bash", {"command": "git status --short"}, None),
            (SPINE, MAIN, 4, "Bash", {"command": f"ls {ADR_DIR}"}, None),
        ):
            connection.execute(
                """UPDATE tool_calls SET name = ?, input = ?, is_error = ?, result = ?
                   WHERE session_id = ? AND source = ? AND "index" = ?""",
                [name, json.dumps(tool_input), result is not None, result, session, source, index],
            )
    finally:
        connection.close()
    return path


def _recovery(
    run: QueryRunner, bindings: dict[str, int | str], *, period: str = "corpus"
) -> list[dict[str, str]]:
    """`missing_file_recovery` over the fixture project, as one mapping per row of a period."""
    arguments = [
        part for name, value in bindings.items() for part in ("--param", f"{name}={value}")
    ]
    output = run(
        "missing_file_recovery", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv", *arguments
    )
    return [row for row in mappings(output) if row["period"] == period]


def _path_failures(
    run: QueryRunner, bindings: dict[str, int | str], *, period: str = "corpus"
) -> list[dict[str, str]]:
    """`path_failures` over the fixture project, as one column mapping per row of a period."""
    arguments = [
        part for name, value in bindings.items() for part in ("--param", f"{name}={value}")
    ]
    output = run("path_failures", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv", *arguments)
    return [row for row in mappings(output) if row["period"] == period]
