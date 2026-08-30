"""The shared scaffolding's own moving parts: taking a store's lock, and letting go of it.

Everything else in `tests/conftest.py` builds data. `locked()` drives another process, so it
is the one piece with failure modes of its own — and both of the suite's known flakes came
from it.
"""

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tests.conftest import LOCK_TIMEOUT, locked, opens_elsewhere, stop

# A holder that will not answer SIGTERM, so only the fallback can end it. Invented, and it
# has to be: the real holder does answer, and the flake this leaf covers is one that
# answered too late — deafness is that lateness taken to its limit, run deterministically.
_DEAF_HOLDER = (
    "import pathlib, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "pathlib.Path(sys.argv[1]).touch(); time.sleep(30)"
)

# How long the leaf gives the holder to answer each signal: long enough that a healthy one
# would, short enough that the fallback costs the suite nothing.
PATIENCE = 0.2


def test_a_holder_that_ignores_sigterm_is_still_stopped(tmp_path: Path) -> None:
    """A lock holder slow to answer SIGTERM is killed, not left to fail the teardown.

    Both lock tests in `tests/view/test_lifecycle.py` end this way, and a holder that took
    its time turned a passing test into a sporadic teardown error.
    """
    # If the holder is running and deaf to SIGTERM...
    ready = tmp_path / "ready"
    holder = subprocess.Popen([sys.executable, "-c", _DEAF_HOLDER, str(ready)])
    deadline = time.monotonic() + LOCK_TIMEOUT
    while not ready.exists():
        assert time.monotonic() < deadline, f"the holder never started within {LOCK_TIMEOUT}s"
        time.sleep(0.05)
    # ...then stopping it returns rather than raising...
    stop(holder, patience=PATIENCE)
    # ...and leaves nothing behind still holding what it held.
    assert holder.poll() is not None


def test_waiting_for_the_lock_opens_the_store_in_no_other_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Waiting for the holder to take the lock takes no connection of its own.

    The suite's second flake: the wait used to poll by opening the store read-only here,
    which takes a shared read lock, and DuckDB refuses a write open against one. A holder
    whose own open landed inside that window died with "Conflicting lock is held … (PID
    <this process>)", and the wait then sat out its whole deadline — CI run 31903080480.
    """
    # If the store exists and every open of it from this process is recorded...
    path = tmp_path / "traces.duckdb"
    duckdb.connect(str(path)).close()
    opened: list[str] = []
    connect = duckdb.connect

    def spy(database: Any, *args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        opened.append(str(database))
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", spy)
    # ...then the lock is held for the length of the block, as another process sees it...
    with locked(path):
        assert not opens_elsewhere(path, read_only=False)
    # ...and the wait that established it opened nothing here, so there was never a read
    # lock of ours for the holder's write open to collide with.
    assert opened == []


def test_a_lock_holder_that_cannot_take_the_lock_fails_the_test_at_once(tmp_path: Path) -> None:
    """A holder that dies without the lock fails its test straight away, quoting the crash.

    The alternative is what the flake did: sit out the full timeout and report only that
    nothing took the lock, with the reason buried in captured stderr.
    """
    # If the write lock is already held — by this process, which is the one holder a test
    # can plant deterministically...
    path = tmp_path / "traces.duckdb"
    held = duckdb.connect(str(path))
    try:
        started = time.monotonic()
        # ...then asking for it fails with what the holder said, not with a bare timeout...
        with pytest.raises(pytest.fail.Exception, match="Conflicting lock"), locked(path):
            pass
        # ...and it fails as soon as the holder dies rather than waiting out the deadline.
        assert time.monotonic() - started < LOCK_TIMEOUT
    finally:
        held.close()
