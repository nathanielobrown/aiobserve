"""Enrichment through the Claude Code CLI: one `claude -p` subprocess per item.

The subscription authenticates only through the CLI's OAuth, so this is the transport a
corpus pass really runs on. It satisfies the same `BatchClient` protocol the API clients do —
a round in, one result per key out — and everything above that seam is unchanged.

Two properties shape the code:

- **A round never raises once it is spending.** `enricher._round` upserts only after `submit`
  returns, so a raise mid-round forfeits every item already paid for. The one exception is
  the round's first item, run alone as a canary: envelope drift crashes there, for the price
  of one item, and is a `Failed(drift)` everywhere after
- **The child process cannot act on what it reads.** Renders carry untrusted transcript text,
  so tools, settings, MCP and slash commands are all switched off, the environment is
  constructed rather than inherited, and the cwd is a temp directory
"""

import json
import os
import subprocess
import tempfile
from collections import deque
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from aiobserve.enrich.batches import EnrichRequest, Failed, Result, Succeeded
from aiobserve.enrich.prompts import OUTPUT_TOOL
from aiobserve.enrich.validation import FailureKind

# Resolved on PATH rather than pinned: the CLI updates itself, and the env below carries the
# PATH the parent was launched with.
CLAUDE = "claude"

# Four `claude` processes for the length of a corpus pass, against a 5-hour allowance this
# project's own agents share. `--limit` is the pacing lever, and it is manual.
DEFAULT_CONCURRENCY = 4

# ~19x the worst wall time probed (2026-08-13). It fires on a hung process, not a slow one.
ITEM_TIMEOUT = 300

# `auth status` makes no model call, so anything but an immediate answer is a broken CLI.
AUTH_TIMEOUT = 30

# One immediate retry, which is what absorbs a transient CLI failure. A second identical
# send cannot improve a bad answer, so only the shapes below are resent.
ATTEMPTS = 2

# Five failures in a row is a run that has stopped working — logged out mid-round, allowance
# gone, a timeout grind. The kinds do not matter; the consecutiveness does.
BREAKER_BOUND = 5

# Failures the transport might not repeat. These are also the failures that saw no envelope
# at all, which is what makes a canary that hits one inconclusive.
_TRANSPORT_FAILURES = frozenset({FailureKind.api_error, FailureKind.timeout})

# The envelope fields this client reads, pinned at claude 2.1.221 and recorded in
# `tests/enrich/fixtures/`. `structured_output` is deliberately not among them: the CLI omits
# it whenever the model produced nothing conforming, which is a bad answer, not drift.
_CONTRACT_FIELDS = ("is_error", "stop_reason", "modelUsage")

# The output contract, as the CLI takes it. The same schema the API path forced as a tool.
_JSON_SCHEMA = json.dumps(OUTPUT_TOOL["input_schema"])


class EnvelopeDrift(Exception):
    """The CLI answered in a shape this client is not pinned to.

    Claude Code owns the envelope and changes it without notice, so a run that kept going
    would be writing rows out of an answer nobody has read. Raised only from a round's canary,
    where the crash costs one item.
    """


def build_env() -> dict[str, str]:
    """The environment every `claude` subprocess runs under — constructed, never inherited.

    One definition, shared by `preflight` and the items: an auth question asked in a different
    process shape than the spend would pass while every item failed.
    """
    return {
        # OAuth lives in the keychain, and without `USER` the CLI reports itself logged out.
        "USER": os.environ["USER"],
        "HOME": os.environ["HOME"],
        "PATH": os.environ["PATH"],
        # Thinking off: 1,168 output tokens for a 40-token answer became 142 in the
        # 2026-08-13 probes. Env rather than settings, which `--setting-sources ""` drops.
        "MAX_THINKING_TOKENS": "0",
        # A stray ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL is absent by construction: it would
        # divert auth off the subscription with no signal.
    }


