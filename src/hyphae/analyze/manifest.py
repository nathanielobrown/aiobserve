"""The library's production defaults, and the function every consumer reads a query through.

Production code, not a test table. A query is described by its own statement — what it binds
and what it counts across — and the one thing a statement cannot say about itself is which
value a bare invocation should run: the number a committed report quotes. That is `DEFAULTS`.

A parameter absent from `DEFAULTS` is required, which is how the keys stay keys: a timeline of
"some session" is not a question anyone asked, and a defaulted line range on `records_slice`
would hand back a window of raw transcript instead of an error. The viewer's half declares no
defaults at all — a page size belongs to the surface that prints it (`view/bounds.py`).

The parameter vocabulary both halves are written in — the types and the widths — is
`analyze/queries.py`.
"""

from hyphae.analyze import queries
from hyphae.analyze.queries import (
    COMMAND_HEAD_CHARS,
    CORPUS_RELATIONS,
    DRAW_SEED,
    ERROR_CHARS,
    LOG_CHARS,
    PARAM_TYPES,
    RAW_CHARS,
    REQUIRED,
    SIGNATURE_CHARS,
    Param,
    ParamValue,
    Query,
    QueryError,
    Scope,
    parameters,
    relations,
    statement,
)

DEFAULTS: dict[str, dict[str, ParamValue]] = {
    # A pair seen in one or two sessions is noise on any corpus worth counting. The floor
    # is bound rather than fixed because a young corpus has nothing above it.
    "co_occurrence": {"min_sessions": 3},
    "context_reloads": {
        # What a call has to write before it counts as starting over, and how much of
        # what it sent that has to be. The share is the detector; the floor only keeps
        # trivia out. Both are tuned in the query's header against the mycelia corpus.
        "min_rebuilt_tokens": 20_000,
        "min_rebuilt_pct": 90,
        # The gap that makes a miss explainable: Claude Code's default cache entry lives
        # 5 minutes, so a thread idle that long had no cache left to hit.
        "idle_seconds": 300,
    },
    "idle_gaps": {
        # Shortest silence worth a row. 300 seconds is Claude Code's default cache
        # lifetime — below it nothing had expired — and it is `context_reloads`'s
        # `idle_seconds`, so the two queries call the same waits idle.
        "min_idle_seconds": 300,
        # The reload detector, at `context_reloads`'s production values: the `reloaded`
        # column has to mean what that query's counts mean.
        "min_rebuilt_tokens": 20_000,
        "min_rebuilt_pct": 90,
    },
    # `short_gap_seconds` is the parameter this query has no default for, for the reason its
    # header gives: the bound is the claim.
    "reload_cost_split": {
        # The floor and the detector, at `idle_gaps`'s values: this splits that query's
        # population, so it has to admit and flag the same silences.
        "min_idle_seconds": 300,
        "min_rebuilt_tokens": 20_000,
        "min_rebuilt_pct": 90,
    },
    "command_failures": {
        # Keep only command lines holding this text. NULL — every command — is the survey;
        # binding it is how a command buried in a pipeline gets counted at all.
        "mentions": None,
        # Calls a shape needs to be listed, matching the other floors in this file.
        "min_occurrences": 5,
        "head_chars": COMMAND_HEAD_CHARS,
        "signature_chars": SIGNATURE_CHARS,
    },
    # One level, or NULL for all three. A real default, not a missing key: the sheet a reader
    # opens first is the whole session, at every level it was described at.
    "enrichment_digest": {"level": None},
    "error_records": {
        # Every thread of the session unless the caller names one. Unlike the session it is
        # keyed by, a sensible default exists and it is the question readers actually ask:
        # where in this session did anything fail?
        "source": None,
        "max_chars": ERROR_CHARS,
    },
    "error_signatures": {
        # Count a phrase wherever it sits in the error text, instead of grouping by the
        # first line. NULL — group everything — is the survey a reader runs first.
        "signature": None,
        # Occurrences a signature needs to be listed. Five, matching the other floors in
        # this file, and for the same reason: below it a group is one session's accident.
        "min_occurrences": 5,
        "signature_chars": SIGNATURE_CHARS,
    },
    "missing_file_recovery": {
        # Calls after the failure that count as the recovery. One, because the claim is
        # about what the thread did *next*: a listing three calls later is as likely to
        # be answering the question after it.
        "within_calls": 1,
        # Keep only failures whose text holds this phrase — "does not exist" narrows the
        # population to the ones a listing could have prevented. NULL is every failed call
        # that named a path, which is the survey a reader runs first.
        "missing": None,
    },
    "path_failures": {
        # Failures a directory needs to be listed, matching the other floors in this file.
        "min_occurrences": 5,
        # Path segments the group key keeps. One is the aggregating default: it is what
        # makes a directory count the same number whichever copy of the repository the
        # call reached into, which is the whole point of grouping paths this way.
        "tail_segments": 1,
    },
    # The line range is required for the reason the cap exists: a default would hand back a
    # window of private transcript with nothing to say it was a guess.
    "records_slice": {"max_chars": RAW_CHARS},
    "run_timeline": {"log_chars": LOG_CHARS},
    "select_runs": {
        # How many runs each `agent_type` gives up per stratum. One apiece keeps the draw
        # at roughly two runs per definition, which is the reading budget the design
        # sized.
        "runs_per_stratum": 1,
        # In-window runs an `agent_type` needs before it earns a reading slot. Matches
        # `select_sessions`'s skill threshold, and for the same reason: both sets are
        # open, and a name used once is a session's invention, not a definition.
        "min_runs": 5,
    },
    "select_enrichments": {
        # Items per category. Two apiece over a fourteen-member taxonomy is a sitting's
        # worth of reading, and every member gets a reader.
        "per_category": 2,
        "seed": DRAW_SEED,
    },
    "select_sessions": {
        "cost_quota": 8,
        "error_quota": 5,
        "compaction_quota": 4,
        "discovery_quota": 8,
        # A skill is major when this many in-window sessions used it.
        "skill_threshold": 5,
        "seed": DRAW_SEED,
        # Api calls a session needs to be in the pool at all. One keeps out the
        # `/model`-only sessions that took three of iteration 1's eight discovery draws;
        # it is bound rather than fixed because the filter is part of what the draw
        # claims, and a citation that omits it describes a pool nobody can reconstruct.
        "min_api_calls": 1,
        # Api calls a session needs on top of that before *discovery* will draw it. A
        # ranked stratum is exempt: what it ranks on is the reason to read the session.
        # Ten sits in the gap the corpus itself leaves — of the 117 in-window pool
        # sessions on 2026-08-13, 47 made between 1 and 9 calls and the next made 12 —
        # and it is what half of iteration 3's discovery draw fell below.
        "min_discovery_api_calls": 10,
    },
    "session_timeline": {"log_chars": LOG_CHARS},
    # The classifier's cut points. Every one is a starting guess, which is why they are
    # bound: a shape that swallows half the corpus is a threshold to move, not a finding.
    "session_shapes": {
        # Share of a session's api calls one skill has to carry to own the session. A
        # percentage, because a bound parameter is an integer, a date, or text.
        "skill_share_pct": 50,
        "delegating_runs": 3,
        "editing_calls": 5,
        # Below this a session is conversational; at or above it with no edits it is
        # analysis. One threshold, so the two shapes cannot overlap or leave a gap.
        "busy_tool_calls": 5,
    },
}


