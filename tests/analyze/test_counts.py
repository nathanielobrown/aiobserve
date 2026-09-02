"""The corpus counts that promote a recurring observation to a counted finding.

`error_signatures` answers "how often did this error happen, and to which tool";
`command_failures` answers "which command produced it" when the text does not say;
`agent_compactions` answers "which kinds of thread run out of context"; `context_reloads`
answers "what did a thread pay to rebuild a context it already had". The leaves here are
about what a group holds: which rows fall into one signature or one command shape, which
thread a compaction is counted under, and what the trailing window leaves out.

The first two need a population the recorded corpus lacks — every recorded error is a one-off
redacted down to a word and every tool input is redacted whole — so each plants one onto real rows
and says so. The last two need no plant: `compaction/`'s agent run compacted, and three recorded
fixture threads rebuilt their whole context mid-run.
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
from tests.conftest import COMPACTED, FORK_ORIGIN, MAIN, MYCELIA, SPINE

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

# The error class that splits itself: a guardrail whose first line names the worktree it
# blocked. Invented text under a fake root, because fixture redaction leaves no error body at
# all, but the shape is the canonical store's — its worktree-isolation guardrail failed 36
# times in the 2026-08-13 window and split into 28 signatures, one per worktree (`--project
# mycelia --as-of 2026-08-13 --param min_occurrences=1`). The call id stands in for the
# volatile segment.
GUARDRAIL_HEAD = "This agent is isolated in the worktree /repo/.claude/worktrees/agent-"
GUARDRAIL_TAIL = ", but this command wanted to write outside it"
# The one group they have to collapse into: the sentence, with the path standing for itself.
GUARDRAIL_SIGNATURE = f"This agent is isolated in the worktree <path>{GUARDRAIL_TAIL}"
# What the plant costs: every corpus `Bash` call — 6 over 4 sessions and 5 threads: two in `SPINE`'s
# main, one apiece in its run, `CONFIG_ONLY`, the architect run and `parallel_tools`'s auditor.
GUARDRAIL_ERRORS = 6
GUARDRAIL_SESSIONS = 4
GUARDRAIL_THREADS = 5

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
# What the wrapped grep fails with. A bare code, which is the whole problem: `Exit code 1`
# names nothing, so the command shape is the only thing left to attribute it to. The `gh`
# calls fail with the guardrail below instead, so one shape's signature is a real sentence.
EXIT_1 = "Exit code 1"
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
# The sentinel a rolled-up row carries where a thread kind would sit, `context_reloads`'s and
# `reload_cost_split`'s alike.
ALL_THREAD_KINDS = "(all)"
# The definition of the one recorded run that compacted, and how many threads it has: the
# ratio a reader ranks definitions by needs a denominator bigger than the compaction.
COMPACTED_DEFINITION = "general-purpose"
COMPACTED_RUN_THREADS = 3

# The three threads of the recorded corpus that rebuilt their context mid-run, and what each
# rebuilt (measured 2026-08-27 by building the store below). `ARCHITECT_RUN` is the sharper
# case: both of its calls read nothing back, so its opening load is a rebuild in every
# respect except being the one the thread started with.
ARCHITECT_RUN = "aarchitect-5144001ac50718bc"
ARCHITECT_SESSION = "10d0349d-0705-4e23-aa64-5b1b97698b2e"
ARCHITECT_DEFINITION = "architect"
ARCHITECT_OPENING_TOKENS = 23_444
ARCHITECT_RELOAD_TOKENS = 89_383
# The silence its rebuild followed: 6,035 seconds, an hour and forty minutes — shorter than the two
# main-thread waits below, which is what puts the corpus's idle reloads either side of a bound.
ARCHITECT_IDLE_SECONDS = 6_035
# `SPINE`'s main thread went 23,276 seconds — 6h27m — between two calls and rebuilt 94,194
# tokens on the far side, so its gap is what a rebound `$idle_seconds` can be walked past.
SPINE_RELOAD_TOKENS = 94_194
SPINE_IDLE_SECONDS = 23_276
# `COMPACTED`'s main thread is the third, and the only one whose rebuild followed a
# compaction: 21,648 seconds of silence over a boundary, 36,465 tokens on the far side.
COMPACTED_RELOAD_TOKENS = 36_465
COMPACTED_IDLE_SECONDS = 21_648
# The shortest silence the recorded corpus has over the five-minute floor: the 302 seconds
# `COMPACTED`'s agent run spent compacting and rebuilding. The silence that pins the measure is
# `ANCESTOR`'s — 319 seconds between two requests, 281 from the first one's reply. A cache entry
# ages from the request that wrote it, so it clears the 300-second floor; measured end to start it
# would fall out of the table.
SHORTEST_IDLE_SECONDS = 302
REQUEST_MEASURED_IDLE_SECONDS = 319
# How many silences over that floor the recorded corpus holds: nine in main threads, two in
# agent runs. The raw table holds two more — `corpus_api_calls` hides a resumed thread's
# replayed rows, and a gap between two of them is not the corpus's to count.
RECORDED_IDLE_GAPS = 11


def test_error_signatures_counts_one_signature_over_many_bodies(
    planted_failures_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Errors that differ only after their first line are counted as one recurring error."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_failures_db, capsys, name, *arguments)

    # If eight tool calls failed with the same opening line and a different body each — the
    # shape of a recurring error, planted because the recorded ones are one-offs — spread
    # over two sessions and three threads...
    rows = [
        row
        for row in _signatures(planted_query, {"min_occurrences": 2})
        if row["tool"] == PLANTED_TOOL
    ]
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

    # If the corpus holds the two planted signatures and the two recorded one-off errors...
    every = _signatures(planted_query, {"min_occurrences": 1}, period="corpus")
    assert sorted(row["signature"] for row in every) == sorted(
        [SIGNATURE, GUARDRAIL_SIGNATURE, *RECORDED_SIGNATURES]
    )
    # ...then the floor keeps the singletons out, which is what bounds a listing on a corpus
    # where most error text is unique...
    kept = _signatures(planted_query, {"min_occurrences": 2}, period="corpus")
    assert [row["signature"] for row in kept] == [SIGNATURE, GUARDRAIL_SIGNATURE]
    # ...and binding a phrase counts just the error holding it, matched anywhere in the text
    # rather than only in the line the signature is cut from — a tail is where the path sits.
    bound = _signatures(planted_query, {"min_occurrences": 1, "signature": "tail for "})
    assert [row["signature"] for row in bound] == [SIGNATURE]
    assert int(bound[0]["errors"]) == PLANTED_ERRORS


def test_error_signatures_groups_past_a_path_inside_the_line(
    planted_failures_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One guardrail message is one error, however many worktrees it names."""

    def planted_query(name: str, *arguments: str) -> Output:
        return query(planted_failures_db, capsys, name, *arguments)

    # If five calls failed with one guardrail message whose *first line* names the worktree it
    # blocked — a different path each time, so the cut that keeps a trailing path out of the
    # signature cannot help...
    rows = [
        row
        for row in _signatures(planted_query, {"min_occurrences": 2})
        if row["signature"] == GUARDRAIL_SIGNATURE
    ]
    # ...then they are one recurring error rather than one group per worktree, which is what
    # iteration 3's isolation guardrail had been split into...
    assert len(rows) == 1
    assert int(rows[0]["errors"]) == GUARDRAIL_ERRORS
    assert int(rows[0]["sessions"]) == GUARDRAIL_SESSIONS
    assert int(rows[0]["threads"]) == GUARDRAIL_THREADS
    # ...and the path is gone from the output rather than shortened, so no signature a report
    # quotes carries a run of somebody's filesystem.
    every = _signatures(planted_query, {"min_occurrences": 1}, period="corpus")
    assert not [row for row in every if "/" in row["signature"]]


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
    # would put `gh pr checks` and `gh pr create` in one group. Its two calls failed with a
    # guardrail naming a different worktree each, and land in one group all the same: the
    # signature is normalized here the way `error_signatures` normalizes it.
    assert int(shapes[GH_HEAD, GUARDRAIL_SIGNATURE]["calls"]) == GH_CHECKS_CALLS
    # Nothing else reaches the output: no head carries a flag, a path, or a quoted argument,
    # and no signature carries a path either...
    assert not [row for row in rows if set(row["command_head"]) & set("-/\"'")]
    assert not [row for row in rows if "/" in row["signature"]]
    # ...and `$head_chars` cuts whatever is left, which is the backstop under that rule — a
    # command line is private text, and a shape nobody anticipated must not carry a run of it.
    capped = _command_failures(planted_query, {"min_occurrences": 1, "head_chars": 4})
    assert max(len(row["command_head"]) for row in capped) == 4


