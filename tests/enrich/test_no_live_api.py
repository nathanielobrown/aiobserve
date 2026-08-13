"""The guard that keeps the suite off the real `claude` binary and the allowance it spends."""

import os
import subprocess
import sys

import pytest

from aiobserve.enrich.client import CliClient, EnrichRequest
from tests.enrich.conftest import LIVE_CLI, SubprocessForbidden


@pytest.mark.parametrize("door", ["run", "Popen"])
def test_a_subprocess_call_is_refused(door: str) -> None:
    """Neither way of starting a process works from an unmarked test."""
    with pytest.raises(SubprocessForbidden, match="tried to start a process"):
        getattr(subprocess, door)([sys.executable, "-c", ""])


def test_an_unpatched_client_trips_the_guard() -> None:
    """A test that forgets to fake the CLI raises rather than spending the allowance.

    The guard shuts the door `CliClient` really uses. A guard on a private helper would pass
    here and let a renamed helper reach the real `claude`.
    """
    client = CliClient("claude-haiku-4-5-20251001")
    request = EnrichRequest(key="turn|s|main|t", instructions="Describe it.", content="# Main turn")
    with pytest.raises(SubprocessForbidden, match="tried to start a process"):
        client.submit([request])


@pytest.mark.live
@pytest.mark.skipif(LIVE_CLI not in os.environ, reason=f"set {LIVE_CLI} to run the live checks")
def test_the_guard_lets_a_live_test_start_a_process() -> None:
    """The exemption works: a `live` test starts a process, which is how the smoke runs at all.

    Runs the interpreter, not `claude` — the marker is what is under test here, not the CLI.
    """
    assert subprocess.run([sys.executable, "-c", ""], check=False).returncode == 0
