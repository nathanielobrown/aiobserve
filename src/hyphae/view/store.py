"""Reading the trace store for one request: the connection, the queries, and a page of rows.

Every request opens its own read-only connection, checks the schema version, reads, and
closes — that is what lets an extract run while a page is open, and what makes a store under
someone else's write lock a 503 rather than a crash.

The three enums are the viewer's whole query catalog, split by what a query is allowed to
select: a page or a fragment truncates every fat column in SQL, and a per-value query is the
declared exception. Naming a query in one of them is what puts it in reach of the payload
scans (`tests/view/test_bounds.py`), so the union is also the checklist.

The SQL a page composes around one of those queries is here too, and nowhere else: `window`
for a numbered page of a query that limits nothing itself, and the session list's sort, filter
and cut below it. A route reads rows; it does not build SQL.
"""

from collections.abc import Generator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

import duckdb

from hyphae.analyze import macros, manifest, queries
from hyphae.analyze.queries import ParamValue
from hyphae.export.duckdb import PAGE_WAIT, open_trace_store
from hyphae.export.schema import SchemaVersionError
from hyphae.projects import project_predicate
from hyphae.view import bounds

Row = dict[str, Any]

# A read that binds nothing a reader typed, which is most of them: a surface prints at its own
# widths, and only the node page's `?detail=` reaches into a query from the URL.
NO_SIZES: Mapping[str, ParamValue] = MappingProxyType({})

# The column both turn timelines are ordered by: unique and ascending within one thread, and
# NULL on the row standing for the calls that answer no turn, which rides no page of them.
TURN_CURSOR = "turn_index"


class Page(StrEnum):
    """The library queries the pages are built from, by the part each one fills."""

    SESSIONS = "view_sessions"
    # Every project the store holds sessions for, which is the landing page: the counts a
    # reader lands on are a corpus's, so they come from the `corpus_*` views.
    PROJECT_ROLLUPS = "view_project_rollups"
    # The names the list's project filter offers, which is a column of the store rather than
    # of the page: the projects on one page of sessions are not the projects to filter by.
    PROJECTS = "view_projects"
    SESSION_HEADER = "view_session_header"
    # Every failed tool call of one session, across every thread — the one page the NavTree
    # cannot lead to, because a failure is scattered rather than nested (`view/failures.py`).
    SESSION_ERRORS = "view_session_errors"
    # One node read whole, the header of its own page. One per kind that has fields of its
    # own; a bucket has none, and a compaction reads out of `view_compactions`.
    RUN_HEADER = "view_run_header"
    TURN_HEADER = "view_turn_header"
    CALL_HEADER = "view_call_header"
    TOOL_HEADER = "view_tool_header"
    # The levels of the NavTree beside a node page: one thin row per child, whatever the level
    # holds. One query per kind of child rather than per kind of parent, so a turn's calls
    # are read the same way under a session, under a run, or under a bucket.
    NAV_TREE_TURNS = "view_nav_tree_turns"
    NAV_TREE_CALLS = "view_nav_tree_calls"
    NAV_TREE_TOOLS = "view_nav_tree_tools"
    # The two turn timelines, shared with `hp query` — the same rows a report cites.
    # One query per thread kind: `session_timeline` reads `main`, `run_timeline` a bound source.
    TIMELINE = "session_timeline"
    RUN_TIMELINE = "run_timeline"
    RUNS = "view_runs"
    COMPACTIONS = "view_compactions"
    # What an enrichment pass said about the session, its turns and its runs. Absent from a
    # store no pass has written to, which is why `view/enrichment.py` asks before it runs.
    ENRICHMENT = "view_enrichment"
    # The same for the list: what the pass said each session was, joined to the page of rows
    # the list just read. Absent from an un-enriched store for the same reason.
    DESCRIBED_SESSIONS = "view_described_sessions"
    # One page of a thread's raw transcript, previewed a record per row, and the line each of
    # the thread's turns was read from — what turns a timeline row into a link into it.
    RECORDS = "view_records"
    TURN_RECORDS = "view_turn_records"
    # One chunk of a tool result written to a file beside the transcript.
    OFFLOAD = "view_offload"


