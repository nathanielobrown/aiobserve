"""The store's two lists: the projects landing, and the session list under its filter form.

One page lists the projects a store holds sessions for, the other the sessions themselves.
What a request may ask of the second is here: which sort and filter keys it offers, what a
query-string value has to parse as, and the links that carry all of it to the next page.

The SQL those choices compose is `view/store.py`'s, beside every other page's composition.
This module hands it a key out of a closed dictionary and a value already parsed, which is
what makes a key outside them a 400 here rather than a fragment of SQL there.
"""

import datetime as dt
from collections.abc import Mapping
from typing import assert_never
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds
from hyphae.view import format as fmt
from hyphae.view.citation import cited
from hyphae.view.components import listing as components
from hyphae.view.components.listing import Control
from hyphae.view.components.parts import Count
from hyphae.view.deps import ViewerDep
from hyphae.view.enrichment import enriched
from hyphae.view.nodes import LIST_URL
from hyphae.view.store import (
    DIRECTIONS,
    FILTERS,
    SORTS,
    Page,
    Row,
    open_store,
    page_rows,
    sorted_sessions,
)

# The HTML input a filter's type gets on the form. One map rather than a field per filter.
CONTROLS: dict[queries.ParamType, str] = {
    queries.ParamType.TEXT: "text",
    queries.ParamType.DATE: "date",
    queries.ParamType.INTEGER: "number",
}


# The same two as `aria-sort` spells them. ARIA defines the tokens and `asc` is not one of
# them, so a heading marked with the query string's own word announces no order at all.
ARIA_SORT: dict[str, str] = {"asc": "ascending", "desc": "descending"}

# Newest first: the session someone is looking for is usually the one that just ran.
DEFAULT_SORT = "started_at"
DEFAULT_DIRECTION = "desc"

# Every query-string key the session list reads: the filters, plus what orders and pages them.
LIST_KEYS = frozenset(FILTERS) | {"sort", "direction", "page", "size"}


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


router = APIRouter()


@router.get("/")
def projects_page(viewer: ViewerDep) -> Response:
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
    viewer: ViewerDep,
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
            headings=[components.Heading(key, label, links[key]) for key, label in SORTS.items()],
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
