"""The session list's sort, filter and paging — the composition the design's seam calls for.

A `?sort=` column and a filter predicate cannot be bound parameters, so the library query
stays the citable core and this module wraps it: a WHERE of fixed predicates, an ORDER BY
built from two dictionary lookups, and a LIMIT. `SORTS` and `FILTERS` are closed, so a key
outside them is a 400 and never a fragment of SQL — every value a request supplied binds as
a parameter, and no request text reaches DuckDB as text.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NamedTuple, assert_never
from urllib.parse import urlencode

import duckdb
from fastapi import HTTPException

from aiobserve.analyze import queries
from aiobserve.analyze.queries import ParamValue
from aiobserve.sessions import project_predicate
from aiobserve.view import bounds
from aiobserve.view.store import Page, Row, fetch

# Where the list is served. Named here because the route and every link the list mints have
# to agree: `/` is the projects landing, and a link that still points there drops the sort and
# the filters this module composed. The templates write it out like any other route.
LIST_URL = "/sessions"

# What the session list can be sorted by: a column of `view_sessions`, mapped to its header
# label. A closed dictionary, and the only place a request's `sort` value is ever looked up —
# an unknown key is a 400, never a fragment of SQL. `tests/view/test_app.py` checks every
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
    # `--project` does. One statement of the rule, in `aiobserve.sessions`.
    "project": Filter(project_predicate("project_dir", "$project"), queries.ParamType.TEXT),
    "since": Filter("started_at >= $since", queries.ParamType.DATE),
    # Inclusive of the day named: someone asking for sessions until the 7th means the 7th.
    "until": Filter("started_at < $until + INTERVAL 1 DAY", queries.ParamType.DATE),
    "skill": Filter("list_contains(skills, $skill)", queries.ParamType.TEXT),
    # A floor rather than a flag, so `errors=1` reads "any" and a larger number "at least".
    "errors": Filter("tool_errors >= $errors", queries.ParamType.INTEGER),
}

# The HTML input a filter's type gets on the form. One map rather than a field per filter.
CONTROLS: dict[queries.ParamType, str] = {
    queries.ParamType.TEXT: "text",
    queries.ParamType.DATE: "date",
    queries.ParamType.INTEGER: "number",
}


class Control(NamedTuple):
    """One filter as the form renders it."""

    key: str
    # The HTML input type, from `CONTROLS`.
    type: str
    # What this request asked for, so the form comes back holding what was typed into it.
    value: str


# The two orderings a reader can ask for, as the SQL keyword each one puts in the ORDER BY.
DIRECTIONS: dict[str, str] = {"asc": "ASC", "desc": "DESC"}

# The same two as `aria-sort` spells them. ARIA defines the tokens and `asc` is not one of
# them, so a heading marked with the query string's own word announces no order at all.
ARIA_SORT: dict[str, str] = {"asc": "ascending", "desc": "descending"}

# Newest first: the session someone is looking for is usually the one that just ran.
DEFAULT_SORT = "started_at"
DEFAULT_DIRECTION = "desc"

# What one row of the list shows of the values a transcript wrote: each string cut to a head,
# the skills and the agent types cut to their first few with a count of what was left, and the
# PR links the page has no column for dropped. Composed here rather than in the query because
# the list's filters read the whole values — a `project` matched against a cut path would miss
# every session under a longer one, and a `skill` outside the first few would find nothing —
# and applied outside the window, so it cuts the rows one page shows and nothing else.
#
# Each cut takes one character more than the row prints, which is how the template knows a
# value was stopped rather than ended and marks it (`view/format.py:cut`).
SHOWN = """SELECT * EXCLUDE (pr_urls) REPLACE (
    substr(title, 1, $head_chars + 1) AS title,
    substr(project_dir, 1, $head_chars + 1) AS project_dir,
    list_transform(list_slice(coalesce(skills, []), 1, $head_items),
        name -> substr(name, 1, $item_chars + 1)) AS skills,
    list_slice(coalesce(agent_types, []), 1, $head_items) AS agent_types
), greatest(len(coalesce(skills, [])) - $head_items, 0) AS skills_cut,
   greatest(len(coalesce(agent_types, [])) - $head_items, 0) AS agent_types_cut FROM"""

# Every query-string key the session list reads: the filters, plus what orders and pages them.
LIST_KEYS = frozenset(FILTERS) | {"sort", "direction", "page", "size"}


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
    listing = queries.load(Page.SESSIONS).strip().rstrip(";")
    # What the pass said each session was, joined before the sort so a row carries it: the
    # left join adds columns and never a row, so it changes neither the order nor the count.
    said = queries.load(Page.DESCRIBED_SESSIONS).strip().rstrip(";")
    joined = f" LEFT JOIN ({said}) USING (session_id)" if described else ""
    # `FILTERS` order, not the query string's: the SQL a citation stands for is the same
    # whichever way a URL was typed.
    applied = [FILTERS[key].predicate for key in FILTERS if key in filters]
    where = f" WHERE {' AND '.join(applied)}" if applied else ""
    # One row past the page: cheaper than a second query, and all a pager needs to know.
    bound: dict[str, ParamValue] = {
        "limit": size + 1,
        "offset": (page - 1) * size,
        "head_chars": queries.LIST_CHARS,
        "item_chars": queries.LIST_ITEM_CHARS,
        "head_items": queries.LIST_ITEMS,
        **filters,
    }
    # The joined query cuts its own strings, and takes the same head a row's other strings do.
    if described:
        bound["tag_chars"] = queries.TAG_CHARS
        bound["kind_chars"] = queries.TAG_CHARS
        bound["head_kinds"] = queries.LIST_CATEGORIES
    rows = fetch(
        connection,
        f"{SHOWN} (SELECT * FROM ({listing}){joined}{where}"
        f" ORDER BY {sort} {keyword} NULLS LAST, session_id {keyword}"
        " LIMIT $limit OFFSET $offset)",
        bound,
    )
    return Listing(rows[:size], len(rows) > size)


def narrowing(params: Mapping[str, str]) -> dict[str, ParamValue]:
    """The filters one request asked for, each parsed as the type its predicate binds.

    A key outside `LIST_KEYS` is a 400 rather than a no-op: FastAPI ignores what it was not
    declared with, so a mistyped filter would otherwise show the whole corpus and look like
    an answer. An empty value is not a filter — the list's form submits every field, so a
    blank one has to mean "not filtering".
    """
    if not params.keys() <= LIST_KEYS:
        raise HTTPException(400, f"The list takes {', '.join(sorted(LIST_KEYS))}.")
    return {key: _as_bound(key, given) for key in FILTERS if (given := params.get(key))}


def _as_bound(key: str, text: str) -> ParamValue:
    """One filter's query-string text as the value DuckDB binds, or a 400."""
    kind = FILTERS[key].type
    try:
        match kind:
            case queries.ParamType.TEXT:
                return text
            case queries.ParamType.INTEGER:
                return int(text)
            case queries.ParamType.DATE:
                return dt.date.fromisoformat(text)
            case _:
                assert_never(kind)
    except ValueError as error:
        raise HTTPException(400, f"The list's {key} takes {kind} values.") from error


def list_url(sort: str, direction: str, page: int, size: int, filters: Mapping[str, str]) -> str:
    """A link back to the list, carrying everything that made this view of it.

    Every link the list writes goes through here. A filter that rode the sort headings but
    not the pager would widen the list halfway through reading it, which is the kind of thing
    a reader notices only after quoting the wrong count.
    """
    query: dict[str, str | int] = {"sort": sort, "direction": direction}
    if page > 1:
        query["page"] = page
    if size != bounds.SESSIONS.default:
        query["size"] = size
    narrowed = {key: value for key, value in filters.items() if value}
    return f"{LIST_URL}?" + urlencode(query | narrowed)
