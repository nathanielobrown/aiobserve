"""One item's round trip: what a recorded envelope becomes, and what is retried or refused.

The fake seam and every envelope are in `fake_cli.py`; what happens when many items run at
once is in `test_client__pool.py`. Here a client answers one item at a time, so each leaf
reads a single reply against a single result.
"""

import json
from typing import Any

import pytest

from hyphae.enrich.client import (
    ATTEMPTS,
    BREAKER_BOUND,
    ITEM_TIMEOUT,
    CliClient,
    EnvelopeDrift,
    Failed,
    Succeeded,
)
from hyphae.enrich.taxonomy import Category, Outcome
from hyphae.enrich.validation import Enrichment, FailureKind, validate
from tests.enrich.fake_cli import (
    MODEL,
    OTHER_MODEL,
    RECORDED_USAGE,
    Install,
    Reply,
    content_of,
    errors,
    hangs,
    kinds,
    mutated,
    recorded,
    requests_for,
    succeeds,
    without,
)


def test_a_recorded_envelope_becomes_a_validated_answer(fake: Install) -> None:
    """A real CLI envelope becomes the answer `validation.validate` then accepts."""
    # If the CLI answers as it really answered on 2026-08-13...
    fake({content_of("item-0"): succeeds()})
    results = CliClient(MODEL).submit(requests_for("item-0"))
    # ...then the envelope's `structured_output` is the output, handed over unchanged...
    assert results == [
        Succeeded(key="item-0", output=recorded("envelope_success")["structured_output"])
    ]
    # ...and validation accepts it, which is the next thing the enricher does with it.
    answer = results[0]
    assert isinstance(answer, Succeeded)
    assert validate(answer.output) == Enrichment(
        description=recorded("envelope_success")["structured_output"]["description"],
        category=Category.implement,
        outcome=Outcome.completed,
        friction=None,
    )


def test_an_errored_envelope_fails_its_item(fake: Install) -> None:
    """An `is_error` answer fails its own item and nothing else."""
    # Derived from the recorded success: `is_error` flipped, the exit code left at zero, so
    # the flag alone decides.
    fake({content_of("item-0"): Reply(stdout=json.dumps(mutated(is_error=True)))})
    results = CliClient(MODEL).submit(requests_for("item-0"))
    assert results == [Failed(key="item-0", kind=FailureKind.api_error)]


@pytest.mark.parametrize(
    "reply",
    [
        # The recorded logged-out call: exit 1 with a full envelope behind it...
        errors(),
        # ...and, invented because no crash was recorded, a CLI that dies before printing
        # anything. The exit code decides alone: nothing is parsed once it is nonzero, so
        # empty stdout never reaches the envelope reader.
        Reply(returncode=1),
    ],
    ids=["logged-out", "printed-nothing"],
)
def test_a_nonzero_exit_is_retried_once(fake: Install, reply: Reply) -> None:
    """A CLI that exits nonzero is asked again before its item is given up on."""
    cli = fake({content_of("item-0"): reply})
    results = CliClient(MODEL).submit(requests_for("item-0"))
    # Whatever it printed, a nonzero exit is sent twice and fails once.
    assert len(cli.calls) == ATTEMPTS == 2
    assert results == [Failed(key="item-0", kind=FailureKind.api_error)]


def test_a_hung_call_times_out_and_is_retried_once(fake: Install) -> None:
    """A hung `claude` fails its item on a deadline every call carries."""
    cli = fake({content_of("item-0"): hangs()})
    results = CliClient(MODEL).submit(requests_for("item-0"))
    assert results == [Failed(key="item-0", kind=FailureKind.timeout)]
    # Both attempts carried the same 300s ceiling — ~19x the worst wall time probed.
    assert len(cli.calls) == 2
    assert [call["timeout"] for call in cli.calls] == [300, 300] == [ITEM_TIMEOUT] * 2


@pytest.mark.parametrize(
    "envelope",
    [
        # Derived: the CLI omits `structured_output` when the model produced nothing
        # conforming, which is what the recorded logged-out envelope shows it doing...
        without("structured_output"),
        # ...and, invented, an answer that is present but is not an object. `validate` reads
        # it by key, so a list would fail there — one item later, having already been stored.
        mutated(structured_output=[{"description": "not an object"}]),
    ],
    ids=["absent", "not-an-object"],
)
def test_an_unusable_answer_fails_without_a_retry(fake: Install, envelope: dict[str, Any]) -> None:
    """An answer carrying no usable structured output is not worth resending."""
    cli = fake({content_of("item-0"): Reply(stdout=json.dumps(envelope))})
    results = CliClient(MODEL).submit(requests_for("item-0"))
    assert results == [Failed(key="item-0", kind=FailureKind.invalid_output)]
    # A second identical send cannot improve a bad answer.
    assert len(cli.calls) == 1


def test_a_truncated_answer_is_an_invalid_output(fake: Install) -> None:
    """An answer cut off at the output cap is a bad answer, not a transport failure."""
    # Derived: `stop_reason` swapped for the truncation value.
    fake({content_of("item-0"): Reply(stdout=json.dumps(mutated(stop_reason="max_tokens")))})
    results = CliClient(MODEL).submit(requests_for("item-0"))
    assert results == [Failed(key="item-0", kind=FailureKind.invalid_output)]


