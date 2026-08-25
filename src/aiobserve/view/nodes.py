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
from typing import NamedTuple

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


class Column(NamedTuple):
    """One column of the pane's children log: what it prints, and how it heads itself.

    The word above the column is not here — it comes from `labels.label(field)`, the registry
    every header on every page reads, so a column and a pane's fact call the same store column
    the same thing. What is here is the icon beside that word and the class the cell carries:
    a number is read down a column, a time is read across a row, and neither survives being
    printed as prose.
    """

    field: str
    icon: str
    # `number` right-aligns and figures the digits, `when` keeps a time on one line, `what`
    # is the one wide column a row is identified by and links from. Plain text otherwise.
    css: str = ""


# What each shape of children log shows, column by column, in the order it shows them. Per
# shape because the columns are the shape's own: what tells two turns apart is not what tells
# two tool calls apart. Every row fills every column of its shape — a log that skipped a cell
# where the store held nothing would slide every later value under the wrong heading — and
# `tests/view/test_node.py` reads head and rows against this table.
#
# One column of each shape is `what`: the wide one carrying the node's own words and the link
# to its page. The last is the control that opens the child's body in place.
COLUMNS: dict[Shape, tuple[Column, ...]] = {
    Shape.TURNS: (
        Column("turn_index", "#", css="number"),
        Column("label", "☰", css="what"),
        Column("api_calls", "⇄", css="number"),
        Column("tool_calls", "⚒", css="number"),
        Column("cost_usd", "$", css="number"),
        Column("started_at", "◷", css="when"),
        Column("body", "⌄"),
    ),
    Shape.CALLS: (
        Column("call_index", "#", css="number"),
        # A call's own words are on its page: the row is named by the model that answered.
        Column("model", "◈", css="what"),
        Column("tool_calls", "⚒", css="number"),
        Column("text_chars", "¶", css="number"),
        Column("cost_usd", "$", css="number"),
        Column("started_at", "◷", css="when"),
        Column("body", "⌄"),
    ),
    Shape.TOOLS: (
        Column("tool_index", "#", css="number"),
        Column("name", "⚒"),
        Column("input_head", "⌨", css="what"),
        Column("is_error", "⚠"),
        Column("result_chars", "¶", css="number"),
        Column("started_at", "◷", css="when"),
        Column("body", "⌄"),
    ),
    Shape.RUNS: (
        Column("agent_type", "◎"),
        Column("label", "☰", css="what"),
        Column("tool_errors", "⚠", css="number"),
        Column("cost_usd", "$", css="number"),
        Column("started_at", "◷", css="when"),
        Column("body", "⌄"),
    ),
}


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


# Which shape of log lists a kind. For the one reader that knows a child and needs its
# parent's table: an expansion arrives as a row of the log it opens under, and that row spans
# the log's columns. A kind lists in one shape of log wherever it lists at all, which is what
# makes the width answerable from the child alone.
LISTED: dict[Kind, Shape] = {
    Kind.TURN: Shape.TURNS,
    Kind.CALL: Shape.CALLS,
    Kind.TOOL: Shape.TOOLS,
    Kind.RUN: Shape.RUNS,
}


def spanned(kind: str) -> int:
    """How many columns the log listing a node of `kind` has, for a row that spans them."""
    return len(COLUMNS[LISTED[Kind(kind)]])


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

    @property
    def label(self) -> str:
        """What the tree's switcher calls this fold, for a reader who never reads the URL."""
        return _PRESET_LABELS[self]


# Beside the enum rather than in it: a StrEnum member holds its URL value, and this is the
# other thing a preset is — the words on the control that turns it on.
_PRESET_LABELS = {
    Preset.FULL: "full",
    Preset.NO_API: "no api calls",
    Preset.AGENTS: "agents only",
}


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


# Every path the viewer serves is built from the three below, and they obey one rule: an id is
# never written next to another id — a word saying what kind of id it is always comes first
# (`docs/viewer.md`). `tests/view/test_app.py` holds every route to it.
def session_url(session_id: str) -> str:
    """Where a session reads, and the head of every path about something inside it."""
    return f"/session/{session_id}"


def thread_url(session_id: str, source: str) -> str:
    """Where one thread of a session begins: `main`, or the id of a run that ran on its own.

    Nothing reads at this path itself — a thread is a place things were recorded rather than a
    node — so what it mints is the segment a turn, a call, a tool call, a compaction, a bucket
    and a raw transcript all hang off.
    """
    return f"{session_url(session_id)}/thread/{source}"


def run_url(session_id: str, run_id: str) -> str:
    """Where an agent run reads.

    Minted here rather than on the node alone: a `Task` tool call's body leads with the way to
    the run it spawned, and at that point the run is a column of the call's header rather than
    a node of its own.
    """
    return f"{session_url(session_id)}/run/{run_id}"


# Where a node's body alone is served from, written once: the routes in `view/app.py` answer
# what `Node.expansion` mints. A fragment path is its node's path under a prefix, so the two
# say the same thing about where a node sits.
BODY_URL = "/fragment/body"
# And where the children one level's window left out are served from, which is what a tail
# row fetches (`Node.rest`).
KIN_URL = "/fragment/kin"


