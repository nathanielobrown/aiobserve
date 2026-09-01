"""What a dry run quotes: arithmetic over rendered prompts and a price table in the code.

No estimate here asks anything what it charges. The rates are a dated constant a reader can
check against Anthropic's price page, and everything else is multiplication over character
counts the planner already holds — so a dry run costs nothing and works offline.
"""

import pytest

from hyphae.enrich.cost import (
    CHARS_PER_TOKEN,
    OUTPUT_TOKENS,
    PRICES,
    TRANSPORT_TOKENS,
    Estimate,
    Prompt,
    estimate,
)
from hyphae.enrich.items import Level
from hyphae.enrich.levels import instructions

MODEL = "claude-haiku-4-5-20251001"


def test_an_estimate_is_multiplication_a_reader_can_redo() -> None:
    """The quoted price is the rendered characters, the instructions, the scaffold, and rates."""
    # If a run would send two prompts — one turn and one session, of known length...
    prompts = [Prompt(Level.turn, "x" * 1_000), Prompt(Level.session, "y" * 3_000)]
    # ...then every number is derived from those lengths: each prompt pays for its content and
    # for the instructions its level carries, since a fresh subprocess caches nothing...
    characters = 4_000 + len(instructions(Level.turn)) + len(instructions(Level.session))
    # ...plus the transport scaffold, which is a flat count per item and holds no instructions
    # of its own — priced with a tiny system prompt for exactly that reason, so summing it
    # here on top of the characters above counts nothing twice...
    input_tokens = int(characters / CHARS_PER_TOKEN) + 2 * TRANSPORT_TOKENS
    output_tokens = 2 * OUTPUT_TOKENS
    rates = PRICES[MODEL]
    full = (input_tokens * rates.input_usd + output_tokens * rates.output_usd) / 1_000_000
    quote = estimate(prompts, MODEL)
    # ...the token counts are exact — the dollars are lifted here and checked below, because
    # float arithmetic is the one thing a whole-object compare cannot state...
    assert quote == Estimate(
        items=2, input_tokens=input_tokens, output_tokens=output_tokens, usd=quote.usd
    )
    # ...and the price is those tokens at the table's rate. There is one price now: every item
    # is a `claude -p` call at list rate, and the batch discount went with the API.
    assert quote.usd == pytest.approx(full)


def test_the_measured_constants_are_pinned_to_their_probe() -> None:
    """The two numbers no arithmetic derives: both came off the 2026-08-13 CLI probes.

    Every other assertion here spends them symbolically, so an edit to either would leave the
    suite green while every quote moved. Changing one means re-measuring first.
    """
    assert (TRANSPORT_TOKENS, OUTPUT_TOKENS) == (700, 230)


def test_an_empty_plan_costs_nothing() -> None:
    """A run with nothing stale quotes zero rather than a floor price."""
    assert estimate([], MODEL) == Estimate(items=0, input_tokens=0, output_tokens=0, usd=0.0)


def test_an_unpriced_model_crashes() -> None:
    """A model the table does not price refuses to be quoted, rather than quoting zero.

    Anthropic adds models faster than this file will be updated, and a silent zero would read
    as "this pass is free" on exactly the run whose price nobody knows.
    """
    with pytest.raises(KeyError, match="claude-opus-9"):
        estimate([Prompt(Level.turn, "x")], "claude-opus-9")
