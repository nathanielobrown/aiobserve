"""What an API call cost, from a price table we maintain.

This table is **ours**, not Claude Code's. A model missing from it is a gap in our list, not
a schema change to surface, so `compute_cost` returns `None` and the caller records the
model name with no cost — a queryable absence that can be filled in later. Crashing here
would kill a backfill the day a new model ships.

Prices are USD per million tokens, read from
<https://platform.claude.com/docs/en/about-claude/pricing> on **2026-08-07**. Nothing in the
test suite can check them against that page; re-read it and update the date when you touch
the table.

Two published modifiers are deliberately absent, because no recorded call uses them: fast
mode and US-only inference. Across the mycelia corpus's ~290,000 assistant records (scanned
2026-08-07), `usage.speed` is `"standard"` on half and absent on the rest, never `"fast"`,
and `usage.inference_geo` is `"not_available"` wherever it appears, never `"us"`. A call
carrying either would price low, so add the multiplier when one shows up.
"""

from typing import NamedTuple

# Claude Code's own placeholder replies — an interrupt notice, a cancelled request — are
# recorded as assistant records under this model name. They report zero tokens.
SYNTHETIC_MODEL = "<synthetic>"

# Cache multipliers on the base input rate, from the pricing page's caching section. The
# TTL is what separates them: a 1-hour cache pays off after two reads, a 5-minute one after
# one.
_CACHE_READ = 0.1
_CACHE_WRITE_5M = 1.25
_CACHE_WRITE_1H = 2.0

_PER_MILLION = 1_000_000


class ModelPrice(NamedTuple):
    """One model's base rates, USD per million tokens. Cache rates derive from `input`."""

    input: float
    output: float


class TokenUsage(NamedTuple):
    """The token counts one reply reported, as the pricing table charges them."""

    input: int
    output: int
    cache_read: int
    cache_creation: int
    # The cache-creation total split by TTL. Both None when the reply reported no split at
    # all, which prices the whole write at the 5-minute rate.
    cache_5m: int | None
    cache_1h: int | None


# Every model the mycelia corpus records, plus the placeholder. Keyed by the exact
# `message.model` string, since that is what the transcript carries.
PRICES: dict[str, ModelPrice] = {
    SYNTHETIC_MODEL: ModelPrice(input=0.0, output=0.0),
    "claude-fable-5": ModelPrice(input=10.0, output=50.0),
    "claude-opus-5": ModelPrice(input=5.0, output=25.0),
    "claude-opus-4-8": ModelPrice(input=5.0, output=25.0),
    "claude-opus-4-1-20250805": ModelPrice(input=15.0, output=75.0),
    # Introductory pricing, in effect through 2026-08-31; it rises to $3/$15 on
    # 2026-09-01. The table is flat, so a call recorded after that date will price low
    # until this line is split by effective date.
    "claude-sonnet-5": ModelPrice(input=2.0, output=10.0),
    "claude-sonnet-4-6": ModelPrice(input=3.0, output=15.0),
    "claude-haiku-4-5-20251001": ModelPrice(input=1.0, output=5.0),
}

# How many tokens of context each model answers in — the scale the viewer's context bar is
# drawn against (`docs/viewer.md`). Keyed like `PRICES`, by the exact `message.model` string,
# and every model that table prices but the placeholder: a `<synthetic>` record never went to
# a model, so it has a stated price of nothing and no window at all. A model absent here draws
# no bar, the way a model absent from `PRICES` shows no cost.
#
# 200,000 is the published window, and the corpus holds it up as the one in force: auto
# compaction fires at a median prompt of 167,385 tokens (`compactions.pre_tokens` where
# `trigger = 'auto'`, 1,225 of them in the canonical store on 2026-08-26), which is where a
# 200,000-token limit puts it, and 98.9% of the 159,907 non-synthetic calls recorded sit under
# it. The 1.06% that do not are 17 sessions out of 596: a larger window can be asked for, and
# the reply still names the base model — `claude-opus-5[1m]` is the alias a request carries,
# not a `message.model` this table could key on. So a call past its window reads full rather
# than getting a scale of its own, and the numbers are the popover's to print.
CONTEXT_WINDOWS: dict[str, int] = {
    "claude-fable-5": 200_000,
    "claude-opus-5": 200_000,
    "claude-opus-4-8": 200_000,
    "claude-opus-4-1-20250805": 200_000,
    "claude-sonnet-5": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


class CostSplit(NamedTuple):
    """What one model's tokens cost in USD, category by category.

    The four rates a reply is billed at, kept apart rather than summed: the viewer's popover
    prints them as a legend saying where a phase's dollars went (`docs/viewer.md`), and the
    total below is the only number the store keeps.
    """

    input: float
    output: float
    cache_read: float
    cache_write: float

    @property
    def total(self) -> float:
        """What the four come to, which is `compute_cost` to within a float's last digit."""
        return self.input + self.output + self.cache_read + self.cache_write


def _charges(model: str, tokens: TokenUsage) -> CostSplit | None:
    """The four charges in USD per million tokens, or None for a model the table lacks.

    The one place the rates are applied. Both callers below divide it down to dollars; what
    they differ in is whether they hand back the four or their sum.
    """
    price = PRICES.get(model)
    if price is None:
        return None
    if tokens.cache_5m is None or tokens.cache_1h is None:
        write = tokens.cache_creation * _CACHE_WRITE_5M
    else:
        write = tokens.cache_5m * _CACHE_WRITE_5M + tokens.cache_1h * _CACHE_WRITE_1H
    return CostSplit(
        input=tokens.input * price.input,
        output=tokens.output * price.output,
        cache_read=tokens.cache_read * price.input * _CACHE_READ,
        cache_write=write * price.input,
    )


def split_cost(model: str, tokens: TokenUsage) -> CostSplit | None:
    """What one reply cost by category, or None when the table does not price its model."""
    charges = _charges(model, tokens)
    return None if charges is None else CostSplit(*(charge / _PER_MILLION for charge in charges))


def compute_cost(model: str, tokens: TokenUsage) -> float | None:
    """What one reply cost in USD, or None when the table does not price its model.

    Summed before the division rather than after, which is what keeps every stored cost the
    number it was: four divisions rounded and then added is not always the same float.
    """
    charges = _charges(model, tokens)
    return None if charges is None else sum(charges) / _PER_MILLION
