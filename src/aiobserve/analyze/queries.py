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
# Which turn, which run, which api call, which tool call. Keys for the same reason, one
# level down: a node's own query is about that node, so absence cannot stand in for its id.
TURN_ID = Param(type=ParamType.TEXT, default=REQUIRED)
RUN_ID = Param(type=ParamType.TEXT, default=REQUIRED)
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

# The viewer's page sizes that a query binds: each is a parameter's default, declared here
# and nowhere else, because the payload bound the design states is arithmetic over these
# numbers, and every character of what a row prints can escape to five bytes.
# `view/bounds.py` names each of them beside the ceiling a URL may not pass, and
# `tests/view/test_bounds.py` asserts the arithmetic still fits.
# How many children one node page's log lists — its api calls, its tool calls, its turns.
# One size for every kind of child, because one pane holds one log. A hundred because the log
# is numbered rather than a cursor: a reader who can see how many pages a level has is reading
# the level, and a level of a hundred rows is one page of it rather than nine.
LOG_ROWS = 100
PAGE_RECORDS = 100
# How many projects the landing page ranks. A corpus grows projects the way it grows sessions,
# so the page is bound like the list — and a store holding more says how many it left out.
PAGE_PROJECTS = 100
# And how many of a session's failed tool calls its errors page lists. Bound the same way and
# for the same reason: nothing about a session caps how often its tools fail. The busiest
# session read so far failed 43 calls (`reports/2026_08_07_mycelia_agent_friction.md`), so
# this is headroom over what the corpus records rather than a number a page has reached.
PAGE_ERRORS = 100

# The two trailing windows the landing page counts a project in, beside its whole history: a
# week and a month. Not `WINDOW_DAYS` above, which is what a report's counts are quoted in and
# four weeks long so a weekly rhythm cannot bias them; these are what a reader scanning for
# what is running lately means, and the page heads its columns from them.
PAGE_RECENT_DAYS = 7
PAGE_WINDOW_DAYS = 30

# How much of an offloaded tool result one chunk of the offload page carries. The only value
# the viewer serves with no ceiling behind it — `offload_files.content` is whatever a tool
# wrote, and the canonical store holds one over 50 MB — so the page is a walk, not a fetch.
CHUNK_CHARS = 50_000

# How much of an agent run's three display columns a chip carries, and of a compaction's
# trigger. The corpus maxima are 60, 22 and 15 characters, but a maximum is an observation:
# a page whose size is arithmetic needs the number bound, not noticed.
CHIP_CHARS = 60
CHIP_CHARS_PARAM = Param(type=ParamType.INTEGER, default=CHIP_CHARS)

# What a header shows of each string it carries, of each member of the two lists a session's
# carries, and how many members of a list it shows before it says how many it left. A header
# is the part of a page no size a reader types bounds, so these are what the ceiling budgets
# it: 100 covers the longest title the canonical store holds (81) and 60 a PR url (51), while
# the lists grow with the session — one has recorded 32 PR links. A run header carries one
# string of its own, the line the run was spawned with, and takes the same head.
HEADER_CHARS = 100
HEADER_ITEM_CHARS = 60
HEADER_ITEMS = 5

# The same three for one row of the session list, which the viewer composes rather than the
# query (`view/listing.py`): the list's filters read the whole values. 100 covers the longest
# title the canonical store holds (81) and its longest project path (58); 4 skills of 20 cover
# the busiest session recorded, whose longest skill name is 18. A skill name is not a PR url,
# which is why the header's 60 does not carry over — the list multiplies its row by the page.
LIST_CHARS = 100
LIST_ITEM_CHARS = 20
LIST_ITEMS = 4

# How many kinds of work one list row names before it says how many it left: the categories a
# pass described that session's turns as. Fewer than the lists above, because the taxonomy is
# closed and small — three names say what a session spent its time on, and a fourth is noise.
LIST_CATEGORIES = 3

# How many projects the list's filter box suggests, and the longest path it offers. The
# suggestions grow with the corpus the way the rows do, so the box is bound too. A path is
# offered whole or left out: a suggestion cut to its head filters to nothing.
LIST_PROJECTS = 10

# How much of a model-written description or friction line a page shows, and how much of a
# taxonomy value one tag carries. The taxonomy is closed and its longest member is 9
# characters, but a page whose size is arithmetic needs the number bound rather than noticed —
# and a description is only as short as the schema the model answered under asked for.
ENRICHMENT_CHARS = 200
TAG_CHARS = 20