class Fragment(StrEnum):
    """The library queries htmx fetches a page of at a time, on expanding something."""

    # One page of the api calls under a turn, and one page of the tool calls under a call.
    TURN_CALLS = "view_turn_calls"
    CALL_TOOLS = "view_call_tools"
    # The numbers behind one NavTree row, fetched when a reader points at it: what the row draws
    # as a bar and a badge, written out. One query for every kind made of api calls, and one
    # apiece for the two kinds made of none — the tool call and the compaction.
    NUMBERS = "view_numbers"
    TOOL_NUMBERS = "view_numbers_tool"
    COMPACTION_NUMBERS = "view_numbers_compaction"


class Value(StrEnum):
    """The library queries that fetch one whole value: the exception to the page bound.

    Every other query truncates in SQL. These return a fat column untruncated because the
    unit *is* one value — the bound is the largest single value in the store, not a page's
    worth of them, and it is only reached when a reader opens that one value.
    """

    CALL_TEXT = "view_call_text"
    CALL_THINKING = "view_call_thinking"
    # What one tool call was asked and what it returned, one value each rather than the row
    # whole: a pane previews the two apart, so each has its own way to the rest of it.
    TOOL_INPUT = "view_tool_input"
    TOOL_RESULT = "view_tool_result"
    # And what a `Bash` call ran, which the input holds escaped onto one line: a value of its
    # own because a shell command is read as shell, not as a string inside JSON.
    TOOL_COMMAND = "view_tool_command"
    RECORD = "view_record"
    # What a turn was asked, what followed the command a slash turn ran, and what an agent
    # run was briefed with. Each is a value a pane previews, cut in the node's header query
    # and fetched whole here.
    TURN_PROMPT = "view_turn_prompt"
    TURN_COMMAND_ARGS = "view_turn_command_args"
    RUN_BRIEF = "view_run_brief"
    # And the two a run's page reads off the call that spawned it: what that call asked for,
    # and what it returned to the agent that made it.
    RUN_PROMPT = "view_run_prompt"
    RUN_RESULT = "view_run_result"
    # And the two lines an enrichment pass wrote about an item, at each of the three levels it
    # writes at. Fat for the same reason the rest are — a pass writes as much as it wants
    # to — and one query each, because a fetch serves one value and a reader opens whichever
    # of the two ran past the width.
    TURN_DESCRIPTION = "view_turn_description"
    TURN_FRICTION = "view_turn_friction"
    RUN_DESCRIPTION = "view_run_description"
    RUN_FRICTION = "view_run_friction"
    SESSION_DESCRIPTION = "view_session_description"
    SESSION_FRICTION = "view_session_friction"


# Any of the three, for the fetch helper they share.
Library = Page | Fragment | Value


