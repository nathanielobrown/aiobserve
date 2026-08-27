"""The one response every node page is: the tree with a path open, beside the pane reading it.

Eight URLs and one answer. What differs per kind is the `Reader` the route passes — its own
header, where it sits, and what its children log lists — and everything else a node page needs
is read here: the session, the corpus the tree is built from, the enrichment a pass wrote, and
the page of children under the selection (`docs/viewer.md`).
"""

from collections.abc import Callable
from dataclasses import replace
from math import ceil
from typing import NamedTuple

import duckdb
from fastapi import HTTPException, Request
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds, errors, nodes, tree, walk
from hyphae.view.citation import cited
from hyphae.view.columns import Shape
from hyphae.view.detail import Detail, enrichment_lines
from hyphae.view.enrichment import Descriptions, Enrichment, described
from hyphae.view.knobs import (
    checked,
    knobs,
    pager,
    preset_choices,
    skipped,
    viewed,
)
from hyphae.view.nodes import Kind, Ref
from hyphae.view.store import (
    Fragment,
    Listed,
    Page,
    Row,
    listed,
    open_store,
    page_rows,
)
from hyphae.view.templating import Viewer


class LogRow(NamedTuple):
    """One row of a pane's children log: the node it links to, beside the row it reads."""

    node: nodes.Node
    row: Row


class Seen(NamedTuple):
    """What one node's own reads answered, whatever kind of node it is.

    `trail` is what the node already knows about where it sits, innermost last — a call and a
    tool name their turn in their own header, so neither costs a read to place; `tree.ancestry`
    resolves the rest. A kind that reads no children answers `Shape.NONE` and no rows.
    """

    header: Row
    trail: list[Ref]
    shape: Shape
    rows: list[LogRow]
    # How many children the level holds in all, which is more than the page shows whenever
    # the level runs past `?log=`: the heading counts the level, and the pager divides it.
    total: int
    details: list[Detail]
    # The transcript line the node was read from, where the store archived one. Only a turn
    # has one: `turns.id` is a record's `uuid`, which is the store's own join down to the
    # bytes Claude Code wrote.
    record: int | None
    ran: tree.Ran


# What one node route does beyond the reads every node page makes: its own header, its trail,
# and its children log. The session header is passed in because every page reads it already.
Reader = Callable[[duckdb.DuckDBPyConnection, tree.Corpus, Row], Seen]


# How a pane names the node it is about, per kind, from the header its own route read. The
# tree built the row the pane stands on and cut its words where a tree row ends, which is a
# third of what a title has to spend (`nodes.Node.pane_title`) — so a page that took the tree's
# word for it would head a turn with the first line of the prompt and stop. The kinds absent
# are the ones no cut reaches: a session's node is read from its own header already, a
# compaction is named by its trigger, and a bucket is named by the viewer.
TITLED: dict[str, Callable[[str, str, Row, tree.Corpus], nodes.Node]] = {
    Kind.TURN: lambda session_id, source, row, corpus: nodes.turn_node(
        session_id, source, row, corpus.whole, corpus.turn_text(source, row["turn_id"])
    ),
    Kind.CALL: lambda session_id, source, row, corpus: nodes.call_node(
        session_id, source, row, corpus.whole
    ),
    Kind.TOOL: lambda session_id, source, row, _: nodes.tool_node(session_id, source, row),
    Kind.RUN: lambda session_id, _, row, corpus: nodes.run_node(
        session_id, row, corpus.whole, corpus.run_text(row["run_id"])
    ),
}


def described_node(descriptions: Descriptions, node: nodes.Node) -> Enrichment | None:
    """What an enrichment pass said about the node a pane is about, when it said anything.

    Three of the eight kinds are describable, and the pass keys turns by thread — which is the
    thread the page was read for, so the selection is always in reach of its own description.
    """
    if node.kind is Kind.SESSION:
        return descriptions.session
    if node.kind is Kind.TURN:
        return descriptions.turns.get(node.node_id)
    if node.kind is Kind.RUN:
        return descriptions.runs.get(node.node_id)
    return None


