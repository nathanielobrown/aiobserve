"""What a node of a session is: its kind, its URL, and how a row becomes one.

Everything a session records is a node — the session, its turns, the runs it spawned, the api
calls those turns made, the tool calls those calls made, the compactions between them, and the
two buckets that hold what attaches to nothing. Each has a page of its own, so each needs one
title, one URL and one share of the spend, minted here and nowhere else: a NavTree row, a crumb
and a pane all read the same node.

`view/nav_tree.py` builds the levels out of these; this module is the vocabulary they are built in.
"""

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

from hyphae.analyze import queries
from hyphae.view.columns import CALL_ICON, COLUMNS, RUN_ICON, TOOL_ICON, Shape
from hyphae.view.format import cut

# How a cost badge is drawn: the steps it has, and how many decades of share they cover. A
# session's cheapest turn and its dearest are three orders of magnitude apart, so the scale is
# logarithmic — a linear one would paint every row but the dearest alike.
STEPS = 10
DECADES = 3

# The context bar's ladder: how many steps a fill or a tip is drawn in, across the whole of the
# model's window. Linear, because what the bar says is fullness against a limit and a log scale
# draws a half-full window as a nearly full one. Twenty steps is five percent apiece — the
# finest a class per step can be without a rule per percent — so a node that added less than
# that draws no tip, and the tokens are the popover's to print.
BAR_STEPS = 20

# What the two buckets are called. Neither is a row of the store: they stand for the rows that
# attach to nothing, so their titles say what is missing rather than naming a thing.
UNATTRIBUTED_TITLE = "calls under no turn of this thread"
UNATTACHED_TITLE = "runs attached to no turn"

# What stands between a node's lead and its words (`Node.title`). A composed title is one whose
# halves neither identify alone — a tree of six `Explore` runs says nothing, and a brief without
# its agent type buries the one word a reader picks a run by. A lead that brackets itself says
# where it ends without a dash, and takes `Node.separator` to a space (`run_node`).
LEAD_SEPARATOR = " — "

# What marks an api call's title as the model's own words rather than a description of what
# the call did (`call_node`). The one glyph a reader can scan a thread for: it says this row is
# something the model said, whether or not the call went on to run tools.
SPEECH_MARK = "💭"

# The most of an api call's title the count of its tool calls may take (`call_node`). Half the
# narrowest width any surface cuts a title to, so the tool the reader picks the row out by
# keeps the other half: the canonical store's worst call names its tools in 93 characters, and
# the whole of a pane's heading is 100.
TALLY_CHARS = queries.HEADER_CHARS // 2


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


# The mark the two buckets share: each holds what the transcript could not attach, and a
# reader meets them as one kind of hole rather than two.
BUCKET_ICON = "∅"

# What each kind is marked with, wherever a page names a node of it — the NavTree row, the crumb,
# the pane's own heading, and the browser tab. Eight characters a reader learns once and then
# reads a NavTree by without reading a title, which is why the table is here rather than in a
# template: one of them written into one surface is a node that looks like something else on
# that surface. Total over `Kind`, so a kind added without a mark is a `KeyError` on the first
# page that renders it rather than a row saying nothing.
GLYPHS: dict[Kind, str] = {
    Kind.SESSION: "❖",
    Kind.TURN: "❯",
    Kind.RUN: RUN_ICON,
    Kind.CALL: CALL_ICON,
    Kind.TOOL: TOOL_ICON,
    Kind.COMPACTION: "⊟",
    Kind.UNATTRIBUTED: BUCKET_ICON,
    Kind.UNATTACHED: BUCKET_ICON,
}

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
        """What the control above the NavTree calls this preset, for a reader who never reads
        the URL."""
        return _PRESET_LABELS[self]


# Beside the enum rather than in it: a StrEnum member holds its URL value, and this is the
# other thing a preset is — the words on the control that turns it on.
_PRESET_LABELS = {
    Preset.FULL: "full",
    Preset.NO_API: "no api calls",
    Preset.AGENTS: "agents only",
}


