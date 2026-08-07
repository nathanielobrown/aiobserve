"""The two real clients, driven by a fake SDK: what they send, and what they make of an answer.

The world here is the `anthropic` SDK's own result objects, built in the test, so a shape
change in the SDK breaks these tests rather than a corpus pass. Their content is invented and
must be — a model's answer is not a recorded transcript, and a batch result that expired
unbilled takes 24 hours to produce.
"""

import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from anthropic.types.messages import (
    MessageBatch,
    MessageBatchCanceledResult,
    MessageBatchErroredResult,
    MessageBatchExpiredResult,
    MessageBatchIndividualResponse,
    MessageBatchRequestCounts,
    MessageBatchResult,
    MessageBatchSucceededResult,
)
from anthropic.types.shared import ErrorResponse
from anthropic.types.shared.api_error_object import APIErrorObject

from aiobserve.enrich.batches import (
    DEFAULT_MODEL,
    AnthropicBatchClient,
    BatchTimedOut,
    EnrichRequest,
    Failed,
    Succeeded,
    SyncClient,
)
from aiobserve.enrich.enricher import enrich
from aiobserve.enrich.prompts import OUTPUT_TOOL, OUTPUT_TOOL_NAME
from aiobserve.enrich.store import EnrichmentStore
from aiobserve.enrich.validation import FailureKind

# Two real item keys from the fixture corpus, in the shape the enricher hands over: a
# level, a session uuid, and a key that is itself a uuid or a run id. Neither survives as a
# `custom_id`, which is where the mapping this file checks comes from.
TURN_KEY = "turn|4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b|main|818588ad-9e70-4a5f-b0b6-9e63d1e9c0f9"
RUN_KEY = "agent_run|10d0349d-0705-4e23-aa64-5b1b97698b2e|aarchitect-5144001ac50718bc"

# What the Batches API accepts as a `custom_id`.
CUSTOM_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def request(key: str) -> EnrichRequest:
    return EnrichRequest(key=key, instructions="Describe the turn.", content=f"# Item {key}")


def answer(key: str) -> dict[str, Any]:
    """A well-formed model answer (invented) for one item."""
    return {
        "description": f"Described {key}.",
        "category": "test",
        "outcome": "completed",
        "friction": None,
    }


def message(output: dict[str, Any] | None) -> Message:
    """One API answer: the forced tool call, or plain text when the model made none."""
    content = (
        [TextBlock(type="text", text="I could not answer.")]
        if output is None
        else [ToolUseBlock(type="tool_use", id="toolu_fake", name=OUTPUT_TOOL_NAME, input=output)]
    )
    return Message(
        id="msg_fake",
        model=DEFAULT_MODEL,
        role="assistant",
        type="message",
        stop_reason="tool_use",
        stop_sequence=None,
        content=cast(list[Any], content),
        usage=Usage(input_tokens=1_000, output_tokens=100),
    )


def refused(status: int, kind: type[anthropic.APIStatusError]) -> anthropic.APIStatusError:
    """An API refusal of one request, as the SDK raises it."""
    return kind(
        message=f"{status} from the fake",
        response=httpx.Response(status, request=httpx.Request("POST", "https://example.invalid")),
        body=None,
    )


def error_result() -> MessageBatchErroredResult:
    return MessageBatchErroredResult(
        type="errored",
        error=ErrorResponse(
            type="error",
            error=APIErrorObject(type="api_error", message="the fake refused this item"),
        ),
    )


class FakeBatches:
    """`client.messages.batches`: one batch, ending after `ends_after` polls.

    `results` are handed back in the order the requests were created, which is *not* what the
    real API promises — the mapping under test is the one from `custom_id` back to item key.
    """

    def __init__(self, results: Sequence[MessageBatchResult], *, ends_after: int) -> None:
        self.results_script = results
        self.ends_after = ends_after
        self.created: list[list[dict[str, Any]]] = []
        self.polls = 0

    def create(self, *, requests: Any) -> MessageBatch:
        self.created.append([dict(entry) for entry in requests])
        return self._batch("in_progress")

    def retrieve(self, batch_id: str) -> MessageBatch:
        self.polls += 1
        return self._batch("ended" if self.polls >= self.ends_after else "in_progress")

    def results(self, batch_id: str) -> Iterator[MessageBatchIndividualResponse]:
        custom_ids = [entry["custom_id"] for entry in self.created[-1]]
        for custom_id, result in zip(custom_ids, self.results_script, strict=True):
            yield MessageBatchIndividualResponse(custom_id=custom_id, result=result)

    def _batch(self, status: Any) -> MessageBatch:
        return MessageBatch(
            id="msgbatch_fake",
            type="message_batch",
            created_at="2026-08-07T00:00:00Z",
            expires_at="2026-08-08T00:00:00Z",
            processing_status=status,
            request_counts=MessageBatchRequestCounts(
                canceled=0, errored=0, expired=0, processing=len(self.results_script), succeeded=0
            ),
        )


