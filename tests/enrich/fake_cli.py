"""The faked `claude` seam every client test drives: recorded envelopes in, invocations out.

No process starts here. `subprocess.run` is replaced by `FakeCli`, which answers from the
recorded envelopes in `fixtures/` and records every invocation — so the argv, the constructed
env, the temp cwd and the deadline are all assertable. The seam is the module attribute the
client really resolves, not a private helper, and the guard in `test_no_live_api.py` is what
proves that: an unpatched client trips it rather than launching `claude`.

Only two envelopes are recorded: a success and the logged-out error. Every other shape is a
mutation of the success, labelled where it is built. A plain module rather than a conftest,
so a reader of either client test file can see the whole fake in one place.
"""

import json
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from hyphae.enrich.client import (
    CLAUDE,
    ITEM_TIMEOUT,
    EnrichRequest,
    Failed,
    Result,
)
from hyphae.enrich.validation import FailureKind

FIXTURES = Path(__file__).parent / "fixtures"


MODEL = "claude-haiku-4-5-20251001"


# A second model id, for the tests where agreeing with `DEFAULT_MODEL` by accident would hide
# a client that ignored the model it was built with.
OTHER_MODEL = "claude-sonnet-4-5-20250929"


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


# The recorded call's own usage numbers, re-keyed below to stand for a substituted model.
RECORDED_USAGE = recorded("envelope_success")["modelUsage"][MODEL]


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
    # What the CLI wrote on its diagnostic stream, which is the only stream a failure keeps.
    stderr: str = ""
    returncode: int = 0
    # A hung process, as `subprocess.run` reports one.
    raises: BaseException | None = None

    @property
    def answers(self) -> bool:
        """Whether this reply carries a usable envelope — what ends the serial canary phase."""
        if self.raises is not None or self.returncode != 0:
            return False
        try:
            envelope = json.loads(self.stdout)
        except json.JSONDecodeError:
            # Stdout the client cannot read is no more an answer than an errored call is.
            return False
        return not envelope.get("is_error", True)


def succeeds() -> Reply:
    return Reply(stdout=json.dumps(recorded("envelope_success")))


def errors() -> Reply:
    """The recorded logged-out call: exit 1, `is_error`, no answer."""
    return Reply(stdout=json.dumps(recorded("envelope_logged_out")), returncode=1)


def refused(stdout: str = "") -> Reply:
    """The recorded refusal: exit 1, one line on stderr, and nothing on stdout.

    What `claude` writes when it is passed a flag it does not take — the shape a CLI version
    bump takes, and the one that fails every item of a round identically. `stdout` plants an
    answer beside it (invented), for the leaves that prove no failure quotes that stream.
    """
    return Reply(
        stdout=stdout,
        stderr=(FIXTURES / "stderr_unknown_option.txt").read_text(),
        returncode=1,
    )


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
            return subprocess.CompletedProcess(
                list(argv), reply.returncode, reply.stdout, reply.stderr
            )
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


Install = Callable[..., FakeCli]


def kinds(results: Sequence[Result]) -> dict[str, FailureKind | None]:
    """Every result by key: its failure kind, or None where the model answered."""
    return {result.key: result.kind if isinstance(result, Failed) else None for result in results}