def meter(share: float | None) -> str:
    """The step class a share's cost badge is drawn with, or `s0` for nothing to draw."""
    if not share:
        return "s0"
    step = math.ceil(STEPS * (1 + math.log10(share) / DECADES))
    return f"s{min(max(step, 1), STEPS)}"


class Context(NamedTuple):
    """Where a node left the model's context window, in tokens (`analyze/macros.py`)."""

    # Everything the node's last answering call was billed for: the cache it read, the cache
    # it wrote, what it sent, and what it said back.
    fill: int
    # How much of that fill the node itself put there. None where the question does not
    # arise — a session, which has nothing before it to have added to.
    added: int | None
    # The window that call's model answers in (`extract/pricing.py:CONTEXT_WINDOWS`).
    window: int


@dataclass(frozen=True)
class Ref:
    """A node named by identity alone: enough to find it, not enough to render it.

    What `ancestry()` resolves bottom-up, before any level has been read. The rendered node
    comes out of its parent's level, so a ref never carries a title or a cost.
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
# And where a row's numbers are served from, for the popover a reader opens by pointing at one
# (`Node.numbers`). Its own path under a prefix, like `expansion`: a popover is about one node.
NUMBERS_URL = "/fragment/numbers"

# The kinds that have numbers to show. Everything made of api calls, plus the tool call, which
# is made of none and prints what it gave back instead. A compaction and the two buckets are
# absent: a bucket is a place rather than a node, and a compaction's own record is its page.
NUMBERED = frozenset({Kind.SESSION, Kind.TURN, Kind.RUN, Kind.CALL, Kind.TOOL})


@dataclass(frozen=True)
class Node:
    """One node of a session, wherever it is read — a NavTree row, a crumb, or the pane itself."""

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
    # A word that goes before the words wherever nothing else says it: the agent type a run
    # ran under, the tool a call invoked. Empty for every kind whose words stand alone. It
    # leads `title` but not `log_title`, because a children log heads it in a column of its
    # own — a row that carried both would print the same word twice.
    lead: str = ""
    # What goes between the lead and the words. A dash by default: two halves of a composed
    # title need something saying which is which, and a lead already closed by a bracket does
    # not.
    separator: str = LEAD_SEPARATOR
    # Whether any of the words are the model's rather than the session's, which is what the
    # glyph beside the title marks. Three kinds can be: a session, a turn and a run.
    enriched: bool = False
    # Whether the tool call came back an error. Only ever True for a `Kind.TOOL` node: it is
    # the column the NavTree's mark and the errors list (`view/errors.py`) are both read from.
    is_error: bool = False
    # What every cut of the title keeps, printed after the words: how many of each tool an api
    # call went on to invoke after the first (`call_node`). A surface cuts the words to its
    # width less this rather than cutting the title and losing the count — a title ending in
    # `+2(Ba…` would say the call did something else without saying what. Empty for every
    # other kind, whose title is all one piece.
    tail: str = ""
    # Where the node left the model's context window, or None for a node that ends on no
    # window at all: a tool call, a compaction, a bucket, and any node whose model our table
    # holds no window for.
    context: Context | None = None

    @property
    def icon(self) -> str:
        """The mark saying what kind of node this is, for every surface that names it."""
        return GLYPHS[self.kind]

    @property
    def title(self) -> str:
        """What this node is called: the whole of it, before any surface cuts it.

        The concept every surface reads and none of them owns — lead, words and tail joined.
        The three below are this title at the width of the surface reading it, and they are
        the only cuts of it: a page that composed its own would be a second answer to "what is
        this node called" (`docs/viewer.md`).
        """
        return self._joined(self.lead, self.words) + self.tail

    def _joined(self, *parts: str) -> str:
        """The parts of a title a width is spent on, in reading order."""
        return self.separator.join(part for part in parts if part)

    def _at(self, chars: int, *parts: str) -> str:
        """`parts` at `chars`, with the tail taken out of the width rather than cut off it."""
        return cut(self._joined(*parts), chars - len(self.tail)) + self.tail

    @property
    def nav_tree_title(self) -> str:
        """The title at the width of a NavTree row, a walk control, or the browser tab."""
        return self._at(queries.NAV_CHARS, self.lead, self.words)

    @property
    def crumb_title(self) -> str:
        """The title at the width of one crumb of the chain above the pane.

        The narrowest of the four, and the only one that is not the whole of what its surface
        could show: a chain is many nodes on one line, and the node the chain ends at is open
        underneath it. Cut here rather than in SQL — the query behind a crumb is the NavTree's,
        which fetched a row's width, and a second query for a narrower copy of the same string
        would be a page cost paid for nothing (`analyze/queries.py:CRUMB_CHARS`).
        """
        return self._at(queries.CRUMB_CHARS, self.lead, self.words)

    @property
    def log_title(self) -> str:
        """The title at the width of a children log's own column.

        Wider than a NavTree row's because the log is a table and the column is the width of the
        pane: a description cut to a NavTree row's width is the reason a reader opens a node to
        find out what it was. The words alone — a log that leads a column with a word heads
        that column with it too (`lead`).
        """
        return self._at(queries.LOG_CHARS, self.words)

    @property
    def pane_title(self) -> str:
        """The title at the head of the node's own pane, where nothing repeats it.

        The widest of the four, because a pane heads one node. A header query returns its
        strings at this width or wider — a tool header's input comes back at a preview's,
        because the same pane previews it — so a title is cut here and marked where the query
        left more behind. A pane names its node from the header it read rather than from the
        NavTree row it stands on (`view/browse.py:TITLED`) — the NavTree cuts at a row's
        width, which would head a turn with a third of the prompt it is about.
        """
        return self._at(queries.HEADER_CHARS, self.lead, self.words)

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
    def numbers(self) -> str:
        """Where the numbers behind this row are fetched, or nothing when it has none.

        What the row's bar and its badge stand for, written out (`docs/viewer.md`). The node's
        own path under a prefix, like `expansion` — a popover reads one node — and empty for a
        kind `NUMBERED` leaves out, which is how the template knows not to wire a fetch.
        """
        return f"{NUMBERS_URL}{self.url}" if self.kind in NUMBERED else ""

    @property
    def rest(self) -> str:
        """Where the children this node's window left out are fetched, for a tail row to open.

        The same level the NavTree drew, past the window it drew — rows ready to stand where the
        tail row stands. Not the node's own path under a prefix like `expansion` is: what the
        route resolves is a level rather than a node, so a kind whose page needs no id — a
        session, either bucket — still names itself and its id here.
        """
        if self.source is None:
            return f"{KIN_URL}{session_url(self.session_id)}/{self.kind}/{self.node_id}"
        return f"{KIN_URL}{self.thread}/{self.kind}/{self.node_id}"

    @property
    def meter(self) -> str:
        """The step class this node's cost badge is drawn with, or nothing to draw."""
        return meter(self.share) if self.cost_usd is not None else ""

    @property
    def bar(self) -> str:
        """The classes this node's context bar is drawn with, or nothing where it has none.

        Two of them: how full the window was when the node ended, and how much of that the node
        itself put there. A session draws the fill alone — nothing ran before it for the tip to
        measure against.
        """
        if self.context is None:
            return ""
        drawn = f"f{_bar_step(self.context.fill, self.context.window)}"
        if self.context.added is None:
            return drawn
        return f"{drawn} t{_bar_step(self.context.added, self.context.window)}"


def _bar_step(tokens: int, window: int) -> int:
    """Which step of the bar a token count lands on, held at the top where it runs past one.

    A request can ask for a larger window than the model's own, and the reply names the model
    either way (`extract/pricing.py:CONTEXT_WINDOWS`) — so a fill above the window is drawn
    full rather than given a scale the table cannot see.
    """
    return min(round(tokens / window * BAR_STEPS), BAR_STEPS)
