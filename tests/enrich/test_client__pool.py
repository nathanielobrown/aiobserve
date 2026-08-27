"""Many items at once: the breaker, interrupts, the child env, preflight and the live smoke.

Driven over the same faked seam as `test_client.py` (`fake_cli.py`), but every leaf here is
about the round rather than the answer — what a pool spends before it stops, what each child
process is handed, and what a run refuses at the door. The two leaves at the bottom are the
only ones that reach the real API, and only under `HYPHAE_LIVE_CLI`.
"""

import json
import os
import signal
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

import pytest

from hyphae import cli
from hyphae.enrich import client
from hyphae.enrich.client import (
    CLAUDE,
    DEFAULT_MODEL,
    CliClient,
    build_env,
    preflight,
)
from hyphae.enrich.prompts import OUTPUT_SCHEMA
from hyphae.enrich.store import EnrichmentStore
from hyphae.enrich.taxonomy import Category, Outcome
from hyphae.enrich.validation import Enrichment, FailureKind, validate
from tests.enrich.conftest import LIVE_CLI
from tests.enrich.fake_cli import (
    AUTH_CALL,
    GATE_TIMEOUT,
    INSTRUCTIONS,
    MODEL,
    OTHER_MODEL,
    RECORDED_USAGE,
    Chain,
    Install,
    Reply,
    content_of,
    errors,
    kinds,
    mutated,
    recorded,
    requests_for,
    succeeds,
)


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
    real_drain = client._drain  # noqa: SLF001 — the seam this test times the interrupt on

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
    """Every item runs as the model it was asked for, with no tools, settings, MCP or session."""
    # Built with a model that is not the default, so a client that ignored what it was given
    # would fail here rather than agree with `--model` by coincidence...
    assert OTHER_MODEL != DEFAULT_MODEL
    # ...answered by a usage map naming that model, which is what keeps the canary quiet.
    cli = fake(
        {
            content_of("item-0"): Reply(
                stdout=json.dumps(mutated(modelUsage={OTHER_MODEL: RECORDED_USAGE}))
            )
        }
    )
    CliClient(OTHER_MODEL).submit(requests_for("item-0"))
    call = cli.calls[0]
    assert call["argv"] == [
        CLAUDE,
        "--print",
        "--output-format",
        "json",
        "--model",
        OTHER_MODEL,
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


# A string condition rather than a boolean, so pytest evaluates it at collection under the
# environment of the run instead of freezing it at import — which is also what lets the leaf
# at the bottom of this file read it in both directions.
LIVE_GATE = f"{LIVE_CLI!r} not in os.environ"


@pytest.mark.live
# Two real `claude` calls, each ~4s of process boot and API time.
@pytest.mark.slow
@pytest.mark.skipif(LIVE_GATE, reason=f"set {LIVE_CLI} to spend on two items")
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


def test_the_live_smoke_is_gated_by_its_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live smoke skips when it is not opted into, rather than passing without running.

    A `live` test that silently became a no-op would report green while proving nothing about
    the CLI — and unmarked, it would trip the subprocess guard instead of spending.
    """
    # `pytestmark` is written onto the function by the decorators, so it is untyped here.
    marks: list[pytest.Mark] = cast(Any, test_two_real_items_come_back_valid).pytestmark
    assert {"live", "slow"} <= {mark.name for mark in marks}
    # The smoke is gated on this one condition and nothing else...
    skipif = next(mark for mark in marks if mark.name == "skipif")
    assert skipif.args == (LIVE_GATE,)
    # ...and the condition, evaluated as pytest evaluates it, tracks the opt-in in both
    # directions: unset — what a bare `mise run test` sees — it skips; set, the smoke runs.
    monkeypatch.delenv(LIVE_CLI, raising=False)
    assert eval(LIVE_GATE, {"os": os}) is True
    monkeypatch.setenv(LIVE_CLI, "1")
    assert eval(LIVE_GATE, {"os": os}) is False
