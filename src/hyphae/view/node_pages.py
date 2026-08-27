"""A URL for every node of a session: the session, a turn, a run, a call, a tool, and the rest.

Each route reads only what its own kind needs and hands the rest to `browse`, which is the page
they all serve. The two buckets are here as well — a thread's api calls that answer no turn,
and the session's runs no tool call spawned — because a bucket gets a page like anything else
(`CONTEXT.md`).
"""

import duckdb
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.routing import BaseRoute

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.model import MAIN_SOURCE
from hyphae.view import bounds, highlight, nav_tree, nodes
from hyphae.view.browse import (
    LogRow,
    Seen,
    browse,
    call_log,
    run_log,
    turn_log,
)
from hyphae.view.columns import Shape
from hyphae.view.detail import detail_of
from hyphae.view.knobs import (
    skipped,
    sliced,
)
from hyphae.view.nodes import Kind, Ref
from hyphae.view.store import (
    TURN_CURSOR,
    Fragment,
    Page,
    Row,
    listed,
    page_rows,
    window,
)
from hyphae.view.templating import Viewer


def routes(viewer: Viewer) -> list[BaseRoute]:
    """Every node page, bound to one viewer, in the order `build_app` registers them."""
    router = APIRouter()

    @router.get("/session/{session_id}")
    def session_page(
        request: Request,
        session_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """A session's own node: what it was, and its main thread as the NavTree's first level."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: nav_tree.Corpus, head: Row) -> Seen:
            offset = skipped(page, log)
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "log_chars": queries.LOG_CHARS,
            }
            turns = window(connection, Page.TIMELINE, TURN_CURSOR, offset, log, **bound)
            return Seen(
                header=head,
                trail=[Ref(Kind.SESSION, None, session_id)],
                shape=Shape.TURNS,
                rows=turn_log(corpus, MAIN_SOURCE, turns.rows),
                total=turns.total,
                details=[],
                record=None,
                ran=[(Page.TIMELINE, bound | {"offset": offset, "limit": log})],
            )

        return browse(viewer, request, session_id, MAIN_SOURCE, nav, kin, log, detail, page, read)

    @router.get("/session/{session_id}/thread/{source}/turn/{turn_id}")
    def turn_page(
        request: Request,
        session_id: str,
        source: str,
        turn_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """One turn: what it was asked, and the api calls that answered it."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: nav_tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "turn_id": turn_id,
                "head_chars": queries.HEADER_CHARS,
                "detail_chars": detail,
            }
            at = f"{nodes.thread_url(session_id, source)}/turn/{turn_id}"
            rows = page_rows(connection, Page.TURN_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No turn with that id is in this thread.")
            # Which line of the transcript each turn of this thread came from. Read for the
            # whole thread because that is what the query answers; two identifier columns per
            # turn, and the pane keeps the one row it is about.
            thread: dict[str, ParamValue] = {"session_id": session_id, "source": source}
            archived = {
                row["turn_id"]: row["line_no"]
                for row in page_rows(connection, Page.TURN_RECORDS, **thread)
            }
            calls, log_rows, ran = call_log(connection, corpus, source, turn_id, page, log)
            return Seen(
                header=rows[0],
                trail=[Ref(Kind.TURN, source, turn_id)],
                shape=Shape.CALLS,
                rows=log_rows,
                total=calls.total,
                details=[
                    item
                    for item in (
                        detail_of(
                            "prompt",
                            rows[0]["prompt"],
                            rows[0]["prompt_chars"],
                            f"/fragment/prompt{at}",
                            detail,
                            markdown=True,
                        ),
                        detail_of(
                            "command_args",
                            rows[0]["command_args"],
                            rows[0]["command_args_chars"],
                            f"/fragment/args{at}",
                            detail,
                            markdown=True,
                        ),
                    )
                    if item is not None
                ],
                record=archived.get(turn_id),
                ran=[(Page.TURN_HEADER, bound), *ran, (Page.TURN_RECORDS, thread)],
            )

        return browse(viewer, request, session_id, source, nav, kin, log, detail, page, read)

    @router.get("/session/{session_id}/run/{run_id}")
    def run_page(
        request: Request,
        session_id: str,
        run_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """One agent run: the brief it was given, and its own thread of turns.

        A run's id is also the `source` its rows carry, which is why the URL needs no thread
        segment and why the enrichment is read at the run.
        """

        def read(connection: duckdb.DuckDBPyConnection, corpus: nav_tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "run_id": run_id,
                "head_chars": queries.HEADER_CHARS,
                "detail_chars": detail,
            }
            rows = page_rows(connection, Page.RUN_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No run with that id is in this session.")
            offset = skipped(page, log)
            timeline: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": run_id,
                "log_chars": queries.LOG_CHARS,
            }
            turns = window(connection, Page.RUN_TIMELINE, TURN_CURSOR, offset, log, **timeline)
            return Seen(
                header=rows[0],
                trail=[Ref(Kind.RUN, run_id, run_id)],
                shape=Shape.TURNS,
                rows=turn_log(corpus, run_id, turns.rows),
                total=turns.total,
                details=[
                    item
                    for item in (
                        detail_of(
                            "brief",
                            rows[0]["brief"],
                            rows[0]["brief_chars"],
                            f"/fragment/brief{nodes.run_url(session_id, run_id)}",
                            detail,
                            markdown=True,
                        ),
                        # The ask and the answer, both markdown: one was written by whoever
                        # spawned the run and the other by the run itself.
                        detail_of(
                            "prompt",
                            rows[0]["prompt"],
                            rows[0]["prompt_chars"],
                            f"/fragment/prompt{nodes.run_url(session_id, run_id)}",
                            detail,
                            markdown=True,
                        ),
                        detail_of(
                            "result",
                            rows[0]["result"],
                            rows[0]["result_chars"],
                            f"/fragment/result{nodes.run_url(session_id, run_id)}",
                            detail,
                            markdown=True,
                        ),
                    )
                    if item is not None
                ],
                record=None,
                ran=[
                    (Page.RUN_HEADER, bound),
                    (Page.RUN_TIMELINE, timeline | {"offset": offset, "limit": log}),
                ],
            )

        return browse(viewer, request, session_id, run_id, nav, kin, log, detail, page, read)

    @router.get("/session/{session_id}/thread/{source}/call/{api_call_id}")
    def call_page(
        request: Request,
        session_id: str,
        source: str,
        api_call_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """One api call: what it answered, what it thought, and the tools it called."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: nav_tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "api_call_id": api_call_id,
                "head_chars": queries.HEADER_CHARS,
                "detail_chars": detail,
            }
            at = f"{nodes.thread_url(session_id, source)}/call/{api_call_id}"
            rows = page_rows(connection, Page.CALL_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No api call with that id is in this thread.")
            row = rows[0]
            tools: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "api_call_id": api_call_id,
                "skipped": skipped(page, log),
                "page_tools": log,
                "log_chars": queries.LOG_CHARS,
            }
            called = listed(
                page_rows(connection, Fragment.CALL_TOOLS, **tools), "matched_tool_calls"
            )
            return Seen(
                header=row,
                # The call's own header says which turn it answers, so its place costs no
                # read: a NULL turn puts it in its thread's unattributed bucket instead.
                trail=[nav_tree.home(source, row["turn_id"]), Ref(Kind.CALL, source, api_call_id)],
                shape=Shape.TOOLS,
                rows=[
                    LogRow(nodes.tool_node(session_id, source, item), item) for item in called.rows
                ],
                total=called.total,
                details=[
                    item
                    for item in (
                        detail_of(
                            "text",
                            row["text_head"],
                            row["text_chars"],
                            f"/fragment/text{at}",
                            detail,
                            markdown=True,
                        ),
                        detail_of(
                            "thinking",
                            row["thinking_head"],
                            row["thinking_chars"],
                            f"/fragment/thinking{at}",
                            detail,
                            markdown=True,
                        ),
                    )
                    if item is not None
                ],
                record=None,
                ran=[(Page.CALL_HEADER, bound), (Fragment.CALL_TOOLS, tools)],
            )

        return browse(viewer, request, session_id, source, nav, kin, log, detail, page, read)

    @router.get("/session/{session_id}/thread/{source}/tool/{tool_call_id}")
    def tool_page(
        request: Request,
        session_id: str,
        source: str,
        tool_call_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """One tool call: what it was passed, and what it returned. Nothing hangs under it."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: nav_tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "tool_call_id": tool_call_id,
                "head_chars": queries.HEADER_CHARS,
                "detail_chars": detail,
            }
            at = f"{nodes.thread_url(session_id, source)}/tool/{tool_call_id}"
            rows = page_rows(connection, Page.TOOL_HEADER, **bound)
            if not rows:
                raise HTTPException(404, "No tool call with that id is in this thread.")
            row = rows[0]
            return Seen(
                header=row,
                # The whole path down, out of one read: the call that made it, and the turn
                # that call answers — else that thread's bucket, by the same rule.
                trail=[
                    nav_tree.home(source, row["turn_id"]),
                    Ref(Kind.CALL, source, row["api_call_id"]),
                    Ref(Kind.TOOL, source, tool_call_id),
                ],
                shape=Shape.NONE,
                rows=[],
                total=0,
                details=[
                    item
                    for item in (
                        # The command first, where the call ran one: it is what the input is
                        # about, and the input below it is the record it was read out of.
                        detail_of(
                            "command",
                            row["command"],
                            row["command_chars"],
                            f"/fragment/command{at}",
                            detail,
                            highlight.Syntax.BASH,
                            markdown=False,
                        ),
                        detail_of(
                            "input",
                            row["input"],
                            row["input_chars"],
                            f"/fragment/input{at}",
                            detail,
                            markdown=False,
                        ),
                        detail_of(
                            "result",
                            row["result_head"],
                            row["result_chars"],
                            f"/fragment/result{at}",
                            detail,
                            highlight.by_suffix(row["result_type"]),
                            markdown=False,
                        ),
                    )
                    if item is not None
                ],
                record=None,
                ran=[(Page.TOOL_HEADER, bound)],
            )

        return browse(viewer, request, session_id, source, nav, kin, log, detail, page, read)

    @router.get("/session/{session_id}/thread/{source}/compaction/{compaction_id}")
    def compaction_page(
        request: Request,
        session_id: str,
        source: str,
        compaction_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """One compaction: where a thread's context was rewritten, and what that cost it.

        Read out of the thread's markers rather than by id — a compaction has no query of its
        own because the thread's whole set is what the NavTree beside it renders anyway.
        """

        def read(connection: duckdb.DuckDBPyConnection, corpus: nav_tree.Corpus, head: Row) -> Seen:
            bound: dict[str, ParamValue] = {
                "session_id": session_id,
                "source": source,
                "chip_chars": queries.HEADER_CHARS,
            }
            found = [
                row
                for row in page_rows(connection, Page.COMPACTIONS, **bound)
                if row["compaction_id"] == compaction_id
            ]
            if not found:
                raise HTTPException(404, "No compaction with that id is in this thread.")
            # Where it hangs is what the query already answered: under the turn it happened
            # during, else beside the turns of its thread. Seeded rather than resolved,
            # because a turn a timestamp lands in is a read this row has made.
            turn_id = found[0]["turn_id"]
            return Seen(
                header=found[0],
                trail=[
                    *([Ref(Kind.TURN, source, turn_id)] if turn_id is not None else []),
                    Ref(Kind.COMPACTION, source, compaction_id),
                ],
                shape=Shape.NONE,
                rows=[],
                total=0,
                details=[],
                record=None,
                ran=[(Page.COMPACTIONS, bound)],
            )

        return browse(viewer, request, session_id, source, nav, kin, log, detail, page, read)

    @router.get("/session/{session_id}/thread/{source}/unattributed")
    def unattributed_page(
        request: Request,
        session_id: str,
        source: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """One thread's api calls that answer no turn — a resume's calls answer turns that
        live in the session it resumed, and this is where they are read."""

        def read(connection: duckdb.DuckDBPyConnection, corpus: nav_tree.Corpus, head: Row) -> Seen:
            standing = nav_tree.unattributed(connection, corpus, source)
            if standing is None:
                raise HTTPException(404, "Every api call on this thread answers a turn.")
            calls, log_rows, ran = call_log(connection, corpus, source, None, page, log)
            return Seen(
                header=standing.row,
                trail=[Ref(Kind.UNATTRIBUTED, source, source)],
                shape=Shape.CALLS,
                rows=log_rows,
                total=calls.total,
                details=[],
                record=None,
                ran=[standing.ran, *ran],
            )

        return browse(viewer, request, session_id, source, nav, kin, log, detail, page, read)

    @router.get("/session/{session_id}/unattached")
    def unattached_page(
        request: Request,
        session_id: str,
        nav: str = nodes.Preset.FULL,
        kin: int = bounds.KIN.default,
        log: int = bounds.LOG.default,
        detail: int = bounds.DETAIL.default,
        page: int = 1,
    ) -> Response:
        """The session's agent runs no spawning call resolved.

        Session-scoped rather than per thread: what makes a run unattached is that nothing says
        which thread spawned it, so the bucket hangs off the session itself.
        """

        def read(connection: duckdb.DuckDBPyConnection, corpus: nav_tree.Corpus, head: Row) -> Seen:
            loose = [run for run in corpus.runs if run["spawn_source"] is None]
            if not loose:
                raise HTTPException(404, "Every agent run in this session was placed.")
            runs = sliced(loose, page, log)
            return Seen(
                header=head,
                trail=[Ref(Kind.UNATTACHED, None, session_id)],
                shape=Shape.RUNS,
                rows=run_log(corpus, runs.rows),
                total=runs.total,
                details=[],
                record=None,
                ran=[],
            )

        return browse(viewer, request, session_id, MAIN_SOURCE, nav, kin, log, detail, page, read)

    return router.routes
