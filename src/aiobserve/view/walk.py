"""Prev and next beside the pane: how a reader gets along a level and back out of it.

Neither control ever descends. Going down is what the tree is for — a click on a row opens
it — so the two controls read the level the reader is standing on: the next sibling, then the
next, and at the end of the level whatever follows the thing it sits inside. Prev is the same
level backwards, and from its first row the node that holds it. A step that leaves the level
says so, because a control that changed depth without warning would move the reader somewhere
they did not ask to be.

What the walk reads is the store, never the rendered rows. A `?kin=` cap cuts what is drawn
beside the pane; it cannot cut what comes next, because a reading order that shortened with
the tree would silently skip nodes. Each step is one level read — an ancestor's children —
on top of the chain the page already resolved.
"""

from collections.abc import Sequence
from typing import NamedTuple

import duckdb

from aiobserve.view.nodes import Node, Preset
from aiobserve.view.tree import Corpus, Ran, children


class _Reader:
    """One request's level reads, kept with the queries they ran so the page can cite them."""

    def __init__(self, connection: duckdb.DuckDBPyConnection, corpus: Corpus) -> None:
        self.connection = connection
        self.corpus = corpus
        self.ran: Ran = []

    def children(self, node: Node) -> list[Node]:
        """One node's children in full-preset tree order, the query line recorded.

        Always full: a filter preset is a view of the session, not a reading order, and three
        orders would be three reading needs to test for the one a reader has.
        """
        level = children(self.connection, self.corpus, node.ref, Preset.FULL, None)
        self.ran.extend(level.ran)
        return level.nodes

    def place(self, siblings: Sequence[Node], node: Node) -> int:
        """Where a node sits in its own level. Absent means the chain and the store disagree."""
        for index, sibling in enumerate(siblings):
            if sibling.key == node.key:
                return index
        raise ValueError(f"{node.key} is not in the level it was reached through")


class Step(NamedTuple):
    """Where one control goes, and whether taking it leaves the level the reader is on."""

    node: Node
    # True where the step lands at an ancestor's level rather than beside the selection, which
    # is what the control marks: the reader is coming out of the branch they were reading.
    climbed: bool


class Walk(NamedTuple):
    """What the two controls point at, and every query answering them."""

    previous: Step | None
    next: Step | None
    ran: Ran


def neighbours(
    connection: duckdb.DuckDBPyConnection, corpus: Corpus, chain: Sequence[Node]
) -> Walk:
    """The nodes read before and after `chain[-1]`, either None at an end of the session.

    `chain` is the open path the page already resolved, outermost first and ending at the
    selection — the walk climbs it rather than resolving ancestors again.
    """
    reader = _Reader(connection, corpus)
    return Walk(_previous(reader, chain), _next(reader, chain), reader.ran)


def _next(reader: _Reader, chain: Sequence[Node]) -> Step | None:
    """The node read next: the following sibling, else what follows the thing this sits inside.

    Climbing is what closes the walk — a node at the end of its level hands on to whatever
    follows its parent, and its parent's level can be at its end too, so the climb repeats
    until a level has something left or the session runs out.
    """
    for depth in range(len(chain) - 1, 0, -1):
        siblings = reader.children(chain[depth - 1])
        after = reader.place(siblings, chain[depth]) + 1
        if after < len(siblings):
            return Step(siblings[after], climbed=depth != len(chain) - 1)
    return None


def _previous(reader: _Reader, chain: Sequence[Node]) -> Step | None:
    """The node read before: the sibling ahead of this one, else the node that holds it.

    The parent rather than the parent's previous sibling, which is what next's climb would
    mirror: the first row of a level has to lead somewhere, and the thing it sits inside is
    where a reader who ran out of level wants to be.
    """
    if len(chain) == 1:
        return None
    siblings = reader.children(chain[-2])
    place = reader.place(siblings, chain[-1])
    if place == 0:
        return Step(chain[-2], climbed=True)
    return Step(siblings[place - 1], climbed=False)
