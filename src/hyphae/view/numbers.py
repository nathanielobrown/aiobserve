"""Where a node's dollars went, by category, for the popover behind a tree row.

A row's badge prints one number, and one number cannot say whether a phase spent its money
reading a cache or writing one. The split is the four charges `extract/pricing.py` already
computes, summed across the models the node used.

Composed here rather than in SQL because the rates are Python's: a phase can mix models — a
session runs Haiku sub-agents under an Opus main thread — so one row of summed tokens times
one price would charge them all at whichever rate won. `view_numbers.sql` groups the node's
tokens by model instead, and each group is priced once.
"""

from collections.abc import Sequence

from hyphae.extract.pricing import CostSplit, TokenUsage, split_cost
from hyphae.view.store import Row


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