def header_bound(session_id: str) -> dict[str, ParamValue]:
    """What `Page.SESSION_HEADER` binds for one session, named once for every reader of it.

    A node page reads the row whole; `errors_page` reads it only to word a 404, but both
    have to bind the same params or a change to one silently stops answering for the other.
    """
    return {
        "session_id": session_id,
        "head_chars": queries.HEADER_CHARS,
        "item_chars": queries.HEADER_ITEM_CHARS,
        "head_items": queries.HEADER_ITEMS,
    }


def browse(
    viewer: Viewer,
    request: Request,
    session_id: str,
    source: str,
    nav: str,
    kin: int,
    log: int,
    detail: int,
    page: int,
    read: Reader,
) -> Response:
    """One node page: the tree with the path to the node open, beside the pane reading it.

    Every kind serves through here, because a node page is one response whatever the node
    is. `source` is the thread the enrichment is read for — `view_enrichment` keys turns by
    thread, and the tree spans the session, so a turn on another thread falls back to its
    prompt. What differs per kind is `read`, which answers the node's own header, where it
    sits, and what its children log lists, and 404s when the node is not in the store.
    """
    preset = viewed(nav)
    checked(kin, bounds.KIN.ceiling)
    checked(log, bounds.LOG.ceiling)
    checked(detail, bounds.DETAIL.ceiling)
    # A page number below the first is a bad ask like a size outside its bounds, and is
    # answered the same way: no level has such a page, so what is wrong is the number and
    # not the node the URL names. Asked before anything is read — it would otherwise bind
    # a negative offset. A number past a level's *last* page is a 404 further down: that
    # one is a question about the node, and only the level can answer it.
    if page < 1:
        raise HTTPException(400, "Ask for a children log page from one upwards.")
    bound = header_bound(session_id)
    # The session's runs are read once and printed twice: as a tree row at its width
    # and as a children log row at the log's. Cut to the wider of the two here, and cut
    # again at each — a row cut to the narrower would print a line already stopped.
    runs_bound = {"session_id": session_id, "chip_chars": queries.LOG_CHARS}
    with open_store(viewer.db) as connection:
        head = page_rows(connection, Page.SESSION_HEADER, **bound)
        if not head:
            raise HTTPException(404, "No session with that id is in this store.")
        # The session's runs whole, once: a run is placed by the call that spawned it
        # rather than by the thread it ran on, so any level of the tree may need any of
        # them, and both buckets are defined against the same set.
        corpus = tree.Corpus(
            session_id=session_id,
            whole=head[0]["cost_usd"] or 0,
            runs=page_rows(connection, Page.RUNS, **runs_bound),
            described=described(connection, session_id, source),
            source=source,
        )
        seen = read(connection, corpus, head[0])
        built = tree.tree(
            connection,
            corpus,
            nodes.session_node(head[0], corpus.described),
            tree.ancestry(corpus, seen.trail),
            preset,
            kin,
        )
        # What the reader reads before and after this node, off the same open path. Read
        # inside the request's own connection because it asks the store for levels the
        # tree did not open.
        walked = walk.neighbours(connection, corpus, built.chain)
        # The failures either side of this one, read only where the pane is standing on a
        # failure. A session-wide list is a query per page load and the step it answers
        # does not exist anywhere else, so every other node page asks the store nothing.
        failed = (
            errors.failures(connection, session_id)
            if built.chain[-1].kind is Kind.TOOL and built.chain[-1].is_error
            else None
        )
    # A page past the last of a level and a node that never had one are the same answer.
    # The first page is not: a node with no children still has its own facts to show.
    if page > 1 and not seen.rows:
        raise HTTPException(404, "This node's children do not run to that page.")
    selection = built.chain[-1]
    # Named from its own header rather than from the tree row it stands on (`TITLED`).
    # The words alone: what the node cost and what share of the session that is are the
    # tree's to work out, against the whole session rather than against one header.
    if (titled := TITLED.get(selection.kind)) is not None:
        selection = replace(selection, words=titled(session_id, source, seen.header, corpus).words)
    ran: tree.Ran = [
        (Page.SESSION_HEADER, bound),
        (Page.RUNS, runs_bound),
        *seen.ran,
        *built.ran,
        *walked.ran,
    ]
    # Only when the store held the tables to ask: a page cites what it ran, and over an
    # un-enriched store this query is not one of them.
    if corpus.described.queried:
        ran.append((Page.ENRICHMENT, {"session_id": session_id, "source": source}))
    # The same rule for the stepper's own read: a page cites what it ran, and most node
    # pages do not run this one.
    if failed is not None:
        ran.extend(failed.ran)
    marks = knobs(preset, kin, log, detail)
    about = described_node(corpus.described, selection)
    return viewer.templates.TemplateResponse(
        request,
        "node.html",
        {
            "selection": selection,
            "preset_choices": preset_choices(selection, preset, kin, log, detail),
            "chain": built.chain,
            "rows": built.rows,
            "header": seen.header,
            "enrichment": about,
            "lines": enrichment_lines(about, session_id, source),
            "details": seen.details,
            # The bytes behind the node: the thread's transcript, and — for a turn — the
            # one line it was read from.
            "source": source,
            "record": seen.record,
            # Where the reading order goes from here, in both directions.
            "previous": walked.previous,
            "next": walked.next,
            # And where the session failed: how many failures it holds, which is what the
            # way into the list says, beside the step to the next one where there is one.
            "session_tool_errors": head[0]["tool_errors"],
            "step": errors.stepped(failed.listed, selection) if failed else None,
            "shape": seen.shape,
            "log": seen.rows,
            # The level's own size, and where in it this page sits — the heading counts
            # the first, the control under the log reads the second.
            "total": seen.total,
            "pager": pager(selection.url, marks, page, ceil(seen.total / log)),
            # What every href on the page carries, so a click serves the URL it displays.
            "suffix": marks,
            "citations": {named.value: cited(named, bound) for named, bound in ran},
        },
    )


