"""What a node of a session is: its kind, its URL, and what it spent.

Everything a session records is a node — the session, its turns, the runs it spawned, the api
calls those turns made, the tool calls those calls made, the compactions between them, and the
two buckets that hold what attaches to nothing. Each has a page of its own, so each needs one
title, one URL and one share of the spend, minted here and nowhere else: a NavTree row, a crumb
and a pane all read the same node.

`view/builders.py` turns a store row into one and `view/pages/node/nav_tree.py` builds the
levels out of them; this module is the vocabulary they are built in.
"""

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

from markupsafe import Markup

from hyphae.analyze import queries
from hyphae.view import bounds
from hyphae.view.store import Row
from hyphae.view.text import inline_markdown
from hyphae.view.text.format import cut

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
TALLY_CHARS = bounds.HEADER_WIDTHS.head_chars // 2


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
# component: one of them written into one surface is a node that looks like something else on
# that surface. Total over `Kind`, so a kind added without a mark is a `KeyError` on the first
# page that renders it rather than a row saying nothing.
GLYPHS: dict[Kind, str] = {
    Kind.SESSION: "❖",
    Kind.TURN: "❯",
    Kind.RUN: "◎",
    Kind.CALL: "⇄",
    Kind.TOOL: "⚒",
    Kind.COMPACTION: "⊟",
    Kind.UNATTRIBUTED: BUCKET_ICON,
    Kind.UNATTACHED: BUCKET_ICON,
}


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
    # The window that call's model answers in (`extract/pricing.py:MODELS`).
    window: int
    # The context the session opened on: what its first main-thread call sent before a word had
    # been said — the system prompt, the project's instructions, the tools' definitions. Only a
    # turn carries one, because only a turn's growth is worth reading against it.
    base: int | None = None


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


@dataclass(frozen=True)
class Ledger:
    """What one session spent, and what the runs under each of its nodes cost.

    Read once per page (`view/pages/node/routes/browse.py`) and handed to every node built for
    it: a badge's first half is what the node's own thread spent, its second that plus what
    `under` holds for the node, and both are washed against `whole`. A node absent from `under`
    has no run below it and draws one number.
    """

    # What the session spent, the basis every share on the page is a share of.
    whole: float
    # Run cost by the node it hangs under, keyed by the ref that node mints for itself.
    under: Mapping[Ref, float]

    def below(self, ref: Ref) -> float:
        """What the runs under one node cost, or nothing where none hang there."""
        return self.under.get(ref, 0)


# What a surface with no page to roll up hands a node: a crumb, a pane heading, an error list,
# a children log of tool calls. Each draws no badge, so a node built for one has an empty
# ledger rather than a share of something it never read.
NO_LEDGER = Ledger(whole=0, under={})


class Spend(NamedTuple):
    """The two halves of a node's cost badge, and the share each is washed at."""

    # What the node's own thread spent. None where it has no spend of its own — a tool call
    # that asked for nothing, a compaction.
    own: float | None
    # That plus every run under it, or None where no run hangs there: a second half repeating
    # the first says the same thing twice, and a reader reads that as two measurements.
    total: float | None
    share: float | None
    total_share: float | None


# Where a cost is rounded back to. Every one the store hands out is already at four decimals
# (`view_runs.sql`), so a sum or a difference of them is put back at the same place: a main
# thread that spent nothing is then exactly nothing rather than a float residue that draws a
# badge at the bottom step.
COST_PLACES = 4

# What a node with no spend of its own carries: a compaction, and a tool call that asked for
# nothing.
NO_SPEND = Spend(own=None, total=None, share=None, total_share=None)


