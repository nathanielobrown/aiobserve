"""`CliClient`: one `claude -p` per item, driven over a faked subprocess seam.

No process starts here. `subprocess.run` is replaced by `FakeCli`, which answers from the
recorded envelopes in `fixtures/` and records every invocation — so the argv, the constructed
env, the temp cwd and the deadline are all assertable. The seam is the module attribute the
client really resolves, not a private helper, and the guard in `test_no_live_api.py` is what
proves that: an unpatched client trips it rather than launching `claude`.

Only two envelopes are recorded: a success and the logged-out error. Every other shape is a
mutation of the success, labelled where it is built.
"""

import json
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from aiobserve import cli
from aiobserve.enrich import client
from aiobserve.enrich.client import (
    ATTEMPTS,
    BREAKER_BOUND,
    CLAUDE,
    ITEM_TIMEOUT,
    CliClient,
    EnrichRequest,
    EnvelopeDrift,
    Failed,
    Result,
    Succeeded,
    build_env,
    preflight,
)
from aiobserve.enrich.prompts import OUTPUT_SCHEMA
from aiobserve.enrich.store import EnrichmentStore
from aiobserve.enrich.taxonomy import Category, Outcome
from aiobserve.enrich.validation import Enrichment, FailureKind, validate
from tests.enrich.conftest import LIVE_CLI

FIXTURES = Path(__file__).parent / "fixtures"

MODEL = "claude-haiku-4-5-20251001"

# Short stand-ins for the real per-level instructions: the client forwards whatever it is
# given, so the text only has to be recognizable in an argv assertion.
INSTRUCTIONS = "Describe the item you are about to read."

# How long a gated fake waits for the call before it in the completion chain. A ceiling, not
# a pace: a mistake in the chain fails the test instead of hanging the run.
GATE_TIMEOUT = 10.0


def recorded(name: str) -> dict[str, Any]:
    """One recorded envelope, fresh each call so a mutation cannot leak between tests."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def mutated(**changes: Any) -> dict[str, Any]:
    """The recorded success envelope with fields replaced — a derived shape, not a recording."""
    return recorded("envelope_success") | changes


def without(*fields: str) -> dict[str, Any]:
    """The recorded success envelope with fields removed — derived, standing for CLI drift."""
    envelope = recorded("envelope_success")
    for name in fields:
        del envelope[name]
    return envelope


def content_of(key: str) -> str:
    """What the item with this key renders to. The fake keys its script on this."""
    return f"# Main turn\n\nrender for {key}"


def requests_for(*keys: str) -> list[EnrichRequest]:
    return [
        EnrichRequest(key=key, instructions=INSTRUCTIONS, content=content_of(key)) for key in keys
    ]


@dataclass(frozen=True)
class Reply:
    """One scripted `claude` invocation: what it writes, or what it raises instead."""

    stdout: str = ""
    returncode: int = 0
    # A hung process, as `subprocess.run` reports one.
    raises: BaseException | None = None

    @property
    def answers(self) -> bool:
        """Whether this reply carries a usable envelope — what ends the serial canary phase."""
        if self.raises is not None or self.returncode != 0:
            return False
        return not json.loads(self.stdout).get("is_error", True)


def succeeds() -> Reply:
    return Reply(stdout=json.dumps(recorded("envelope_success")))


def errors() -> Reply:
    """The recorded logged-out call: exit 1, `is_error`, no answer."""
    return Reply(stdout=json.dumps(recorded("envelope_logged_out")), returncode=1)


def hangs() -> Reply:
    return Reply(raises=subprocess.TimeoutExpired(CLAUDE, ITEM_TIMEOUT))


class FakeCli:
    """Stands in for `subprocess.run`: answers from a script, and records every call.

    Scripted by item content, which is unique per key, so a retry of the same item gets the
    same reply. `gate` optionally holds a call open until another call has started, which is
    how a completion order that is not the submission order gets forced.
    """

    def __init__(
        self,
        replies: Mapping[str, Reply],
        *,
        gate: Callable[[str], None] | None = None,
    ) -> None:
        self.replies = replies
        self.gate = gate
        self.calls: list[dict[str, Any]] = []
        # Every content the fake was asked for, in call-start order.
        self.started: list[str] = []
        self._lock = threading.Lock()
        self._live = 0
        self._answered = 0
        # The widest overlap seen while no call had yet returned an answer — 1 means the
        # calls before the first answer were strictly serial.
        self.peak_before_an_answer = 0

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        key = kwargs.get("input", AUTH_CALL)
        with self._lock:
            self.calls.append({"argv": list(argv), **kwargs})
            self.started.append(key)
            self._live += 1
            if not self._answered:
                self.peak_before_an_answer = max(self.peak_before_an_answer, self._live)
        try:
            if self.gate is not None:
                self.gate(key)
            reply = self.replies[key]
            if reply.raises is not None:
                raise reply.raises
            return subprocess.CompletedProcess(list(argv), reply.returncode, reply.stdout, "")
        finally:
            with self._lock:
                self._live -= 1
                if key in self.replies and self.replies[key].answers:
                    self._answered += 1

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "FakeCli":
        monkeypatch.setattr(subprocess, "run", self)
        return self


# What the fake calls the `claude auth status` call, which passes no `input`.
AUTH_CALL = "<auth status>"


class Chain:
    """Forces a completion order by holding each call until another call has started.

    A call starting is the only thing a fake can see that proves the client recorded an
    earlier result: the client feeds one new item per completion it records. So each gated
    call waits for the *start* of the item fed by the completion it must follow.
    """

    def __init__(self, waits_for: Mapping[str, str]) -> None:
        self.waits_for = waits_for
        self.started: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def _event(self, key: str) -> threading.Event:
        with self._lock:
            return self.started.setdefault(key, threading.Event())

    def __call__(self, key: str) -> None:
        self._event(key).set()
        awaited = self.waits_for.get(key)
        if awaited is not None and not self._event(awaited).wait(timeout=GATE_TIMEOUT):
            raise RuntimeError(f"{key!r} waited {GATE_TIMEOUT}s for {awaited!r} to start")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, refuse_subprocess: None) -> Callable[..., FakeCli]:
    """Install a `FakeCli` over the guard, so the test's own seam is the one in place."""

    def install(replies: Mapping[str, Reply], *, gate: Callable[[str], None] | None = None):
        return FakeCli(replies, gate=gate).install(monkeypatch)

    return install


