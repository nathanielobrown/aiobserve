"""The session list at `/sessions`: what a request may ask of it, and the page that answers.

Which sort and filter keys the list offers, what a query-string value has to parse as, and the
page those choices compose. The SQL is `view/store.py`'s, beside every other page's
composition. This module hands it a key out of a closed dictionary and a value already parsed,
which is what makes a key outside them a 400 here rather than a fragment of SQL there.
"""

import datetime as dt
from collections.abc import Mapping
from typing import assert_never

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds
from hyphae.view.citation import cited
from hyphae.view.components.parts import Count
from hyphae.view.deps import ViewerDep
from hyphae.view.enrichment import enriched
from hyphae.view.links import DEFAULT_DIRECTION, DEFAULT_SORT, LIST_URL, list_url
from hyphae.view.pages.sessions import markup
from hyphae.view.pages.sessions.markup import Control
from hyphae.view.store import (
    DESCRIBED_BOUND,
    DIRECTIONS,
    FILTERS,
    SORTS,
    Page,
    Row,
    list_bound,
    open_store,
    page_rows,
    sorted_sessions,
)

router = APIRouter()


# The HTML input a filter's type gets on the form. One map rather than a field per filter.
CONTROLS: dict[queries.ParamType, str] = {
    queries.ParamType.TEXT: "text",
    queries.ParamType.DATE: "date",
    queries.ParamType.INTEGER: "number",
}


# The same two as `aria-sort` spells them. ARIA defines the tokens and `asc` is not one of
# them, so a heading marked with the query string's own word announces no order at all.
ARIA_SORT: dict[str, str] = {"asc": "ascending", "desc": "descending"}


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


def _session_row(row: Row) -> markup.SessionRow:
    """One store row as the row the session list prints.

    The three lists arrive as DuckDB lists and are NULL where the session has none, so each is
    coalesced here — the component prints what it is handed. The enrichment columns are absent
    entirely over a store with no pass to join, which is why they are read with `get`.
    """
    said = row.get("description")
    return markup.SessionRow(
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
        described=markup.Described(said, row["category"], row["outcome"]) if said else None,
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
        markup.sessions_page(
            rows=[_session_row(row) for row in rows],
            # One heading per sortable column, in `SORTS` order, each carrying the link
            # that re-sorts by it.
            headings=[markup.Heading(key, label, links[key]) for key, label in SORTS.items()],
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
            pages=markup.Pages(
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
                    # The bindings the query above ran, out of the one builder it read
                    # them from — including the widths, which are composed around the
                    # file like the paging is: re-running it alone answers with whole
                    # titles, paths and skill lists. The sort and the direction are the
                    # composition's own and bind nothing, so they are stated here.
                    {"sort": sort, "direction": direction, **list_bound(page, size, filters)},
                ),
                # Joined to that page rather than run against it, so it is cited on its
                # own — and only over a store whose enrichment tables exist to join.
                **(
                    {Page.DESCRIBED_SESSIONS.value: cited(Page.DESCRIBED_SESSIONS, DESCRIBED_BOUND)}
                    if describes
                    else {}
                ),
            },
            dev=viewer.dev,
        )
    )
