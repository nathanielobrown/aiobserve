"""The guard that keeps the suite off the Anthropic API."""

import anthropic
import pytest

from tests.enrich.conftest import LiveApiForbidden


def test_the_sdk_cannot_reach_the_network() -> None:
    """An SDK call from an unmarked test raises instead of billing an account."""
    client = anthropic.Anthropic(api_key="not-a-real-key")
    with pytest.raises(LiveApiForbidden, match="tried to reach the network"):
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "hello"}],
        )
