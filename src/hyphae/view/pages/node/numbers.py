"""Where a node's dollars went, by category, for the popover behind a NavTree row.

A row's badge prints one number, and one number cannot say whether a phase spent its money
reading a cache or writing one. The split is the four charges `extract/pricing.py` already
computes, summed across the models the node used.

Every line here is the node's own thread, whatever kind of node it is. What the agent runs
below it spent stands apart, in the two lines `breakout` composes: one reading under one label
is what a session popover used to be, and the runs are most of a spend when there are any.

Composed here rather than in SQL because the rates are Python's: a phase can mix models — a
session runs Haiku sub-agents under an Opus main thread — so one row of summed tokens times
one price would charge them all at whichever rate won. `view_numbers.sql` groups the node's
tokens by model instead, and each group is priced once.
"""

from collections.abc import Sequence
from typing import NamedTuple

from hyphae.extract.pricing import CostSplit, TokenUsage, split_cost
from hyphae.view.nodes import COST_PLACES, meter
from hyphae.view.store import Row


class Charge(NamedTuple):
    """One line of the popover: a count of tokens, and what those tokens cost."""

    # What the popover calls the line, and the fields its two numbers are labelled with.
    label: str
    field: str
    cost_field: str
    tokens: int | None
    # None where our price table holds no rate for the model the node answered on. The count
    # beside it still prints: a reading we have no price for is not a reading we do not have.
    cost: float | None
    # The step class the dollar's ground is drawn at — the badge's own, so the popover and the
    # row it opened from wash one number the same way.
    wash: str


# What the popover calls each line, beside the name its two numbers are labelled with: the
# store's own column less its `_tokens`, and that same name under a `cost_` for the dollar.
_LINES = (("cache read", "cached"), ("new input", "new_input"), ("output", "output"))


class Breakout(NamedTuple):
    """The two lines under the total, on a node with agent runs hanging below it.

    What the node's own thread spent is the column above; this is what the runs it asked for
    spent, and the two together. Absent where no run hangs there — see `breakout`.
    """

    # What the runs below the node spent, and what that is with the node's own added back.
    subagents: float
    total: float
    # The ground each is drawn on, the badge's own, as every other dollar here takes it.
    subagents_wash: str
    total_wash: str


def breakout(own: float | None, under: float | None, whole: float | None) -> Breakout | None:
    """The subagent and total lines, or None where nothing hangs under the node.

    None rather than a pair of zeroes: a subagent charge of nothing and a total repeating the
    figure above it are two ways of saying what the node already said, and a reader who sees
    the lines on every row stops reading them. Rounded back to where a cost is stored so the
    sum of two four-decimal figures is one too (`view/nodes.py:COST_PLACES`).
    """
    if not under:
        return None
    total = round((own or 0) + under, COST_PLACES)
    return Breakout(under, total, wash(under, whole), wash(total, whole))


def charges(row: Row, split: CostSplit | None, whole: float | None) -> list[Charge]:
    """The three lines the popover prints between the window and the total.

    The counts come off the node's last answering call and add up to the window it left; the
    dollars are every call the node made and add up to the total under them. The cache a call
    wrote rides on the new-input line rather than on one of its own, because that is where its
    tokens are counted (`view_numbers.sql`) — a fourth dollar would leave a column of charges
    coming to nothing the reader can see.
    """
    priced: tuple[float | None, ...] = (
        (split.cache_read, split.input + split.cache_write, split.output)
        if split is not None
        else (None, None, None)
    )
    return [
        Charge(label, field, f"cost_{field}", row[f"{field}_tokens"], cost, wash(cost, whole))
        for (label, field), cost in zip(_LINES, priced, strict=True)
    ]


def wash(cost: float | None, whole: float | None) -> str:
    """The ground one dollar figure is drawn on: its share of what the session spent.

    The badge's own ladder (`view/nodes.py:meter`), so a number in a popover and the same
    number on the row behind it are washed at one depth. A session that spent nothing, or a
    dollar we have none of, takes no share and draws no ground.
    """
    return meter(cost / whole if cost and whole else None)


def spend(groups: Sequence[Row]) -> CostSplit | None:
    """The four charges a node's tokens come to, or None where nothing in it could be priced.

    `groups` is `view_numbers.sql`'s `spent` — one member per model, with that model's tokens
    summed. A group our price table lacks is left out rather than counted as zero, which is
    the same nothing the badge above prints: the popover says how many calls went unpriced.
    """
    priced = [
        charged
        for group in groups
        if (charged := split_cost(group["model"], _usage(group))) is not None
    ]
    if not priced:
        return None
    return CostSplit(*(sum(parts) for parts in zip(*priced, strict=True)))


def _usage(group: Row) -> TokenUsage:
    """One model's summed tokens as the price table takes them.

    The TTL split is summed per call in SQL, under the same fallback `pricing.py` applies to
    one — a call that reported no split puts its whole write on the 5-minute rate — so the
    group carries a split whether or not every call in it did.
    """
    return TokenUsage(
        input=group["input_tokens"],
        output=group["output_tokens"],
        cache_read=group["cache_read_tokens"],
        cache_creation=group["cache_creation_tokens"],
        cache_5m=group["cache_5m_tokens"],
        cache_1h=group["cache_1h_tokens"],
    )
