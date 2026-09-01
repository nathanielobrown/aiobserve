"""The fetches a page makes for itself: one whole value, one popover, one enrichment line.

Nothing here serves a document. Every route answers one element that a page already showing
its head asks for — the rest of a cut value, or the numbers behind a NavTree row — and every
answer is a fragment of markup htmx swaps into the page that asked (`docs/viewer.md`).

The pane decides what to offer and mints the URL; a route here only reads the one column it
was asked for and renders it. A row that exists with nothing under it is a 404: nothing links
here unless there is a value to fetch.
"""

from collections.abc import Mapping

import duckdb
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.model import MAIN_SOURCE
from hyphae.view import nodes, reads
from hyphae.view.components import numbers, values
from hyphae.view.deps import Db, Viewer, ViewerDep
from hyphae.view.enrichment import enriched
from hyphae.view.nodes import Kind, Ref
from hyphae.view.numbers import breakout, charges, spend, wash
from hyphae.view.store import Fragment, Row, Value, page_rows
from hyphae.view.text import highlight

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
        keyed: dict[str, ParamValue] = {
            "session_id": session_id,
            "source": source,
            "tool_call_id": node_id,
            "item_chars": queries.HEADER_ITEM_CHARS,
            "head_items": queries.HEADER_ITEMS,
        }
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
    bound: dict[str, ParamValue] = {
        "session_id": session_id,
        "source": source,
        "node_id": node_id,
        "kind": kind,
        "model_chars": queries.MODEL_CHARS,
    }
    rows = page_rows(connection, Fragment.NUMBERS, **bound)
    # The query aggregates, so it answers a row for a node that is not there as readily as
    # for one that is — a node with no api calls under it is a real reading, and the
    # popover prints it as the dashes it is.
    whole = rows[0]["session_usd"]
    return viewer.html(
        numbers.popover(
            key=Ref(kind, source, node_id).key,
            citation=queries.citation(Fragment.NUMBERS, bound),
            node=reads.window_numbers(rows[0]),
            # The three lines between the window and the total, each priced and washed
            # here rather than in the component: what a charge is made of is arithmetic
            # (`view/numbers.py`), and the total under them takes the same ground.
            charges=charges(rows[0], spend(rows[0]["spent"]), whole),
            total_wash=wash(rows[0]["cost_usd"], whole),
            # And the two lines under them, where agent runs hang below this node: None
            # where none does, which is what keeps the breakout off every other row.
            breakout=breakout(rows[0]["cost_usd"], rows[0]["subtree_usd"], whole),
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
    keyed: dict[str, ParamValue] = {
        "session_id": session_id,
        "source": source,
        "compaction_id": compaction_id,
        "chip_chars": queries.CHIP_CHARS,
    }
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


def prose(
    viewer: Viewer,
    connection: duckdb.DuckDBPyConnection,
    value: Value,
    keyed: Mapping[str, ParamValue],
    column: str,
    detail: str,
) -> Response:
    """One whole value that was written as markdown, in the block its head was previewed in.

    `detail` is the name the pane files this value under, and the fragment replaces that
    whole section, so it carries the name out with it — the styling that tells an ask from
    an answer reads it.
    """
    row, citation = fetched(connection, value, keyed, column)
    return viewer.html(values.prose(node=values.Whole(row[column], detail, citation)))


def code(
    viewer: Viewer,
    connection: duckdb.DuckDBPyConnection,
    value: Value,
    keyed: Mapping[str, ParamValue],
    column: str,
    detail: str,
    syntax: highlight.Syntax | None = None,
) -> Response:
    """One whole value that was never prose, marked up in the syntax it was written in.

    `syntax` is what the route knows the value is written in. A value whose language is a
    property of the row instead — the file a `Read` returned — carries it in the query's
    own `result_type`, so the fetch is marked up the way its preview on the pane was, and
    falls back to JSON: a tool's arguments are JSON far more often than they are anything.
    """
    row, citation = fetched(connection, value, keyed, column)
    written = syntax or highlight.by_suffix(row.get("result_type")) or highlight.Syntax.JSON
    return viewer.html(
        values.code(node=values.Whole(row[column], detail, citation), syntax=written)
    )


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
    row, citation = fetched(connection, value, keyed, field)
    return viewer.html(values.enrichment_line(node=values.Whole(row[field], field, citation)))


@router.get("/fragment/description/session/{session_id}/thread/{source}/turn/{turn_id}")
def turn_description(
    session_id: str, source: str, turn_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """The whole of what a pass said one turn did."""
    keyed = {"session_id": session_id, "source": source, "turn_id": turn_id}
    return enrichment_line(viewer, connection, Value.TURN_SAID, keyed, "description")


@router.get("/fragment/friction/session/{session_id}/thread/{source}/turn/{turn_id}")
def turn_friction(
    session_id: str, source: str, turn_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """The whole of the friction a pass saw in one turn."""
    keyed = {"session_id": session_id, "source": source, "turn_id": turn_id}
    return enrichment_line(viewer, connection, Value.TURN_SAID, keyed, "friction")


@router.get("/fragment/description/session/{session_id}/run/{run_id}")
def run_description(session_id: str, run_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of what a pass said one agent run did."""
    keyed = {"session_id": session_id, "run_id": run_id}
    return enrichment_line(viewer, connection, Value.RUN_SAID, keyed, "description")


@router.get("/fragment/friction/session/{session_id}/run/{run_id}")
def run_friction(session_id: str, run_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of the friction a pass saw in one agent run."""
    keyed = {"session_id": session_id, "run_id": run_id}
    return enrichment_line(viewer, connection, Value.RUN_SAID, keyed, "friction")


@router.get("/fragment/description/session/{session_id}")
def session_description(session_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of what a pass said one session did."""
    return enrichment_line(
        viewer, connection, Value.SESSION_SAID, {"session_id": session_id}, "description"
    )


@router.get("/fragment/friction/session/{session_id}")
def session_friction(session_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of the friction a pass saw in one session."""
    return enrichment_line(
        viewer, connection, Value.SESSION_SAID, {"session_id": session_id}, "friction"
    )


@router.get("/fragment/text/session/{session_id}/thread/{source}/call/{api_call_id}")
def call_text(
    session_id: str, source: str, api_call_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """What one api call said, whole."""
    keyed = {"session_id": session_id, "source": source, "api_call_id": api_call_id}
    return prose(viewer, connection, Value.CALL_TEXT, keyed, "value", "text")


@router.get("/fragment/thinking/session/{session_id}/thread/{source}/call/{api_call_id}")
def call_thinking(
    session_id: str, source: str, api_call_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """What one api call thought, whole."""
    keyed = {"session_id": session_id, "source": source, "api_call_id": api_call_id}
    return prose(viewer, connection, Value.CALL_THINKING, keyed, "value", "thinking")


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


@router.get("/fragment/input/session/{session_id}/thread/{source}/tool/{tool_call_id}")
def tool_input(
    session_id: str, source: str, tool_call_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """What one tool call was passed, whole."""
    keyed = {"session_id": session_id, "source": source, "tool_call_id": tool_call_id}
    return code(viewer, connection, Value.TOOL_INPUT, keyed, "value", "input")


@router.get("/fragment/result/session/{session_id}/thread/{source}/tool/{tool_call_id}")
def tool_result(
    session_id: str, source: str, tool_call_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """What one tool call returned, whole — the largest single fetch the viewer makes."""
    keyed = {
        "session_id": session_id,
        "source": source,
        "tool_call_id": tool_call_id,
        # Not a cut of the answer, which rides whole: the bound on the file suffix beside
        # it, which is what says how the answer is marked up.
        "head_chars": queries.HEADER_CHARS,
    }
    return code(viewer, connection, Value.TOOL_RESULT, keyed, "value", "result")


@router.get("/fragment/command/session/{session_id}/thread/{source}/tool/{tool_call_id}")
def tool_command(
    session_id: str, source: str, tool_call_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """What one `Bash` call ran, whole — read as the shell reads it."""
    keyed = {"session_id": session_id, "source": source, "tool_call_id": tool_call_id}
    return code(
        viewer, connection, Value.TOOL_COMMAND, keyed, "value", "command", highlight.Syntax.BASH
    )


@router.get("/fragment/prompt/session/{session_id}/thread/{source}/turn/{turn_id}")
def turn_prompt(
    session_id: str, source: str, turn_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """What one turn was asked, whole."""
    keyed = {"session_id": session_id, "source": source, "turn_id": turn_id}
    return prose(viewer, connection, Value.TURN_PROMPT, keyed, "value", "prompt")


@router.get("/fragment/args/session/{session_id}/thread/{source}/turn/{turn_id}")
def turn_command_args(
    session_id: str, source: str, turn_id: str, viewer: ViewerDep, connection: Db
) -> Response:
    """What followed the slash command one turn ran, whole."""
    keyed = {"session_id": session_id, "source": source, "turn_id": turn_id}
    return prose(viewer, connection, Value.TURN_COMMAND_ARGS, keyed, "value", "command_args")


@router.get("/fragment/brief/session/{session_id}/run/{run_id}")
def run_brief(session_id: str, run_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole brief one agent run was given."""
    keyed = {"session_id": session_id, "run_id": run_id}
    return prose(viewer, connection, Value.RUN_BRIEF, keyed, "value", "brief")


@router.get("/fragment/prompt/session/{session_id}/run/{run_id}")
def run_prompt(session_id: str, run_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of what one agent run was asked, off the call that spawned it."""
    keyed = {"session_id": session_id, "run_id": run_id}
    return prose(viewer, connection, Value.RUN_PROMPT, keyed, "value", "prompt")


@router.get("/fragment/result/session/{session_id}/run/{run_id}")
def run_result(session_id: str, run_id: str, viewer: ViewerDep, connection: Db) -> Response:
    """The whole of what one agent run sent back to the agent that spawned it."""
    keyed = {"session_id": session_id, "run_id": run_id}
    return prose(viewer, connection, Value.RUN_RESULT, keyed, "value", "result")
