"""The query library: one `.sql` file per question, and the vocabulary its manifest is written in.

A finding cites the query behind it, so a question lives in a versioned file a report can
name and anyone can re-run — not in a Python string. Two consumers share this library: the
`aiobserve query` runner and the trace viewer (`plans/trace-viewer/design.md`).

What is here is how a query is declared and every width one binds: the parameter types, the
shared `Param`s, and the character and row counts that bound what a page can ask for. What
each query takes is `analyze/manifest.py`, and the SQL itself is `analyze/queries/`.
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
# sensible default — a timeline of "some session" is not a question anyone asked.
SESSION_ID = Param(type=ParamType.TEXT, default=REQUIRED)
SOURCE = Param(type=ParamType.TEXT, default=REQUIRED)
# Which turn, which run, which api call, which tool call. Keys for the same reason, one
# level down: a node's own query is about that node, so absence cannot stand in for its id.
TURN_ID = Param(type=ParamType.TEXT, default=REQUIRED)
RUN_ID = Param(type=ParamType.TEXT, default=REQUIRED)
API_CALL_ID = Param(type=ParamType.TEXT, default=REQUIRED)
TOOL_CALL_ID = Param(type=ParamType.TEXT, default=REQUIRED)
# And the pair a query serving every kind of node takes instead: whichever id the node carries,
# and the word saying what kind of id it is. `view_numbers` is the one query written that way —
# what a node's numbers are made of differs by kind, and four files answering one question are
# four chances for them to disagree.
NODE_ID = Param(type=ParamType.TEXT, default=REQUIRED)
NODE_KIND = Param(type=ParamType.TEXT, default=REQUIRED)

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

# How much of a model name the popover prints, and the width its per-model token groups are
# keyed at. Wide enough that nothing our price table names is cut — the longest key is 24
# characters — because a group keyed on a cut name is a group nothing can price.
MODEL_CHARS = 60

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

# How much of a title a tree row carries — a turn's, a run's, an api call's. What a row *can*
# say rather than what fits: the tree is draggable (`view/static/tree-width.js`), and at 48 a
# reader who widened it got more whitespace and no more of the title, because the cut had
# already happened in SQL. CSS clamps the line to one with an ellipsis, so this is the reach
# of a drag and not a wrap. It prices every row of the tree (`view/bounds.py:TREE_ROW_BYTES`).
NAV_CHARS = 110
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

# The turn id `session_timeline` and `run_timeline` give the api calls that sit under no turn. A
# sentinel rather than NULL so it can travel in a URL; `view_turn_calls` takes NULL for the
# same rows, and the viewer translates at the route.
UNATTRIBUTED = "(unattributed)"

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
