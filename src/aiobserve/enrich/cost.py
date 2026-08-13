"""What an enrichment pass would cost, before it spends anything.

Arithmetic only: character counts the planner already holds, a chars-per-token ratio, and a
price table in this file. Nothing here reaches the network, so `--dry-run` works offline and
answers in the time it takes to render the prompts.

Every request pays for its own instructions and its own transport scaffold, which under the
CLI is simply true: each item is a fresh subprocess with nothing left to cache. The estimate
still reads low on one axis — a run may cascade further than the plan can see — so it is not
a bound; quote it as an estimate.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from aiobserve.enrich.prompts import Level, instructions


@dataclass(frozen=True)
class Rates:
    """What one model charges, in US dollars per million tokens."""

    input_usd: float
    output_usd: float


# Anthropic's list prices, read 2026-08-07. Only the models `--model` is likely to name: an
# unpriced one crashes rather than quoting zero for a pass nobody has costed.
PRICES = {
    "claude-haiku-4-5-20251001": Rates(input_usd=1.00, output_usd=5.00),
    "claude-sonnet-4-5-20250929": Rates(input_usd=3.00, output_usd=15.00),
}

# The low end of the corpus's measured 3.3-4 range, so the token count reads high. Prompts are
# dense with paths, ids and code fragments, which tokenize worse than prose.
CHARS_PER_TOKEN = 3.3

# One answer is four short fields, and the schema caps the description. Measured at 229 on a
# realistic render in the 2026-08-13 CLI probes, rounded up.
OUTPUT_TOKENS = 230

# What a `claude -p` call costs before it has read a word of the item: the CLI's own framing
# and the `--json-schema` payload. Measured at 684 with a tiny system prompt, so it counts no
# instructions — `estimate` sums those separately, and double-counting them would add ~1.5K
# tokens an item.
TRANSPORT_TOKENS = 700


@dataclass(frozen=True)
class Prompt:
    """One prompt a run would send: the content, and the level whose instructions it carries."""

    level: Level
    content: str


@dataclass(frozen=True)
class Estimate:
    """What a pass would cost, at the one price there is to pay."""

    items: int
    input_tokens: int
    output_tokens: int
    usd: float


def estimate(prompts: Sequence[Prompt], model: str) -> Estimate:
    """Price a set of prompts. Crashes on a model this build does not have a rate for."""
    if model not in PRICES:
        raise KeyError(f"no price recorded for {model} — add it to aiobserve.enrich.cost.PRICES")
    rates = PRICES[model]
    characters = sum(len(prompt.content) + len(instructions(prompt.level)) for prompt in prompts)
    input_tokens = int(characters / CHARS_PER_TOKEN) + len(prompts) * TRANSPORT_TOKENS
    output_tokens = len(prompts) * OUTPUT_TOKENS
    return Estimate(
        items=len(prompts),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd=(input_tokens * rates.input_usd + output_tokens * rates.output_usd) / 1_000_000,
    )