def test_agent_compactions_counts_a_compaction_under_the_thread_that_had_it(
    run_query: QueryRunner, corpus_db: Path
) -> None:
    """A run that ran out of context is counted against its definition, not its session."""
    # If one compaction happened inside an agent run rather than on a main thread —
    # `compaction/`'s `general-purpose` run, the only one the corpus records...
    rows = _compactions(run_query)
    # ...then it is counted under that run's definition, once, and against every run the
    # definition has — which is the ratio a reader ranks definitions by...
    definition = rows[COMPACTED_DEFINITION]
    assert (int(definition["compactions"]), int(definition["compacting_threads"])) == (1, 1)
    assert int(definition["threads"]) == COMPACTED_RUN_THREADS
    assert float(definition["compactions_per_thread"]) == round(1 / COMPACTED_RUN_THREADS, 2)
    # ...and it is counted there instead of under the session's own thread: every compaction
    # the period holds is in exactly one row, so the column sums to the store's own total.
    total = scalar(
        corpus_db,
        """SELECT count(*) FROM corpus_compactions k JOIN sessions s ON s.id = k.session_id
           WHERE s.project_dir = ?""",
        MYCELIA,
    )
    assert total > 1
    assert sum(int(row["compactions"]) for row in rows.values()) == total


def test_agent_compactions_separates_how_many_threads_from_how_often(
    run_query: QueryRunner, corpus_db: Path
) -> None:
    """The main thread's row says both how many sessions compacted and how often they did."""
    # If one session's main thread compacted twice and others compacted once...
    threads, compactions = scalar(
        corpus_db,
        """SELECT count(DISTINCT k.session_id), count(*)
           FROM corpus_compactions k JOIN sessions s ON s.id = k.session_id
           WHERE s.project_dir = ? AND k.source = 'main'""",
        MYCELIA,
        columns=2,
    )
    assert compactions > threads
    # ...then the main-thread row keeps the two apart, so "most sessions compact" and "a few
    # sessions compact repeatedly" cannot be read for one another...
    rows = _compactions(run_query)
    assert int(rows[MAIN_THREAD]["compacting_threads"]) == threads
    assert int(rows[MAIN_THREAD]["compactions"]) == compactions
    # ...and its population is every session in the period, not only the ones that compacted,
    # so the rate underneath is a rate and not a share of the sessions that already did.
    sessions = scalar(corpus_db, "SELECT count(*) FROM sessions WHERE project_dir = ?", MYCELIA)
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
    # Each grain rounds its own sum, so the total sits within half a cent per row of theirs.
    parts = [float(row["thread_cost_usd"]) for row in threads.values()]
    assert float(total["thread_cost_usd"]) == pytest.approx(sum(parts), abs=0.005 * len(rows))
    # ...and the counts a finding would quote add up the same way, which is what a session
    # sitting in both periods must not disturb.
    for column in ("reloads", "idle_reloads", "rebuilt_tokens"):
        assert int(total[column]) == sum(int(row[column]) for row in threads.values())
    assert int(total["sessions"]) == len({session for session, _ in threads})


