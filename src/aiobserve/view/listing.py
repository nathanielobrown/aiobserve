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
from aiobserve.view.store import Page, Row, fetch

# What the session list can be sorted by: a column of `view_sessions`, mapped to its header
# label. A closed dictionary, and the only place a request's `sort` value is ever looked up —
# an unknown key is a 400, never a fragment of SQL. `tests/view/test_app.py` checks every
# key against the columns the query returns.
SORTS: dict[str, str] = {
    "started_at": "Started",
    "title": "Session",
    "project_dir": "Project",
    "turns": "Turns",
    "api_calls": "Calls",
    "tool_calls": "Tools",
    "tool_errors": "Errors",
    "agent_runs": "Runs",
    "compactions": "Compactions",
    "cost_usd": "Cost",
    "output_tokens": "Output",
    "wall_ms": "Wall",
    "active_ms": "Active",
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
    "project": Filter("project_dir = $project", queries.ParamType.TEXT),
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


class Direction(NamedTuple):
    """One sort direction, as the two SQL fragments it puts in the ORDER BY."""

    keyword: str
    # Where the NULLs go. Opposite ends in the two directions, so that reversing a sort
    # reverses the whole list rather than pinning the empty rows to one end.
    nulls: str


DIRECTIONS: dict[str, Direction] = {
    "asc": Direction("ASC", "NULLS LAST"),
    "desc": Direction("DESC", "NULLS FIRST"),
}

# Newest first: the session someone is looking for is usually the one that just ran.
DEFAULT_SORT = "started_at"
DEFAULT_DIRECTION = "desc"

# How many sessions a page of the list holds, and the most it will hold on request. The list
# is the one page that grows with the corpus: 575 sessions rendered whole came to 587 KB,
# past the design's page ceiling, so the size is bound rather than assumed small. The maximum
# is what fits under that ceiling at the measured cost of a row, not a round number — a
# `?size=` above it is a page the design's bound does not cover.
PAGE_SESSIONS = 200
MAX_PAGE_SESSIONS = 300

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
) -> Listing:
    """One page of the session list, ordered by one of `SORTS` — the design's composition.

    The library query stays the citable core: it goes in a subquery untouched, and what is
    wrapped around it is a WHERE of `FILTERS` predicates, an ORDER BY built from two
    dictionary lookups, and a LIMIT — every value a request supplied bound as a parameter.
    `session_id` breaks ties in the same direction, which makes every sort a total order,
    its reverse exact, and the page boundaries stable between requests.
    """
    # A sort or filter key *is* part of a SQL fragment, so membership is the whole guard —
    # and it is checked here as well as at the route, because this builds the SQL.
    if sort not in SORTS or not filters.keys() <= FILTERS.keys():
        raise KeyError(sort)
    order = DIRECTIONS[direction]
    listing = queries.load(Page.SESSIONS).strip().rstrip(";")
    # `FILTERS` order, not the query string's: the SQL a citation stands for is the same
    # whichever way a URL was typed.
    applied = [FILTERS[key].predicate for key in FILTERS if key in filters]
    where = f" WHERE {' AND '.join(applied)}" if applied else ""
    # One row past the page: cheaper than a second query, and all a pager needs to know.
    rows = fetch(
        connection,
        f"SELECT * FROM ({listing}){where}"
        f" ORDER BY {sort} {order.keyword} {order.nulls}, session_id {order.keyword}"
        " LIMIT $limit OFFSET $offset",
        {"limit": size + 1, "offset": (page - 1) * size, **filters},
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
    if size != PAGE_SESSIONS:
        query["size"] = size
    return "/?" + urlencode(query | {key: value for key, value in filters.items() if value})
