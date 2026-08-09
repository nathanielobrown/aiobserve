"""The corpus counts that promote a recurring observation to a counted finding.

`error_signatures` answers "how often did this error happen, and to which tool";
`command_failures` answers "which command produced it" when the text does not say;
`agent_compactions` answers "which kinds of thread run out of context"; `context_reloads`
answers "what did a thread pay to rebuild a context it already had". The leaves here are
about what a group holds: which rows fall into one signature or one command shape, which
thread a compaction is counted under, and what the trailing window leaves out.

The first three need a population the recorded corpus lacks — every recorded error is a
one-off redacted down to a word, every tool input is redacted whole, and no recorded run
compacted — so each plants one onto real rows and says so. `context_reloads` needs no plant:
two recorded fixture threads rebuilt their whole context mid-run.
"""

import json
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
from tests.conftest import FORK_ORIGIN, MAIN, MYCELIA, SPINE

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

# Command lines planted onto real calls so `command_failures` has command text to shape. They
# are invented — every fixture tool input is `[redacted]` — but the shapes are the canonical
# store's: over `--project mycelia --as-of 2026-08-07`, 839 of the window's 1,487 failed Bash
# commands open with a `cd … &&` wrapper, so the head after the wrapper is what attribution
# needs, and `gh pr checks` is one of the two benign patterns iteration 1 could not count.
WRAPPED_GREP = 'cd /tmp/fixture-worktree && grep -rn "pattern" src/ | head -20'
BARE_GREP = "grep -c pattern README.md"
GH_CHECKS = "gh pr checks --watch"
# What each of those lines has to reduce to: the command word plus the bare words after it,
# with the wrapper, the flags, the quoted pattern and the paths gone.
GREP_HEAD = "grep"
GH_HEAD = "gh pr checks"
# The two exit codes the plant fails with. Bare codes, which is the whole problem: `Exit code
# 1` names nothing, so the command shape is the only thing left to attribute it to.
EXIT_1 = "Exit code 1"
EXIT_8 = "Exit code 8"
# How the plant is spread: `SPINE`'s four `Read` calls, over its two threads, become wrapped
# grep failures; `FORK_ORIGIN`'s four become two `gh pr checks` failures and two grep calls
# that succeeded — the denominator an error count is read against.
WRAPPED_GREP_CALLS = 4
WRAPPED_GREP_THREADS = 2
GH_CHECKS_CALLS = 2
BARE_GREP_CALLS = 2

# The row `agent_compactions` gives a session's own thread, so a definition's rate has the
# thing it has to beat beside it. The query writes the sentinel; nothing in Python reads it.
MAIN_THREAD = "(main thread)"
# The agent definition the planted compaction lands under.
PLANTED_DEFINITION = "auditor"

# The two threads of the recorded corpus that rebuilt their context mid-run, and what each
# rebuilt (measured 2026-08-09 by building the store below). `ARCHITECT_RUN` is the sharper
# case: both of its calls read nothing back, so its opening load is a rebuild in every
# respect except being the one the thread started with.
ARCHITECT_RUN = "aarchitect-5144001ac50718bc"
ARCHITECT_SESSION = "10d0349d-0705-4e23-aa64-5b1b97698b2e"
ARCHITECT_DEFINITION = "architect"
ARCHITECT_OPENING_TOKENS = 23_444
ARCHITECT_RELOAD_TOKENS = 89_383
# `SPINE`'s main thread went 23,773 seconds — 6h36m — between two calls and rebuilt 94,194
# tokens on the far side, so its gap is what a rebound `$idle_seconds` can be walked past.
SPINE_RELOAD_TOKENS = 94_194
SPINE_IDLE_SECONDS = 23_773


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