@pytest.mark.parametrize(
    ("reply", "kind", "calls"),
    [
        (Reply(stdout=json.dumps(mutated(is_error=True))), FailureKind.api_error, 2),
        (hangs(), FailureKind.timeout, 2),
        (Reply(stdout=json.dumps(without("structured_output"))), FailureKind.invalid_output, 1),
        (Reply(stdout=json.dumps(without("modelUsage"))), FailureKind.drift, 1),
    ],
)
def test_only_transport_failures_are_retried(
    fake: Install, reply: Reply, kind: FailureKind, calls: int
) -> None:
    """Only a failure the transport might not repeat is worth a second call."""
    # With a canary already answered, so every shape below is judged after the canary...
    cli = fake({content_of("canary"): succeeds(), content_of("item-0"): reply})
    results = CliClient(MODEL, concurrency=1).submit(requests_for("canary", "item-0"))
    # ...each shape fails as its own kind, and only the transport ones are sent twice.
    assert kinds(results) == {"canary": None, "item-0": kind}
    assert len(cli.calls) == 1 + calls


# Written out rather than read from `_CONTRACT_FIELDS`, so a field dropped from that tuple
# fails here instead of silently shrinking this test to the fields that are left.
@pytest.mark.parametrize("field", ["is_error", "stop_reason", "modelUsage"])
def test_a_first_item_missing_a_contract_field_raises(fake: Install, field: str) -> None:
    """A CLI that stops writing any contracted field crashes the run on the first item."""
    # Derived: one field removed. One item's spend is the whole price of the crash — and an
    # unread field would otherwise surface as a `KeyError` mid-round, forfeiting the paid work.
    fake({content_of("item-0"): Reply(stdout=json.dumps(without(field)))})
    with pytest.raises(EnvelopeDrift, match=field):
        CliClient(MODEL).submit(requests_for("item-0"))


def test_stdout_that_is_not_json_raises_on_the_canary(fake: Install) -> None:
    """A first call that printed something other than JSON crashes the run."""
    # Invented, because no such call was recorded: `--output-format json` promises one JSON
    # document, so a *zero* exit that printed anything else is the flag no longer meaning what
    # it means — and every later item in the round would be unreadable the same way.
    fake({content_of("item-0"): Reply(stdout="Usage: claude [options] [command]\n")})
    with pytest.raises(EnvelopeDrift, match="not JSON"):
        CliClient(MODEL).submit(requests_for("item-0"))


@pytest.mark.parametrize(
    "usage",
    [
        # The usage map rekeyed to another model, as a silent substitution would leave it...
        {OTHER_MODEL: RECORDED_USAGE},
        # ...and one naming the model asked for *and* another, which is the shape a mid-call
        # fallback really takes: the asked-for model is present, and still not what answered.
        {MODEL: RECORDED_USAGE, OTHER_MODEL: RECORDED_USAGE},
    ],
    ids=["substituted", "two-models"],
)
def test_a_usage_map_naming_another_model_raises_on_the_canary(
    fake: Install, usage: dict[str, Any]
) -> None:
    """A run any other model had a hand in crashes rather than mislabels its rows."""
    fake({content_of("item-0"): Reply(stdout=json.dumps(mutated(modelUsage=usage)))})
    with pytest.raises(EnvelopeDrift, match=OTHER_MODEL):
        CliClient(MODEL).submit(requests_for("item-0"))


def test_drift_after_the_canary_fails_its_item(fake: Install) -> None:
    """Once the round is spending, drift fails one item instead of forfeiting the paid ones."""
    # If the canary answered and a later item drifts...
    fake(
        {
            content_of("canary"): succeeds(),
            content_of("item-0"): Reply(stdout=json.dumps(without("modelUsage"))),
        }
    )
    # ...then the round returns rather than raising — the property `enricher._round` rests on.
    results = CliClient(MODEL, concurrency=1).submit(requests_for("canary", "item-0"))
    assert kinds(results) == {"canary": None, "item-0": FailureKind.drift}


def test_an_inconclusive_canary_recanaries_before_the_pool_opens(fake: Install) -> None:
    """A canary that never saw an envelope is retried alone, not answered by opening the pool."""
    # If the first two items error — which validates no envelope at all...
    inconclusive = Reply(stdout=json.dumps(mutated(is_error=True)))
    cli = fake(
        {content_of(key): inconclusive for key in ("item-0", "item-1")}
        | {content_of(f"item-{index}"): succeeds() for index in range(2, 6)}
    )
    results = CliClient(MODEL, concurrency=4).submit(
        requests_for(*(f"item-{index}" for index in range(6)))
    )
    # ...then no two calls ran at once until one of them came back with an envelope...
    assert cli.peak_before_an_answer == 1
    # ...and the pool only then took the rest.
    assert kinds(results) == {
        "item-0": FailureKind.api_error,
        "item-1": FailureKind.api_error,
        "item-2": None,
        "item-3": None,
        "item-4": None,
        "item-5": None,
    }


def test_a_canary_that_never_answers_ends_the_round(fake: Install) -> None:
    """Re-canarying is bounded by the breaker: five silent items end the round, unsent."""
    # If every call errors, the serial canary never opens the pool...
    keys = [f"item-{index}" for index in range(8)]
    cli = fake({content_of(key): errors() for key in keys})
    results = CliClient(MODEL).submit(requests_for(*keys))
    # ...and the breaker stops it after five items rather than walking the whole round.
    assert (
        list(kinds(results).values())
        == [FailureKind.api_error] * BREAKER_BOUND + [FailureKind.aborted] * 3
    )
    assert len(cli.calls) == BREAKER_BOUND * ATTEMPTS