Install = Callable[..., FakeCli]


def kinds(results: Sequence[Result]) -> dict[str, FailureKind | None]:
    """Every result by key: its failure kind, or None where the model answered."""
    return {result.key: result.kind if isinstance(result, Failed) else None for result in results}


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
        # anything. The exit code has to decide on its own here: read as an envelope, empty
        # stdout is drift, and drift on the round's first item crashes the whole run.
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


def test_an_unusable_answer_fails_without_a_retry(fake: Install) -> None:
    """An answer with no structured output is not worth resending."""
    # Derived: the CLI omits `structured_output` when the model produced nothing conforming,
    # which is what the recorded logged-out envelope shows it doing.
    fake({content_of("item-0"): Reply(stdout=json.dumps(without("structured_output")))})
    cli_client = CliClient(MODEL)
    results = cli_client.submit(requests_for("item-0"))
    assert results == [Failed(key="item-0", kind=FailureKind.invalid_output)]


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


def test_a_first_item_missing_a_contract_field_raises(fake: Install) -> None:
    """A CLI that stops writing a contracted field crashes the run on the first item."""
    # Derived: `modelUsage` removed. One item's spend is the whole price of the crash.
    fake({content_of("item-0"): Reply(stdout=json.dumps(without("modelUsage")))})
    with pytest.raises(EnvelopeDrift, match="modelUsage"):
        CliClient(MODEL).submit(requests_for("item-0"))


def test_a_substituted_model_key_raises_on_the_canary(fake: Install) -> None:
    """A run answered by a model nobody asked for crashes rather than mislabels its rows."""
    # Derived: the usage map rekeyed to another model, as a silent fallback would leave it.
    usage = recorded("envelope_success")["modelUsage"]["claude-haiku-4-5-20251001"]
    fake(
        {
            content_of("item-0"): Reply(
                stdout=json.dumps(mutated(modelUsage={"claude-sonnet-4-5-20250929": usage}))
            )
        }
    )
    with pytest.raises(EnvelopeDrift, match="claude-sonnet-4-5-20250929"):
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