def turn_log(corpus: tree.Corpus, source: str, rows: list[Row]) -> list[LogRow]:
    """A page of one thread's timeline as a children log reads it: a row per turn."""
    return [
        LogRow(
            nodes.turn_node(
                corpus.session_id,
                source,
                row,
                corpus.whole,
                corpus.turn_text(source, row["turn_id"]),
            ),
            row,
        )
        for row in rows
    ]


def call_log(
    connection: duckdb.DuckDBPyConnection,
    corpus: tree.Corpus,
    source: str,
    turn_id: str | None,
    page: int,
    log: int,
) -> tuple[Listed, list[LogRow], tree.Ran]:
    """One page of the api calls under a turn — or, at `turn_id` NULL, under a bucket.

    One function for both because the two differ by that binding alone, which is the same
    rule the tree's level reads by: a call answering no turn sits in its thread's bucket.
    """
    bound: dict[str, ParamValue] = {
        "session_id": corpus.session_id,
        "source": source,
        "turn_id": turn_id,
        "skipped": skipped(page, log),
        "page_calls": log,
        "log_chars": queries.LOG_CHARS,
    }
    calls = listed(page_rows(connection, Fragment.TURN_CALLS, **bound), "matched_api_calls")
    rows = [
        LogRow(nodes.call_node(corpus.session_id, source, row, corpus.whole), row)
        for row in calls.rows
    ]
    return calls, rows, [(Fragment.TURN_CALLS, bound)]


def run_log(corpus: tree.Corpus, rows: list[Row]) -> list[LogRow]:
    """A list of agent runs as a children log reads it: a row per run."""
    return [
        LogRow(
            nodes.run_node(corpus.session_id, row, corpus.whole, corpus.run_text(row["run_id"])),
            row,
        )
        for row in rows
    ]
