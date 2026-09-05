"""What an enrichment pass would cost, before it spends anything.

Arithmetic only: character counts the planner already holds, a chars-per-token ratio, and the
rates in `hyphae.extract.pricing.MODELS`. Nothing here reaches the network, so `--dry-run`
works offline and answers in the time it takes to render the prompts.

Every request pays for its own instructions and its own transport scaffold, which under the
CLI is simply true: each item is a fresh subprocess with nothing left to cache. The estimate
still reads low on one axis — a run may cascade further than the plan can see — so it is not
a bound; quote it as an estimate.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from hyphae.enrich.items import Level
from hyphae.enrich.levels import instructions
from hyphae.extract.pricing import MODELS, PER_MILLION

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
    """Price a set of prompts. Crashes on a model the price table does not carry."""
    spec = MODELS[model]
    characters = sum(len(prompt.content) + len(instructions(prompt.level)) for prompt in prompts)
    input_tokens = int(characters / CHARS_PER_TOKEN) + len(prompts) * TRANSPORT_TOKENS
    output_tokens = len(prompts) * OUTPUT_TOKENS
    return Estimate(
        items=len(prompts),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd=(input_tokens * spec.input + output_tokens * spec.output) / PER_MILLION,
    )