def test_a_tripped_breaker_returns_the_paid_work(fake: Install) -> None:
    """A round that gives up still hands back every answer it already paid for."""
    # If three items answer and then five in a row fail...
    keys = [f"item-{index}" for index in range(10)]
    replies = {content_of(key): succeeds() for key in keys[:3]}
    replies |= {content_of(key): errors() for key in keys[3:8]}
    fake(replies)
    # ...serially, so the trip point is exactly where the fifth failure lands...
    results = CliClient(MODEL, concurrency=1).submit(requests_for(*keys))
    # ...then the answers survive, the failures name their own kind, and the two items that
    # were never sent are `aborted` — not the kind that tripped the breaker.
    assert kinds(results) == {
        "item-0": None,
        "item-1": None,
        "item-2": None,
        "item-3": FailureKind.api_error,
        "item-4": FailureKind.api_error,
        "item-5": FailureKind.api_error,
        "item-6": FailureKind.api_error,
        "item-7": FailureKind.api_error,
        "item-8": FailureKind.aborted,
        "item-9": FailureKind.aborted,
    }


def test_a_success_resets_the_breaker(fake: Install) -> None:
    """Scattered failures never end a round, however many of them there are."""
    # If failures alternate with answers past the breaker's bound...
    keys = [f"item-{index}" for index in range(12)]
    replies = {
        content_of(key): errors() if index % 2 else succeeds() for index, key in enumerate(keys)
    }
    fake(replies)
    results = CliClient(MODEL, concurrency=1).submit(requests_for(*keys))
    # ...then nothing is aborted: six failures, none of them consecutive.
    assert sorted(kinds(results)) == sorted(keys)
    assert FailureKind.aborted not in set(kinds(results).values())


# The completion order the chain below forces, as a comment reads it: the pool holds four
# items, and one new item starts for every completion the client records — so waiting for a
# start is how a fake waits for a record. `pool-0` answers, and is held until four failures
# have landed, which is what makes the trip depend on completion order rather than submission
# order. Submitted in order, `pool-0`'s answer would reset the counter at the second item and
# the round would end six items later, aborting six; counted per worker, four workers sharing
# thirteen failures would never reach five and nothing would abort at all.
_COMPLETION_CHAIN = {
    "pool-2": "pool-4",
    "pool-1": "pool-5",
    "pool-4": "pool-6",
    "pool-0": "pool-7",
    "pool-5": "pool-8",
    "pool-6": "pool-9",
    "pool-7": "pool-10",
    "pool-8": "pool-11",
    "pool-9": "pool-12",
    "pool-10": "pool-12",
    "pool-11": "pool-12",
    "pool-12": "pool-12",
}


def test_the_breaker_counts_completions_across_workers(fake: Install) -> None:
    """One counter, advanced as answers land — not one per worker, and not by send order."""
    # If a pool of four runs fourteen items whose completion order is not their send order...
    keys = [f"pool-{index}" for index in range(14)]
    replies = {content_of("canary"): succeeds(), content_of("pool-0"): succeeds()}
    replies |= {content_of(key): errors() for key in keys[1:]}
    chain = Chain({content_of(key): content_of(value) for key, value in _COMPLETION_CHAIN.items()})
    fake(replies, gate=chain)
    results = CliClient(MODEL, concurrency=4).submit(requests_for("canary", *keys))
    # ...then the fifth consecutive failure *to land* ends the round, which leaves exactly one
    # item never sent.
    aborted = {key for key, kind in kinds(results).items() if kind is FailureKind.aborted}
    assert aborted == {"pool-13"}