def ledger(session_id: str, whole: float, runs: Sequence[Row]) -> Ledger:
    """Where each run's spend lands, walked once for a page. `view_runs.sql` holds the edges.

    A run's cost is charged to every node it hangs under: the ⚒ tool call that asked for it,
    the api call that made that tool call, the turn that call answers, each run above it, and
    the session. Which makes `total >= own` true by construction — a subtree's own is one of
    the numbers summed into it — and makes a level of parallel spawns sum past the call that
    made them, because one api call is the nearest priced thing to each of them (`docs/viewer.md`).

    The unattached bucket is left out on purpose: its own is already the sum of the loose runs
    it gathers, so a second total over the same rows would say the same thing twice.
    """
    held = {run["run_id"]: run for run in runs}
    under: dict[Ref, float] = {}
    for run in runs:
        own = run["cost_usd"]
        # Every run is under the session, whether or not the transcript placed it anywhere else.
        _charge(under, Ref(Kind.SESSION, None, session_id), own)
        at: Row | None = run
        climbed: set[str] = set()
        while at is not None and at["run_id"] not in climbed:
            climbed.add(at["run_id"])
            for ref in _asked(at):
                _charge(under, ref, own)
            # Up to the run this one hangs under: the parent the transcript names, else the
            # thread the spawning call was made from, which is a run's id wherever it is not
            # the session's own. A run already climbed ends the walk rather than looping.
            above = at["parent_agent_id"] if at["parent_agent_id"] in held else at["spawn_source"]
            at = held.get(above) if above is not None else None
            if at is not None:
                _charge(under, Ref(Kind.RUN, at["run_id"], at["run_id"]), own)
    return Ledger(whole=whole, under=under)


def _charge(under: dict[Ref, float], ref: Ref, cost: float) -> None:
    """Add one run's spend to what hangs under a node."""
    under[ref] = under.get(ref, 0) + cost


def _asked(run: Row) -> Iterator[Ref]:
    """The nodes that asked for one run, on the thread that asked: its ⚒ call, and up from there.

    Nothing where the spawning call resolved to nothing — an unattached run hangs off no tool
    call, no api call and no turn, which is the whole definition of one.
    """
    source = run["spawn_source"]
    if source is None:
        return
    yield Ref(Kind.TOOL, source, run["tool_use_id"])
    yield Ref(Kind.CALL, source, run["spawn_call_id"])
    # The turn that call answers, or — where it answers none — that thread's own bucket.
    turn_id = run["spawn_turn_id"]
    if turn_id is None:
        yield Ref(Kind.UNATTRIBUTED, source, source)
    else:
        yield Ref(Kind.TURN, source, turn_id)


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

