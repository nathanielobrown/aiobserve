"""One whole line an enrichment pass wrote, fetched from the block that previewed its head.

A description or a friction line is a fat value like any other, and is offered the same way:
the pane prints the head cut at its width and mints the URL for the rest. What is different is
that no pass may have run, and then there is no line and no table to read it from
(`docs/enrichment.md`).
"""

from collections.abc import Mapping

import duckdb
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from hyphae.analyze.queries import ParamValue
from hyphae.view.deps import Db, Viewer, ViewerDep
from hyphae.view.enrichment import enriched
from hyphae.view.pages.node.markup import values
from hyphae.view.pages.node.routes.details import fetched
from hyphae.view.store import Value

router = APIRouter()


def enrichment_line(
    viewer: Viewer,
    connection: duckdb.DuckDBPyConnection,
    value: Value,
    keyed: Mapping[str, ParamValue],
    field: str,
) -> Response:
    """One whole line an enrichment pass wrote, or a 404 where no pass wrote one.

    A pass creates the enrichment tables rather than the exporter, so a store none has
    touched holds no such line — the same nothing a missing row is, and the same answer
    (`view/enrichment.py`). Asked per request and not at startup, because a pass can run
    against the store while the viewer is reading it.
    """
    written = enriched(connection)
    if not written:
        raise HTTPException(404, "No enrichment pass has written to this store.")
    # The query answers the one line under `value`, like every other per-value query; `field`
    # is the name the block it swaps into is filed under, which is what the pane calls it.
    row, citation = fetched(connection, value, keyed, "value")
    return viewer.html(values.enrichment_line(node=values.Whole(row["value"], field, citation)))


@router.get("/fragment/description/session/{session_id}/thread/{source}/turn/{turn_id}")
def turn_description(
    session_id: str, source: str, turn_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """The whole of what a pass said one turn did."""
    keyed = {"session_id": session_id, "source": source, "turn_id": turn_id}
    return enrichment_line(viewer, connection, Value.TURN_DESCRIPTION, keyed, "description")


@router.get("/fragment/friction/session/{session_id}/thread/{source}/turn/{turn_id}")
def turn_friction(
    session_id: str, source: str, turn_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """The whole of the friction a pass saw in one turn."""
    keyed = {"session_id": session_id, "source": source, "turn_id": turn_id}
    return enrichment_line(viewer, connection, Value.TURN_FRICTION, keyed, "friction")


@router.get("/fragment/description/session/{session_id}/run/{run_id}")
def run_description(session_id: str, run_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of what a pass said one agent run did."""
    keyed = {"session_id": session_id, "run_id": run_id}
    return enrichment_line(viewer, connection, Value.RUN_DESCRIPTION, keyed, "description")


@router.get("/fragment/friction/session/{session_id}/run/{run_id}")
def run_friction(session_id: str, run_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of the friction a pass saw in one agent run."""
    keyed = {"session_id": session_id, "run_id": run_id}
    return enrichment_line(viewer, connection, Value.RUN_FRICTION, keyed, "friction")


@router.get("/fragment/description/session/{session_id}")
def session_description(session_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of what a pass said one session did."""
    return enrichment_line(
        viewer, connection, Value.SESSION_DESCRIPTION, {"session_id": session_id}, "description"
    )


@router.get("/fragment/friction/session/{session_id}")
def session_friction(session_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of the friction a pass saw in one session."""
    return enrichment_line(
        viewer, connection, Value.SESSION_FRICTION, {"session_id": session_id}, "friction"
    )