def test_context_reloads_reads_a_call_once_however_many_periods_hold_it(
    run_query: QueryRunner,
) -> None:
    """A session that sits in both periods is measured once, not once per period."""
    # If every session of the corpus is also inside the trailing window, so both periods hold
    # exactly the same threads...
    threads = _threads(_reloads(run_query, {}, period="corpus"))
    window = _threads(_reloads(run_query, {}, period="trailing_window"))
    assert threads.keys() == window.keys()
    # ...then the two periods report identical numbers for every one of them. The gap and the
    # thread's first call are read from the calls of one thread, and `session_period` carries
    # an in-window session twice — so a query that fanned the periods out before measuring
    # would sit each call next to its own copy and report gaps of zero. DuckDB is free to
    # compute the windows before the join, which hides that mistake on some runs, so read this
    # leaf as a probabilistic guard on the join order and the query's own note as the rule.
    for key, thread in threads.items():
        assert {name: value for name, value in window[key].items() if name != "period"} == {
            name: value for name, value in thread.items() if name != "period"
        }
    # ...and at least one of those threads had a gap to measure, so the agreement is evidence.
    assert any(int(row["idle_reloads"]) > 0 for row in threads.values())


def test_idle_gaps_gives_the_wait_that_context_reloads_only_flags(
    run_query: QueryRunner,
) -> None:
    """A silence the other query marks idle arrives here as its length in seconds."""
    # If a thread went hours between two calls and rebuilt everything on the far side, so
    # `context_reloads` counts one idle reload against it...
    assert int(_threads(_reloads(run_query, {}))[SPINE, MAIN]["idle_reloads"]) == 1
    # ...then that silence has a row of its own here, carrying the wait itself — the number a
    # reader needs to ask how much of a population sits under a break-even...
    thread = [
        row for row in _gaps(run_query, {}) if (row["session_id"], row["source"]) == (SPINE, MAIN)
    ]
    (idle,) = [row for row in thread if row["reloaded"] == "True"]
    assert int(idle["idle_seconds"]) == SPINE_IDLE_SECONDS
    # ...beside what the call that broke it rebuilt and which kind of thread waited, so a gap
    # can be priced without going back to the query that flagged it.
    assert int(idle["rebuilt_tokens"]) == SPINE_RELOAD_TOKENS
    assert idle["agent_type"] == MAIN_THREAD
    # ...and beside the lifetime the wait was racing, since the call before it had paid for
    # hour-long cache entries — a six-hour silence outlives those too, but a threshold read
    # without that column would put every gap against the five-minute default.
    assert idle["cached_1h"] == "True"