# How much of a raw record one browser row shows. Long enough to tell a `user` record from an
# `assistant` one and to recognise a line already read; short enough that a hundred of them
# is a page rather than a transcript.
RECORD_PREVIEW = 160

# How much of a label a tree row carries — a turn's, a run's, an api call's. Short because a
# tree is scanned rather than read, and because it is the one list whose rows a reader sees
# all of: a tree node is a line, and a line that wraps three times is not one.
NAV_CHARS = 48
NAV_CHARS_PARAM = Param(type=ParamType.INTEGER, default=NAV_CHARS)

# How much of a string one row of a children log carries — a model name, a tool name. Wider
# than a tree row, which is a line, and far narrower than the pane above it, which is one
# node read whole: a log is a dozen rows a reader picks the next node out of.
LOG_CHARS = 300
LOG_CHARS_PARAM = Param(type=ParamType.INTEGER, default=LOG_CHARS)

# How much of the one value a node page is *about* the pane shows before it offers the rest:
# an api call's answer, a tool call's result, the prompt a turn was given. Far wider than any
# repeated row, because it is not repeated — one node, one value, and the whole of it is a
# click away (`view/store.py`'s per-value queries). `?detail=` only goes down.
DETAIL_CHARS = 4_000
DETAIL_CHARS_PARAM = Param(type=ParamType.INTEGER, default=DETAIL_CHARS)

# The keyset cursor before the first row: "the last index already shown", and indexes start
# at 0. Defaulted to it, so a bare invocation of a paging query returns its first page.
FIRST_PAGE = -1
AFTER = Param(type=ParamType.INTEGER, default=FIRST_PAGE)
# The other way a query skips what a reader has already seen: how many rows lie before this
# page of a numbered set. Defaulted to none, so a bare invocation returns page one.
SKIPPED = Param(type=ParamType.INTEGER, default=0)