class FakeMessages:
    """`client.messages`: `create` for the synchronous path, `batches` for the batch one."""

    def __init__(
        self,
        *,
        replies: Sequence[Message | BaseException] = (),
        results: Sequence[MessageBatchResult] = (),
        ends_after: int = 1,
    ) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []
        self.batches = FakeBatches(results, ends_after=ends_after)

    def create(self, **params: Any) -> Message:
        reply = self.replies[len(self.calls)]
        self.calls.append(params)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def sdk(messages: FakeMessages) -> anthropic.Anthropic:
    """The fake wearing the SDK's type. Nothing in it can reach the network."""
    return cast(anthropic.Anthropic, SimpleNamespace(messages=messages))


def test_a_batch_classifies_every_result_type() -> None:
    """A batch answers each item as itself: one row's worth of output, or one kind of failure.

    `expired` is the reason this is spelled out — the Batches API drops requests unbilled at
    its 24h limit, and reading that as an error or as a success both lie about coverage.
    """
    # If one batch comes back holding one of each result type the API defines...
    keys = ["turn|s|main|succeeded", "turn|s|main|errored", "turn|s|main|canceled", RUN_KEY]
    messages = FakeMessages(
        results=[
            MessageBatchSucceededResult(type="succeeded", message=message(answer(keys[0]))),
            error_result(),
            MessageBatchCanceledResult(type="canceled"),
            MessageBatchExpiredResult(type="expired"),
        ]
    )
    client = AnthropicBatchClient(sdk(messages), DEFAULT_MODEL)
    # ...then one item carries an answer to validate and the other three carry a kind, each
    # naming its own type, and none of them carrying anything for a row to be written from.
    assert client.submit([request(key) for key in keys]) == [
        Succeeded(key=keys[0], output=answer(keys[0])),
        Failed(key=keys[1], kind=FailureKind.api_error),
        Failed(key=keys[2], kind=FailureKind.canceled),
        Failed(key=keys[3], kind=FailureKind.expired),
    ]


def test_custom_ids_are_legal_and_map_back_to_item_keys() -> None:
    """Results come back under the item keys, through ids the Batches API will accept."""
    # If a batch is submitted for a main turn and an agent run — keys carrying pipes, uuids
    # and a run id, none of which a `custom_id` may hold...
    keys = [TURN_KEY, RUN_KEY]
    messages = FakeMessages(
        results=[
            MessageBatchSucceededResult(type="succeeded", message=message(answer(key)))
            for key in keys
        ]
    )
    results = AnthropicBatchClient(sdk(messages), DEFAULT_MODEL).submit(
        [request(key) for key in keys]
    )
    # ...then each request went out under an id the API accepts, carrying that item's own
    # instructions and content and the tool it must answer with...
    sent = messages.batches.created[0]
    assert [entry["custom_id"] for entry in sent] == ["item_0", "item_1"]
    assert all(CUSTOM_ID.match(entry["custom_id"]) for entry in sent)
    assert [entry["params"]["messages"][0]["content"] for entry in sent] == [
        f"# Item {key}" for key in keys
    ]
    assert sent[0]["params"]["tool_choice"] == {"type": "tool", "name": OUTPUT_TOOL_NAME}
    assert sent[0]["params"]["tools"] == [OUTPUT_TOOL]
    assert sent[0]["params"]["model"] == DEFAULT_MODEL
    # ...and every answer is restored to the key the enricher asked about.
    assert [result.key for result in results] == keys


