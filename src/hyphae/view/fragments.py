"""The fetches a page makes for itself: one whole value, one popover, one enrichment line.

Nothing here serves a document. Every route answers one element that a page already showing
its head asks for — the rest of a cut value, or the numbers behind a NavTree row — and every
answer is a fragment of markup htmx swaps into the page that asked (`docs/viewer.md`).

The pane decides what to offer and mints the URL; a route here only reads the one column it
was asked for and renders it. A row that exists with nothing under it is a 404: nothing links
here unless there is a value to fetch.
"""

from collections.abc import Mapping

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.routing import BaseRoute

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.model import MAIN_SOURCE
from hyphae.view import highlight, nodes, numbers
from hyphae.view.enrichment import enriched
from hyphae.view.nodes import Kind, Ref
from hyphae.view.store import Fragment, Value, open_store, page_rows
from hyphae.view.templating import Viewer


def routes(viewer: Viewer) -> list[BaseRoute]:
    """Every fragment route, bound to one viewer, in the order `build_app` registers them."""
    router = APIRouter()

    def counted(
        request: Request, kind: Kind, session_id: str, source: str, node_id: str
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
            with open_store(viewer.db) as connection:
                rows = page_rows(connection, Fragment.TOOL_NUMBERS, **keyed)
            if not rows:
                raise HTTPException(404, "No tool call with that id is in this thread.")
            return viewer.templates.TemplateResponse(
                request,
                "fragments/numbers_tool.html",
                {
                    "key": Ref(kind, source, node_id).key,
                    "row": rows[0],
                    "citation": queries.citation(Fragment.TOOL_NUMBERS, keyed),
                },
            )
        bound: dict[str, ParamValue] = {
            "session_id": session_id,
            "source": source,
            "node_id": node_id,
            "kind": kind,
            "model_chars": queries.MODEL_CHARS,
        }
        with open_store(viewer.db) as connection:
            rows = page_rows(connection, Fragment.NUMBERS, **bound)
        # The query aggregates, so it answers a row for a node that is not there as readily as
        # for one that is — a node with no api calls under it is a real reading, and the
        # popover prints it as the dashes it is.
        whole = rows[0]["session_usd"]
        return viewer.templates.TemplateResponse(
            request,
            "fragments/numbers.html",
            {
                "key": Ref(kind, source, node_id).key,
                "row": rows[0],
                # The three lines between the window and the total, each priced and washed
                # here rather than in the template: what a charge is made of is arithmetic
                # (`view/numbers.py`), and the total under them takes the same ground.
                "charges": numbers.charges(rows[0], numbers.spend(rows[0]["spent"]), whole),
                "total_wash": numbers.wash(rows[0]["cost_usd"], whole),
                "citation": queries.citation(Fragment.NUMBERS, bound),
            },
        )

    @router.get(
        f"{nodes.NUMBERS_URL}/session/{{session_id}}/thread/{{source}}/{{kind}}/{{node_id}}"
    )
    def node_numbers(
        request: Request, kind: str, session_id: str, source: str, node_id: str
    ) -> Response:
        """The numbers behind a turn, an api call, or a tool call recorded on a thread."""
        if kind not in nodes.NUMBERED:
            raise HTTPException(404, "No numbers are served for that kind of node.")
        return counted(request, Kind(kind), session_id, source, node_id)

    @router.get(f"{nodes.NUMBERS_URL}/session/{{session_id}}/{Kind.RUN}/{{run_id}}")
    def run_numbers(request: Request, session_id: str, run_id: str) -> Response:
        """One agent run's numbers, read on the thread the run's id also names."""
        return counted(request, Kind.RUN, session_id, run_id, run_id)

    @router.get(f"{nodes.NUMBERS_URL}/session/{{session_id}}")
    def session_numbers(request: Request, session_id: str) -> Response:
        """A whole session's numbers: the main thread's window, and every thread's spend."""
        return counted(request, Kind.SESSION, session_id, MAIN_SOURCE, session_id)

    def whole(
        request: Request,
        value: Value,
        template: str,
        keyed: Mapping[str, ParamValue],
        column: str,
        detail: str | None,
        syntax: highlight.Syntax | None = None,
    ) -> Response:
        """One per-value fragment: the whole value, or a 404 when nothing is stored under it.

        `column` is where the query puts the value this fragment is for. A row can exist with
        nothing under it — a `Read` has no command, a turn no prompt — and that is a 404 and
        not an empty page: nothing on a pane links here unless there is a value to fetch, so a
        request for one that is not there is a URL somebody typed or a link somebody kept.

        `detail` is the name the pane files this value under, and the fragment replaces that
        whole section, so it carries the name out with it — the styling that tells an ask from
        an answer reads it. A fragment that is nobody's detail — the archived record — has none.

        `syntax` is what the route knows the value is written in. A value whose language is a
        property of the row instead — the file a `Read` returned — carries it in the query's
        own `result_type`, so the fetch is marked up the way its preview on the pane was.
        """
        with open_store(viewer.db) as connection:
            rows = page_rows(connection, value, **keyed)
        if not rows or rows[0][column] is None:
            raise HTTPException(404, "Nothing in this store is stored under that id.")
        # Under `value`, whatever the query called it: a fragment renders the one column it is
        # for, so a template that names it would be a second answer to which column that is.
        row = rows[0] | {"value": rows[0][column]}
        # The keys travel into the context as well: a fragment that links anywhere needs the
        # session and thread it was fetched for, and they are exactly what keyed it.
        return viewer.templates.TemplateResponse(
            request,
            f"fragments/{template}.html",
            dict(keyed)
            | {
                "row": row,
                "detail": detail,
                "citation": queries.citation(value, keyed),
                "syntax": syntax or highlight.by_suffix(row.get("result_type")),
            },
        )

    def enrichment_line(
        request: Request, value: Value, keyed: Mapping[str, ParamValue], field: str
    ) -> Response:
        """One whole line an enrichment pass wrote, or a 404 where no pass wrote one.

        A pass creates the enrichment tables rather than the exporter, so a store none has
        touched holds no such line — the same nothing a missing row is, and the same answer
        (`view/enrichment.py`). Asked per request and not at startup, because a pass can run
        against the store while the viewer is reading it.
        """
        with open_store(viewer.db) as connection:
            written = enriched(connection)
        if not written:
            raise HTTPException(404, "No enrichment pass has written to this store.")
        return whole(request, value, "enrichment_line", keyed, field, field)

    @router.get("/fragment/description/session/{session_id}/thread/{source}/turn/{turn_id}")
    def turn_description(request: Request, session_id: str, source: str, turn_id: str) -> Response:
        """The whole of what a pass said one turn did."""
        keyed = {"session_id": session_id, "source": source, "turn_id": turn_id}
        return enrichment_line(request, Value.TURN_SAID, keyed, "description")

    @router.get("/fragment/friction/session/{session_id}/thread/{source}/turn/{turn_id}")
    def turn_friction(request: Request, session_id: str, source: str, turn_id: str) -> Response:
        """The whole of the friction a pass saw in one turn."""
        keyed = {"session_id": session_id, "source": source, "turn_id": turn_id}
        return enrichment_line(request, Value.TURN_SAID, keyed, "friction")

    @router.get("/fragment/description/session/{session_id}/run/{run_id}")
    def run_description(request: Request, session_id: str, run_id: str) -> Response:
        """The whole of what a pass said one agent run did."""
        return enrichment_line(
            request, Value.RUN_SAID, {"session_id": session_id, "run_id": run_id}, "description"
        )

    @router.get("/fragment/friction/session/{session_id}/run/{run_id}")
    def run_friction(request: Request, session_id: str, run_id: str) -> Response:
        """The whole of the friction a pass saw in one agent run."""
        return enrichment_line(
            request, Value.RUN_SAID, {"session_id": session_id, "run_id": run_id}, "friction"
        )

    @router.get("/fragment/description/session/{session_id}")
    def session_description(request: Request, session_id: str) -> Response:
        """The whole of what a pass said one session did."""
        return enrichment_line(
            request, Value.SESSION_SAID, {"session_id": session_id}, "description"
        )

    @router.get("/fragment/friction/session/{session_id}")
    def session_friction(request: Request, session_id: str) -> Response:
        """The whole of the friction a pass saw in one session."""
        return enrichment_line(request, Value.SESSION_SAID, {"session_id": session_id}, "friction")

    @router.get("/fragment/text/session/{session_id}/thread/{source}/call/{api_call_id}")
    def call_text(request: Request, session_id: str, source: str, api_call_id: str) -> Response:
        """What one api call said, whole."""
        return whole(
            request,
            Value.CALL_TEXT,
            "value",
            {"session_id": session_id, "source": source, "api_call_id": api_call_id},
            "value",
            "text",
        )

    @router.get("/fragment/thinking/session/{session_id}/thread/{source}/call/{api_call_id}")
    def call_thinking(request: Request, session_id: str, source: str, api_call_id: str) -> Response:
        """What one api call thought, whole."""
        return whole(
            request,
            Value.CALL_THINKING,
            "value",
            {"session_id": session_id, "source": source, "api_call_id": api_call_id},
            "value",
            "thinking",
        )

    @router.get("/fragment/record/session/{session_id}/thread/{source}/line/{line_no}")
    def record_value(request: Request, session_id: str, source: str, line_no: int) -> Response:
        """One raw transcript record whole, as the browser's preview was cut from."""
        return whole(
            request,
            Value.RECORD,
            "record",
            {"session_id": session_id, "source": source, "line_no": line_no},
            # The record itself, which the store holds NOT NULL.
            "raw",
            # The line a node was read from, not one of the node's own values: nothing on a
            # pane files it under a name, and nothing swaps it into a detail.
            None,
        )

    @router.get("/fragment/input/session/{session_id}/thread/{source}/tool/{tool_call_id}")
    def tool_input(request: Request, session_id: str, source: str, tool_call_id: str) -> Response:
        """What one tool call was passed, whole."""
        return whole(
            request,
            Value.TOOL_INPUT,
            "raw",
            {"session_id": session_id, "source": source, "tool_call_id": tool_call_id},
            "value",
            "input",
        )

    @router.get("/fragment/result/session/{session_id}/thread/{source}/tool/{tool_call_id}")
    def tool_result(request: Request, session_id: str, source: str, tool_call_id: str) -> Response:
        """What one tool call returned, whole — the largest single fetch the viewer makes."""
        return whole(
            request,
            Value.TOOL_RESULT,
            "raw",
            {
                "session_id": session_id,
                "source": source,
                "tool_call_id": tool_call_id,
                # Not a cut of the answer, which rides whole: the bound on the file suffix
                # beside it, which is what says how the answer is marked up.
                "head_chars": queries.HEADER_CHARS,
            },
            "value",
            "result",
        )

    @router.get("/fragment/command/session/{session_id}/thread/{source}/tool/{tool_call_id}")
    def tool_command(request: Request, session_id: str, source: str, tool_call_id: str) -> Response:
        """What one `Bash` call ran, whole — read as the shell reads it."""
        return whole(
            request,
            Value.TOOL_COMMAND,
            "raw",
            {"session_id": session_id, "source": source, "tool_call_id": tool_call_id},
            "value",
            "command",
            highlight.Syntax.BASH,
        )

    @router.get("/fragment/prompt/session/{session_id}/thread/{source}/turn/{turn_id}")
    def turn_prompt(request: Request, session_id: str, source: str, turn_id: str) -> Response:
        """What one turn was asked, whole."""
        return whole(
            request,
            Value.TURN_PROMPT,
            "value",
            {"session_id": session_id, "source": source, "turn_id": turn_id},
            "value",
            "prompt",
        )

    @router.get("/fragment/args/session/{session_id}/thread/{source}/turn/{turn_id}")
    def turn_command_args(request: Request, session_id: str, source: str, turn_id: str) -> Response:
        """What followed the slash command one turn ran, whole."""
        return whole(
            request,
            Value.TURN_COMMAND_ARGS,
            "value",
            {"session_id": session_id, "source": source, "turn_id": turn_id},
            "value",
            "command_args",
        )

    @router.get("/fragment/brief/session/{session_id}/run/{run_id}")
    def run_brief(request: Request, session_id: str, run_id: str) -> Response:
        """The whole brief one agent run was given."""
        return whole(
            request,
            Value.RUN_BRIEF,
            "value",
            {"session_id": session_id, "run_id": run_id},
            "value",
            "brief",
        )

    @router.get("/fragment/prompt/session/{session_id}/run/{run_id}")
    def run_prompt(request: Request, session_id: str, run_id: str) -> Response:
        """The whole of what one agent run was asked, off the call that spawned it."""
        return whole(
            request,
            Value.RUN_PROMPT,
            "value",
            {"session_id": session_id, "run_id": run_id},
            "value",
            "prompt",
        )

    @router.get("/fragment/result/session/{session_id}/run/{run_id}")
    def run_result(request: Request, session_id: str, run_id: str) -> Response:
        """The whole of what one agent run sent back to the agent that spawned it."""
        return whole(
            request,
            Value.RUN_RESULT,
            "value",
            {"session_id": session_id, "run_id": run_id},
            "value",
            "result",
        )

    return router.routes
