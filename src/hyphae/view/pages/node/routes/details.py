"""The rest of one cut value: what a pane previewed at its width, fetched whole.

Every fat value on a node page is printed to its cut and marked, and the mark links here
(`docs/viewer-bounds.md`). One handler serves all sixteen, because a Detail declares
everything the fetch needs (`view/detail.py:DETAILS`): the query behind it, how it was
written, and the keys its route carries. A row that exists with nothing under it is a 404:
nothing links here unless there is a value to fetch.

The record route is the exception and keeps its own handler — it arrives with a header line
of its own, nothing previews a head of it, and no pane files it under a name.
"""

from collections.abc import Callable, Mapping
from typing import assert_never

import duckdb
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds
from hyphae.view.deps import Db, Viewer, ViewerDep
from hyphae.view.detail import DETAILS, Spec, Written, syntax_of
from hyphae.view.enrichment import enriched
from hyphae.view.pages.node import reads
from hyphae.view.pages.node.markup import values
from hyphae.view.store import Row, Value, page_rows

router = APIRouter()


def fetched(
    connection: duckdb.DuckDBPyConnection,
    value: Value,
    keyed: Mapping[str, ParamValue],
    column: str,
) -> tuple[Row, str]:
    """The one row a per-value fragment is for, and the query that found it.

    `column` is where the query puts the value this fragment is for. A row can exist with
    nothing under it — a `Read` has no command, a turn no prompt — and that is a 404 and
    not an empty page: nothing on a pane links here unless there is a value to fetch, so a
    request for one that is not there is a URL somebody typed or a link somebody kept.
    """
    rows = page_rows(connection, value, **keyed)
    if not rows or rows[0][column] is None:
        raise HTTPException(404, "Nothing in this store is stored under that id.")
    return rows[0], queries.citation(value, keyed)


def fetch(spec: Spec, request: Request, viewer: Viewer, connection: Db) -> Response:
    """One Detail whole, in the block its head was previewed in.

    The keys come off the request rather than a signature per route: the path named them, the
    spec's own route template is what minted the URL, and `queries.citation` prints them back
    into the line the fragment carries. A key the query does not bind is a crash there, which
    is a route registered against the wrong `whole`.

    `Written` decides the rest — the gate, the extra binding, and which of the three blocks
    the value comes back in. Nothing here asks the row what it is holding except through
    `syntax_of`, so a pane and its fetch cannot mark the same value up two ways.
    """
    if spec.written is Written.LINE and not enriched(connection):
        # A pass creates the enrichment tables rather than the exporter, so a store none has
        # touched holds no such line — the same nothing a missing row is, and the same answer
        # (`view/enrichment.py`). Asked per request and not at startup, because a pass can run
        # against the store while the viewer is reading it. Ahead of the read, which would
        # otherwise fail on the missing table rather than on the missing line.
        raise HTTPException(404, "No enrichment pass has written to this store.")
    keyed: dict[str, ParamValue] = dict(request.path_params)
    if spec.written is Written.NAMED_FILE:
        # Not a cut of the answer, which rides whole: the bound on the file suffix beside it,
        # which is what says how the answer is marked up. The one arm that reads the row to
        # find that out is the one arm that pays for it.
        keyed["head_chars"] = bounds.HEADER_WIDTHS.head_chars
    row, citation = fetched(connection, spec.whole, keyed, "value")
    whole = values.Whole(row["value"], spec.name, citation)
    match spec.written:
        case Written.LINE:
            return viewer.html(values.enrichment_line(node=whole))
        case Written.MARKDOWN:
            return viewer.html(values.prose(node=whole))
        case Written.BASH | Written.JSON | Written.NAMED_FILE:
            syntax = syntax_of(spec.written, row)
            assert syntax is not None  # noqa: S101  # only the two prose arms answer None
            return viewer.html(values.code(node=whole, syntax=syntax))
        case _:
            assert_never(spec.written)


def serving(spec: Spec) -> Callable[[Request, ViewerDep, Db], Response]:
    """One spec bound into an endpoint FastAPI can read a signature off.

    A closure rather than sixteen stubs: what changes between the routes is the spec, and
    what stays is the three things every fragment takes.
    """

    def serve(request: Request, viewer: ViewerDep, connection: Db) -> Response:
        return fetch(spec, request, viewer, connection)

    return serve


def register(on: APIRouter) -> None:
    """Every Detail the registry declares, as a route of its own on `on`.

    The public URLs are the registry's own, one route each — not one route under a
    `/fragment/{detail}` segment, which would collide with the popover and expansion
    fragments and move the unknown-name 404 out of the router and into the handler.
    """
    for spec in DETAILS:
        on.add_api_route(spec.route, serving(spec), methods=["GET"], name=spec.whole.value)


register(router)


@router.get("/fragment/record/session/{session_id}/thread/{source}/line/{line_no}")
def record_value(
    session_id: str, source: str, line_no: int, viewer: ViewerDep, connection: Db
) -> Response:
    """One raw transcript record whole, as the browser's preview was cut from.

    Its own renderer rather than a value fragment: a record arrives with a header line of
    its own, and it is the line a node was read from rather than one of the node's values,
    so nothing on a pane files it under a name and nothing swaps it into a detail.
    """
    keyed = {"session_id": session_id, "source": source, "line_no": line_no}
    # The record itself, which the store holds NOT NULL.
    row, citation = fetched(connection, Value.RECORD, keyed, "raw")
    return viewer.html(values.record(node=reads.record_value(row, citation)))
