"""One item out of a fixture store, picked by id, and enriched where a parent must embed it.

Rows come from a real store built by running the pipeline over `tests/fixtures/`, so a render
test names the turn, run or session it means and gets the item the pipeline really wrote. Each
picker asserts it named exactly one item: a fixture that stops carrying the shape fails here
rather than rendering something else. A plain module, read by both prompt test files.
"""

from hyphae.enrich.prompts import (
    AgentRunItem,
    Item,
    SessionItem,
    TurnItem,
)
from hyphae.enrich.store import EnrichmentStore, Stamp
from hyphae.enrich.taxonomy import (
    Category,
    Outcome,
)
from hyphae.enrich.validation import Enrichment


def turn(store: EnrichmentStore, session_id: str, prefix: str) -> TurnItem:
    """The one main turn of `session_id` whose id starts with `prefix`."""
    items = [
        item
        for item in store.turn_items()
        if item.session_id == session_id and item.turn_id.startswith(prefix)
    ]
    assert len(items) == 1, f"{prefix} named {len(items)} turns"
    return items[0]


def run(store: EnrichmentStore, agent_run_id: str) -> AgentRunItem:
    """The store's one agent run with this id."""
    items = [item for item in store.run_items() if item.agent_run_id == agent_run_id]
    assert len(items) == 1, f"{agent_run_id} named {len(items)} runs"
    return items[0]


def ended(rendered: str) -> str:
    """The render's last line — the one that says how the item ended."""
    return rendered.rsplit("\n", 1)[-1]


def session(store: EnrichmentStore, session_id: str) -> SessionItem:
    """The store's one enrichable session with this id."""
    items = [item for item in store.session_items() if item.session_id == session_id]
    assert len(items) == 1, f"{session_id} named {len(items)} sessions"
    return items[0]


def describe(store: EnrichmentStore, item: Item, description: str) -> None:
    """Enrich one item, so a render of its parent has a child description to embed."""
    store.upsert(
        item,
        Enrichment(
            description=description,
            category=Category.explore,
            outcome=Outcome.completed,
            friction=None,
        ),
        # The stamp decides re-enrichment, which no render reads.
        Stamp(input_hash="unused", prompt_version=1, taxonomy_version=1, model="fake"),
    )