def test_idle_gaps_keeps_the_silences_that_ended_in_no_rebuild(run_query: QueryRunner) -> None:
    """A wait nothing rebuilt is a row too: it is the denominator of the waits that did."""
    # If the corpus holds waits over the floor that cost nothing on the far side...
    gaps = _gaps(run_query, {})
    assert [row for row in gaps if row["reloaded"] == "False"]
    # ...then each is listed once however many periods hold its session, because a detail row
    # counted twice is a population sized twice...
    keys = [(row["session_id"], row["source"], row["gap_start"]) for row in gaps]
    assert len(set(keys)) == len(keys) == RECORDED_IDLE_GAPS
    # ...each one measured request to request, the interval a cache entry ages over: one
    # silence ran 319 seconds between requests and 281 from the first reply, and it is the
    # request pair that decides it clears the five-minute floor...
    lengths = {int(row["idle_seconds"]) for row in gaps}
    assert REQUEST_MEASURED_IDLE_SECONDS in lengths
    assert min(lengths) == SHORTEST_IDLE_SECONDS
    # ...and the floor is the caller's: dropped to nothing it admits the short waits no cache
    # could have expired over, and raised past the longest silence it admits none.
    assert len(_gaps(run_query, {"min_idle_seconds": 0})) > len(gaps)
    longest = max(int(row["idle_seconds"]) for row in gaps)
    assert _gaps(run_query, {"min_idle_seconds": longest + 1}) == []


