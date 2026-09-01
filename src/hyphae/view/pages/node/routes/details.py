"""The rest of one cut value: what a pane previewed at its width, fetched whole.

Every fat value on a node page is printed to its cut and marked, and the mark links here
(`docs/viewer-bounds.md`). A route reads the one column it was asked for and hands it back as
the block it was previewed in — prose where it was written as markdown, marked-up code where it
was not. A row that exists with nothing under it is a 404: nothing links here unless there is a
value to fetch.
"""

from collections.abc import Mapping

import duckdb
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view.deps import Db, Viewer, ViewerDep
from hyphae.view.pages.node import reads
from hyphae.view.pages.node.markup import values
from hyphae.view.store import Row, Value, page_rows
from hyphae.view.text import highlight

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