def test_command_failures_groups_by_the_shape_of_the_command_line(
    planted_commands_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Failures of one command are counted together however the command line was wrapped."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_commands_db, capsys, name, *arguments)

    rows = _command_failures(planted_query, {"min_occurrences": 1})
    shapes = {(row["command_head"], row["signature"]): row for row in rows}
    # If four calls failed with a bare `Exit code 1` behind a `cd … &&` wrapper...
    wrapped = shapes[GREP_HEAD, EXIT_1]
    # ...then the wrapper, the flags, the quoted pattern and the paths are all gone, and what
    # is left is the command word — which is the attribution the error text cannot give...
    assert int(wrapped["calls"]) == WRAPPED_GREP_CALLS
    assert int(wrapped["threads"]) == WRAPPED_GREP_THREADS
    # ...with the head marked as standing for a chain, so nobody reads it as the whole command.
    assert int(wrapped["chained"]) == WRAPPED_GREP_CALLS
    # ...and two calls of the same command that succeeded come back as their own row, under a
    # NULL signature: the denominator that says whether the failures are the norm for it...
    assert int(shapes[GREP_HEAD, ""]["calls"]) == BARE_GREP_CALLS
    assert int(shapes[GREP_HEAD, ""]["chained"]) == 0
    # ...while the head's error total rides on both rows, so ranking shapes by failures takes
    # no arithmetic.
    assert {int(shapes[GREP_HEAD, key]["head_errors"]) for key in (EXIT_1, "")} == {
        WRAPPED_GREP_CALLS
    }
    # ...and a command whose subcommands name what it did keeps them, because `gh` alone
    # would put `gh pr checks` and `gh pr create` in one group.
    assert int(shapes[GH_HEAD, EXIT_8]["calls"]) == GH_CHECKS_CALLS
    # Nothing else reaches the output: no head carries a flag, a path, or a quoted argument...
    assert not [row for row in rows if set(row["command_head"]) & set("-/\"'")]
    # ...and `$head_chars` cuts whatever is left, which is the backstop under that rule — a
    # command line is private text, and a shape nobody anticipated must not carry a run of it.
    capped = _command_failures(planted_query, {"min_occurrences": 1, "head_chars": 4})
    assert max(len(row["command_head"]) for row in capped) == 4


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


def test_context_reloads_leaves_out_the_context_a_thread_loaded_to_start(
    run_query: QueryRunner, corpus_db: Path
) -> None:
    """A thread's opening load is not a reload, however cold it was."""
    # If a run's first call read nothing back and wrote its whole prompt to the cache — the
    # shape of a reload, and above the floor one has to clear...
    opening = scalar(
        corpus_db,
        """SELECT cache_read_tokens, cache_creation_tokens FROM api_calls
           WHERE session_id = ? AND source = ? ORDER BY "index" LIMIT 1""",
        ARCHITECT_SESSION,
        ARCHITECT_RUN,
        columns=2,
    )
    assert opening == (0, ARCHITECT_OPENING_TOKENS)
    # ...then it is the later rebuild alone that the run's row counts, because a thread that
    # loads its context once has not started over...
    row = _threads(_reloads(run_query, {}))[ARCHITECT_SESSION, ARCHITECT_RUN]
    assert (int(row["reloads"]), int(row["rebuilt_tokens"])) == (1, ARCHITECT_RELOAD_TOKENS)
    # ...filed under the definition that ran it and the session that spawned it, which is the
    # row a report cites when it names a run...
    assert row["agent_type"] == ARCHITECT_DEFINITION
    # ...and what the whole run cost rides beside it, so the rebuild is readable as a share of
    # the spend it taxed rather than as a number with no denominator.
    assert 0 < float(row["reload_cost_usd"]) < float(row["thread_cost_usd"])


def test_context_reloads_says_which_reloads_an_expired_cache_explains(
    run_query: QueryRunner,
) -> None:
    """The idle gap classifies a reload; it never decides whether one is counted."""
    # If a thread went hours between two calls and rebuilt everything on the far side...
    row = _threads(_reloads(run_query, {}))[SPINE, MAIN]
    assert (int(row["reloads"]), int(row["rebuilt_tokens"])) == (1, SPINE_RELOAD_TOKENS)
    # ...then at the five minutes a cache entry lives, the gap accounts for the miss...
    assert int(row["idle_reloads"]) == 1
    # ...while asking for a gap longer than the thread's leaves the reload counted and no
    # longer accounted for — which is the reading the column exists to keep honest, since a
    # miss with the thread still working is a miss the transcript cannot explain.
    patient = _threads(_reloads(run_query, {"idle_seconds": SPINE_IDLE_SECONDS + 1}))[SPINE, MAIN]
    assert (int(patient["reloads"]), int(patient["idle_reloads"])) == (1, 0)


@pytest.mark.parametrize("period", ["corpus", "trailing_window"])
def test_context_reloads_totals_the_threads_it_lists(run_query: QueryRunner, period: str) -> None:
    """The corpus row of a period is the sum of that period's thread rows."""
    # If a period holds several affected threads across several sessions...
    rows = _reloads(run_query, {}, period=period)
    threads = _threads(rows)
    assert len(threads) > 1
    # ...then the row above them totals the threads rather than the events, so no thread's
    # cost is counted once per reload it happened to hold...
    (total,) = [row for row in rows if row["grain"] == "corpus"]
    assert int(total["threads"]) == len(threads)
    assert float(total["thread_cost_usd"]) == sum(
        float(row["thread_cost_usd"]) for row in threads.values()
    )
    # ...and the counts a finding would quote add up the same way, which is what a session
    # sitting in both periods must not disturb.
    for column in ("reloads", "idle_reloads", "rebuilt_tokens"):
        assert int(total[column]) == sum(int(row[column]) for row in threads.values())
    assert int(total["sessions"]) == len({session for session, _ in threads})


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


@pytest.fixture(scope="session")
def planted_commands_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with eight real calls rewritten as Bash calls carrying a command line.

    Invented text, and it has to be: fixture redaction replaces every tool input, so the
    recorded corpus holds eight `[redacted]` command lines and no failed one at all. What is
    real here is the rows — their sessions, threads and periods — and the shapes the lines
    were drawn from, which are the canonical store's (see the constants above).
    """
    path = tmp_path_factory.mktemp("commands") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    connection = duckdb.connect(str(path))
    try:
        # `SPINE`'s reads become the wrapped failures, spread over the two threads it has...
        connection.execute(
            """UPDATE tool_calls SET name = 'Bash', input = ?, is_error = true, result = ?
               WHERE name = ? AND session_id = ?""",
            [json.dumps({"command": WRAPPED_GREP}), EXIT_1, PLANTED_TOOL, SPINE],
        )
        # ...and `FORK_ORIGIN`'s split into the failing `gh` calls and the succeeding ones.
        # Its fork replays every call under a second source, so only the live rows are rewritten.
        ids = [
            row[0]
            for row in connection.execute(
                """SELECT id FROM tool_calls
                   WHERE name = ? AND session_id = ? AND NOT replayed ORDER BY id""",
                [PLANTED_TOOL, FORK_ORIGIN],
            ).fetchall()
        ]
        assert len(ids) == GH_CHECKS_CALLS + BARE_GREP_CALLS
        for call_id, (command, error) in zip(
            ids,
            [(GH_CHECKS, EXIT_8)] * GH_CHECKS_CALLS + [(BARE_GREP, None)] * BARE_GREP_CALLS,
            strict=True,
        ):
            connection.execute(
                """UPDATE tool_calls SET name = 'Bash', input = ?, is_error = ?, result = ?
                   WHERE id = ? AND session_id = ? AND NOT replayed""",
                [
                    json.dumps({"command": command}),
                    error is not None,
                    error,
                    call_id,
                    FORK_ORIGIN,
                ],
            )
    finally:
        connection.close()
    return path


def _command_failures(
    run: QueryRunner, bindings: dict[str, int | str], *, period: str = "corpus"
) -> list[dict[str, str]]:
    """`command_failures` over the fixture project, as one column mapping per row of a period."""
    arguments = [
        part for name, value in bindings.items() for part in ("--param", f"{name}={value}")
    ]
    output = run(
        "command_failures", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv", *arguments
    )
    return [row for row in mappings(output) if row["period"] == period]


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


def _reloads(
    run: QueryRunner, bindings: dict[str, int | str], *, period: str = "corpus"
) -> list[dict[str, str]]:
    """`context_reloads` over the fixture project, as one column mapping per row of a period."""
    arguments = [
        part for name, value in bindings.items() for part in ("--param", f"{name}={value}")
    ]
    output = run(
        "context_reloads", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv", *arguments
    )
    return [row for row in mappings(output) if row["period"] == period]


def _threads(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    """The thread-grain rows of one `context_reloads` result, by session and source."""
    return {(row["session_id"], row["source"]): row for row in rows if row["grain"] == "thread"}


def _compactions(run: QueryRunner, *, period: str = "corpus") -> dict[str, dict[str, str]]:
    """`agent_compactions` over the fixture project, by `agent_type`, for one period."""
    output = run("agent_compactions", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv")
    return {row["agent_type"]: row for row in mappings(output) if row["period"] == period}