@pytest.mark.parametrize("rebuilt_pct", [90, 50])
def test_idle_gaps_calls_the_same_waits_idle_that_context_reloads_does(
    run_query: QueryRunner, rebuilt_pct: int
) -> None:
    """The gaps that ended in a rebuild are exactly the idle reloads the other query counts."""
    # If both queries are asked what a rebuild is on the same terms — the shared detector, at
    # its production share and at a looser one...
    bindings: dict[str, int | str] = {"min_rebuilt_pct": rebuilt_pct}
    reloaded = [row for row in _gaps(run_query, bindings) if row["reloaded"] == "True"]
    (corpus,) = [row for row in _reloads(run_query, bindings) if row["grain"] == "corpus"]
    # ...then the silences this one says were followed by a rebuild are, one for one, the
    # `idle_reloads` the other counts. The two answer one question at two grains, so a reader
    # who thresholds these lengths is narrowing that count rather than a different one.
    assert len(reloaded) == int(corpus["idle_reloads"]) > 0


def test_reload_cost_split_says_what_share_of_a_rebuild_bill_short_waits_ran_up(
    run_query: QueryRunner,
) -> None:
    """The tokens rebuilt after short silences, as a share of everything idle waits rebuilt."""
    # If the corpus's three idle reloads sit either side of a bound — `SPINE`'s six-hour main
    # thread wait over it, `COMPACTED`'s six-hour one and an agent run's hour and forty
    # minutes under it. Keyed by thread, because two of the three waited on a main thread...
    reloaded = {
        (row["session_id"], row["source"]): row
        for row in _gaps(run_query, {})
        if row["reloaded"] == "True"
    }
    assert {key: int(row["idle_seconds"]) for key, row in reloaded.items()} == {
        (SPINE, MAIN): SPINE_IDLE_SECONDS,
        (COMPACTED, MAIN): COMPACTED_IDLE_SECONDS,
        (ARCHITECT_SESSION, ARCHITECT_RUN): ARCHITECT_IDLE_SECONDS,
    }
    # ...then splitting at the longest puts two reloads on the short side and one above...
    rows = _split(run_query, {"short_gap_seconds": SPINE_IDLE_SECONDS})
    corpus = rows[ALL_THREAD_KINDS]
    assert (int(corpus["reloads"]), int(corpus["short_reloads"])) == (3, 2)
    # ...and the query's two shares come out as different numbers, which is the whole reason
    # it exists: two thirds of the events are not two thirds of the bill.
    short_tokens = ARCHITECT_RELOAD_TOKENS + COMPACTED_RELOAD_TOKENS
    every_token = short_tokens + SPINE_RELOAD_TOKENS
    assert float(corpus["short_reload_pct"]) == round(100 * 2 / 3, 1)
    assert int(corpus["rebuilt_tokens"]) == every_token
    assert int(corpus["short_rebuilt_tokens"]) == short_tokens
    share = 100 * short_tokens / every_token
    assert float(corpus["short_token_pct"]) == round(share, 1) != round(100 * 2 / 3, 1)
    # ...filed under the kind of thread that waited, so a recommendation scoped to short gaps
    # can say which threads it would apply to instead of inferring it from a corpus total.
    assert int(rows[ARCHITECT_DEFINITION]["short_rebuilt_tokens"]) == ARCHITECT_RELOAD_TOKENS
    assert int(rows[MAIN_THREAD]["short_rebuilt_tokens"]) == COMPACTED_RELOAD_TOKENS


def test_reload_cost_split_counts_the_silences_that_rebuilt_nothing(run_query: QueryRunner) -> None:
    """Every wait is in the split, not only the ones that ended in a rebuild."""
    # If the corpus holds more silences than reloads — the denominator a keep-warm heartbeat
    # would fire over, since it pays on the waits that would have cost nothing too...
    gaps = _gaps(run_query, {})
    rows = _split(run_query, {"short_gap_seconds": SPINE_IDLE_SECONDS})
    corpus = rows[ALL_THREAD_KINDS]
    assert int(corpus["gaps"]) == len(gaps) == RECORDED_IDLE_GAPS > int(corpus["reloads"])
    short = [row for row in gaps if int(row["idle_seconds"]) < SPINE_IDLE_SECONDS]
    assert int(corpus["short_gaps"]) == len(short)
    # ...and the thread kinds under it partition that population, so no wait is counted twice
    # or dropped between them.
    kinds = [row for name, row in rows.items() if name != ALL_THREAD_KINDS]
    assert sum(int(row["gaps"]) for row in kinds) == RECORDED_IDLE_GAPS