def names() -> list[str]:
    """Every query the library ships, by file stem: the directory is the registry."""
    return sorted(path.stem for path in queries.QUERY_DIR.glob("*.sql"))


def describe(name: str) -> Query:
    """What the runner needs to bind and scope one query, read off its statement.

    Read on demand rather than built at import, so a caller that plants a `.sql` under a
    patched `QUERY_DIR` is describing the real file. Raises `QueryError` for a name the
    library does not ship, a parameter `PARAM_TYPES` does not type, and a default no
    statement binds — the two ways what stays in Python can drift from the SQL.
    """
    if name not in set(names()):
        raise QueryError(f"no query named {name!r}. Known queries: {', '.join(names())}")
    text = statement(name)
    bound = parameters(text)
    defaults = DEFAULTS.get(name, {})
    if untyped := [parameter for parameter in bound if parameter not in PARAM_TYPES]:
        raise QueryError(
            f"{name} binds {', '.join(untyped)}, which analyze/queries.py:PARAM_TYPES does not "
            "type: name it there with what it binds as"
        )
    if orphans := [key for key in defaults if key not in bound]:
        raise QueryError(
            f"{name} declares a default for {', '.join(orphans)}, which its statement does not "
            "bind: the parameter was renamed or dropped and its default outlived it"
        )
    # A statement counts across sessions when it reads what `--project` builds, and nothing
    # else makes it one: a query joining neither relation is not scoped to a project at all.
    corpus = bool(relations(text) & set(CORPUS_RELATIONS))
    return Query(
        scope=Scope.CORPUS if corpus else Scope.KEYED,
        params={
            parameter: Param(type=PARAM_TYPES[parameter], default=defaults.get(parameter, REQUIRED))
            for parameter in bound
        },
    )


def catalog() -> dict[str, Query]:
    """The whole library described, in name order: what `--list` and the viewer read."""
    return {name: describe(name) for name in names()}
