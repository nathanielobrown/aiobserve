"""A child opened in place: the body a log row's View button swaps in, and the rows under it.

An expansion is a node's own body without its page — the same title, facts and details, read
from the same header queries — so a reader can open a child without losing the log they are
reading. What it does not open is another level: a count and a link stand in for one, except
where the level below opens nothing further (`docs/viewer.md`).
"""

from collections.abc import Callable
from typing import NamedTuple

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds, builders, nodes
from hyphae.view.browse import header_bound
from hyphae.view.citation import Ran, cited
from hyphae.view.columns import Shape
from hyphae.view.components import nav_tree, node_body
from hyphae.view.components.logs import Logged
from hyphae.view.components.nav_tree import NavTreeRow
from hyphae.view.deps import Viewer, ViewerDep
from hyphae.view.enrichment import described
from hyphae.view.knobs import Knobs, KnobsDep
from hyphae.view.nav_tree import Corpus, children, spread, windowed
from hyphae.view.nodes import Kind, Ref
from hyphae.view.store import (
    Fragment,
    Page,
    Row,
    open_store,
    page_rows,
)


class Listing(NamedTuple):
    """The level an expansion lists under the body instead of only counting.

    One kind has one: an api call's expansion lists the tools it called, because a tool call
    opens nothing further — the rows come with no opener on them, so the level a reader opens
    is still the last one. `size` is what the query calls its page size, and `build` turns one
    row into the node its row links to.
    """

    query: Fragment
    size: str
    build: Callable[[str, str, Row], nodes.Node]


class Body(NamedTuple):
    """How one kind answers an expansion: the header it reads, and what it says is under it.

    `children` is the column counting what the full view would have listed, and `shape` names
    those children the way the full view's log heading does. A kind with neither ends the NavTree.
    Where `listed` is None the count and a link stand in for the list.
    """

    page: Page
    # The binding the header query takes the node's id as.
    keyed: str
    build: Callable[[str, str, Row, str | None], nodes.Node]
    shape: Shape
    children: str | None
    # Whether a pass can have described this kind, and so whether the title may be the model's.
    described: bool
    listed: Listing | None


# Every kind a children log lists, except the run: a run's URL carries its id where the others
# carry a thread, so it has a mount of its own.
BODIES: dict[str, Body] = {
    Kind.TURN: Body(
        Page.TURN_HEADER,
        "turn_id",
        lambda session_id, source, row, text: builders.turn_node(
            session_id, source, row, nodes.NO_LEDGER, text
        ),
        Shape.CALLS,
        "api_calls",
        described=True,
        listed=None,
    ),
    Kind.CALL: Body(
        Page.CALL_HEADER,
        "api_call_id",
        lambda session_id, source, row, _: builders.call_node(
            session_id, source, row, nodes.NO_LEDGER
        ),
        Shape.TOOLS,
        "tool_calls",
        described=False,
        listed=Listing(
            Fragment.CALL_TOOLS,
            "page_tools",
            lambda session_id, source, row: builders.tool_node(
                session_id, source, row, nodes.NO_LEDGER
            ),
        ),
    ),
    Kind.TOOL: Body(
        Page.TOOL_HEADER,
        "tool_call_id",
        lambda session_id, source, row, _: builders.tool_node(
            session_id, source, row, nodes.NO_LEDGER
        ),
        Shape.NONE,
        None,
        described=False,
        listed=None,
    ),
}


router = APIRouter()


def expanded(
    viewer: Viewer,
    node: nodes.Node,
    row: Row,
    shape: Shape,
    children: int | None,
    marks: str,
    ran: Ran,
    under: list[Logged],
) -> Response:
    """One node's body alone, the way an expansion in someone else's log mounts it.

    The same component the full view's pane renders through, so the two cannot drift apart;
    where the page has the crumbs and prev/next, this has the way to the node's own page.
    `under` is the level the expansion lists, empty for every kind that stops at the count.
    `marks` is the knobs the page around the expansion was read under, which every link out
    of here carries on.
    """
    return viewer.html(
        node_body.expansion(
            node=node,
            facts=builders.node_facts(node, row),
            suffix=marks,
            shape=shape,
            children=children,
            rows=under,
            citations={named.value: cited(named, bound) for named, bound in ran},
        )
    )


