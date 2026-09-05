"""The numbers behind one NavTree row, fetched when a reader points at it or tabs to it.

One popover per node, and one route per kind because each kind is keyed differently. What it
draws is what the row already shows — the cost badge and the context bar — written out: the
node's own spend, and what the agent runs under it spent broken out below (`CONTEXT.md`).
"""

import duckdb
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.model import MAIN_SOURCE
from hyphae.view import bounds, nodes
from hyphae.view.deps import Db, Viewer, ViewerDep
from hyphae.view.nodes import Kind, Ref
from hyphae.view.pages.node import reads
from hyphae.view.pages.node.markup import numbers
from hyphae.view.pages.node.numbers import breakout, charges, spend, wash
from hyphae.view.store import Fragment, bound, page_rows

router = APIRouter()


def counted(
    viewer: Viewer,
    connection: duckdb.DuckDBPyConnection,
    kind: Kind,
    session_id: str,
    source: str,
    node_id: str,
) -> Response:
    """One node's numbers, for the popover its NavTree row fetches.

    `source` is the thread the window is read on, which is not always the thread the node
    sits on: a session's reader is reading `main`, and its spend is every thread's. What
    differs between the kinds is inside the query; what differs here is only the tool call,
    which has no api calls to be measured out of.
    """
    if kind is Kind.TOOL:
        keyed = bound(
            Fragment.TOOL_NUMBERS,
            bounds.POPOVER_WIDTHS,
            session_id=session_id,
            source=source,
            tool_call_id=node_id,
        )
        rows = page_rows(connection, Fragment.TOOL_NUMBERS, **keyed)
        if not rows:
            raise HTTPException(404, "No tool call with that id is in this thread.")
        return viewer.html(
            numbers.tool(
                key=Ref(kind, source, node_id).key,
                citation=queries.citation(Fragment.TOOL_NUMBERS, keyed),
                node=reads.tool_numbers(rows[0]),
            )
        )
    binds = bound(
        Fragment.NUMBERS,
        bounds.POPOVER_WIDTHS,
        session_id=session_id,
        source=source,
        node_id=node_id,
        kind=kind,
    )
    rows = page_rows(connection, Fragment.NUMBERS, **binds)
    # The query aggregates, so it answers a row for a node that is not there as readily as
    # for one that is — a node with no api calls under it is a real reading, and the
    # popover prints it as the dashes it is.
    read = reads.node_numbers(rows[0])
    whole = read.session_usd
    return viewer.html(
        numbers.popover(
            key=Ref(kind, source, node_id).key,
            citation=queries.citation(Fragment.NUMBERS, binds),
            node=read.window,
            # The three lines between the window and the total, each priced and washed
            # here rather than in the component: what a charge is made of is arithmetic
            # (`view/numbers.py`), and the total under them takes the same ground.
            charges=charges(read, spend(read.spent), whole),
            total_wash=wash(read.cost_usd, whole),
            # And the two lines under them, where agent runs hang below this node: None
            # where none does, which is what keeps the breakout off every other row.
            breakout=breakout(read.cost_usd, read.subtree_usd, whole),
        )
    )


@router.get(
    f"{nodes.NUMBERS_URL}/session/{{session_id}}/thread/{{source}}"
    f"/{Kind.COMPACTION}/{{compaction_id}}"
)
def compaction_numbers(
    session_id: str, source: str, compaction_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """One compaction's numbers: the window it dropped, and the word recorded for why.

    Its own route rather than a branch of `counted`, because a compaction shares nothing
    with the kinds made of api calls — no window to stand on, no model, no dollar. It must
    stay above the route below it, whose `{kind}` matches this path too: which of the two
    answers is decided by the order they are registered in.
    """
    keyed = bound(
        Fragment.COMPACTION_NUMBERS,
        bounds.POPOVER_WIDTHS,
        session_id=session_id,
        source=source,
        compaction_id=compaction_id,
    )
    rows = page_rows(connection, Fragment.COMPACTION_NUMBERS, **keyed)
    if not rows:
        raise HTTPException(404, "No compaction with that id is on this thread.")
    return viewer.html(
        numbers.compaction(
            key=Ref(Kind.COMPACTION, source, compaction_id).key,
            citation=queries.citation(Fragment.COMPACTION_NUMBERS, keyed),
            node=reads.compaction_numbers(rows[0]),
        )
    )


@router.get(f"{nodes.NUMBERS_URL}/session/{{session_id}}/thread/{{source}}/{{kind}}/{{node_id}}")
def node_numbers(
    kind: str, session_id: str, source: str, node_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """The numbers behind a turn, an api call, or a tool call recorded on a thread."""
    if kind not in nodes.NUMBERED:
        raise HTTPException(404, "No numbers are served for that kind of node.")
    return counted(viewer, connection, Kind(kind), session_id, source, node_id)


@router.get(f"{nodes.NUMBERS_URL}/session/{{session_id}}/{Kind.RUN}/{{run_id}}")
def run_numbers(session_id: str, run_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """One agent run's numbers, read on the thread the run's id also names."""
    return counted(viewer, connection, Kind.RUN, session_id, run_id, run_id)


@router.get(f"{nodes.NUMBERS_URL}/session/{{session_id}}")
def session_numbers(session_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """A whole session's numbers: the main thread's window, and every thread's spend."""
    return counted(viewer, connection, Kind.SESSION, session_id, MAIN_SOURCE, session_id)
