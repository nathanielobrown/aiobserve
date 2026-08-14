"""The counts that read a tool call's path rather than its error text.

`path_failures` answers "which directory was the failing call pointed at" — the question
`error_signatures` cannot answer, because the path is exactly what its signature drops.

Every recorded fixture input is `[redacted]`, so a path a query can group by is something no
recording carries. Both leaves plant one and say so; what stays real is the rows the plant
lands on — their sessions, threads, tools and periods.
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


def _path_failures(
    run: QueryRunner, bindings: dict[str, int | str], *, period: str = "corpus"
) -> list[dict[str, str]]:
    """`path_failures` over the fixture project, as one column mapping per row of a period."""
    arguments = [
        part for name, value in bindings.items() for part in ("--param", f"{name}={value}")
    ]
    output = run("path_failures", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv", *arguments)
    return [row for row in mappings(output) if row["period"] == period]