@router.get(f"{nodes.BODY_URL}/session/{{session_id}}/thread/{{source}}/{{kind}}/{{node_id}}")
def thread_body(
    kind: str,
    session_id: str,
    source: str,
    node_id: str,
    viewer: ViewerDep,
    knobs: KnobsDep,
) -> Response:
    """The body of a turn, an api call, or a tool call, for an expansion in its parent.

    The knobs come along for the links this serves, not for what it reads: the mount
    carries the page's own query string so a reader who opens an expansion and clicks
    through it keeps the preset and the sizes they were reading under.
    """
    shaped = BODIES.get(kind)
    if shaped is None:
        raise HTTPException(404, "No expansion is served for that kind of node.")
    bound: dict[str, ParamValue] = {
        "session_id": session_id,
        "source": source,
        shaped.keyed: node_id,
        "head_chars": queries.HEADER_CHARS,
        # A body renders facts and no fat value, so the columns a pane would preview are
        # read at the width the title is cut from rather than at the reader's `?detail=`.
        "detail_chars": queries.HEADER_CHARS,
    }
    keyed: dict[str, ParamValue] = {"session_id": session_id, "source": source}
    # The level the expansion lists, where its kind lists one: the first page of it, at the
    # size the reader is reading logs under. Which page is not a question an expansion
    # asks — the way past the first is the link to the node's own page.
    level: dict[str, ParamValue] = {
        **keyed,
        shaped.keyed: node_id,
        "skipped": 0,
        "log_chars": queries.LOG_CHARS,
    }
    if shaped.listed is not None:
        level[shaped.listed.size] = knobs.log
    with open_store(viewer.db) as connection:
        rows = page_rows(connection, shaped.page, **bound)
        if not rows:
            raise HTTPException(404, "No node with that id is in this thread.")
        under = (
            [
                builders.logged(shaped.shape, shaped.listed.build(session_id, source, item), item)
                for item in page_rows(connection, shaped.listed.query, **level)
            ]
            if shaped.listed is not None
            else []
        )
        # The title is the model's words wherever a pass reached the node, exactly as the
        # log row that opened this expansion has it.
        describes = described(connection, session_id, source) if shaped.described else None
    told = describes.turns.get(node_id) if describes else None
    ran: Ran = [(shaped.page, bound)]
    if shaped.listed is not None:
        ran.append((shaped.listed.query, level))
    if describes is not None and describes.queried:
        ran.append((Page.ENRICHMENT, keyed))
    return expanded(
        viewer,
        shaped.build(session_id, source, rows[0], told.description if told else None),
        rows[0],
        shaped.shape,
        rows[0][shaped.children] if shaped.children else None,
        knobs.suffix,
        ran,
        under,
    )


@router.get(f"{nodes.BODY_URL}/session/{{session_id}}/{Kind.RUN}/{{run_id}}")
def run_body(
    session_id: str,
    run_id: str,
    viewer: ViewerDep,
    knobs: KnobsDep,
) -> Response:
    """One agent run's body. Its own mount: a run's URL carries its id where a thread goes."""
    bound: dict[str, ParamValue] = {
        "session_id": session_id,
        "run_id": run_id,
        "head_chars": queries.HEADER_CHARS,
        "detail_chars": queries.HEADER_CHARS,
    }
    keyed: dict[str, ParamValue] = {"session_id": session_id, "source": run_id}
    with open_store(viewer.db) as connection:
        rows = page_rows(connection, Page.RUN_HEADER, **bound)
        if not rows:
            raise HTTPException(404, "No agent run with that id is in this session.")
        # A run's id is the thread its own rows carry, so it is what the pass keyed on too.
        describes = described(connection, session_id, run_id)
    row = describes.runs.get(run_id)
    ran: Ran = [(Page.RUN_HEADER, bound)]
    if describes.queried:
        ran.append((Page.ENRICHMENT, keyed))
    return expanded(
        viewer,
        builders.run_node(session_id, rows[0], nodes.NO_LEDGER, row.description if row else None),
        rows[0],
        Shape.TURNS,
        rows[0]["turns"],
        knobs.suffix,
        ran,
        [],
    )


