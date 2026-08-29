"""The pages that are not a node's: a session's failures, a query, the raw records, a file.

Each answers a question about a session that no single node holds — every failed tool call on
every thread, the SQL behind a page, the transcript as Claude Code wrote it, and the output a
tool wrote to a file instead of the transcript (`docs/viewer.md`).
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.routing import BaseRoute

from hyphae.analyze import macros, manifest, queries
from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds, errors
from hyphae.view.browse import (
    header_bound,
)
from hyphae.view.citation import QUERY_URL, cited
from hyphae.view.knobs import (
    checked,
)
from hyphae.view.store import (
    Page,
    open_store,
    page_rows,
    paged,
)
from hyphae.view.viewer import Viewer


def routes(viewer: Viewer) -> list[BaseRoute]:
    """Every page that is not a node's, bound to one viewer, in registration order."""
    router = APIRouter()

    @router.get("/session/{session_id}/errors")
    def errors_page(request: Request, session_id: str) -> Response:
        """Every failed tool call of one session, in the order they happened.

        Not a node page: a failure is a property of a tool call rather than a place in the
        NavTree, and a session's failures are scattered across every thread it ran. So this is a
        list, and each row leads to the tool call's own page — which opens the NavTree at it and
        carries the crumbs that place it.
        """
        with open_store(viewer.db) as connection:
            failed = errors.failures(connection, session_id)
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
        return viewer.templates.TemplateResponse(
            request,
            "errors.html",
            {
                "session_id": session_id,
                "listed": failed.listed,
                "cut": failed.cut,
                "citations": {named.value: cited(named, bound) for named, bound in failed.ran},
            },
        )

    @router.get(f"{QUERY_URL}/{{query_name}}")
    def query_page(request: Request, query_name: str) -> Response:
        """One library query's SQL, under the bindings a page cited it with.

        Where every citation in a footer goes. The name is a key of the query manifest and
        never a path: a name the manifest does not declare is a 404 before anything is read,
        which is what makes a request for `../../secret` a miss rather than a file.
        """
        if query_name not in manifest.QUERIES:
            raise HTTPException(404, "No query by that name ships with this build.")
        return viewer.templates.TemplateResponse(
            request,
            "query.html",
            # Whatever the citation carried, printed back rather than bound to anything: this
            # page runs no query, so a binding here is a fact about the page that sent you.
            {
                "name": query_name,
                "sql": queries.load(query_name),
                # What a shell has to run first, where the statement calls a library macro:
                # both consumers install these, and a reader pasting the statement alone has
                # no way to find out why the catalog does not know the name.
                "macros": macros.needed_by(queries.load(query_name)),
                "bindings": dict(request.query_params),
            },
        )

    @router.get("/session/{session_id}/thread/{source}/records")
    def records_page(
        request: Request,
        session_id: str,
        source: str,
        after: int = queries.FIRST_PAGE,
        size: int = bounds.RECORDS.default,
    ) -> Response:
        """One page of a thread's raw transcript — where a report's citation lands.

        A citation names `(session_id, source, line_no)`; the URL for it is this path with
        `?after={line_no - 1}#L{line_no}`, so the cited record is the first row on the page.
        """
        checked(size, bounds.RECORDS.ceiling)
        keyed: dict[str, ParamValue] = {"session_id": session_id, "source": source}
        bound = keyed | {
            "after": after,
            "page_records": size,
            "preview_chars": queries.RECORD_PREVIEW,
        }
        with open_store(viewer.db) as connection:
            page = paged(
                page_rows(connection, Page.RECORDS, **bound),
                "matched_records",
                "line_no",
            )
        # A thread the store never held and a cursor past the end of one it does are the same
        # answer — nothing at this URL. Neither is a page worth rendering empty.
        if not page.rows:
            raise HTTPException(404, "This store holds no records for that thread at that line.")
        # The one record the page fetches unasked: the first row, which is the one a citation
        # named — but only where a record that wide stays inside a page's budget
        # (`bounds.OPENED_RECORD_CHARS`). Past it the row is where every other row is, one
        # click from its own fetch, because a reader who paged here asked for no such thing.
        first = page.rows[0]
        opened = first["line_no"] if first["raw_chars"] <= bounds.OPENED_RECORD_CHARS else None
        return viewer.templates.TemplateResponse(
            request,
            "records.html",
            {
                "session_id": session_id,
                "source": source,
                "page": page,
                "size": size,
                "opened": opened,
                "citations": {Page.RECORDS.value: cited(Page.RECORDS, bound)},
            },
        )

    @router.get("/session/{session_id}/offload/{offload_name:path}")
    def offload_page(
        request: Request,
        session_id: str,
        offload_name: str,
        after: int = 0,
        size: int = bounds.CHUNK.default,
    ) -> Response:
        """One chunk of a tool result Claude Code wrote to a file beside the transcript.

        The name is the transcript's own file name, so it may hold anything a tool named a
        file — spaces, percent signs, something shaped like a path. It is a key into the store
        and never a path the server opens, which is what makes the shape of it uninteresting.
        """
        checked(size, bounds.CHUNK.ceiling)
        if after < 0:
            raise HTTPException(400, "Ask for an offset of 0 or more.")
        bound: dict[str, ParamValue] = {
            "session_id": session_id,
            "name": offload_name,
            "after_chars": after,
            "chunk_chars": size,
        }
        with open_store(viewer.db) as connection:
            rows = page_rows(connection, Page.OFFLOAD, **bound)
        if not rows:
            raise HTTPException(404, "No offloaded result of that name is in this session.")
        row = rows[0]
        served = after + len(row["chunk"])
        return viewer.templates.TemplateResponse(
            request,
            "offload.html",
            {
                "session_id": session_id,
                "row": row,
                "size": size,
                # Where the next chunk starts, or None when this one reached the end.
                "after": served if served < row["content_chars"] else None,
                "citations": {Page.OFFLOAD.value: cited(Page.OFFLOAD, bound)},
            },
        )

    return router.routes