def test_a_process_the_machine_could_not_start_fails_one_item(fake: Install) -> None:
    """A `claude` that never launched costs its own item, not the answers around it."""
    # Invented, because no such run was recorded: `subprocess.run` raises `OSError` before the
    # child exists — no file descriptor left at concurrency 4, no memory to fork with, the
    # binary gone mid-round. Three items are already paid for when it happens...
    keys = [f"item-{index}" for index in range(6)]
    replies = {content_of(key): succeeds() for key in keys}
    replies[content_of("item-3")] = Reply(raises=OSError(24, "Too many open files"))
    cli = fake(replies)
    results = CliClient(MODEL, concurrency=1).submit(requests_for(*keys))
    # ...and they all come back, with the refused item one classified failure among them
    # rather than an exception that forfeits the round.
    assert kinds(results) == {
        "item-0": None,
        "item-1": None,
        "item-2": None,
        "item-3": FailureKind.api_error,
        "item-4": None,
        "item-5": None,
        # Five of these in a row would trip the breaker, which is the shape a machine that
        # has stopped being able to start processes takes here.
    }
    # It is a transport failure, so it was sent twice before it was given up on.
    assert len(cli.calls) == len(keys) + 1


@pytest.mark.parametrize(
    ("interrupt_the_drain", "in_flight_kind"),
    # One Ctrl-C: the answers in the air are waited out and written...
    [(False, None), (True, FailureKind.aborted)],
    ids=["drained", "interrupted-again"],
)
def test_an_interrupt_ends_the_round_without_forfeiting_it(
    fake: Install,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_the_drain: bool,
    in_flight_kind: FailureKind | None,
) -> None:
    """Ctrl-C hands back everything the round already paid for, and stops the run after it."""
    # If Ctrl-C reaches the collecting thread — where a terminal sends it, and the thread that
    # would otherwise write the round — while two pool items are still in flight...
    sibling_started = threading.Event()
    collecting_gave_up = threading.Event()
    collecting = threading.main_thread().ident
    assert collecting is not None
    # ...held there by the two items, which return only once the collecting thread has given
    # up and started draining. That is the one wake-up in this test: an item finishing first
    # would wake the collector on its own, and the interrupt would land somewhere else.
    real_drain = client._drain

    def release_the_pool_then_drain(*args: Any) -> None:
        collecting_gave_up.set()
        if interrupt_the_drain:
            # ...and where the second Ctrl-C of an impatient operator lands, giving up on
            # them. The round still comes back, one result per key.
            raise KeyboardInterrupt("interrupted again")
        real_drain(*args)

    monkeypatch.setattr(client, "_drain", release_the_pool_then_drain)

    def interrupt_once_both_run(key: str) -> None:
        if key == content_of("item-1"):
            sibling_started.set()
        if key == content_of("item-0"):
            assert sibling_started.wait(GATE_TIMEOUT), "the sibling item never started"
            signal.pthread_kill(collecting, signal.SIGINT)
        if key in (content_of("item-0"), content_of("item-1")):
            assert collecting_gave_up.wait(GATE_TIMEOUT), "the interrupt never landed"

    # The two items ahead of them warm the pool, so the collector is waiting on answers when
    # the signal arrives rather than starting a worker thread, which is also interruptible.
    keys = ["canary", "warm-0", "warm-1", "item-0", "item-1", "item-2"]
    cli = fake({content_of(key): succeeds() for key in keys}, gate=interrupt_once_both_run)
    cli_client = CliClient(MODEL, concurrency=2)
    results = cli_client.submit(requests_for(*keys))
    # ...then the round returns instead of raising, and every answer already bought is there
    # for `enricher._round` to write — a raise would have thrown away the whole round, up to
    # ~1,900 items in the deepest one. The item never sent is `aborted`, and so is anything
    # the second interrupt gave up on: one result per key either way...
    assert kinds(results) == {
        "canary": None,
        "warm-0": None,
        "warm-1": None,
        "item-0": in_flight_kind,
        "item-1": in_flight_kind,
        "item-2": FailureKind.aborted,
    }
    assert content_of("item-2") not in cli.started
    # ...and the interrupt is delivered at the next round, which is the first moment the paid
    # work is written and the first moment stopping costs nothing.
    with pytest.raises(KeyboardInterrupt):
        cli_client.submit(requests_for("next-round"))
    assert content_of("next-round") not in cli.started


@pytest.mark.parametrize("concurrency", [0, -1])
def test_a_pool_narrower_than_one_item_is_refused_before_it_spends(
    fake: Install, concurrency: int
) -> None:
    """A concurrency no pool can honour is refused at construction rather than mid-round."""
    cli = fake({content_of("item-0"): succeeds()})
    with pytest.raises(ValueError, match="at least 1"):
        CliClient(MODEL, concurrency=concurrency)
    # The canary runs before the pool opens, so the same check inside `submit` would have
    # spent an item first and then raised it away.
    assert cli.started == []


