"""The seam between the enricher and Anthropic: one protocol, requests in, results out.

Everything above this line is pure and testable without a network — the enricher renders,
hands over a batch, and reads back a result per key. The real implementations live behind
`BatchClient`: the Message Batches API for a corpus pass, and a synchronous client for
prompt iteration.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
from anthropic.types import Message
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages import (
    MessageBatchCanceledResult,
    MessageBatchErroredResult,
    MessageBatchExpiredResult,
    MessageBatchIndividualResponse,
    MessageBatchSucceededResult,
)
from anthropic.types.messages.batch_create_params import Request

from aiobserve.enrich.prompts import OUTPUT_TOOL, OUTPUT_TOOL_NAME
from aiobserve.enrich.validation import FailureKind

# Cheap enough to enrich the whole corpus, and the classification is a short judgement over
# text a bigger model would not read differently.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Room for one tool call holding two sentences and a friction line, with slack. A response
# that runs out of tokens makes no tool call and fails its item, so this is not a place to
# be tight.
MAX_OUTPUT_TOKENS = 1_024

# The API expires a batch at 24 hours, unbilled. A round still running an hour past that is
# not a round that will finish.
BATCH_DEADLINE = 25 * 60 * 60

# Batches take tens of minutes at least; polling faster only spends requests.
BATCH_POLL_INTERVAL = 60.0


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


class BatchTimedOut(TimeoutError):
    """A batch had not ended when the client stopped waiting.

    Names the batch id: the work is still on the server, and the next run resubmits from
    scratch, so a long-running round is worth looking at by hand before paying for it twice.
    """


class AnthropicBatchClient:
    """One round through the Message Batches API: half price, and hours rather than seconds.

    `submit` creates the batch, waits for it to end, and returns one result per request —
    which is the whole round, however long it takes.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str,
        *,
        poll_interval: float = BATCH_POLL_INTERVAL,
        deadline: float = BATCH_DEADLINE,
    ) -> None:
        self.client = client
        self.model = model
        self.poll_interval = poll_interval
        self.deadline = deadline

    def submit(self, requests: Sequence[EnrichRequest]) -> list[Result]:
        # The API's `custom_id` is a short token, and an item key carries pipes and a pair of
        # uuids — so the key travels only in this mapping, and the results come back through it.
        keys = {f"item_{index}": entry.key for index, entry in enumerate(requests)}
        batch = self.client.messages.batches.create(
            requests=[
                Request(custom_id=custom_id, params=_params(entry, self.model))
                for custom_id, entry in zip(keys, requests, strict=True)
            ]
        )
        self._wait(batch.id)
        return [
            _from_batch(keys[entry.custom_id], entry)
            for entry in self.client.messages.batches.results(batch.id)
        ]

    def _wait(self, batch_id: str) -> None:
        """Poll until the batch has ended, or give up saying what it was still doing."""
        give_up_at = time.monotonic() + self.deadline
        while True:
            batch = self.client.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                return
            if time.monotonic() >= give_up_at:
                counts = batch.request_counts
                raise BatchTimedOut(
                    f"batch {batch_id} was still {batch.processing_status} after "
                    f"{self.deadline:.0f}s, with {counts.processing} request(s) processing"
                )
            time.sleep(self.poll_interval)


class SyncClient:
    """The dev path: one Messages API call per item, at full price and in minutes.

    Prompt iteration cannot wait on a batch round that the API allows a day to finish, and a
    `--limit`-sized run is small enough that the discount is not worth the latency.
    """

    def __init__(self, client: anthropic.Anthropic, model: str) -> None:
        self.client = client
        self.model = model

    def submit(self, requests: Sequence[EnrichRequest]) -> list[Result]:
        return [self._one(entry) for entry in requests]

    def _one(self, entry: EnrichRequest) -> Result:
        try:
            message = self.client.messages.create(**_params(entry, self.model))
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            # Not an item failure: it fails every item identically, and reading it as a
            # per-item one would bury the cause in a summary of the whole corpus.
            raise
        except anthropic.APIStatusError:
            return Failed(key=entry.key, kind=FailureKind.api_error)
        return _from_message(entry.key, message)


def _params(entry: EnrichRequest, model: str) -> MessageCreateParamsNonStreaming:
    """The one call shape both clients send: instructions, the item, and a forced tool call."""
    return MessageCreateParamsNonStreaming(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=entry.instructions,
        messages=[{"role": "user", "content": entry.content}],
        tools=[OUTPUT_TOOL],
        tool_choice={"type": "tool", "name": OUTPUT_TOOL_NAME},
    )


def _from_batch(key: str, entry: MessageBatchIndividualResponse) -> Result:
    """One batch result as this run reads it. Every type the API defines, or a crash."""
    match entry.result:
        case MessageBatchSucceededResult(message=message):
            return _from_message(key, message)
        case MessageBatchErroredResult():
            return Failed(key=key, kind=FailureKind.api_error)
        case MessageBatchCanceledResult():
            return Failed(key=key, kind=FailureKind.canceled)
        case MessageBatchExpiredResult():
            # Dropped unbilled at the 24h limit — normal at scale, and neither an error nor
            # an answer. The item stays stale and the next run asks again.
            return Failed(key=key, kind=FailureKind.expired)
        case _:
            raise ValueError(f"unknown batch result type {entry.result.type!r} for {key}")


def _from_message(key: str, message: Message) -> Result:
    """The forced tool call's input, or a failure — the model's prose is never an answer."""
    for block in message.content:
        if block.type == "tool_use" and block.name == OUTPUT_TOOL_NAME:
            if isinstance(block.input, Mapping):
                return Succeeded(key=key, output=block.input)
            break
    # Tool use was forced, so no usable call means the answer ran out of tokens or the API
    # changed shape. Either way there is nothing to validate.
    return Failed(key=key, kind=FailureKind.invalid_output)
