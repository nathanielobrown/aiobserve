"""Prev and next beside the pane: the order a reader gets through a whole session in.

The tree opens one path, so the two controls are how a reader reaches everything the tree is
not showing. The order is depth-first over the session as the tree draws it — into a node's
children, then on to its next sibling, then out — and both buckets are steps like any other,
descended into rather than stepped over: the calls and runs they hold happened, and nothing
else on the page reaches them.

What the walk reads is the store, never the rendered rows. A `?kin=` cap cuts what is drawn
beside the pane; it cannot cut what comes next, because a reading order that shortened with
the sidebar would silently skip nodes. Each step is one level read — the selection's children,
or an ancestor's — on top of the chain the page already resolved.
"""

from collections.abc import Sequence

import duckdb

from aiobserve.view.nodes import Node, Preset
from aiobserve.view.tree import CHILDREN, Corpus, Ran


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
        level = CHILDREN[(node.kind, Preset.FULL)](self.connection, self.corpus, node)
        self.ran.extend(level.ran)
        return level.nodes

    def place(self, siblings: Sequence[Node], node: Node) -> int:
        """Where a node sits in its own level. Absent means the chain and the store disagree."""
        for index, sibling in enumerate(siblings):
            if sibling.key == node.key:
                return index
        raise ValueError(f"{node.key} is not in the level it was reached through")


class Walk:
    """What the two controls point at, and every query answering them."""

    def __init__(self, previous: Node | None, following: Node | None, ran: Ran) -> None:
        self.previous = previous
        self.following = following
        self.ran = ran


def neighbours(
    connection: duckdb.DuckDBPyConnection, corpus: Corpus, chain: Sequence[Node]
) -> Walk:
    """The nodes read before and after `chain[-1]`, either None at an end of the session.

    `chain` is the open path the page already resolved, outermost first and ending at the
    selection — the walk climbs it rather than resolving ancestors again.
    """
    reader = _Reader(connection, corpus)
    return Walk(_previous(reader, chain), _following(reader, chain), reader.ran)


def _following(reader: _Reader, chain: Sequence[Node]) -> Node | None:
    """The node read next: this node's first child, else the nearest following sibling.

    Climbing is what closes the walk — a node at the end of its level hands on to whatever
    follows the thing it sits inside, and a session whose last leaf is reached has nowhere
    left to climb to.
    """
    children = reader.children(chain[-1])
    if children:
        return children[0]
    for depth in range(len(chain) - 1, 0, -1):
        siblings = reader.children(chain[depth - 1])
        after = reader.place(siblings, chain[depth]) + 1
        if after < len(siblings):
            return siblings[after]
    return None


def _previous(reader: _Reader, chain: Sequence[Node]) -> Node | None:
    """The node read before: the last thing inside the previous sibling, else the parent.

    The mirror of `_following`'s descent — where next enters a subtree at its first node,
    prev leaves one at its last, so the two walk the same order in opposite directions.
    """
    if len(chain) == 1:
        return None
    parent = chain[-2]
    siblings = reader.children(parent)
    place = reader.place(siblings, chain[-1])
    if place == 0:
        return parent
    node = siblings[place - 1]
    while children := reader.children(node):
        node = children[-1]
    return node
