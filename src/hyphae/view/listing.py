"""The store's two lists: the projects landing and the session list, with the composition behind
them.

A `?sort=` column and a filter predicate cannot be bound parameters, so the library query
stays the citable core and this module wraps it: a WHERE of fixed predicates, an ORDER BY
built from two dictionary lookups, and a LIMIT. `SORTS` and `FILTERS` are closed, so a key
outside them is a 400 and never a fragment of SQL — every value a request supplied binds as
a parameter, and no request text reaches DuckDB as text.

The two pages are here because they are the readers of all that: one lists the projects a
store holds sessions for, the other lists the sessions themselves, and the sorts and filters
they offer are the ones composed above.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NamedTuple, assert_never
from urllib.parse import urlencode

import duckdb
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.routing import BaseRoute

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.projects import project_predicate
from hyphae.view import bounds
from hyphae.view import format as fmt
from hyphae.view.citation import cited
from hyphae.view.components import listing as components
from hyphae.view.components.listing import LIST_URL, Control
from hyphae.view.components.parts import Count
from hyphae.view.enrichment import enriched
from hyphae.view.store import (
    Page,
    Row,
    fetch,
    open_store,
    page_rows,
)
from hyphae.view.viewer import Viewer

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

# The HTML input a filter's type gets on the form. One map rather than a field per filter.
CONTROLS: dict[queries.ParamType, str] = {
    queries.ParamType.TEXT: "text",
    queries.ParamType.DATE: "date",
    queries.ParamType.INTEGER: "number",
}


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
# Each cut takes one character more than the row prints, which is how the component knows a
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


def project_link(project_dir: str | None) -> str | None:
    """The session list narrowed to one project, or None when there is no list to open.

    The path is the whole one and not the head a row shows — the list's filter matches a path
    prefix, and a cut one matches nothing. A row the query left NULL is a row with no link:
    the sessions that named no directory, and a path longer than the head this page shows.
    """
    if project_dir is None:
        return None
    return list_url(
        DEFAULT_SORT, DEFAULT_DIRECTION, 1, bounds.SESSIONS.default, {"project": project_dir}
    )


def _project_row(row: Row) -> components.ProjectRow:
    """One store row as the row the landing page prints.

    The link is minted through the list's own builder, so a project opens the list the way the
    list links to itself, and off `project_filter` rather than the path the row shows: the
    filter matches a whole path, and a cut one matches nothing.
    """
    return components.ProjectRow(
        project_dir=row["project_dir"],
        link=project_link(row["project_filter"]),
        recent_sessions=row["recent_sessions"],
        recent_cost=row["recent_cost"],
        recent_unpriced=row["recent_unpriced"],
        window_sessions=row["window_sessions"],
        window_cost=row["window_cost"],
        window_unpriced=row["window_unpriced"],
        sessions=row["sessions"],
        cost_usd=row["cost_usd"],
        unpriced_api_calls=row["unpriced_api_calls"],
        last_active=row["last_active"],
    )


def _session_row(row: Row) -> components.SessionRow:
    """One store row as the row the session list prints.

    The three lists arrive as DuckDB lists and are NULL where the session has none, so each is
    coalesced here — the component prints what it is handed. The enrichment columns are absent
    entirely over a store with no pass to join, which is why they are read with `get`.
    """
    said = row.get("description")
    return components.SessionRow(
        session_id=row["session_id"],
        started_at=row["started_at"],
        title=row["title"],
        project_dir=row["project_dir"],
        turns=row["turns"],
        api_calls=row["api_calls"],
        tool_calls=row["tool_calls"],
        compactions=row["compactions"],
        tool_errors=row["tool_errors"],
        cost_usd=row["cost_usd"],
        output_tokens=row["output_tokens"],
        unpriced_api_calls=row["unpriced_api_calls"],
        wall_ms=row["wall_ms"],
        active_ms=row["active_ms"],
        agent_types=[Count(kind["name"], kind["runs"]) for kind in row["agent_types"] or []],
        agent_types_cut=row["agent_types_cut"],
        skills=row["skills"] or [],
        skills_cut=row["skills_cut"],
        work=[Count(kind["name"], kind["turns"]) for kind in row.get("work") or []],
        work_cut=row.get("work_cut", 0),
        described=components.Described(said, row["category"], row["outcome"]) if said else None,
    )


def routes(viewer: Viewer) -> list[BaseRoute]:
    """The store's two lists as pages, bound to one viewer, in registration order."""
    router = APIRouter()

    @router.get("/")
    def projects_page() -> Response:
        """Every project the store holds sessions for, most recently active first."""
        # The clock both trailing windows are measured back from, read here and bound like
        # any other parameter. The query reads no clock of its own: a page counting "the last
        # 7 days" from SQL's `now()` would cite a line that answers something else tomorrow,
        # and the footer's whole promise is that a reader can re-run what the page ran.
        bound: dict[str, ParamValue] = {
            "as_of": fmt.utcnow().date(),
            "recent_days": queries.PAGE_RECENT_DAYS,
            "window_days": queries.PAGE_WINDOW_DAYS,
            "head_chars": queries.LIST_CHARS,
            "projects": bounds.PROJECTS.default,
        }
        with open_store(viewer.db) as connection:
            rows = page_rows(connection, Page.PROJECT_ROLLUPS, **bound)
        return viewer.html(
            components.projects_page(
                rows=[_project_row(row) for row in rows],
                # The bindings the two window headings print, so a heading and its column read
                # the same numbers — the citation below carries them too.
                recent_days=queries.PAGE_RECENT_DAYS,
                window_days=queries.PAGE_WINDOW_DAYS,
                # What the page cut, which the query counted before its LIMIT: a landing page
                # that silently dropped projects would be a corpus a reader cannot see.
                cut=(rows[0]["matched_projects"] - len(rows)) if rows else 0,
                citations={Page.PROJECT_ROLLUPS.value: cited(Page.PROJECT_ROLLUPS, bound)},
                dev=viewer.dev,
            )
        )

    @router.get(LIST_URL)
    def session_list(
        request: Request,
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIRECTION,
        page: int = 1,
        size: int = bounds.SESSIONS.default,
    ) -> Response:
        """One page of sessions, under the filter, sort and size the URL carries."""
        if sort not in SORTS or direction not in DIRECTIONS:
            raise HTTPException(
                400,
                f"Sort by one of {', '.join(SORTS)}, in direction {' or '.join(DIRECTIONS)}.",
            )
        if page < 1 or not 1 <= size <= bounds.SESSIONS.ceiling:
            raise HTTPException(
                400, f"Ask for page 1 or later, at a size between 1 and {bounds.SESSIONS.ceiling}."
            )
        filters = narrowing(request.query_params)
        # What the URL said, kept as text: the links have to reproduce the request, and the
        # form has to come back filled in with what was typed into it.
        given = {key: request.query_params.get(key, "") for key in FILTERS}
        with open_store(viewer.db) as connection:
            # Whether the store holds the enrichment tables at all, which decides both what
            # the list joins and what it cites: a page cites what it ran.
            describes = enriched(connection)
            rows, more = sorted_sessions(
                connection, sort, direction, page, size, filters, described=describes
            )
            projects = page_rows(
                connection,
                Page.PROJECTS,
                head_chars=queries.LIST_CHARS,
                head_projects=queries.LIST_PROJECTS,
            )
        # A header link flips the direction of the column already sorted by, and opens any
        # other column at the direction that puts its largest values first. Re-sorting starts
        # from the first page: page 4 of one order says nothing about page 4 of another.
        flipped = "asc" if direction == "desc" else "desc"
        links = {
            key: list_url(key, flipped if key == sort else DEFAULT_DIRECTION, 1, size, given)
            for key in SORTS
        }
        return viewer.html(
            components.sessions_page(
                rows=[_session_row(row) for row in rows],
                # One heading per sortable column, in `SORTS` order, each carrying the link
                # that re-sorts by it.
                headings=[
                    components.Heading(key, label, links[key]) for key, label in SORTS.items()
                ],
                sort=sort,
                direction=direction,
                # The same ordering in ARIA's vocabulary, for the heading that marks it: the
                # form and the links carry the query string's word, the mark carries ARIA's.
                aria_direction=ARIA_SORT[direction],
                # One input per filter, in `FILTERS` order, carrying what this request asked.
                controls=[
                    Control(key, CONTROLS[spec.type], given[key]) for key, spec in FILTERS.items()
                ],
                projects=[row["project_dir"] for row in projects],
                pages=components.Pages(
                    first=(page - 1) * size + 1,
                    shown=len(rows),
                    previous=list_url(sort, direction, page - 1, size, given) if page > 1 else None,
                    next=list_url(sort, direction, page + 1, size, given) if more else None,
                ),
                # Whether the store holds an enrichment pass's answers at all, which decides
                # whether the list carries a work column: an empty one over a store no pass
                # has touched is a claim the store cannot support.
                describes=describes,
                citations={
                    Page.SESSIONS.value: cited(
                        Page.SESSIONS,
                        {
                            "sort": sort,
                            "direction": direction,
                            "limit": size,
                            "offset": (page - 1) * size,
                            # What the page shows of each row, which is composed around the
                            # query like the paging is: re-running the file alone answers
                            # with whole titles, paths and skill lists.
                            "head_chars": queries.LIST_CHARS,
                            "item_chars": queries.LIST_ITEM_CHARS,
                            "head_items": queries.LIST_ITEMS,
                            **filters,
                        },
                    ),
                    # Joined to that page rather than run against it, so it is cited on its
                    # own — and only over a store whose enrichment tables exist to join.
                    **(
                        {
                            Page.DESCRIBED_SESSIONS.value: cited(
                                Page.DESCRIBED_SESSIONS,
                                {
                                    "head_chars": queries.LIST_CHARS,
                                    "tag_chars": queries.TAG_CHARS,
                                    "kind_chars": queries.TAG_CHARS,
                                    "head_kinds": queries.LIST_CATEGORIES,
                                },
                            )
                        }
                        if describes
                        else {}
                    ),
                },
                dev=viewer.dev,
            )
        )

    return router.routes