def test_every_request_gets_exactly_one_result(fake: Install) -> None:
    """Every key comes back once, whether the round finished or gave up — `_round` needs that."""
    # A clean round...
    keys = [f"item-{index}" for index in range(6)]
    fake({content_of(key): succeeds() for key in keys})
    clean = CliClient(MODEL).submit(requests_for(*keys))
    assert sorted(result.key for result in clean) == sorted(keys)
    # ...and a round the breaker ended answer the same requests exactly once each.
    fake({content_of(key): errors() for key in keys})
    tripped = CliClient(MODEL, concurrency=1).submit(requests_for(*keys))
    assert sorted(result.key for result in tripped) == sorted(keys)


def test_preflight_and_items_share_one_env(fake: Install) -> None:
    """The auth check runs under the environment the items will spend under, not the shell's."""
    # If preflight and then an item run through the same fake...
    cli = fake(
        {
            AUTH_CALL: Reply(stdout=json.dumps(recorded("auth_status_logged_in"))),
            content_of("item-0"): succeeds(),
        }
    )
    preflight()
    CliClient(MODEL).submit(requests_for("item-0"))
    # ...then the auth question and the spend carried the very mapping the one builder
    # returns. A preflight run in the parent env would pass while every item failed.
    auth, item = cli.calls
    assert auth["env"] == item["env"] == build_env()


def test_the_child_env_is_constructed_not_inherited(
    fake: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key or a base url in the parent shell never reaches the child, so auth cannot divert."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.invalid")
    cli = fake({content_of("item-0"): succeeds()})
    CliClient(MODEL).submit(requests_for("item-0"))
    assert cli.calls[0]["env"] == {
        "HOME": os.environ["HOME"],
        "PATH": os.environ["PATH"],
        "USER": os.environ["USER"],
        # The one switch that keeps thinking off, and env rather than settings because
        # `--setting-sources ""` would drop a settings file.
        "MAX_THINKING_TOKENS": "0",
    }


