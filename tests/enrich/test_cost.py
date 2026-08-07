"""What a dry run quotes: arithmetic over rendered prompts and a price table in the code.

No estimate here asks anything what it charges. The rates are a dated constant a reader can
check against Anthropic's price page, and everything else is multiplication over character
counts the planner already holds — so a dry run costs nothing and works offline.
"""

import pytest

from aiobserve.enrich.cost import (
    BATCH_DISCOUNT,
    CHARS_PER_TOKEN,
    OUTPUT_TOKENS,
    PRICES,
    Estimate,
    Prompt,
    estimate,
)
from aiobserve.enrich.prompts import Level, instructions

MODEL = "claude-haiku-4-5-20251001"


def test_an_estimate_is_multiplication_a_reader_can_redo() -> None:
    """The quoted price is the rendered characters, the instructions, and the rate table."""
    # If a run would send two prompts — one turn and one session, of known length...
    prompts = [Prompt(Level.turn, "x" * 1_000), Prompt(Level.session, "y" * 3_000)]
    # ...then every number is derived from those lengths: each prompt pays for its content and
    # for the instructions its level carries, because nothing here counts caching...
    characters = 4_000 + len(instructions(Level.turn)) + len(instructions(Level.session))
    input_tokens = int(characters / CHARS_PER_TOKEN)
    output_tokens = 2 * OUTPUT_TOKENS
    rates = PRICES[MODEL]
    full = (input_tokens * rates.input_usd + output_tokens * rates.output_usd) / 1_000_000
    quote = estimate(prompts, MODEL)
    # ...the token counts are exact — the dollars are lifted here and checked below, because
    # float arithmetic is the one thing a whole-object compare cannot state...
    assert quote == Estimate(
        items=2,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        batched_usd=quote.batched_usd,
        unbatched_usd=quote.unbatched_usd,
    )
    # ...and the price is those tokens at the table's rate, halved on the batch path. That
    # discount is the reason production runs batched at all, so a dry run quotes both.
    assert quote.unbatched_usd == pytest.approx(full)
    assert quote.batched_usd == pytest.approx(full * BATCH_DISCOUNT)


def test_an_empty_plan_costs_nothing() -> None:
    """A run with nothing stale quotes zero rather than a floor price."""
    assert estimate([], MODEL) == Estimate(
        items=0, input_tokens=0, output_tokens=0, batched_usd=0.0, unbatched_usd=0.0
    )


def test_an_unpriced_model_crashes() -> None:
    """A model the table does not price refuses to be quoted, rather than quoting zero.

    Anthropic adds models faster than this file will be updated, and a silent zero would read
    as "this pass is free" on exactly the run whose price nobody knows.
    """
    with pytest.raises(KeyError, match="claude-opus-9"):
        estimate([Prompt(Level.turn, "x")], "claude-opus-9")
