"""What each analysis query takes: one manifest entry per `.sql` file, and the whole registry.

Production code, not a test table. Every parameter a query declares gets either a production
default — the value a bare invocation runs, and the value a committed report quotes — or
`REQUIRED`, for a choice the caller has to make: a defaulted line range on `records_slice`
would quietly hand back a window of raw transcript instead of an error.

`QUERIES` at the foot is the library whole: these entries and the viewer's, which are declared
beside the page sizes they carry (`view/manifest.py`). The runner binds against it and the
smoke tier holds it to the query directory, so adding a query means adding its file *and* its
entry in one of the two halves; either alone fails there.

The parameter vocabulary both halves are written in — the types, the widths, and the shared
`Param`s — is `analyze/queries.py`.
"""

from hyphae.analyze.queries import (
    COMMAND_HEAD_CHARS,
    DRAW_SEED,
    ERROR_CHARS,
    LOG_CHARS_PARAM,
    RAW_CHARS,
    REQUIRED,
    SESSION_ID,
    SIGNATURE_CHARS,
    SOURCE,
    Param,
    ParamType,
    Query,
    Scope,
)
from hyphae.view.manifest import VIEW_QUERIES

ANALYSIS: dict[str, Query] = {
    "agent_compactions": Query(scope=Scope.CORPUS, params={}),
    "agent_types": Query(scope=Scope.CORPUS, params={}),
    "co_occurrence": Query(
        scope=Scope.CORPUS,
        # A pair seen in one or two sessions is noise on any corpus worth counting. The floor
        # is bound rather than fixed because a young corpus has nothing above it.
        params={"min_sessions": Param(type=ParamType.INTEGER, default=3)},
    ),
    "context_reloads": Query(
        scope=Scope.CORPUS,
        params={
            # What a call has to write before it counts as starting over, and how much of
            # what it sent that has to be. The share is the detector; the floor only keeps
            # trivia out. Both are tuned in the query's header against the mycelia corpus.
            "min_rebuilt_tokens": Param(type=ParamType.INTEGER, default=20_000),
            "min_rebuilt_pct": Param(type=ParamType.INTEGER, default=90),
            # The gap that makes a miss explainable: Claude Code's default cache entry lives
            # 5 minutes, so a thread idle that long had no cache left to hit.
            "idle_seconds": Param(type=ParamType.INTEGER, default=300),
        },
    ),
    "idle_gaps": Query(
        scope=Scope.CORPUS,
        params={
            # Shortest silence worth a row. 300 seconds is Claude Code's default cache
            # lifetime — below it nothing had expired — and it is `context_reloads`'s
            # `idle_seconds`, so the two queries call the same waits idle.
            "min_idle_seconds": Param(type=ParamType.INTEGER, default=300),
            # The reload detector, at `context_reloads`'s production values: the `reloaded`
            # column has to mean what that query's counts mean.
            "min_rebuilt_tokens": Param(type=ParamType.INTEGER, default=20_000),
            "min_rebuilt_pct": Param(type=ParamType.INTEGER, default=90),
        },
    ),
    "reload_cost_split": Query(
        scope=Scope.CORPUS,
        params={
            # Where the split falls. No default: the bound is the claim the query makes, and
            # it moves with the pricing table a break-even was computed from and with the
            # cache lifetime a wait was racing. A defaulted one would be quoted as ours.
            "short_gap_seconds": Param(type=ParamType.INTEGER, default=REQUIRED),
            # The floor and the detector, at `idle_gaps`'s values: this splits that query's
            # population, so it has to admit and flag the same silences.
            "min_idle_seconds": Param(type=ParamType.INTEGER, default=300),
            "min_rebuilt_tokens": Param(type=ParamType.INTEGER, default=20_000),
            "min_rebuilt_pct": Param(type=ParamType.INTEGER, default=90),
        },
    ),
    "command_failures": Query(
        scope=Scope.CORPUS,
        params={
            # Keep only command lines holding this text. NULL — every command — is the survey;
            # binding it is how a command buried in a pipeline gets counted at all.
            "mentions": Param(type=ParamType.TEXT, default=None),
            # Calls a shape needs to be listed, matching the other floors in this file.
            "min_occurrences": Param(type=ParamType.INTEGER, default=5),
            "head_chars": Param(type=ParamType.INTEGER, default=COMMAND_HEAD_CHARS),
            "signature_chars": Param(type=ParamType.INTEGER, default=SIGNATURE_CHARS),
        },
    ),
    "cost_distribution": Query(scope=Scope.CORPUS, params={}),
    # The enrichment family reads tables an enrichment pass writes (`docs/enrichment.md`). A
    # store no pass has touched does not hold them, and these queries fail on it saying so.
    "enrichment_coverage": Query(scope=Scope.CORPUS, params={}),
    "enrichment_digest": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # One level, or NULL for all three. A real default, not a missing key: the sheet
            # a reader opens first is the whole session, at every level it was described at.
            "level": Param(type=ParamType.TEXT, default=None),
        },
    ),
    "error_records": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # Every thread of the session unless the caller names one. Unlike the keys above,
            # a sensible default exists and it is the question readers actually ask: where in
            # this session did anything fail?
            "source": Param(type=ParamType.TEXT, default=None),
            "max_chars": Param(type=ParamType.INTEGER, default=ERROR_CHARS),
        },
    ),
    "error_signatures": Query(
        scope=Scope.CORPUS,
        params={
            # Count a phrase wherever it sits in the error text, instead of grouping by the
            # first line. NULL — group everything — is the survey a reader runs first.
            "signature": Param(type=ParamType.TEXT, default=None),
            # Occurrences a signature needs to be listed. Five, matching the other floors in
            # this file, and for the same reason: below it a group is one session's accident.
            "min_occurrences": Param(type=ParamType.INTEGER, default=5),
            "signature_chars": Param(type=ParamType.INTEGER, default=SIGNATURE_CHARS),
        },
    ),
    "missing_file_recovery": Query(
        scope=Scope.CORPUS,
        params={
            # Calls after the failure that count as the recovery. One, because the claim is
            # about what the thread did *next*: a listing three calls later is as likely to
            # be answering the question after it.
            "within_calls": Param(type=ParamType.INTEGER, default=1),
            # Keep only failures whose text holds this phrase — "does not exist" narrows the
            # population to the ones a listing could have prevented. NULL is every failed call
            # that named a path, which is the survey a reader runs first.
            "missing": Param(type=ParamType.TEXT, default=None),
        },
    ),
    "path_failures": Query(
        scope=Scope.CORPUS,
        params={
            # Failures a directory needs to be listed, matching the other floors in this file.
            "min_occurrences": Param(type=ParamType.INTEGER, default=5),
            # Path segments the group key keeps. One is the aggregating default: it is what
            # makes a directory count the same number whichever copy of the repository the
            # call reached into, which is the whole point of grouping paths this way.
            "tail_segments": Param(type=ParamType.INTEGER, default=1),
        },
    ),
    "records_slice": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # The line range is required for the same reason the cap exists: a default would
            # hand back a window of private transcript with nothing to say it was a guess.
            "first_line": Param(type=ParamType.INTEGER, default=REQUIRED),
            "last_line": Param(type=ParamType.INTEGER, default=REQUIRED),
            "max_chars": Param(type=ParamType.INTEGER, default=RAW_CHARS),
        },
    ),
    "run_timeline": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "log_chars": LOG_CHARS_PARAM},
    ),
    "select_runs": Query(
        scope=Scope.CORPUS,
        params={
            # How many runs each `agent_type` gives up per stratum. One apiece keeps the draw
            # at roughly two runs per definition, which is the reading budget the design
            # sized.
            "runs_per_stratum": Param(type=ParamType.INTEGER, default=1),
            # In-window runs an `agent_type` needs before it earns a reading slot. Matches
            # `select_sessions`'s skill threshold, and for the same reason: both sets are
            # open, and a name used once is a session's invention, not a definition.
            "min_runs": Param(type=ParamType.INTEGER, default=5),
        },
    ),
    "select_enrichments": Query(
        scope=Scope.CORPUS,
        params={
            # Which level to check. No default: the three are different populations — 2,500
            # runs, 1,400 turns, 470 sessions — and a draw over "some level" answers nobody.
            "level": Param(type=ParamType.TEXT, default=REQUIRED),
            # Items per category. Two apiece over a fourteen-member taxonomy is a sitting's
            # worth of reading, and every member gets a reader.
            "per_category": Param(type=ParamType.INTEGER, default=2),
            "seed": DRAW_SEED,
        },
    ),
    "select_sessions": Query(
        scope=Scope.CORPUS,
        params={
            "cost_quota": Param(type=ParamType.INTEGER, default=8),
            "error_quota": Param(type=ParamType.INTEGER, default=5),
            "compaction_quota": Param(type=ParamType.INTEGER, default=4),
            "discovery_quota": Param(type=ParamType.INTEGER, default=8),
            # A skill is major when this many in-window sessions used it.
            "skill_threshold": Param(type=ParamType.INTEGER, default=5),
            "seed": DRAW_SEED,
            # Api calls a session needs to be in the pool at all. One keeps out the
            # `/model`-only sessions that took three of iteration 1's eight discovery draws;
            # it is bound rather than fixed because the filter is part of what the draw
            # claims, and a citation that omits it describes a pool nobody can reconstruct.
            "min_api_calls": Param(type=ParamType.INTEGER, default=1),
            # Api calls a session needs on top of that before *discovery* will draw it. A
            # ranked stratum is exempt: what it ranks on is the reason to read the session.
            # Ten sits in the gap the corpus itself leaves — of the 117 in-window pool
            # sessions on 2026-08-13, 47 made between 1 and 9 calls and the next made 12 —
            # and it is what half of iteration 3's discovery draw fell below.
            "min_discovery_api_calls": Param(type=ParamType.INTEGER, default=10),
        },
    ),
    "session_counts": Query(scope=Scope.CORPUS, params={}),
    "session_timeline": Query(
        scope=Scope.KEYED, params={"session_id": SESSION_ID, "log_chars": LOG_CHARS_PARAM}
    ),
    "session_overview": Query(scope=Scope.KEYED, params={"session_id": SESSION_ID}),
    "session_shapes": Query(
        scope=Scope.CORPUS,
        # The classifier's cut points. Every one is a starting guess, which is why they are
        # bound: a shape that swallows half the corpus is a threshold to move, not a finding.
        params={
            # Share of a session's api calls one skill has to carry to own the session. A
            # percentage, because a bound parameter is an integer, a date, or text.
            "skill_share_pct": Param(type=ParamType.INTEGER, default=50),
            "delegating_runs": Param(type=ParamType.INTEGER, default=3),
            "editing_calls": Param(type=ParamType.INTEGER, default=5),
            # Below this a session is conversational; at or above it with no edits it is
            # analysis. One threshold, so the two shapes cannot overlap or leave a gap.
            "busy_tool_calls": Param(type=ParamType.INTEGER, default=5),
        },
    ),
    "sessions": Query(scope=Scope.CORPUS, params={}),
    "skill_activity": Query(scope=Scope.CORPUS, params={}),
    "slash_commands": Query(scope=Scope.CORPUS, params={}),
    "tool_failures": Query(scope=Scope.CORPUS, params={}),
    "weekly_trend": Query(scope=Scope.CORPUS, params={}),
}

# The library as its two consumers read it: one name space, so a citation, a `--param` and a
# `/query/` page mean the same thing whichever half declared the query.
QUERIES: dict[str, Query] = ANALYSIS | VIEW_QUERIES
