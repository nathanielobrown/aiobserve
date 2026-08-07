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


def compute_cost(model: str, tokens: TokenUsage) -> float | None:
    """What one reply cost in USD, or None when the table does not price its model."""
    price = PRICES.get(model)
    if price is None:
        return None
    if tokens.cache_5m is None or tokens.cache_1h is None:
        write = tokens.cache_creation * _CACHE_WRITE_5M
    else:
        write = tokens.cache_5m * _CACHE_WRITE_5M + tokens.cache_1h * _CACHE_WRITE_1H
    return (
        tokens.input * price.input
        + tokens.output * price.output
        + tokens.cache_read * price.input * _CACHE_READ
        + write * price.input
    ) / _PER_MILLION
