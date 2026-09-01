"""Where a session failed: the list of every failed tool call, and the step between two.

The NavTree opens one path and the walk reads the session in order, so neither gets a reader to
the third failure of a run five spawns down without reading everything in front of it. This
module is the way that does not: one list of every `is_error` tool call the session holds,
whichever thread it ran on, in the order they happened — and, where the pane is already
standing on one of them, the failure read before it and the one after.

Session-wide for the reason the unattached bucket is: what a subagent failed at is what the
session failed at. The list is capped like the landing page (`view/bounds.py`) rather than
paged, and the stepper walks that same capped list — a failure past the cap is one neither
surface reaches, rather than one the stepper offers and the list denies.

`view/walk.py` is the neighbouring concern, and reads the same way: what is beside the pane,
answered from the store rather than from the rows the page happens to have drawn.
"""

import datetime as dt
from collections.abc import Sequence
from typing import NamedTuple

import duckdb

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds
from hyphae.view.builders import tool_node
from hyphae.view.citation import Ran
from hyphae.view.nodes import NO_LEDGER, Node
from hyphae.view.store import Page, dropped, page_rows


class Failure(NamedTuple):
    """One failed tool call as both surfaces read it: the node it leads to, and when it ran."""

    node: Node
    started_at: dt.datetime


class Failures(NamedTuple):
    """One session's failures in reading order, what the cap left, and the query behind them."""

    listed: list[Failure]
    # How many the session failed beyond what the cap admits, for the tail that says so.
    cut: int
    ran: Ran


def failures(connection: duckdb.DuckDBPyConnection, session_id: str) -> Failures:
    """Every failed tool call of one session, capped at what a page of them shows.

    Read at the NavTree's title width rather than a log's: a row here leads to a node, so it
    is named the way that node is named everywhere else it appears.
    """
    bound: dict[str, ParamValue] = {
        "session_id": session_id,
        "nav_chars": queries.NAV_CHARS,
        "errors": bounds.ERRORS.default,
    }
    rows = page_rows(connection, Page.SESSION_ERRORS, **bound)
    listed = [
        Failure(tool_node(session_id, row["source"], row, NO_LEDGER), row["started_at"])
        for row in rows
    ]
    # Counted by the query before its LIMIT bit, so a page that cut some says how many rather
    # than reading as the whole list.
    return Failures(listed, dropped(rows), [(Page.SESSION_ERRORS, bound)])


class Step(NamedTuple):
    """What the stepper points at: the failure read before this one, and the one after."""

    previous: Node | None
    next: Node | None


def stepped(listed: Sequence[Failure], node: Node) -> Step:
    """The failures either side of `node` in the list, either None at an end of it.

    Both None where the node is not in the list at all, which is what a failure past the cap
    is: the store holds it, and no surface here claims to reach what comes next.
    """
    place = next((at for at, failure in enumerate(listed) if failure.node.key == node.key), None)
    if place is None:
        return Step(None, None)
    return Step(
        listed[place - 1].node if place else None,
        listed[place + 1].node if place + 1 < len(listed) else None,
    )