def bound(
    page: Library,
    widths: bounds.Widths,
    sizes: Mapping[str, ParamValue] = NO_SIZES,
    /,
    **keys: ParamValue,
) -> dict[str, ParamValue]:
    """What one read binds: its keys, the surface's widths, and the sizes a reader asked for.

    The first three are positional so a key can be named anything a statement binds: a read
    keyed by a column called `sizes` is a store shape away, and it would land here as an
    argument rather than as a key.

    Every parameter `page` declares is filled from one of the three or this raises, naming the
    page, the parameter and the surface; a parameter filled twice raises too — a key or a size
    that names a width is an override, which is a second surface, so declare one
    (`view/bounds.py`), and a key that names a size is two answers to what the URL asked.
    DuckDB refuses a read short of a parameter as well, but only once a connection is open and
    a statement handed over, and its message names neither the surface nor which half should
    have carried it.

    The mapping comes back in the order it was spelled — keys, then widths, then sizes — which
    is the order the citation under the page quotes them in (`view/citation.py`).
    """
    surface = type(widths).__name__
    declared = manifest.describe(page).params
    fields = widths._asdict()
    # Ahead of the merge, which is where the shadowing would happen: `sizes` is written second
    # into the mapping, so the reader's number would take the read's silently.
    if twice := sorted(keys.keys() & sizes.keys()):
        raise ValueError(
            f"{page} is passed {', '.join(twice)} twice: a size is what a reader asked the page "
            f"for, so a read that keys one of its own is answering the URL over the reader"
        )
    given = {**keys, **sizes}
    if claimed := sorted(given.keys() & fields.keys()):
        raise ValueError(
            f"{page} takes {', '.join(claimed)} from the {surface} surface: a read that prints "
            f"at another width names another surface rather than binding one of its own"
        )
    if excess := sorted(given.keys() - declared.keys()):
        raise ValueError(
            f"{page} binds no {', '.join(excess)}: the read passes a key its statement never names"
        )
    if missing := [name for name in declared if name not in given and name not in fields]:
        raise ValueError(
            f"{page} binds {', '.join(missing)}, which no key carries and the {surface} surface "
            f"does not declare: pass it as a key, or give the surface the width in view/bounds.py"
        )
    return {**keys, **{name: fields[name] for name in fields if name in declared}, **sizes}


class SchemaMoved(Exception):
    """The store's schema version is not the one this build reads."""


@contextmanager
def open_store(db_path: Path) -> Generator[duckdb.DuckDBPyConnection]:
    """A read-only connection for one request, checked and closed.

    The store's one opener (`export/duckdb.py`), told how long a page may hang, with the
    viewer's own refusal over it: `SchemaMoved` when the store moved under the running
    viewer. That is checked per request rather than at startup because an extract can land
    between two page loads, and so is the opener's own `StoreLocked`, which the request
    reaches only after waiting `PAGE_WAIT` for the writer. Only the open is translated — an
    error a page raises while reading is the page's own.
    """
    opened = ExitStack()
    try:
        connection = opened.enter_context(open_trace_store(db_path, read_only=True, wait=PAGE_WAIT))
    except SchemaVersionError as error:
        # Carried whole: the opener already picked the remedy that fits this store, and a
        # reader sent to a fresh one where a migration would have done can lose a session.
        raise SchemaMoved(str(error)) from error
    with opened:
        # The library's shared SQL functions, which several of the queries below call by name.
        macros.install(connection)
        yield connection


def fetch(
    connection: duckdb.DuckDBPyConnection, sql: str, bindings: Mapping[str, ParamValue]
) -> list[Row]:
    """Run one statement and hand back its rows as dicts, keyed by column name."""
    cursor = connection.execute(sql, dict(bindings))
    columns = tuple(column[0] for column in cursor.description or ())
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def page_rows(
    connection: duckdb.DuckDBPyConnection, page: Library, **bindings: ParamValue
) -> list[Row]:
    """The rows of one library query, bound as given."""
    return fetch(connection, queries.load(page), bindings)


class Paged(NamedTuple):
    """One keyset page: the rows, what is behind them, and where to resume.

    The records browser's way of paging, and the only one left: a citation names a line, so the
    page for it is the one that *starts* at that line rather than the nth page of the thread.
    """

    rows: list[Row]
    # How many rows the cap cut, for the "+N more" the page shows instead of losing them.
    more: int
    # The `$after` cursor the next fetch binds, or None when this page is the last.
    after: int | None


class Listed(NamedTuple):
    """One numbered page of a level: the rows, and how many the level holds in all.

    `total` is the count before the LIMIT bit, which is what lets a page say which of how many
    it is — and what lets a heading count the level rather than the rows in front of the reader.
    """

    rows: list[Row]
    total: int


# The one name for how many rows matched before a LIMIT bit, wherever that count is computed:
# by the query itself where it limits its own rows, and by `window` where it limits nothing and
# the viewer wraps it. One name is what lets a route read the count without knowing which query
# it came from — and the two ways of computing it never meet, because a query that limits itself
# is never one `window` wraps.
MATCHED_ROWS = "matched_rows"


