"""What a node of a session is: its kind, its URL, and how a row becomes one.

Everything a session records is a node — the session, its turns, the runs it spawned, the api
calls those turns made, the tool calls those calls made, the compactions between them, and the
two buckets that hold what attaches to nothing. Each has a page of its own, so each needs one
label, one URL and one share of the spend, minted here and nowhere else: a tree row, a crumb
and a pane all read the same node.

`view/tree.py` builds the levels out of these; this module is the vocabulary they are built in.
"""

import math
from dataclasses import dataclass
from enum import StrEnum

from aiobserve.analyze import queries
from aiobserve.view.enrichment import Descriptions
from aiobserve.view.format import cut
from aiobserve.view.store import Row

# How a spend bar is drawn: the steps it has, and how many decades of share they cover. A
# session's cheapest turn and its dearest are three orders of magnitude apart, so the scale is
# logarithmic — a linear one draws every row but the largest as the same empty bar.
STEPS = 10
DECADES = 3

# What the two buckets are called. Neither is a row of the store: they stand for the rows that
# attach to nothing, so their labels say what is missing rather than naming a thing.
UNATTRIBUTED_LABEL = "calls under no turn of this thread"
UNATTACHED_LABEL = "runs attached to no turn"

# Where a hoisted run renders: after the api call that spawned it, wherever that call sits.
# The tie says which, because the run is a child of the turn rather than of the call.
TIE = "↖ from api call {index}"


class Shape(StrEnum):
    """What the pane's children log lists, which decides the macro a row renders through.

    A function of the selection's kind rather than a choice: a turn's children are api calls
    however the reader arrived at the turn, and a node with nothing under it has no log.
    """

    TURNS = "turns"
    CALLS = "calls"
    TOOLS = "tools"
    RUNS = "runs"
    NONE = "none"


class Kind(StrEnum):
    """What a node is: the segment its URL carries, and the query its children come from."""

    SESSION = "session"
    TURN = "turn"
    RUN = "run"
    CALL = "call"
    TOOL = "tool"
    COMPACTION = "compaction"
    # The two buckets. A run or a call the transcript could not attach still happened, so each
    # thread's unattached rows get a node of their own rather than being dropped or hidden
    # under something they did not come from.
    UNATTRIBUTED = "unattributed"
    UNATTACHED = "unattached"


class Preset(StrEnum):
    """Which children a level shows: the value `?nav=` carries, full when it carries none.

    A view of the same session rather than a different session — nothing is dropped, only
    folded away, and the path the reader is standing on renders whatever the preset hides.
    """

    FULL = "full"
    # The api calls folded away, so a turn's tool calls stand directly under it: what an agent
    # did, without a row per round trip to the model.
    NO_API = "noapi"
    # Agent runs only, each under the run that spawned it: the session as a spawn tree.
    AGENTS = "agents"


def meter(share: float | None) -> str:
    """The step class a share's spend bar is drawn with, or `s0` for nothing to draw."""
    if not share:
        return "s0"
    step = math.ceil(STEPS * (1 + math.log10(share) / DECADES))
    return f"s{min(max(step, 1), STEPS)}"


@dataclass(frozen=True)
class Ref:
    """A node named by identity alone: enough to find it, not enough to render it.

    What `ancestry()` resolves bottom-up, before any level has been read. The rendered node
    comes out of its parent's level, so a ref never carries a label or a cost.
    """

    kind: Kind
    # The thread it was recorded on, `main` or a run's id. None where the node is not on one:
    # the session, and the unattached bucket that spans every thread.
    source: str | None
    node_id: str

    @property
    def key(self) -> str:
        """`kind:id` — what a row is marked with, and how a test names the row it means."""
        return f"{self.kind}:{self.node_id}"


def run_url(session_id: str, run_id: str) -> str:
    """Where an agent run reads.

    Minted here rather than on the node alone: a `Task` tool call's body leads with the way to
    the run it spawned, and at that point the run is a column of the call's header rather than
    a node of its own.
    """
    return f"/session/{session_id}/run/{run_id}"


