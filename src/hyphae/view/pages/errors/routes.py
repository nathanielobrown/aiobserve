"""The errors page: every failed tool call of one session, on every thread, in order.

Not a node page: a failure is a property of a tool call rather than a place in the NavTree, and
a session's failures are scattered across every thread it ran (`docs/viewer.md`).
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from hyphae.view import failures
from hyphae.view.citation import cited
from hyphae.view.deps import ViewerDep
from hyphae.view.pages.errors import markup
from hyphae.view.store import (
    Page,
    header_bound,
    open_store,
    page_rows,
)

router = APIRouter()


@router.get("/session/{session_id}/errors")
def errors_page(session_id: str, viewer: ViewerDep) -> Response:
    """Every failed tool call of one session, in the order they happened.

    Not a node page: a failure is a property of a tool call rather than a place in the
    NavTree, and a session's failures are scattered across every thread it ran. So this is a
    list, and each row leads to the tool call's own page — which opens the NavTree at it and
    carries the crumbs that place it.
    """
    with open_store(viewer.db) as connection:
        failed = failures.failures(connection, session_id)
        # A session the store never held and one whose calls all succeeded are both
        # nothing at this URL, and not the same nothing. The header is read only when
        # there is a 404 to word, so the page a reader actually opens runs one query.
        held = bool(failed.listed) or bool(
            page_rows(connection, Page.SESSION_HEADER, **header_bound(session_id))
        )
    if not failed.listed:
        raise HTTPException(
            404,
            "This session's tool calls all succeeded."
            if held
            else "No session with that id is in this store.",
        )
    return viewer.html(
        markup.errors_page(
            session_id=session_id,
            listed=failed.listed,
            cut=failed.cut,
            citations={named.value: cited(named, bound) for named, bound in failed.ran},
            dev=viewer.dev,
        )
    )