def _core(page: Library) -> str:
    """One library query as a subquery: its own text, unchanged, ready to be wrapped."""
    return queries.load(page).strip().rstrip(";")


def window(
    connection: duckdb.DuckDBPyConnection,
    page: Library,
    cursor: str,
    skipped: int,
    size: int,
    **bindings: ParamValue,
) -> Listed:
    """One numbered page of a library query that limits nothing itself.

    The session list's composition below is the other case: a query whose whole result a
    report quotes cannot carry a viewer's LIMIT, so the viewer wraps it. Rows
    come back ordered by `cursor`, which is a column name this package supplies — never
    request text — while `skipped` and `size` bind. A row the query gives no cursor value is
    outside every page and outside the count (`cursorless_rows`).
    """
    rows = fetch(
        connection,
        f"SELECT *, count(*) OVER () AS {MATCHED_ROWS} FROM ({_core(page)})"
        f" WHERE {cursor} IS NOT NULL ORDER BY {cursor} LIMIT $size OFFSET $skipped",
        {"skipped": skipped, "size": size, **bindings},
    )
    return listed(rows)


def cursorless_rows(
    connection: duckdb.DuckDBPyConnection,
    page: Library,
    cursor: str,
    limit: int,
    **bindings: ParamValue,
) -> list[Row]:
    """The rows a paged query gives no cursor value, which no window can reach.

    The timelines' unattributed row is the case: it stands for the calls that answer no turn,
    so it has no turn index and rides the last page instead. `limit` is what the page that
    renders them budgeted; a query answering with more raises, because these rows arrive
    outside the size the reader asked for and a page that serves them anyway is a page whose
    ceiling was computed against something else.
    """
    rows = fetch(
        connection,
        f"SELECT * FROM ({_core(page)}) WHERE {cursor} IS NULL LIMIT $cursorless",
        {"cursorless": limit + 1, **bindings},
    )
    if len(rows) > limit:
        raise ValueError(f"{page} gave more than {limit} row(s) with no {cursor}")
    return rows


# The session list's own composition, which is the other case `window` names: a `?sort=` column
# and a filter predicate cannot be bound parameters, so the library query stays the citable core
# and what follows wraps it. `SORTS`, `FILTERS` and `DIRECTIONS` are closed, so a key outside
# them is a `KeyError` here and a 400 at the route (`view/pages/sessions/routes.py`) and never
# a fragment of SQL — every value a request supplied binds as a parameter, and no request text
# reaches DuckDB as text.

# What the session list can be sorted by: a column of `view_sessions`, mapped to its header
# label. A closed dictionary, and the only place a request's `sort` value is ever looked up —
# an unknown key is a 400, never a fragment of SQL. `tests/view/test_app__list.py` checks every
# key against the columns the query returns. Output tokens and active time are not here: they
# ride the row as the second line of the cost and wall cells, and a column nobody ranks a
# corpus by is texture rather than a heading.
SORTS: dict[str, str] = {
    "started_at": "Started",
    "title": "Session",
    "project_dir": "Project",
    "turns": "Turns",
    "api_calls": "Calls",
    "tool_calls": "Tools",
    "compactions": "Compactions",
    # By the count, though the cell shows the rate: one tool call that failed is a session at
    # 100%, and not the session a reader sorting by errors is looking for.
    "tool_errors": "Errors",
    "cost_usd": "Cost",
    "wall_ms": "Wall",
    "agent_runs": "Subagents",
}


@dataclass(frozen=True)
class Filter:
    """One way the session list can be narrowed, as the two halves that make it safe."""

    # The predicate composed into the WHERE, naming its own bound parameter and nothing else.
    # It reads a column of `view_sessions`, which is what the composition wraps.
    predicate: str
    # What a request's value has to parse as before it can bind. A value that will not parse
    # is a 400, so the type is also the only vetting a filter value gets.
    type: queries.ParamType