def test_polling_stops_at_a_deadline_and_names_the_batch() -> None:
    """A batch that never ends fails with a deadline, rather than waiting out the job.

    An unbounded poll against a batch the API allows 24 hours would burn a whole run and
    print no failure line.
    """
    # If the batch never reports that it ended...
    messages = FakeMessages(results=[], ends_after=1_000_000)
    client = AnthropicBatchClient(sdk(messages), DEFAULT_MODEL, poll_interval=0.001, deadline=0.05)
    # ...then the client gives up at its deadline, naming the batch left on the server...
    with pytest.raises(BatchTimedOut, match="msgbatch_fake") as timeout:
        client.submit([request(TURN_KEY)])
    assert "in_progress" in str(timeout.value)
    # ...having actually polled more than once on the way there.
    assert messages.batches.polls > 1


def test_an_answer_without_the_output_tool_fails_its_item() -> None:
    """A response that made no tool call has no answer in it, and fails rather than guessing."""
    messages = FakeMessages(
        results=[MessageBatchSucceededResult(type="succeeded", message=message(None))]
    )
    assert AnthropicBatchClient(sdk(messages), DEFAULT_MODEL).submit([request(TURN_KEY)]) == [
        Failed(key=TURN_KEY, kind=FailureKind.invalid_output)
    ]


def test_the_sync_client_answers_one_request_at_a_time() -> None:
    """`SyncClient` sends the same call the batch path does, and returns the same results."""
    keys = [TURN_KEY, RUN_KEY]
    messages = FakeMessages(replies=[message(answer(key)) for key in keys])
    results = SyncClient(sdk(messages), DEFAULT_MODEL).submit([request(key) for key in keys])
    assert results == [Succeeded(key=key, output=answer(key)) for key in keys]
    # The dev path is the batch path's call, one at a time — same system prompt, same tool.
    assert [call["system"] for call in messages.calls] == ["Describe the turn."] * 2
    assert messages.calls[0]["tool_choice"] == {"type": "tool", "name": OUTPUT_TOOL_NAME}


def test_a_refused_request_fails_only_its_own_item() -> None:
    """One request the API turns away is one item's failure, not the round's."""
    # If the API refuses the first of two requests...
    keys = [TURN_KEY, RUN_KEY]
    messages = FakeMessages(
        replies=[
            refused(429, anthropic.RateLimitError),
            message(answer(keys[1])),
        ]
    )
    results = SyncClient(sdk(messages), DEFAULT_MODEL).submit([request(key) for key in keys])
    # ...then that item fails and its sibling is still answered.
    assert results == [
        Failed(key=keys[0], kind=FailureKind.api_error),
        Succeeded(key=keys[1], output=answer(keys[1])),
    ]


def test_a_rejected_key_is_not_an_item_failure() -> None:
    """A bad API key crashes the run instead of failing every item one at a time.

    It would otherwise read as a corpus-wide model failure in the crash summary, which is a
    different thing to go and fix.
    """
    messages = FakeMessages(replies=[refused(401, anthropic.AuthenticationError)])
    with pytest.raises(anthropic.AuthenticationError):
        SyncClient(sdk(messages), DEFAULT_MODEL).submit([request(TURN_KEY)])


def test_both_clients_write_the_same_rows(mutable_db: Path, tmp_path: Path) -> None:
    """A store enriched through the batch path and through the dev path holds the same rows.

    `--no-batch` is a latency choice, not a different enrichment: the two clients differ in
    what they send it through, and in nothing a row can see.
    """
    # If the same store is enriched twice — once through each client, both answering the
    # same way...
    other = tmp_path / "sync.duckdb"
    other.write_bytes(mutable_db.read_bytes())
    with EnrichmentStore(mutable_db) as store:
        keys = [item.key for item in store.turn_items()]
        batched = FakeMessages(
            results=[
                MessageBatchSucceededResult(type="succeeded", message=message(answer(key)))
                for key in keys
            ]
        )
        enrich(store, AnthropicBatchClient(sdk(batched), DEFAULT_MODEL))
        through_batches = rows(store)
    with EnrichmentStore(other) as store:
        direct = FakeMessages(replies=[message(answer(key)) for key in keys])
        enrich(store, SyncClient(sdk(direct), DEFAULT_MODEL))
        # ...then both wrote a row per turn, and the rows are the same rows.
        assert rows(store) == through_batches
        assert len(through_batches) == len(keys)


def rows(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    """Every enrichment row but `enriched_at`, which is a clock reading, not a result."""
    return store.connection.execute(
        "SELECT session_id, source, turn_id, description, category, outcome, friction,"
        " input_hash, prompt_version, taxonomy_version, model"
        " FROM turn_enrichments ORDER BY session_id, turn_id"
    ).fetchall()
