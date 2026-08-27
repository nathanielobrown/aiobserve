"""What a test drives an enrichment pass with, and how it reads back what the pass wrote.

`FakeClient` records what it was asked and answers from a script, so every claim about rounds,
staleness and failure handling is checked against real rows without a request leaving the
machine. Its answers are invented, as model output must be — there is no recorded session to
draw them from. The readers below turn the store back into the keys and stamps a leaf asserts
on. A plain module rather than the conftest, so both enricher test files see one fake.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from aiobserve.enrich.client import (
    EnrichRequest,
    Result,
    Succeeded,
)
from aiobserve.enrich.prompts import (
    AgentRunItem,
    TurnItem,
)
from aiobserve.enrich.store import EnrichmentStore
from tests.enrich.conftest import (
    MODEL,
)

# The recorded `claude` envelopes, shared with `fake_cli.py`.
FIXTURES = Path(__file__).parent / "fixtures"


# An invented credential, in a shape the screen knows, for the answer that must be refused.
FAKE_SECRET = "AKIAIOSFODNN7EXAMPLE"


# A sentinel inside an out-of-vocabulary answer: if it reaches the crash summary, so would
# whatever a real answer had said there.
FAKE_CATEGORY = "SENTINEL-5c1a-out-of-vocabulary"


class FakeClient:
    """Answers every request, records every round, and never starts a process.

    `answers` overrides the reply for one key — a failure, or an answer the validator will
    refuse. Everything else gets a well-formed description naming its own key, so a row can
    be traced back to the request that wrote it.
    """

    def __init__(self, model: str = MODEL, answers: Mapping[str, Result] | None = None) -> None:
        self.model = model
        self.answers = answers or {}
        self.rounds: list[tuple[EnrichRequest, ...]] = []

    def submit(self, requests: Sequence[EnrichRequest]) -> list[Result]:
        self.rounds.append(tuple(requests))
        return [
            self.answers.get(request.key, Succeeded(key=request.key, output=answer(request.key)))
            for request in requests
        ]

    @property
    def keys(self) -> list[str]:
        """Every key the client was asked about, in the order it was asked."""
        return [request.key for sent in self.rounds for request in sent]


def answer(key: str, **overrides: object) -> dict[str, Any]:
    """A well-formed model answer (invented) for one item."""
    return {
        "description": f"Described {key}.",
        "category": "test",
        "outcome": "completed",
        "friction": None,
    } | overrides


def turns(store: EnrichmentStore) -> list[TurnItem]:
    """The store's main turns in the order a run sends them — the order they happened in."""
    return store.turn_items()


def runs(store: EnrichmentStore) -> list[AgentRunItem]:
    """The store's agent runs, in no particular order — the rounds decide what goes when."""
    return store.run_items()


def key_of(store: EnrichmentStore, agent_run_id: str) -> str:
    """The item key one agent run is sent and stored under."""
    return next(item.key for item in runs(store) if item.agent_run_id == agent_run_id)


def turn_key(store: EnrichmentStore, prefix: str) -> str:
    """The item key of the one main turn whose id starts with `prefix`."""
    return next(item.key for item in turns(store) if item.turn_id.startswith(prefix))


def session_key(store: EnrichmentStore, session_id: str) -> str:
    """The item key one session is sent and stored under."""
    return next(item.key for item in store.session_items() if item.session_id == session_id)


def stored_sessions(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    return store.connection.execute(
        "SELECT session_id, description, input_hash FROM session_enrichments ORDER BY session_id"
    ).fetchall()


def stored_runs(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    return store.connection.execute(
        "SELECT agent_run_id, description, input_hash, enriched_at"
        " FROM agent_run_enrichments ORDER BY agent_run_id"
    ).fetchall()


def written_at(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    """Every enrichment row of every level, against the moment it was written."""
    return store.connection.execute(
        "SELECT turn_id, enriched_at FROM turn_enrichments"
        " UNION ALL SELECT agent_run_id, enriched_at FROM agent_run_enrichments"
        " UNION ALL SELECT session_id, enriched_at FROM session_enrichments"
        " ORDER BY 1"
    ).fetchall()


def stored(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    return store.connection.execute(
        "SELECT session_id, source, turn_id, description, category, outcome, friction,"
        " input_hash, prompt_version, taxonomy_version, model"
        " FROM turn_enrichments ORDER BY turn_id"
    ).fetchall()