# What the session list can be narrowed by, per query-string key. Closed, like `SORTS`: a key
# outside it is a 400, and a key inside it contributes a fixed predicate and a bound value —
# request text never becomes SQL. Composed in this order, so the WHERE and the citation read
# the same whatever order a URL happened to put them in.
FILTERS: dict[str, Filter] = {
    # A path prefix, not a path: a worktree checkout sits under the repository it was cut
    # from, so filtering by a project has to hold its worktrees' sessions the way the CLI's
    # `--project` does. One statement of the rule, in `hyphae.projects`.
    "project": Filter(project_predicate("project_dir", "$project"), queries.ParamType.TEXT),
    "since": Filter("started_at >= $since", queries.ParamType.DATE),
    # Inclusive of the day named: someone asking for sessions until the 7th means the 7th.
    "until": Filter("started_at < $until + INTERVAL 1 DAY", queries.ParamType.DATE),
    "skill": Filter("list_contains(skills, $skill)", queries.ParamType.TEXT),
    # A floor rather than a flag, so `errors=1` reads "any" and a larger number "at least".
    "errors": Filter("tool_errors >= $errors", queries.ParamType.INTEGER),
}

# The two orderings a reader can ask for, as the SQL keyword each one puts in the ORDER BY.
DIRECTIONS: dict[str, str] = {"asc": "ASC", "desc": "DESC"}

# What one row of the list shows of the values a transcript wrote: each string cut to a head,
# the skills and the agent types cut to their first few with a count of what was left, and the
# PR links the page has no column for dropped. Composed here rather than in the query because
# the list's filters read the whole values — a `project` matched against a cut path would miss
# every session under a longer one, and a `skill` outside the first few would find nothing —
# and applied outside the window, so it cuts the rows one page shows and nothing else.
#
# The `cut` macro takes one character more than the row prints, which is how the component
# knows a value was stopped rather than ended and marks it (`view/text/format.py:cut`). It is a
# macro of the library, so this runs only on a connection `macros.install` has seen — which
# `open_store` above is, and so is every fixture that reaches here.
SHOWN = """SELECT * EXCLUDE (pr_urls) REPLACE (
    cut(title, $head_chars) AS title,
    cut(project_dir, $head_chars) AS project_dir,
    list_transform(list_slice(coalesce(skills, []), 1, $head_items),
        name -> cut(name, $item_chars)) AS skills,
    list_slice(coalesce(agent_types, []), 1, $head_items) AS agent_types
), greatest(len(coalesce(skills, [])) - $head_items, 0) AS skills_cut,
   greatest(len(coalesce(agent_types, [])) - $head_items, 0) AS agent_types_cut FROM"""


# How many rows past the page the list reads: enough to know whether there is another page,
# never enough to show one. `sorted_sessions` is the only place it is spent, and the citation
# under the page quotes the size the reader asked for instead (`view/pages/sessions/routes.py`).
PAGER_PROBE = 1


def list_bound(page: int, size: int, filters: Mapping[str, ParamValue]) -> dict[str, ParamValue]:
    """What one page of the session list binds: its window, its row cut, and its filters.

    Read twice — by the query below, and by the citation the page prints under it
    (`view/pages/sessions/routes.py`) — because a citation that drifted from its query is a
    false citation. The one difference is the query's `PAGER_PROBE`, added where it is spent.
    """
    return {
        "limit": size,
        "offset": (page - 1) * size,
        # Read off the surface a field at a time rather than through `bound`: the statement
        # these three go to is composed here, so the manifest has no parameter list to fill
        # from — `SHOWN` binds them, the library query below binds `item_chars` again, and the
        # window and the filters are the composition's own.
        "head_chars": bounds.LIST_WIDTHS.head_chars,
        "item_chars": bounds.LIST_WIDTHS.item_chars,
        "head_items": bounds.LIST_WIDTHS.head_items,
        **filters,
    }


class Listing(NamedTuple):
    """One page of the session list, and whether the store holds another after it."""

    rows: list[Row]
    more: bool