def spilled(
    viewer: Viewer, session_id: str, at: Ref, thread: str, depth: int, opened: str, knobs: Knobs
) -> Response:
    """The children one level's window left out: the rows a `+N more` row stands in for.

    The NavTree draws a window on a level and a tail row saying how many it left out; this
    serves the rest of that level, at the depth the NavTree had reached, so a click can stand
    them where the tail row stood. `opened` is the key of the child the open path descends
    through, which the window keeps wherever in the level it sits — the page sent it so
    that the two halves of one split agree, and this is the half that must not repeat it.

    `thread` is the reader's, not the level's: the enrichment is keyed by thread, so a page
    draws a turn of any other thread by its prompt, and a row served here has to read the
    way the page beside it would have drawn it.

    Unbounded on purpose: what comes back is a level less a window, so a node with ten
    thousand children answers with ten thousand rows.
    """
    if not 0 < depth <= bounds.DEPTH:
        raise HTTPException(400, f"A NavTree row sits between depth 1 and {bounds.DEPTH}.")
    keyed: dict[str, ParamValue] = {"session_id": session_id}
    with open_store(viewer.db) as connection:
        head = page_rows(connection, Page.SESSION_HEADER, **header_bound(session_id))
        if not head:
            raise HTTPException(404, "No session with that id is in this store.")
        runs = page_rows(connection, Page.RUNS, **keyed, chip_chars=queries.NAV_CHARS)
        corpus = Corpus(
            session_id=session_id,
            held=nodes.ledger(session_id, head[0]["cost_usd"] or 0, runs),
            runs=runs,
            described=described(connection, session_id, thread),
            source=thread,
        )
        level = children(connection, corpus, at, knobs.nav, opened or None)
    # Each row shut, and under it whatever a shut row stands: the runs it hides come back
    # with it, the way the page's own rows carry them. None of them is a step of the open
    # path — the cap keeps the child the path descends through inside the window, and this
    # fetch is what it left out.
    #
    # They arrive with no wrapper of their own: inside the list the tail row was in, each
    # inherits the NavTree's swap from `#nav-tree-rows` like every other row.
    return viewer.html(
        nav_tree.lines(
            rows=[
                row
                for node in windowed(level.nodes, knobs.kin, [opened]).cut
                for row in [
                    NavTreeRow(node, depth, selected=False, ancestor=False),
                    *spread(corpus, node, depth + 1),
                ]
            ],
            suffix=knobs.suffix,
            thread=thread,
        )
    )


@router.get(f"{nodes.KIN_URL}/session/{{session_id}}/thread/{{source}}/{{kind}}/{{node_id}}")
def node_kin(
    kind: str,
    session_id: str,
    source: str,
    node_id: str,
    thread: str,
    depth: int,
    viewer: ViewerDep,
    knobs: KnobsDep,
    opened: str = "",
) -> Response:
    """The rest of one level, under a node recorded on a thread.

    Neither `thread` nor `depth` has a default: these rows are going somewhere in a NavTree
    that already exists, and only the row that asked for them knows where they land and
    which thread's descriptions the NavTree around them was drawn by.
    """
    if kind not in set(Kind):
        raise HTTPException(404, "No level is served for that kind of node.")
    at = Ref(kind=Kind(kind), source=source, node_id=node_id)
    return spilled(viewer, session_id, at, thread, depth, opened, knobs)


@router.get(f"{nodes.KIN_URL}/session/{{session_id}}/{{kind}}/{{node_id}}")
def loose_kin(
    kind: str,
    session_id: str,
    node_id: str,
    thread: str,
    depth: int,
    viewer: ViewerDep,
    knobs: KnobsDep,
    opened: str = "",
) -> Response:
    """The rest of one level, under a node that carries no thread of its own.

    The session, an agent run, and the unattached bucket: their URLs have no room for the
    thread the node was recorded on, and the level does not need it — each builder reads
    the thread out of the node it hangs under.
    """
    if kind not in set(Kind):
        raise HTTPException(404, "No level is served for that kind of node.")
    at = Ref(kind=Kind(kind), source=None, node_id=node_id)
    return spilled(viewer, session_id, at, thread, depth, opened, knobs)