def test_reload_cost_split_is_bound_at_the_length_the_caller_names(run_query: QueryRunner) -> None:
    """The bound is the caller's, and a wait is short only when it ran strictly under it."""
    # If the bound is raised by one second past the longest reloaded wait...
    inclusive = _split(run_query, {"short_gap_seconds": SPINE_IDLE_SECONDS + 1})
    # ...then that wait joins the short side and the whole bill sits on it — which is what
    # pins the comparison as strict rather than inclusive, since the bound at the wait's own
    # length left it out above.
    assert int(inclusive[ALL_THREAD_KINDS]["short_reloads"]) == 3
    assert float(inclusive[ALL_THREAD_KINDS]["short_token_pct"]) == 100.0
    # ...while a bound under every recorded silence reports a share of zero rather than an
    # empty one: no short reload is a number, not a missing answer.
    none = _split(run_query, {"short_gap_seconds": SHORTEST_IDLE_SECONDS})[ALL_THREAD_KINDS]
    assert (int(none["short_gaps"]), int(none["short_reloads"])) == (0, 0)
    assert float(none["short_token_pct"]) == float(none["short_reload_pct"]) == 0.0


@pytest.fixture(scope="session")
def planted_failures_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with two recurring errors planted: one splitting after its first line, one
    inside it.

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
        # Every `Bash` call gets the guardrail, whose volatile segment is the call id: one
        # message class over four worktrees, which the corpus has and no fixture records.
        connection.execute(
            "UPDATE tool_calls SET is_error = true, result = ? || id || ? WHERE name = 'Bash'",
            [GUARDRAIL_HEAD, GUARDRAIL_TAIL],
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
        for call_id, (command, fails) in zip(
            ids,
            [(GH_CHECKS, True)] * GH_CHECKS_CALLS + [(BARE_GREP, False)] * BARE_GREP_CALLS,
            strict=True,
        ):
            # The failing ones carry the guardrail, whose volatile segment is the call id, so
            # this query's signature has the same path to normalize away that its own does.
            connection.execute(
                """UPDATE tool_calls SET name = 'Bash', input = ?, is_error = ?, result = ?
                   WHERE id = ? AND session_id = ? AND NOT replayed""",
                [
                    json.dumps({"command": command}),
                    fails,
                    f"{GUARDRAIL_HEAD}{call_id}{GUARDRAIL_TAIL}" if fails else None,
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


def _gaps(run: QueryRunner, bindings: dict[str, int | str]) -> list[dict[str, str]]:
    """`idle_gaps` over the fixture project, as one column mapping per silence."""
    arguments = [
        part for name, value in bindings.items() for part in ("--param", f"{name}={value}")
    ]
    output = run("idle_gaps", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv", *arguments)
    return mappings(output)


def _split(
    run: QueryRunner, bindings: dict[str, int | str], *, period: str = "corpus"
) -> dict[str, dict[str, str]]:
    """`reload_cost_split` over the fixture project, by thread kind, for one period."""
    arguments = [
        part for name, value in bindings.items() for part in ("--param", f"{name}={value}")
    ]
    output = run(
        "reload_cost_split", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv", *arguments
    )
    return {row["agent_type"]: row for row in mappings(output) if row["period"] == period}


def _threads(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    """The thread-grain rows of one `context_reloads` result, by session and source."""
    return {(row["session_id"], row["source"]): row for row in rows if row["grain"] == "thread"}


def _compactions(run: QueryRunner, *, period: str = "corpus") -> dict[str, dict[str, str]]:
    """`agent_compactions` over the fixture project, by `agent_type`, for one period."""
    output = run("agent_compactions", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv")
    return {row["agent_type"]: row for row in mappings(output) if row["period"] == period}