def sorted_sessions(
    connection: duckdb.DuckDBPyConnection,
    sort: str,
    direction: str,
    page: int,
    size: int,
    filters: Mapping[str, ParamValue],
    described: bool,
) -> Listing:
    """One page of the session list, ordered by one of `SORTS` — the design's composition.

    The library query stays the citable core: it goes in a subquery untouched, and what is
    wrapped around it is a WHERE of `FILTERS` predicates, an ORDER BY built from two
    dictionary lookups, a LIMIT, and `SHOWN` over the rows that survive all three — every
    value a request supplied bound as a parameter. `session_id` breaks ties in the same
    direction, which makes every sort a total order, its reverse exact, and the page
    boundaries stable between requests. The rows carrying no value sort last either way:
    "the store does not know" is not the largest reading of a column, or the smallest.

    `described` says whether the store holds the enrichment tables to join — a caller asks
    `view/enrichment.py`, which is where that catalog check lives. It is an argument rather
    than a check here because it is a fact about the store, not about the request.
    """
    # A sort or filter key *is* part of a SQL fragment, so membership is the whole guard —
    # and it is checked here as well as at the route, because this builds the SQL.
    if sort not in SORTS or not filters.keys() <= FILTERS.keys():
        raise KeyError(sort)
    keyword = DIRECTIONS[direction]
    # What the pass said each session was, joined before the sort so a row carries it: the
    # left join adds columns and never a row, so it changes neither the order nor the count.
    joined = (
        f" LEFT JOIN ({_core(Page.DESCRIBED_SESSIONS)}) USING (session_id)" if described else ""
    )
    # `FILTERS` order, not the query string's: the SQL a citation stands for is the same
    # whichever way a URL was typed.
    applied = [FILTERS[key].predicate for key in FILTERS if key in filters]
    where = f" WHERE {' AND '.join(applied)}" if applied else ""
    binds = list_bound(page, size, filters)
    # The one place the query and the citation under it differ, and deliberately: reading a row
    # past the page is cheaper than a second query and all a pager needs to know there is
    # another one, while a footer quoting that limit would offer a row the page never showed.
    binds["limit"] = size + PAGER_PROBE
    # The joined query cuts its own strings, and takes the same head a row's other strings do —
    # one surface prints the row. The page cites this half on its own out of the same call
    # (`view/pages/sessions/routes.py`), and the two cannot drift: both read one profile
    # through one manifest.
    if described:
        binds |= bound(Page.DESCRIBED_SESSIONS, bounds.LIST_WIDTHS)
    rows = fetch(
        connection,
        f"{SHOWN} (SELECT * FROM ({_core(Page.SESSIONS)}){joined}{where}"
        f" ORDER BY {sort} {keyword} NULLS LAST, session_id {keyword}"
        " LIMIT $limit OFFSET $offset)",
        binds,
    )
    return Listing(rows[:size], len(rows) > size)


def listed(rows: list[Row]) -> Listed:
    """A page of rows and the size of the level it came from, out of the query's own count.

    Every paging query carries `MATCHED_ROWS`: how many rows matched before the LIMIT, computed
    with a window function — so a page knows the whole level without a second query, and a level
    whose page is empty is one whose pages ran out.
    """
    return Listed(rows, rows[0][MATCHED_ROWS] if rows else 0)


def dropped(rows: list[Row]) -> int:
    """How many rows the query's own LIMIT left off, for a page that says so rather than lose them.

    A count of rows, not a shortened string: `format.cut` and the `cut` SQL macro are the other
    thing that word means here. Zero on an empty page — a level with nothing in it lost nothing.
    """
    return listed(rows).total - len(rows)


def paged(rows: list[Row], cursor: str) -> Paged:
    """A page of rows and its continuation, from a query's own pre-LIMIT match count.

    `MATCHED_ROWS` carries how many rows the cursor had ahead of it, which the paging queries
    compute with a window function — so a page knows what it cut without a second query, and
    cannot report "+0 more" for rows it silently dropped.
    """
    if not rows:
        return Paged(rows, 0, None)
    behind = dropped(rows)
    return Paged(rows, behind, rows[-1][cursor] if behind else None)