# Where a node's body alone is served from, written once: the routes in `view/app.py` answer
# what `Node.expansion` mints.
BODY_URL = "/fragment/body"


@dataclass(frozen=True)
class Node:
    """One node of a session, wherever it is read — a tree row, a crumb, or the pane itself."""

    kind: Kind
    session_id: str
    source: str | None
    node_id: str
    label: str
    # What it cost, and how many calls under it our price table could not price: a total
    # missing calls is not what the node cost, so the two always travel together. None where
    # the node has no spend of its own — a tool call's cost is the api call's.
    cost_usd: float | None
    unpriced_api_calls: int
    # Its share of what the session spent, or None when there is no share to draw.
    share: float | None
    # Where a hoisted run sits, or None for a node that is where it was recorded.
    tie: str | None = None
    # Whether the label is the model's words rather than the session's, which is what the
    # glyph beside it marks. Three kinds can be: a session, a turn and a run.
    enriched: bool = False

    @property
    def ref(self) -> Ref:
        """The identity half, for the path resolution that works in refs."""
        return Ref(self.kind, self.source, self.node_id)

    @property
    def key(self) -> str:
        """`kind:id` — what a row is marked with, and how a test names the row it means."""
        return f"{self.kind}:{self.node_id}"

    @property
    def url(self) -> str:
        """Where the node reads: the link a row carries, and the URL a click fetches."""
        if self.kind is Kind.SESSION:
            return f"/session/{self.session_id}"
        if self.kind is Kind.UNATTACHED:
            return f"/session/{self.session_id}/unattached"
        if self.kind is Kind.UNATTRIBUTED:
            return f"/session/{self.session_id}/unattributed/{self.source}"
        # A run's id is also the thread its own rows carry, so one key answers both questions
        # and the URL says it once.
        if self.kind is Kind.RUN:
            return run_url(self.session_id, self.node_id)
        return f"/session/{self.session_id}/{self.kind}/{self.source}/{self.node_id}"

    @property
    def expansion(self) -> str:
        """Where the node's body alone is fetched — the mount a log row opens it through.

        The same node, read without the page around it: an expansion is the body and a count of
        what is under it, so a reader can look inside a child without leaving the parent.
        """
        if self.kind is Kind.RUN:
            return f"{BODY_URL}/{Kind.RUN}/{self.session_id}/{self.node_id}"
        return f"{BODY_URL}/{self.kind}/{self.session_id}/{self.source}/{self.node_id}"

    @property
    def meter(self) -> str:
        """The step class this node's spend bar is drawn with, or nothing to draw."""
        return meter(self.share) if self.cost_usd is not None else ""


def _share(cost: float | None, whole: float) -> float | None:
    """A node's share of the session's spend, or None when there is no share to speak of."""
    return cost / whole if cost is not None and whole else None


def _cut(text: str | None) -> str:
    """A label at the width of a tree row, marked where the rest was left behind.

    Every column a label is composed from comes back one character past this width, so a
    label that fills the row is one the reader can tell was stopped (`view/format.py:cut`).
    """
    return cut(text or "", queries.NAV_CHARS)


def session_node(header: Row, described: Descriptions) -> Node:
    """The root of every tree: the session everything under it was recorded in."""
    cost = header["cost_usd"] or 0
    return Node(
        kind=Kind.SESSION,
        session_id=header["session_id"],
        source=None,
        node_id=header["session_id"],
        # What the enrichment pass said it was, else the title Claude Code gave it, else the
        # id — which is what a reader pasted to arrive here, so the row is never blank.
        label=_cut(
            (described.session.description if described.session else None)
            or header["title"]
            or header["session_id"]
        ),
        cost_usd=cost,
        unpriced_api_calls=header["unpriced_api_calls"],
        share=1.0 if cost else None,
        enriched=described.session is not None,
    )