def preflight() -> None:
    """Refuse the run now if the CLI cannot spend the subscription.

    Runs `claude auth status` under `build_env`, so what it validates is the process shape the
    items spend under. Echoes nothing from the answer: it carries an email, an org id and an
    org name.
    """
    try:
        done = subprocess.run(
            [CLAUDE, "auth", "status"],
            env=build_env(),
            capture_output=True,
            text=True,
            timeout=AUTH_TIMEOUT,
        )
    except FileNotFoundError as error:
        raise SystemExit(
            f"no {CLAUDE} on PATH: enrichment runs through the Claude Code CLI"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise SystemExit(f"`{CLAUDE} auth status` said nothing in {AUTH_TIMEOUT}s") from error
    try:
        status = json.loads(done.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(
            f"`{CLAUDE} auth status` answered with something other than JSON"
        ) from error
    # Absence is the answer: the recorded logged-out blob carries neither field.
    if not status.get("loggedIn"):
        raise SystemExit(f"the Claude Code CLI is logged out — run `{CLAUDE}`, log in, and rerun")
    if not status.get("subscriptionType"):
        raise SystemExit(
            "the Claude Code CLI is logged in with no subscription behind it, which is what "
            "enrichment spends"
        )


@dataclass
class _Breaker:
    """The round's one circuit breaker: consecutive failures, counted as answers land.

    One counter for the whole round rather than one per worker, advanced by the single thread
    that collects results — so it counts in completion order, which is the order the run is
    really failing in.
    """

    consecutive: int = 0

    def record(self, result: Result) -> None:
        self.consecutive = 0 if isinstance(result, Succeeded) else self.consecutive + 1

    @property
    def tripped(self) -> bool:
        return self.consecutive >= BREAKER_BOUND


class CliClient:
    """One enrichment round through `claude -p`, one subprocess per item over a thread pool.

    `submit` runs the round's first item alone as a canary, fans the rest out, and returns
    whatever it has — including after the breaker ends the round early, when the unsent
    remainder comes back as `Failed(aborted)`. It raises only from the canary.
    """

    def __init__(self, model: str, *, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.model = model
        self.concurrency = concurrency

    def submit(self, requests: Sequence[EnrichRequest]) -> list[Result]:
        results: list[Result] = []
        breaker = _Breaker()
        # Consumed from the front by both phases below; whatever is left was never sent.
        pending = deque(requests)
        # `sessions.py` keys the projects directory on the cwd, so running here is what keeps
        # any session the CLI still writes out of every extractable project.
        with tempfile.TemporaryDirectory(prefix="aiobserve-enrich-") as cwd:
            self._canary(pending, results, breaker, cwd)
            if pending and not breaker.tripped:
                self._fan_out(pending, results, breaker, cwd)
        results += [Failed(key=request.key, kind=FailureKind.aborted) for request in pending]
        return results

    def _canary(
        self,
        pending: deque[EnrichRequest],
        results: list[Result],
        breaker: _Breaker,
        cwd: str,
    ) -> None:
        """Run items one at a time until one produces an envelope, or the breaker gives up.

        A canary that errored or timed out validated nothing, so the next item re-canaries
        rather than opening the pool onto a contract no answer has confirmed.
        """
        while pending and not breaker.tripped:
            result = self._one(pending.popleft(), cwd, canary=True)
            results.append(result)
            breaker.record(result)
            if not (isinstance(result, Failed) and result.kind in _TRANSPORT_FAILURES):
                return

    def _fan_out(
        self,
        pending: deque[EnrichRequest],
        results: list[Result],
        breaker: _Breaker,
        cwd: str,
    ) -> None:
        """Run the rest over the pool, starting one new item for every result recorded.

        Fed rather than submitted all at once: on a trip nothing further starts, so the
        remainder is exactly the work nobody paid for. Items already running are collected —
        their spend has landed either way.
        """
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            in_flight: dict[Future[Result], EnrichRequest] = {}
            while True:
                while pending and len(in_flight) < self.concurrency and not breaker.tripped:
                    request = pending.popleft()
                    in_flight[pool.submit(self._one, request, cwd, canary=False)] = request
                if not in_flight:
                    return
                # One at a time, so the breaker advances in the order answers land.
                finished = next(as_completed(in_flight))
                del in_flight[finished]
                result = finished.result()
                results.append(result)
                breaker.record(result)

    def _one(self, request: EnrichRequest, cwd: str, *, canary: bool) -> Result:
        """One item, sent again only when the transport rather than the model was what failed."""
        result = self._attempt(request, cwd, canary=canary)
        for _ in range(ATTEMPTS - 1):
            if not (isinstance(result, Failed) and result.kind in _TRANSPORT_FAILURES):
                break
            result = self._attempt(request, cwd, canary=canary)
        return result

    def _attempt(self, request: EnrichRequest, cwd: str, *, canary: bool) -> Result:
        """One `claude -p` call: the render over stdin, the answer or a failure back."""
        try:
            done = subprocess.run(
                self._argv(request),
                input=request.content,
                env=build_env(),
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=ITEM_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return Failed(key=request.key, kind=FailureKind.timeout)
        if done.returncode != 0:
            # Where the CLI's own refusals land — a logged-out call exits 1.
            return Failed(key=request.key, kind=FailureKind.api_error)
        try:
            return _answer(request.key, done.stdout, self.model)
        except EnvelopeDrift:
            if canary:
                raise
            # Past the canary the round is spending, and a raise would forfeit every answer
            # already paid for. The crash summary names the kind instead.
            return Failed(key=request.key, kind=FailureKind.drift)

    def _argv(self, request: EnrichRequest) -> list[str]:
        """The one call shape every item takes: no tools, no settings, no MCP, no session."""
        return [
            CLAUDE,
            "--print",
            "--output-format",
            "json",
            "--model",
            self.model,
            # Replacement, not append: the default scaffold costs ~18.8K tokens an item.
            "--system-prompt",
            request.instructions,
            "--json-schema",
            _JSON_SCHEMA,
            # The render is untrusted transcript text, so nothing it says can reach a tool, a
            # settings file, an MCP server or a slash command.
            "--tools",
            "",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            # A render full of private transcript text is never written under `~/.claude`.
            "--no-session-persistence",
        ]


def _answer(key: str, stdout: str, model: str) -> Result:
    """One envelope as this client reads it, or `EnvelopeDrift` if it is not that shape.

    Reads four fields and ignores everything else the CLI writes, so a new field is not drift.
    """
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise EnvelopeDrift("`--output-format json` wrote something that is not JSON") from error
    missing = [field for field in _CONTRACT_FIELDS if field not in envelope]
    if missing:
        raise EnvelopeDrift(f"the answer envelope carries no {', '.join(missing)}")
    if envelope["is_error"]:
        # An errored call carries no answer, and no usage to check one against.
        return Failed(key=key, kind=FailureKind.api_error)
    if set(envelope["modelUsage"]) != {model}:
        raise EnvelopeDrift(
            f"{model} was asked for and modelUsage names {sorted(envelope['modelUsage'])} — a "
            "substituted model would mislabel every row the round wrote"
        )
    # Absent whenever the model produced nothing conforming, which the recorded logged-out
    # envelope shows the CLI doing. A bad answer, not a changed envelope.
    output = envelope.get("structured_output")
    if envelope["stop_reason"] == "max_tokens" or not isinstance(output, Mapping):
        return Failed(key=key, kind=FailureKind.invalid_output)
    return Succeeded(key=key, output=output)
