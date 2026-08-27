"""What a recorded reply cost, from our own price table.

The table is ours, not Claude Code's — a model it lacks is a gap in our list, not a schema
change, so the tests below pin behaviour on both sides of that line. What they cannot pin
is whether the numbers match Anthropic's published prices: no seam reaches that, and a test
asserting a constant against itself proves nothing. `pricing.py` records the check date.
"""

import pytest

from hyphae.extract.pricing import (
    CONTEXT_WINDOWS,
    PRICES,
    SYNTHETIC_MODEL,
    TokenUsage,
    compute_cost,
    split_cost,
)

# Lifted verbatim from `tests/fixtures/spine/`, CC 2.1.221 — the usage of
# `msg_011CdmMjFXDofyYSMxYtXa5n`, a `claude-fable-5` reply that put its whole cache write
# on the 1-hour TTL.
SPINE_SPLIT = TokenUsage(
    input=2, output=415, cache_read=9768, cache_creation=20257, cache_5m=0, cache_1h=20257
)


def test_a_reply_is_priced_by_its_model_and_its_four_token_kinds():
    """Input, output, cache read and cache write each price at their own rate.

    The four are also readable one at a time: the viewer's popover prints a legend saying
    where a phase's dollars went, and a legend derived from anything but this arithmetic
    would be a second answer to what a call cost (`docs/viewer.md`).
    """
    # If a Fable 5 reply reports the four token kinds — $10/MTok in, $50/MTok out, cache
    # reads at 0.1x input and a 1-hour cache write at 2x input...
    cost = compute_cost("claude-fable-5", SPINE_SPLIT)
    split = split_cost("claude-fable-5", SPINE_SPLIT)

    # ...then each is charged at its own rate and the four are summed.
    kinds = (
        2 * 10.0,  # input
        415 * 50.0,  # output
        9768 * 10.0 * 0.1,  # cache read
        20257 * 10.0 * 2.0,  # 1-hour cache write
    )
    assert cost == pytest.approx(sum(kinds) / 1_000_000)
    assert cost == pytest.approx(0.435678)
    # ...and the split hands back the same four separately, in the same USD the total is in.
    assert split is not None
    assert split == pytest.approx(tuple(kind / 1_000_000 for kind in kinds))
    # The total is the split summed, so the legend can never disagree with the badge above it.
    assert split.total == pytest.approx(cost)


def test_the_cache_write_splits_by_ttl():
    """A 1-hour cache write costs 2x input; a 5-minute one costs 1.25x."""
    # If two replies write the same number of cache tokens under different TTLs...
    hour = TokenUsage(
        input=0, output=0, cache_read=0, cache_creation=1000, cache_5m=0, cache_1h=1000
    )
    five = TokenUsage(
        input=0, output=0, cache_read=0, cache_creation=1000, cache_5m=1000, cache_1h=0
    )

    # ...then the 1-hour write costs 2x the base input rate and the 5-minute one 1.25x,
    # so the same tokens cost 60% more on the longer TTL.
    assert compute_cost("claude-opus-5", hour) == pytest.approx(1000 * 5.0 * 2.0 / 1_000_000)
    assert compute_cost("claude-opus-5", five) == pytest.approx(1000 * 5.0 * 1.25 / 1_000_000)


def test_an_unsplit_cache_write_is_charged_at_the_five_minute_rate():
    """When a reply reports no TTL split, its whole cache write prices as 5-minute.

    INVENTED shape: every assistant record in the mycelia corpus carries
    `usage.cache_creation` (scanned 2026-08-07), so the unsplit reading is a fallback we
    chose, not one the corpus shows.
    """
    unsplit = TokenUsage(
        input=0, output=0, cache_read=0, cache_creation=1000, cache_5m=None, cache_1h=None
    )

    assert compute_cost("claude-opus-5", unsplit) == pytest.approx(1000 * 5.0 * 1.25 / 1_000_000)


def test_a_model_the_table_lacks_costs_nothing_rather_than_zero():
    """An unpriced model reports no cost at all, so a query can find it and fill it in.

    The fail-fast rule guards Claude Code's schema, not our price list: a model released
    mid-backfill must not kill the backfill, and pricing it at zero would report a free
    session — the prior importer's bug.
    """
    assert compute_cost("claude-mythos-9", SPINE_SPLIT) is None
    # And the split says the same nothing, so a legend cannot print four zeroes where the
    # badge beside it printed no number at all.
    assert split_cost("claude-mythos-9", SPINE_SPLIT) is None


def test_a_synthetic_reply_costs_zero():
    """Claude Code's own placeholder replies are priced, and priced at nothing.

    The 205 `<synthetic>` records in the corpus all report zero tokens, so the zero is
    over-determined — but the table names the model so the cost is a stated zero rather
    than an unpriced null.
    """
    assert compute_cost(SYNTHETIC_MODEL, SPINE_SPLIT) == 0.0


def test_every_model_we_price_declares_the_window_it_answers_in():
    """The two tables answer one census, so a model we can cost is a model we can size.

    The placeholder is the exception and the only one: a `<synthetic>` record is Claude Code
    writing in its own voice, so it has a price — nothing — and no context window at all. Any
    other model in one table and not the other is a bar the viewer silently stops drawing, or
    a window nothing ever looks up.
    """
    assert set(CONTEXT_WINDOWS) == set(PRICES) - {SYNTHETIC_MODEL}
    assert all(window > 0 for window in CONTEXT_WINDOWS.values())