def turn_node(session_id: str, source: str, row: Row, whole: float, described: str | None) -> Node:
    """One turn as a node, from a tree row, a digest row, or the turn's own header."""
    cost = row["cost_usd"]
    return Node(
        kind=Kind.TURN,
        session_id=session_id,
        source=source,
        node_id=row["turn_id"],
        label=_cut(described or _turn_label(row)),
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=_share(cost, whole),
        enriched=described is not None,
    )


def run_node(session_id: str, row: Row, whole: float, described: str | None) -> Node:
    """One agent run as a node, hoisted to wherever its spawning call sits."""
    cost = row["cost_usd"]
    return Node(
        kind=Kind.RUN,
        session_id=session_id,
        # A run's id is the source its own rows carry.
        source=row["run_id"],
        node_id=row["run_id"],
        # What the pass said it did, else the brief it was given, else the definition it ran.
        label=_cut(described or row["description"] or row["agent_type"]),
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=_share(cost, whole),
        tie=(
            TIE.format(index=row["spawn_call_index"])
            if row["spawn_call_index"] is not None
            else None
        ),
        enriched=described is not None,
    )


def call_node(session_id: str, source: str, row: Row, whole: float) -> Node:
    """One api call as a node: what it said, else the model that said it."""
    cost = row["cost_usd"] or 0
    return Node(
        kind=Kind.CALL,
        session_id=session_id,
        source=source,
        node_id=row["api_call_id"],
        # A call that answered with tool calls and no text has nothing to quote, so the
        # model names the row rather than leaving it blank.
        label=_cut(row.get("text_head") or row["model"]),
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=_share(cost, whole),
    )


def tool_node(session_id: str, source: str, row: Row) -> Node:
    """One tool call as a node. No cost of its own: what it took is the api call's."""
    return Node(
        kind=Kind.TOOL,
        session_id=session_id,
        source=source,
        node_id=row["tool_call_id"],
        # The name and the head of what it was asked, which is what tells two calls of one
        # tool apart in the width of a tree.
        label=_cut(f"{row['name']} {row.get('input_head') or ''}".strip()),
        cost_usd=None,
        unpriced_api_calls=0,
        share=None,
    )


def compaction_node(session_id: str, source: str, row: Row) -> Node:
    """One compaction as a node. A stop on the walk, and a node with no spend of its own."""
    return Node(
        kind=Kind.COMPACTION,
        session_id=session_id,
        source=source,
        node_id=row["compaction_id"],
        label=_cut(f"compaction · {row['trigger']}"),
        cost_usd=None,
        unpriced_api_calls=0,
        share=None,
    )


def unattributed_node(session_id: str, source: str, row: Row, whole: float) -> Node:
    """One thread's calls that answer no turn, as the digest's own cursorless row reads them."""
    cost = row["cost_usd"]
    return Node(
        kind=Kind.UNATTRIBUTED,
        session_id=session_id,
        source=source,
        node_id=source,
        label=UNATTRIBUTED_LABEL,
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=_share(cost, whole),
    )


def unattached_node(session_id: str, rows: list[Row], whole: float) -> Node:
    """The session's runs no spawning call resolved, gathered under one node.

    Spans every thread rather than sitting on one: what makes a run unattached is that nothing
    says which thread spawned it, so the bucket hangs off the session.
    """
    cost = sum(row["cost_usd"] for row in rows)
    return Node(
        kind=Kind.UNATTACHED,
        session_id=session_id,
        source=None,
        node_id=session_id,
        label=UNATTACHED_LABEL,
        cost_usd=cost,
        unpriced_api_calls=sum(row["unpriced_api_calls"] for row in rows),
        share=_share(cost, whole),
    )


def _turn_label(row: Row) -> str:
    """What to call a turn: the command it ran and what followed, else the prompt as typed.

    The prompt is last because a slash command's prompt is the `<command-…>` wrapper Claude
    Code put around it, which says nothing in the width of a tree.
    """
    if row["command_name"] is not None:
        return f"{row['command_name']} {row['command_args'] or ''}".strip()
    # The store declares a turn's prompt NOT NULL (`export/duckdb.py`), so this arm always
    # has something to say, even when what it says is the empty string.
    return row["prompt"]