# What every seeded draw hashes its keys with. Any fixed value serves; what matters is that
# the citation carries it, so a draw can be re-run — and rotated when a read wants new ground.
DRAW_SEED = Param(type=ParamType.TEXT, default="aiobserve")

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
    "run_digest": Query(
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
    "session_digest": Query(
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
            "skipped": SKIPPED,
            "page_tools": Param(type=ParamType.INTEGER, default=LOG_ROWS),
            "log_chars": LOG_CHARS_PARAM,
        },
    ),
    "view_call_header": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "api_call_id": API_CALL_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "detail_chars": DETAIL_CHARS_PARAM,
        },
    ),
    "view_described_sessions": Query(
        scope=Scope.KEYED,
        # What a list row shows of a session's enrichment. The description takes a row's head
        # rather than a page's, because the list multiplies its row by the size of the page,
        # and the work cell is cut here rather than in the composition: nothing filters on it.
        params={
            "head_chars": Param(type=ParamType.INTEGER, default=LIST_CHARS),
            "tag_chars": Param(type=ParamType.INTEGER, default=TAG_CHARS),
            "kind_chars": Param(type=ParamType.INTEGER, default=TAG_CHARS),
            "head_kinds": Param(type=ParamType.INTEGER, default=LIST_CATEGORIES),
        },
    ),
    "view_enrichment": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # Which thread's turns to describe. Required like every other source: the turn
            # keys are `(session, source, turn)`, so a default would silently answer for the
            # main thread on a run's page.
            "source": SOURCE,
            "description_chars": Param(type=ParamType.INTEGER, default=ENRICHMENT_CHARS),
            "tag_chars": Param(type=ParamType.INTEGER, default=TAG_CHARS),
            # The width the model's own name is cut to: a header's, not a tag's — a model
            # string is longer than a taxonomy word and shorter than a sentence.
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
        },
    ),
    "view_compactions": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "chip_chars": CHIP_CHARS_PARAM},
    ),
    "view_run_header": Query(
        scope=Scope.KEYED,
        # The run's id is also the source its rows carry, so one key answers both questions.
        params={
            "session_id": SESSION_ID,
            "run_id": RUN_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "detail_chars": DETAIL_CHARS_PARAM,
        },
    ),
    "view_run_brief": Query(scope=Scope.KEYED, params={"session_id": SESSION_ID, "run_id": RUN_ID}),
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
    "view_project_rollups": Query(
        scope=Scope.KEYED,
        params={
            # The clock both windows are measured back from. No default: a landing page's
            # "last 7 days" is only reproducible if the day it counted from is bound and
            # cited, and SQL's own clock would answer something else tomorrow.
            "as_of": Param(type=ParamType.DATE, default=REQUIRED),
            "recent_days": Param(type=ParamType.INTEGER, default=PAGE_RECENT_DAYS),
            "window_days": Param(type=ParamType.INTEGER, default=PAGE_WINDOW_DAYS),
            # A project path takes a row's head, like the list's — and the row links by the
            # whole path, so the head is what the page shows and not what it filters by.
            "head_chars": Param(type=ParamType.INTEGER, default=LIST_CHARS),
            "projects": Param(type=ParamType.INTEGER, default=PAGE_PROJECTS),
        },
    ),
    "view_projects": Query(
        scope=Scope.KEYED,
        params={
            "head_chars": Param(type=ParamType.INTEGER, default=LIST_CHARS),
            "head_projects": Param(type=ParamType.INTEGER, default=LIST_PROJECTS),
        },
    ),
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
    "view_session_header": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "item_chars": Param(type=ParamType.INTEGER, default=HEADER_ITEM_CHARS),
            "head_items": Param(type=ParamType.INTEGER, default=HEADER_ITEMS),
        },
    ),
    "view_session_errors": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # Labelled at a tree row's width, because the rows link to nodes: a failure reads
            # as the same line here as it does in the tree beside its own page.
            "nav_chars": NAV_CHARS_PARAM,
            "errors": Param(type=ParamType.INTEGER, default=PAGE_ERRORS),
        },
    ),
    "view_sessions": Query(
        scope=Scope.KEYED,
        # How much of each agent definition's name a row's list carries. The viewer composes
        # the rest of a row's cuts around this query (`view/listing.py`) because its filters
        # read whole values; nothing filters on this one, so it is cut in the file.
        params={"item_chars": Param(type=ParamType.INTEGER, default=LIST_ITEM_CHARS)},
    ),
    "view_tool_header": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "tool_call_id": TOOL_CALL_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "detail_chars": DETAIL_CHARS_PARAM,
        },
    ),
    "view_tool_input": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "tool_call_id": TOOL_CALL_ID},
    ),
    "view_tool_result": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "tool_call_id": TOOL_CALL_ID},
    ),
    "view_tree_calls": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # NULL is the real question "which calls sit under no turn", so the key is
            # required rather than defaulted: absence cannot stand in for it.
            "turn_id": TURN_ID,
            "nav_chars": NAV_CHARS_PARAM,
        },
    ),
    "view_tree_tools": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # Both NULL-able and both required for the same reason as `view_tree_calls`:
            # NULL is the question "under this turn, whichever call made it" at the first and
            # "under no turn of this thread" at the second, not a key left out.
            "api_call_id": API_CALL_ID,
            "turn_id": TURN_ID,
            "nav_chars": NAV_CHARS_PARAM,
        },
    ),
    "view_tree_turns": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "nav_chars": NAV_CHARS_PARAM},
    ),
    "view_turn_calls": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # NULL is the real question "which calls sit under no turn", so the key is
            # required rather than defaulted: absence cannot stand in for it.
            "turn_id": TURN_ID,
            "skipped": SKIPPED,
            "page_calls": Param(type=ParamType.INTEGER, default=LOG_ROWS),
            # The two model names a call row shows, cut like every other log row's strings.
            "log_chars": LOG_CHARS_PARAM,
        },
    ),
    "view_turn_header": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # Which turn. A key like the session and the thread: "some turn of this thread"
            # is not a question anyone asked.
            "turn_id": TURN_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "detail_chars": DETAIL_CHARS_PARAM,
        },
    ),
    "view_turn_prompt": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "turn_id": TURN_ID,
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
    bound = " ".join(f"{key}={shown(value)}" for key, value in bindings.items())
    return f"-- queries/{name}.sql {bound}".rstrip()


def shown(value: ParamValue) -> str:
    """A binding as it is written down — NULL is a value a reader can rebind.

    One spelling for both places a binding leaves the process: the citation line above, and the
    link a page's footer makes out of it (`view/app.py:cited`).
    """
    return "NULL" if value is None else str(value)
