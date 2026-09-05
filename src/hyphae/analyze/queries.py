"""The query library: one `.sql` file per question, and the vocabulary its manifest is written in.

A finding cites the query behind it, so a question lives in a versioned file a report can
name and anyone can re-run — not in a Python string. Two consumers share this library: the
`hp query` runner and the trace viewer (`plans/trace-viewer/design.md`).

What is here is how a query is declared: the type of each parameter name, the readers that
take a statement apart, and the few values no statement can say about itself. The production
defaults are `analyze/manifest.py` and the SQL itself is `analyze/queries/`. A width a page
binds is not here: it belongs to the surface that prints at it (`view/bounds.py`).
"""

import datetime as dt
import re
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


# The two relations `analyze/runner.py` builds from `--project`, and so the whole of what
# makes a statement a corpus one: a query reading neither is not scoped to a project at all,
# whatever anyone says about it. Declared here rather than beside the SQL that creates them
# because it is the contract between a statement and the runner, and both sides read it.
CORPUS_RELATIONS = ("project_sessions", "session_period")


class QueryError(Exception):
    """The caller asked for something the library cannot run, and it says which part."""


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


# What every parameter in the library binds as, by name. A type belongs to the name rather
# than to the query that takes it: `head_chars` is a width wherever it appears, and one name
# meaning two things would be a name to fix rather than a table to grow. A statement binding a
# name absent here is refused (`analyze/manifest.py:describe`), which is what stands between a
# new query and a value the binder guesses at.
PARAM_TYPES: dict[str, ParamType] = {
    # The ids a keyed query is about: which session, which thread inside it, which node of
    # that thread, and which file it wrote out. `node_id` with `kind` is the pair a query
    # serving every kind of node takes instead of an id of its own.
    **dict.fromkeys(
        (
            "session_id",
            "source",
            "turn_id",
            "run_id",
            "api_call_id",
            "tool_call_id",
            "compaction_id",
            "node_id",
            "kind",
            "name",
        ),
        ParamType.TEXT,
    ),
    # Text a caller chooses rather than an id: which level to read, a phrase to keep rows by,
    # the seed a draw hashes with.
    **dict.fromkeys(("level", "mentions", "missing", "seed", "signature"), ParamType.TEXT),
    # The widths a value is cut to before it is printed. A width is the surface's number
    # rather than the query's; the ones an analysis query defaults to are the constants below.
    **dict.fromkeys(
        (
            "after_chars",
            "chip_chars",
            "chunk_chars",
            "description_chars",
            "detail_chars",
            "head_chars",
            "item_chars",
            "kind_chars",
            "log_chars",
            "max_chars",
            "model_chars",
            "nav_chars",
            "preview_chars",
            "signature_chars",
            "tag_chars",
        ),
        ParamType.INTEGER,
    ),
    # How many rows come back and where they start: the page sizes, the cursors, and the
    # line ranges a raw fetch is bound by.
    **dict.fromkeys(
        (
            "after",
            "errors",
            "first_line",
            "head_items",
            "head_kinds",
            "head_projects",
            "last_line",
            "line_no",
            "page_calls",
            "page_records",
            "page_tools",
            "projects",
            "skipped",
        ),
        ParamType.INTEGER,
    ),
    # The thresholds, quotas and windows an analysis query's claim is bound by. A number here
    # is part of what the query asserts, which is why it is bound and cited rather than fixed.
    **dict.fromkeys(
        (
            "busy_tool_calls",
            "compaction_quota",
            "cost_quota",
            "delegating_runs",
            "discovery_quota",
            "editing_calls",
            "error_quota",
            "idle_seconds",
            "min_api_calls",
            "min_discovery_api_calls",
            "min_idle_seconds",
            "min_occurrences",
            "min_rebuilt_pct",
            "min_rebuilt_tokens",
            "min_runs",
            "min_sessions",
            "per_category",
            "recent_days",
            "runs_per_stratum",
            "short_gap_seconds",
            "skill_share_pct",
            "skill_threshold",
            "tail_segments",
            "window_days",
            "within_calls",
        ),
        ParamType.INTEGER,
    ),
    # The clock point a trailing window is measured back from: bound, so a landing page's
    # "last 7 days" says which day it counted from.
    "as_of": ParamType.DATE,
}

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

# How much of a prompt a timeline row carries — `session_timeline` and `run_timeline` cut theirs
# to it (`analyze/manifest.py`). It is also what the viewer's children log prints a string at,
# since a log row is one row of a timeline read for the strings rather than the counts
# (`view/bounds.py:LOG_WIDTHS`). Wider than a NavTree row, which is a line, and far narrower than
# the pane above it, which is one node read whole: a log is a dozen rows a reader picks the next
# node out of.
LOG_CHARS = 300

# The keyset cursor before the first row: "the last index already shown", and indexes start
# at 0, so this is what a page asking for the first one binds.
FIRST_PAGE = -1

# What every seeded draw hashes its keys with. Any fixed value serves; what matters is that
# the citation carries it, so a draw can be re-run — and rotated when a read wants new ground.
DRAW_SEED = "hyphae"

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


def statement(name: str) -> str:
    """One query's SQL with its comments cut: what actually runs, and what declares it.

    A header explains the query to a reader and may name a relation or a `$parameter` it
    does not read. Everything derived from a query file is derived from this.
    """
    return re.sub(r"--[^\n]*", "", load(name))


def parameters(statement: str) -> tuple[str, ...]:
    """The `$name`s a statement binds, first appearance first — the order `--list` prints.

    Statement order rather than sorted, because what a caller reads it as is the query's own
    argument list: the keys it is about, then what bounds them.
    """
    return tuple(dict.fromkeys(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", statement)))


def relations(statement: str) -> set[str]:
    """What a statement reads: the identifier after each FROM or JOIN, CTE names included.

    A rollup column is named after the table it counts (`turns`, `api_calls`), so a bare
    identifier scan cannot tell a table read from a column selected.
    """
    return set(re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", statement))


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
    link a page's footer makes out of it (`view/citation.py:cited`).
    """
    return "NULL" if value is None else str(value)