# The kinds that have numbers to show: every kind that stands for a row of the store. Most are
# made of api calls; the tool call prints what it gave back instead, and the compaction the
# window it dropped. Only the two buckets are absent, because a bucket is a place rather than a
# node and there is no row under it to count.
NUMBERED = frozenset({Kind.SESSION, Kind.TURN, Kind.RUN, Kind.CALL, Kind.TOOL, Kind.COMPACTION})


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
    # reader can tell was stopped (`view/text/format.py:cut`).
    words: str
    # What it cost and what everything under it did, with the share each is washed at
    # (`_spend`), beside how many calls under it our price table could not price: a total
    # missing calls is not what the node cost, so the two always travel together.
    spend: Spend
    unpriced_api_calls: int
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
    # the column the NavTree's mark and the errors list (`view/failures.py`) are both read from.
    is_error: bool = False
    # What every cut of the title keeps, printed after the words: how many of each tool an api
    # call went on to invoke after the first (`call_node`). A surface cuts the words to its
    # width less this rather than cutting the title and losing the count — a title ending in
    # `+2(Ba…` would say the call did something else without saying what. Empty for every
    # other kind, whose title is all one piece.
    tail: str = ""
    # Where the node left the model's context window, or None for a node that ends on no
    # window at all: a tool call, a bucket, and any node whose model our table holds no
    # window for.
    context: Context | None = None
    # Whether the node's own thread ran its window out. Only ever True for a `Kind.RUN`: a
    # subagent that compacted did so unasked and unseen, and the bar is where the reader who
    # is wondering why its answer thinned out finds out (`docs/viewer.md`).
    maxed: bool = False
    # How often it happened, which is the same fact counted rather than flagged. Zero for every
    # other kind and for a run whose thread never compacted, and zero draws no badge at all: a
    # main-thread compaction is already a ⊟ row of the tree, so a run's row is the one place
    # the count is the only way to see it.
    compactions: int = 0

    @property
    def icon(self) -> str:
        """The mark saying what kind of node this is, for every surface that names it."""
        return GLYPHS[self.kind]

    @property
    def title(self) -> str:
        """What this node is called: the whole of it, before any surface cuts it.

        The concept every surface reads and none of them owns — lead, words and tail joined,
        in the markdown whoever wrote it typed. The five below are this title at the width of
        the surface reading it, and they are the only cuts of it: a page that composed its own
        would be a second answer to "what is this node called" (`docs/viewer-titles.md`).
        """
        return self._joined(self.lead, self.words) + self.tail

    def _joined(self, *parts: str) -> str:
        """The parts of a title a width is spent on, in reading order."""
        return self.separator.join(part for part in parts if part)

    def _at(self, chars: int, *parts: str, links: bool, source_cap: int) -> Markup:
        """`parts` rendered at `chars`, with the tail taken out of the width, not cut off it.

        The width is spent on what a reader sees: a description written in markdown is rendered
        rather than printed, so its syntax costs the surface nothing
        (`view/text/inline_markdown.py`). Which is why `source_cap` comes too — the width the query
        cut the words at is then the only thing that knows a line with room to spare was still
        stopped. `links` is the surface's own answer — see `pane_title`.
        """
        joined = self._joined(*parts)
        # The cap is the query's, and the query cut the words: whatever the join puts in front
        # of them was composed here and is whole, so it is room the cap has to allow for.
        return inline_markdown.cut(
            joined,
            chars - len(self.tail),
            links=links,
            source_cap=source_cap + len(joined) - len(self.words),
        ) + (self.tail)

    def _cut_at(self, chars: int) -> int:
        """The width the query behind the words cut them at, for a surface that reads `chars`.

        Every query composing a title cuts it to the width of the surface it was read for —
        except a description, which a pass wrote and `view_enrichment` cuts at a width of its
        own, wherever it is printed. A cap read off the surface instead would mark a described
        row that nothing had stopped.
        """
        return bounds.ENRICHMENT_WIDTHS.description_chars if self.enriched else chars

    def _plain(self, chars: int, *parts: str) -> str:
        """The same cut, as the text under it: for the surfaces that cannot carry markup."""
        return cut(inline_markdown.strip(self._joined(*parts)), chars - len(self.tail)) + self.tail

    @property
    def nav_tree_title(self) -> Markup:
        """The title at the width of a NavTree row, a walk control, or an errors-list row."""
        return self._at(
            bounds.NAV_TREE_WIDTHS.nav_chars,
            self.lead,
            self.words,
            links=False,
            source_cap=self._cut_at(bounds.NAV_TREE_WIDTHS.nav_chars),
        )

    @property
    def tab_title(self) -> str:
        """The title at a row's width with its markup gone, for the browser tab.

        A `<title>` element and an attribute both print an element as characters or act on it,
        and neither is what the words say — so the one surface with nowhere to put markup takes
        the text under it, cut at the same place the row beside it stops.
        """
        return self._plain(bounds.NAV_TREE_WIDTHS.nav_chars, self.lead, self.words)

    @property
    def crumb_title(self) -> Markup:
        """The title at the width of one crumb of the chain above the pane.

        The narrowest of the five, and the only one that is not the whole of what its surface
        could show: a chain is many nodes on one line, and the node the chain ends at is open
        underneath it. Cut here rather than in SQL — the query behind a crumb is the NavTree's,
        which fetched a row's width, and a second query for a narrower copy of the same string
        would be a page cost paid for nothing (`analyze/queries.py:CRUMB_CHARS`).
        """
        # Cut to a crumb's width against a NavTree row's cap, because the row's query is where
        # the words came from: what stopped them is that cut, not the narrower one here.
        return self._at(
            queries.CRUMB_CHARS,
            self.lead,
            self.words,
            links=False,
            source_cap=self._cut_at(bounds.NAV_TREE_WIDTHS.nav_chars),
        )

    @property
    def log_title(self) -> Markup:
        """The title at the width of a children log's own column.

        Wider than a NavTree row's because the log is a table and the column is the width of the
        pane: a description cut to a NavTree row's width is the reason a reader opens a node to
        find out what it was. The words alone — a log that leads a column with a word heads
        that column with it too (`lead`).
        """
        return self._at(
            bounds.LOG_WIDTHS.log_chars,
            self.words,
            links=False,
            source_cap=self._cut_at(bounds.LOG_WIDTHS.log_chars),
        )

    @property
    def pane_title(self) -> Markup:
        """The title at the head of the node's own pane, where nothing repeats it.

        The widest of the five, because a pane heads one node. A header query returns its
        strings at this width or wider — a tool header's input comes back at a preview's,
        because the same pane previews it — so a title is cut here and marked where the query
        left more behind. A pane names its node from the header it read rather than from the
        NavTree row it stands on (`view/pages/node/routes/browse.py:TITLED`) — the NavTree cuts
        at a row's width, which would head a turn with a third of the prompt it is about.

        The one surface a link in a title becomes an `<a>` on: every other one prints its
        title inside a link already, and an `<a>` inside an `<a>` is markup a browser undoes.
        """
        # The cap is a preview's rather than this width, because a header query returns its
        # strings at this width *or wider*: the pane cannot tell where such a string was cut,
        # so its own budget is the only cut it may mark.
        return self._at(
            bounds.HEADER_WIDTHS.head_chars,
            self.lead,
            self.words,
            links=True,
            source_cap=self._cut_at(bounds.DETAIL.ceiling),
        )

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
        kind `NUMBERED` leaves out, which is how the component knows not to wire a fetch.
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
    def cost_usd(self) -> float | None:
        """What this node's own thread spent — the first half of its badge, and every log's."""
        return self.spend.own

    @property
    def total_usd(self) -> float | None:
        """What its whole subtree spent, or None where no run hangs under it."""
        return self.spend.total

    @property
    def meter(self) -> str:
        """The step class the first half of this node's cost badge is drawn with."""
        return meter(self.spend.share) if self.spend.own is not None else ""

    @property
    def total_meter(self) -> str:
        """The step class the second half is drawn with, or nothing where there is no second.

        Its own share and not the first's: two halves of one badge are two shares of what the
        session spent, and drawing them at one depth would say a subtree cost what its root did.
        """
        return meter(self.spend.total_share) if self.spend.total is not None else ""

    @property
    def bar(self) -> str:
        """The classes this node's context bar is drawn with, or nothing where it has none.

        Up to three edges, each a prefix of the one outside it: where the window stood when the
        node ended, where the node's own share of it begins, and — on a turn — where the
        conversation begins, which is the context the session opened on. A session draws the
        fill alone: nothing ran before it for a band to measure against.

        The nesting is arithmetic here rather than paint order in the stylesheet, so a reader
        of the markup and a reader of the page see the same bar. A turn's base runs past its
        prior wherever the conversation is younger than the prompt it opened on — the session's
        first turn, every time — and holding the inner edge at the outer one is what draws that
        turn as the prompt it mostly is rather than as growth it mostly is not.

        `maxed` rides beside them, and alone where a run's own thread compacted without leaving
        a window to draw against: what it says is that the run ran out, which is a fact about
        the thread rather than a share of anything.
        """
        if self.context is None:
            return "maxed" if self.maxed else ""
        window = self.context.window
        fill = _bar_step(self.context.fill, window)
        drawn = [f"f{fill}"]
        base = min(_bar_step(self.context.base, window), fill) if self.context.base else None
        if self.context.added is not None:
            stood = max(self.context.fill - self.context.added, 0)
            prior = min(_bar_step(stood, window), fill)
            drawn.append(f"p{max(prior, base or 0)}")
        if base is not None:
            drawn.append(f"b{base}")
        if self.maxed:
            drawn.append("maxed")
        return " ".join(drawn)


def _bar_step(tokens: int, window: int) -> int:
    """Which step of the bar a token count lands on, held at the top where it runs past one.

    A request can ask for a larger window than the model's own, and the reply names the model
    either way (`extract/pricing.py:MODELS`) — so a fill above the window is drawn
    full rather than given a scale the table cannot see.
    """
    return min(round(tokens / window * BAR_STEPS), BAR_STEPS)
