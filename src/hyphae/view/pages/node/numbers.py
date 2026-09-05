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
from hyphae.view.pages.node.markup import numbers as markup
from hyphae.view.text.labels import label


class Numbers(NamedTuple):
    """One node's popover, read off the row `view_numbers` answered.

    The counts are the node's last answering call and come to the window above them; the
    dollars are every call it made. Filled in `view/pages/node/reads.py`, which is where the
    row stops being a bag of columns.
    """

    # What the popover prints of the window, which the component takes whole.
    window: markup.Window
    # The three counts the charge lines stand on, named for the store columns they sum.
    cache_read_tokens: int | None
    new_input_tokens: int | None
    output_tokens: int | None
    # What the node's own calls cost, what the runs below it cost, and what the whole session
    # cost — the last being the share every dollar here is washed against.
    cost_usd: float | None
    subtree_usd: float | None
    session_usd: float | None
    # One (model, usage) pair per group the query summed, for `spend` to price.
    spent: tuple[tuple[str, TokenUsage], ...]


def breakout(own: float | None, under: float | None, whole: float | None) -> markup.Breakout | None:
    """The subagent and total lines, or None where nothing hangs under the node.

    None rather than a pair of zeroes: a subagent charge of nothing and a total repeating the
    figure above it are two ways of saying what the node already said, and a reader who sees
    the lines on every row stops reading them. Rounded back to where a cost is stored so the
    sum of two four-decimal figures is one too (`view/nodes.py:COST_PLACES`).
    """
    if not under:
        return None
    total = round((own or 0) + under, COST_PLACES)
    return markup.Breakout(under, total, wash(under, whole), wash(total, whole))


def charges(read: Numbers, split: CostSplit | None, whole: float | None) -> list[markup.Charge]:
    """The three lines the popover prints between the window and the total.

    The counts come off the node's last answering call and add up to the window it left; the
    dollars are every call the node made and add up to the total under them. The cache a call
    wrote rides on the new-input line rather than on one of its own, because that is where its
    tokens are counted (`view_numbers.sql`) — a fourth dollar would leave a column of charges
    coming to nothing the reader can see.

    Each line is written out, and each takes the label registry's word for the column it
    counts: a line composed out of the column name would be a second vocabulary, and a word
    the registry cannot see is a word `tests/view/test_app__headers.py` cannot close over.
    """
    cache_read, new_input, output = (
        (split.cache_read, split.input + split.cache_write, split.output)
        if split is not None
        else (None, None, None)
    )
    return [
        markup.Charge(
            label=label("cache_read_tokens"),
            field="cache_read_tokens",
            cost_field="cache_read_usd",
            tokens=read.cache_read_tokens,
            cost=cache_read,
            wash=wash(cache_read, whole),
        ),
        markup.Charge(
            label=label("new_input_tokens"),
            field="new_input_tokens",
            cost_field="new_input_usd",
            tokens=read.new_input_tokens,
            cost=new_input,
            wash=wash(new_input, whole),
        ),
        markup.Charge(
            label=label("output_tokens"),
            field="output_tokens",
            cost_field="output_usd",
            tokens=read.output_tokens,
            cost=output,
            wash=wash(output, whole),
        ),
    ]


def wash(cost: float | None, whole: float | None) -> str:
    """The ground one dollar figure is drawn on: its share of what the session spent.

    The badge's own ladder (`view/nodes.py:meter`), so a number in a popover and the same
    number on the row behind it are washed at one depth. A session that spent nothing, or a
    dollar we have none of, takes no share and draws no ground.
    """
    return meter(cost / whole if cost and whole else None)


def spend(spent: Sequence[tuple[str, TokenUsage]]) -> CostSplit | None:
    """The four charges a node's tokens come to, or None where nothing in it could be priced.

    `spent` is `Numbers.spent` — one pair per model, with that model's tokens summed. A group
    our price table lacks is left out rather than counted as zero, which is the same nothing
    the badge above prints: the popover says how many calls went unpriced.
    """
    priced = [
        charged for model, usage in spent if (charged := split_cost(model, usage)) is not None
    ]
    if not priced:
        return None
    return CostSplit(*(sum(parts) for parts in zip(*priced, strict=True)))
