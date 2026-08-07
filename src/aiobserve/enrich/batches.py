"""The seam between the enricher and Anthropic: one protocol, requests in, results out.

Everything above this line is pure and testable without a network — the enricher renders,
hands over a batch, and reads back a result per key. The real implementations live behind
`BatchClient`: the Message Batches API for a corpus pass, and a synchronous client for
prompt iteration.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from aiobserve.enrich.validation import FailureKind

# Cheap enough to enrich the whole corpus, and the classification is a short judgement over
# text a bigger model would not read differently.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class EnrichRequest:
    """One item to describe."""

    # The item's key, echoed back on the result — the Batches API answers out of order.
    key: str
    instructions: str
    content: str


@dataclass(frozen=True)
class Succeeded:
    """The model answered. The output is unvalidated: `validation.validate` is next."""

    key: str
    output: Mapping[str, Any]


@dataclass(frozen=True)
class Failed:
    """The request did not produce an answer. Carries no model output, by construction."""

    key: str
    kind: FailureKind


Result = Succeeded | Failed


class BatchClient(Protocol):
    """Runs one round of requests to completion, whatever "completion" costs.

    A round can take a day (Message Batches) or a minute (synchronous). Either way `submit`
    returns exactly one result per request, in any order, and raises only when the whole
    round failed — a single item's failure comes back as `Failed`.
    """

    # The model the results were produced by, which is part of what makes a row stale.
    model: str

    def submit(self, requests: Sequence[EnrichRequest]) -> list[Result]: ...
