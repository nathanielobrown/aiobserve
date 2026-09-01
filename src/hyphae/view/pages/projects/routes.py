"""The projects landing at `/`: every project the store holds sessions for.

The page reads one rollup query and prints a row per project. What it cuts, it says: the
count the query took before its LIMIT rides in the footer beside the citation.
"""

from fastapi import APIRouter
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds
from hyphae.view.citation import cited
from hyphae.view.deps import ViewerDep
from hyphae.view.links import project_link
from hyphae.view.pages.projects import markup
from hyphae.view.store import Page, Row, dropped, open_store, page_rows
from hyphae.view.text import format as fmt

router = APIRouter()


def _project_row(row: Row) -> markup.ProjectRow:
    """One store row as the row the landing page prints.

    The link is minted through the list's own builder, so a project opens the list the way the
    list links to itself, and off `project_filter` rather than the path the row shows: the
    filter matches a whole path, and a cut one matches nothing.
    """
    return markup.ProjectRow(
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
        markup.projects_page(
            rows=[_project_row(row) for row in rows],
            # The bindings the two window headings print, so a heading and its column read
            # the same numbers — the citation below carries them too.
            recent_days=queries.PAGE_RECENT_DAYS,
            window_days=queries.PAGE_WINDOW_DAYS,
            # What the page cut, which the query counted before its LIMIT: a landing page
            # that silently dropped projects would be a corpus a reader cannot see.
            cut=dropped(rows),
            citations={Page.PROJECT_ROLLUPS.value: cited(Page.PROJECT_ROLLUPS, bound)},
            dev=viewer.dev,
        )
    )
