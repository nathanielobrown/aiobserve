"""The query library: one `.sql` file per question, and the manifest that says how to run it.

A finding cites the query behind it, so a question lives in a versioned file a report can
name and anyone can re-run — not in a Python string. Two consumers share this library: the
`aiobserve query` runner and the trace viewer (`plans/trace-viewer/design.md`).

The manifest is production code. It gives every parameter a query declares either a
production default — the value a bare invocation runs, and the value a committed report
quotes — or `REQUIRED`, for a choice the caller has to make: a defaulted line range on
`records_slice` would quietly hand back a window of raw transcript instead of an error.

Adding a query means adding its file *and* its manifest entry; the smoke tier fails on
either half alone.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path

QUERY_DIR = Path(__file__).parent / "queries"

# The trailing window every corpus count is reported in beside the full corpus. Fixed, not
# decayed: a window is a filter a reader can re-run and argue with.
WINDOW_DAYS = 28


class Scope(StrEnum):
    """What a query is asking about, which decides what the runner has to give it."""

    # Counts across sessions: takes `--project`, and reads the corpus predicate and the
    # trailing window through the runner's `project_sessions` table.
    CORPUS = "corpus"
    # Anything that is not a count across sessions: a fetch keyed by `session_id`/`source`,
    # or the viewer's whole-store list. Exempt from both — a corpus predicate on
    # `WHERE session_id = $session_id` is noise, and the viewer browses the store it is
    # pointed at rather than one analyzed repository.
    KEYED = "keyed"


class ParamType(StrEnum):
    """How a `--param` string becomes the value DuckDB binds."""

    TEXT = "text"
    INTEGER = "integer"
    DATE = "date"


class NoDefault(Enum):
    """Marker for a parameter with no default: the caller must bind it or get an error."""

    REQUIRED = "required"


REQUIRED = NoDefault.REQUIRED

# What a bound parameter can be. NULL is a real default — `$since` unset means the whole
# corpus — so absence cannot stand in for "required", which is why `REQUIRED` is a marker.
ParamValue = str | int | dt.date | None


@dataclass(frozen=True)
class Param:
    """One DuckDB named parameter a query file declares."""

    type: ParamType
    # The production default, or REQUIRED when no sensible one exists.
    default: ParamValue | NoDefault


@dataclass(frozen=True)
class Query:
    """What the runner needs to know about one `.sql` file to bind and scope it."""

    scope: Scope
    params: Mapping[str, Param]


# The keys of a keyed query: which session, and which thread inside it. Neither has a
# sensible default — a digest of "some session" is not a question anyone asked.
SESSION_ID = Param(type=ParamType.TEXT, default=REQUIRED)
SOURCE = Param(type=ParamType.TEXT, default=REQUIRED)
# Which api call, and which tool call. Keys for the same reason, one level down.
API_CALL_ID = Param(type=ParamType.TEXT, default=REQUIRED)
TOOL_CALL_ID = Param(type=ParamType.TEXT, default=REQUIRED)

# How much of one raw record `records_slice` returns. A cap, not a limit: a reader can raise
# it, and the design says so — the mechanism here is that the number is stated and cited.
RAW_CHARS = 2000

# How much of a failed tool call's text `error_records` returns. Enough for the signature —
# the sentence that names what went wrong — and short enough that a session's whole error
# list stays a table rather than a transcript.
ERROR_CHARS = 200

# How much of an error's first line `error_signatures` groups on. Long enough to tell two
# failures of one tool apart, short enough that the path or command trailing the sentence
# does not split one recurring error into a group per call site.
SIGNATURE_CHARS = 120

# How much of a command line `command_failures` groups on. The grouping already keeps only
# the command word and the bare words after it, so this is the backstop: a command line is
# private text, and no run of it may reach a table a report quotes.
COMMAND_HEAD_CHARS = 60

# The viewer's page sizes. Every one of them is a bound parameter with a default here and
# nowhere else, because the payload bound the design states is arithmetic over these numbers:
# a page of calls carries at most `PAGE_CALLS` × (2 KB of text + `PAGE_TOOLS` tool rows), and
# every character of that content can escape to five bytes. The two sizes multiply, which is
# what keeps them this small — a call row costs about 12 KB before its tool rows.
# `tests/view/test_bounds.py` pins the values and asserts the arithmetic still fits.
PAGE_CALLS = 10
PAGE_TOOLS = 12
PAGE_RECORDS = 100

# How much of an offloaded tool result one chunk of the offload page carries. The only value
# the viewer serves with no ceiling behind it — `offload_files.content` is whatever a tool
# wrote, and the canonical store holds one over 50 MB — so the page is a walk, not a fetch.
CHUNK_CHARS = 50_000

# How much of an agent run's three display columns a chip carries, and of a compaction's
# trigger. The corpus maxima are 60, 22 and 15 characters, but a maximum is an observation:
# a page whose size is arithmetic needs the number bound, not noticed.
CHIP_CHARS = 60
CHIP_CHARS_PARAM = Param(type=ParamType.INTEGER, default=CHIP_CHARS)

# How much of a raw record one browser row shows. Long enough to tell a `user` record from an
# `assistant` one and to recognise a line already read; short enough that a hundred of them
# is a page rather than a transcript.
RECORD_PREVIEW = 160

# The keyset cursor before the first row: "the last index already shown", and indexes start
# at 0. Defaulted to it, so a bare invocation of a paging query returns its first page.
FIRST_PAGE = -1
AFTER = Param(type=ParamType.INTEGER, default=FIRST_PAGE)

# The turn id `session_digest` and `run_digest` give the api calls that sit under no turn. A
# sentinel rather than NULL so it can travel in a URL; `view_turn_calls` takes NULL for the
# same rows, and the viewer translates at the route.
UNATTRIBUTED = "(unattributed)"

QUERIES: dict[str, Query] = {
    "agent_compactions": Query(scope=Scope.CORPUS, params={}),
    "agent_types": Query(scope=Scope.CORPUS, params={}),
    "co_occurrence": Query(
        scope=Scope.CORPUS,
        # A pair seen in one or two sessions is noise on any corpus worth counting. The floor
        # is bound rather than fixed because a young corpus has nothing above it.
        params={"min_sessions": Param(type=ParamType.INTEGER, default=3)},
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
    "run_digest": Query(scope=Scope.KEYED, params={"session_id": SESSION_ID, "source": SOURCE}),
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
    "select_sessions": Query(
        scope=Scope.CORPUS,
        params={
            "cost_quota": Param(type=ParamType.INTEGER, default=8),
            "error_quota": Param(type=ParamType.INTEGER, default=5),
            "compaction_quota": Param(type=ParamType.INTEGER, default=4),
            "discovery_quota": Param(type=ParamType.INTEGER, default=8),
            # A skill is major when this many in-window sessions used it.
            "skill_threshold": Param(type=ParamType.INTEGER, default=5),
            # Any fixed value serves; what matters is that the citation carries it, so the
            # discovery draw can be re-run — and rotated when an iteration wants new ground.
            "seed": Param(type=ParamType.TEXT, default="aiobserve"),
            # Api calls a session needs before it is worth a reading slot. One keeps out the
            # `/model`-only sessions that took three of iteration 1's eight discovery draws;
            # it is bound rather than fixed because the filter is part of what the draw
            # claims, and a citation that omits it describes a pool nobody can reconstruct.
            "min_api_calls": Param(type=ParamType.INTEGER, default=1),
        },
    ),
    "session_counts": Query(scope=Scope.CORPUS, params={}),
    "session_digest": Query(scope=Scope.KEYED, params={"session_id": SESSION_ID}),
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
    # The `view_` family belongs to the trace viewer (`plans/trace-viewer/design.md`). They
    # are library queries like any other — runnable and citable — and the viewer composes
    # sort and filter around them rather than embedding SQL of its own.
    "view_call_text": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "api_call_id": API_CALL_ID},
    ),
    "view_call_thinking": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "api_call_id": API_CALL_ID},
    ),
    "view_call_tools": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "api_call_id": API_CALL_ID,
            "after": AFTER,
            "page_tools": Param(type=ParamType.INTEGER, default=PAGE_TOOLS),
        },
    ),
    "view_compactions": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "chip_chars": CHIP_CHARS_PARAM},
    ),
    "view_run_header": Query(
        scope=Scope.KEYED,
        # The run's id is also the source its rows carry, so one key answers both questions.
        params={"session_id": SESSION_ID, "run_id": Param(type=ParamType.TEXT, default=REQUIRED)},
    ),
    "view_offload": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # Which file. Required for the same reason the session is: a default would pick
            # some session's offloaded tool output at random.
            "name": Param(type=ParamType.TEXT, default=REQUIRED),
            "after_chars": Param(type=ParamType.INTEGER, default=0),
            "chunk_chars": Param(type=ParamType.INTEGER, default=CHUNK_CHARS),
        },
    ),
    "view_projects": Query(scope=Scope.KEYED, params={}),
    "view_record": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # Which line. A key like the two above: "some record of this thread" is not a
            # question anyone asked, and the answer would be private transcript either way.
            "line_no": Param(type=ParamType.INTEGER, default=REQUIRED),
        },
    ),
    "view_records": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "after": AFTER,
            "page_records": Param(type=ParamType.INTEGER, default=PAGE_RECORDS),
            "preview_chars": Param(type=ParamType.INTEGER, default=RECORD_PREVIEW),
        },
    ),
    "view_runs": Query(
        scope=Scope.KEYED, params={"session_id": SESSION_ID, "chip_chars": CHIP_CHARS_PARAM}
    ),
    "view_session_header": Query(scope=Scope.KEYED, params={"session_id": SESSION_ID}),
    "view_sessions": Query(scope=Scope.KEYED, params={}),
    "view_tool_value": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "tool_call_id": TOOL_CALL_ID},
    ),
    "view_turn_calls": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # NULL is the real question "which calls sit under no turn", so the key is
            # required rather than defaulted: absence cannot stand in for it.
            "turn_id": Param(type=ParamType.TEXT, default=REQUIRED),
            "after": AFTER,
            "page_calls": Param(type=ParamType.INTEGER, default=PAGE_CALLS),
        },
    ),
    "view_turn_records": Query(
        scope=Scope.KEYED, params={"session_id": SESSION_ID, "source": SOURCE}
    ),
    "weekly_trend": Query(scope=Scope.CORPUS, params={}),
}

# The prefix that marks a query as the viewer's. The viewer's payload bound is a property of
# its queries, so its tier scans them as a set (`tests/view/test_bounds.py`).
VIEW_PREFIX = "view_"


def load(name: str) -> str:
    """The SQL text of one library query, by file stem."""
    return (QUERY_DIR / f"{name}.sql").read_text()


def citation(name: str, bindings: Mapping[str, ParamValue]) -> str:
    """Query file and bindings as a SQL comment: what a report quotes and a reader re-runs.

    Both consumers cite the same way. The viewer passes what it composed around the query —
    the sort a page applied is as much a part of what produced it as a bound parameter.
    """
    bound = " ".join(f"{key}={_show(value)}" for key, value in bindings.items())
    return f"-- queries/{name}.sql {bound}".rstrip()


def _show(value: ParamValue) -> str:
    """A binding as it goes in the citation — NULL is a value a reader can rebind."""
    return "NULL" if value is None else str(value)