@dataclass(frozen=True)
class Node:
    """One node of a session, wherever it is read — a tree row, a crumb, or the pane itself."""

    kind: Kind
    session_id: str
    source: str | None
    node_id: str
    # What the node is called, before any surface cuts it: the model's description where a
    # pass wrote one, else what the session called it. Every query that composes it comes
    # back one character past the width it was cut to, so a name that fills a row is one the
    # reader can tell was stopped (`view/format.py:cut`).
    words: str
    # What it cost, and how many calls under it our price table could not price: a total
    # missing calls is not what the node cost, so the two always travel together. None where
    # the node has no spend of its own — a tool call's cost is the api call's.
    cost_usd: float | None
    unpriced_api_calls: int
    # Its share of what the session spent, or None when there is no share to draw.
    share: float | None
    # Whether the label is the model's words rather than the session's, which is what the
    # glyph beside it marks. Three kinds can be: a session, a turn and a run.
    enriched: bool = False
    # Whether the tool call came back an error. Only ever True for a `Kind.TOOL` node: it is
    # the column the tree's mark and the errors list (`view/errors.py`) are both read from.
    is_error: bool = False

    @property
    def label(self) -> str:
        """The node's name at the width of a tree row, a crumb, or a walk control."""
        return cut(self.words, queries.NAV_CHARS)

    @property
    def line(self) -> str:
        """The node's name at the width of a children log's own column.

        Wider than a label because the log is a table and the column is the width of the
        pane: a description cut to a tree row's 48 characters is the reason a reader opens
        a node to find out what it was.
        """
        return cut(self.words, queries.LOG_CHARS)

    @property
    def title(self) -> str:
        """The node's name at the head of its own pane, where nothing repeats it.

        The widest of the three, because a pane heads one node. A header query returns its
        strings at this width or wider — a tool header's input comes back at a preview's,
        because the same pane previews it — so a name is cut here and marked where the query
        left more behind. A pane names its node from the header it read rather than from the
        tree row it stands on (`view/app.py:TITLED`) — the tree cuts at a row's width, which
        would head a turn with a third of the prompt it is about.
        """
        return cut(self.words, queries.HEADER_CHARS)

    @property
    def ref(self) -> Ref:
        """The identity half, for the path resolution that works in refs."""
        return Ref(self.kind, self.source, self.node_id)

    @property
    def key(self) -> str:
        """`kind:id` — what a row is marked with, and how a test names the row it means."""
        return f"{self.kind}:{self.node_id}"

    @property
    def thread(self) -> str:
        """Where this node's thread begins, for the paths that hang off it.

        Only a node recorded on one has this. The session and the unattached bucket span every
        thread and say so by carrying none; every builder of any other kind reads the column,
        so a node here without one is a query that dropped it rather than a node with nowhere
        to sit.
        """
        if self.source is None:
            raise ValueError(f"a {self.kind} node was built with no thread: {self.node_id}")
        return thread_url(self.session_id, self.source)

    @property
    def url(self) -> str:
        """Where the node reads: the link a row carries, and the URL a click fetches."""
        if self.kind is Kind.SESSION:
            return session_url(self.session_id)
        # The unattached bucket hangs off the session, and both buckets are named by what
        # they hold rather than by an id of their own — so their paths end on the word.
        if self.kind is Kind.UNATTACHED:
            return f"{session_url(self.session_id)}/unattached"
        if self.kind is Kind.UNATTRIBUTED:
            return f"{self.thread}/unattributed"
        # A run's id is also the thread its own rows carry, so one key answers both questions
        # and the URL says it once.
        if self.kind is Kind.RUN:
            return run_url(self.session_id, self.node_id)
        return f"{self.thread}/{self.kind}/{self.node_id}"

    @property
    def expansion(self) -> str:
        """Where the node's body alone is fetched — the mount a log row opens it through.

        The same node, read without the page around it: an expansion is the body and a count of
        what is under it, so a reader can look inside a child without leaving the parent. The
        node's own path under a prefix, so the two never disagree about where the node sits.

        A kind with no body to serve has no route behind this — the two buckets, and a session
        — and nothing offers one: a log lists only the kinds `app.BODIES` covers.
        """
        return f"{BODY_URL}{self.url}"

    @property
    def rest(self) -> str:
        """Where the children this node's window left out are fetched, for a tail row to open.

        The same level the tree drew, past the window it drew — rows ready to stand where the
        tail row stands. Not the node's own path under a prefix like `expansion` is: what the
        route resolves is a level rather than a node, so a kind whose page needs no id — a
        session, either bucket — still names itself and its id here.
        """
        if self.source is None:
            return f"{KIN_URL}{session_url(self.session_id)}/{self.kind}/{self.node_id}"
        return f"{KIN_URL}{self.thread}/{self.kind}/{self.node_id}"

    @property
    def meter(self) -> str:
        """The step class this node's spend bar is drawn with, or nothing to draw."""
        return meter(self.share) if self.cost_usd is not None else ""


def _share(cost: float | None, whole: float) -> float | None:
    """A node's share of the session's spend, or None when there is no share to speak of."""
    return cost / whole if cost is not None and whole else None


def _words(text: str | None) -> str:
    """What a node is called, whatever the query that composed it left NULL."""
    return text or ""


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
        words=_words(
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
        words=_words(described or _turn_label(row)),
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
        words=_words(described or row["description"] or row["agent_type"]),
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=_share(cost, whole),
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
        words=_words(row.get("text_head") or row["model"]),
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
        words=_words(f"{row['name']} {row.get('input_head') or ''}".strip()),
        cost_usd=None,
        unpriced_api_calls=0,
        share=None,
        # Every query a tool node is built from selects it, and the column is NOT NULL, so a
        # row arriving without it is a query that forgot rather than a call that may have
        # failed (`export/duckdb.py`).
        is_error=row["is_error"],
    )


def compaction_node(session_id: str, source: str, row: Row) -> Node:
    """One compaction as a node. A stop on the walk, and a node with no spend of its own."""
    return Node(
        kind=Kind.COMPACTION,
        session_id=session_id,
        source=source,
        node_id=row["compaction_id"],
        words=_words(f"compaction · {row['trigger']}"),
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
        words=UNATTRIBUTED_LABEL,
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
        words=UNATTACHED_LABEL,
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