def test_every_call_carries_the_isolation_flags(fake: Install) -> None:
    """Every item runs with no tools, no settings, no MCP and no session on disk."""
    cli = fake({content_of("item-0"): succeeds()})
    CliClient(MODEL).submit(requests_for("item-0"))
    call = cli.calls[0]
    assert call["argv"] == [
        CLAUDE,
        "--print",
        "--output-format",
        "json",
        "--model",
        MODEL,
        "--system-prompt",
        INSTRUCTIONS,
        "--json-schema",
        json.dumps(OUTPUT_SCHEMA),
        "--tools",
        "",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    # No MCP config to go with the strict flag, which is what makes it a closure...
    assert "--mcp-config" not in call["argv"]
    # ...and the render — untrusted transcript text, and long enough to blow an argv limit —
    # travels over stdin, never on the command line.
    assert call["input"] == content_of("item-0")
    assert all(content_of("item-0") not in argument for argument in call["argv"])


def test_calls_run_in_a_temp_cwd(fake: Install) -> None:
    """Items run outside every extractable project, so a stray session cannot be re-ingested."""
    cli = fake({content_of("item-0"): succeeds()})
    CliClient(MODEL).submit(requests_for("item-0"))
    # `sessions.py` keys the projects directory on the cwd, so a temp cwd is the control.
    cwd = Path(cli.calls[0]["cwd"])
    assert cwd.is_relative_to(tempfile.gettempdir())
    assert cwd != Path.cwd()


@pytest.fixture
def refuse_binary(monkeypatch: pytest.MonkeyPatch, refuse_subprocess: None) -> None:
    """A machine with no `claude` on PATH, as `subprocess.run` reports one."""

    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", CLAUDE)

    monkeypatch.setattr(subprocess, "run", missing)


@pytest.mark.parametrize(
    "envelope",
    [
        # Recorded: what `claude auth status` writes with no OAuth in reach.
        recorded("auth_status_logged_out"),
        # Derived from the recorded logged-in blob: an auth with no subscription behind it,
        # which would spend against something other than the allowance this run assumes.
        {
            key: value
            for key, value in recorded("auth_status_logged_in").items()
            if key != "subscriptionType"
        },
    ],
)
def test_preflight_refuses_an_unusable_auth(fake: Install, envelope: dict[str, Any]) -> None:
    """A run refuses at the door rather than failing every one of thousands of items."""
    fake({AUTH_CALL: Reply(stdout=json.dumps(envelope), returncode=1)})
    with pytest.raises(SystemExit):
        preflight()


def test_preflight_refuses_a_missing_binary(refuse_binary: None) -> None:
    """Enrichment runs through the CLI, so a machine without it stops before it reads a store."""
    with pytest.raises(SystemExit, match=CLAUDE):
        preflight()


def test_preflight_never_prints_the_auth_blob(fake: Install, capsys: pytest.CaptureFixture) -> None:
    """The refusal names the problem and nothing else — the blob carries an email and an org."""
    # Derived from the recorded logged-in blob: logged out, but still carrying the identity
    # fields the real one carries.
    fake(
        {
            AUTH_CALL: Reply(
                stdout=json.dumps(recorded("auth_status_logged_in") | {"loggedIn": False})
            )
        }
    )
    with pytest.raises(SystemExit) as refusal:
        preflight()
    printed = capsys.readouterr()
    for secret in ("REDACTED-EMAIL-9f2c", "REDACTED-ORG-ID-9f2c", "REDACTED-ORG-NAME-9f2c"):
        assert secret not in str(refusal.value)
        assert secret not in printed.out
        assert secret not in printed.err


def test_preflight_accepts_a_recorded_subscription(
    fake: Install, capsys: pytest.CaptureFixture
) -> None:
    """A logged-in subscription passes without a word."""
    fake({AUTH_CALL: Reply(stdout=json.dumps(recorded("auth_status_logged_in")))})
    preflight()
    assert capsys.readouterr().out == ""


@pytest.mark.live
# Two real `claude` calls, each ~4s of process boot and API time.
@pytest.mark.slow
@pytest.mark.skipif(LIVE_CLI not in os.environ, reason=f"set {LIVE_CLI} to spend on two items")
def test_two_real_items_come_back_valid(mutable_db: Path) -> None:
    """Two items through the real CLI land two rows the validator accepts.

    The only check that the pinned envelope, the keychain OAuth the constructed env reaches,
    and `MAX_THINKING_TOKENS=0` all still behave — everything else here reads a recording.
    Run it by hand once per Claude Code release; drift shows up as `EnvelopeDrift` from the
    canary, which is the crash the recordings cannot produce.
    """
    # If the smallest run that opens the pool is spent on a real store...
    cli.main("enrich", "--db", str(mutable_db), "--limit", "2")
    # ...then two rows landed — the deepest round first, so both are agent runs...
    with EnrichmentStore(mutable_db) as store:
        rows = store.connection.execute(
            "SELECT description, category, outcome, friction FROM agent_run_enrichments"
        ).fetchall()
    assert len(rows) == 2
    # ...and each one is an answer the validator accepts on its own, out of the store.
    for description, category, outcome, friction in rows:
        assert validate(
            {
                "description": description,
                "category": category,
                "outcome": outcome,
                "friction": friction,
            }
        ) == Enrichment(
            description=description,
            category=Category(category),
            outcome=Outcome(outcome),
            friction=friction,
        )


def test_the_live_smoke_is_gated_by_its_environment_variable() -> None:
    """The live smoke skips when it is not opted into, rather than passing without running.

    A `live` test that silently became a no-op would report green while proving nothing about
    the CLI — and unmarked, it would trip the subprocess guard instead of spending.
    """
    # `pytestmark` is written onto the function by the decorators, so it is untyped here.
    marks: list[pytest.Mark] = cast(Any, test_two_real_items_come_back_valid).pytestmark
    assert {"live", "slow"} <= {mark.name for mark in marks}
    # The skip condition tracks the opt-in in both directions: set, the smoke runs; unset —
    # which is what a bare `mise run test` sees — it skips.
    skipif = next(mark for mark in marks if mark.name == "skipif")
    assert skipif.args == (LIVE_CLI not in os.environ,)
