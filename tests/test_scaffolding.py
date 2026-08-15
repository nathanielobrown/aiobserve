"""The shared scaffolding's own moving part: ending the process that holds a store's lock.

Everything else in `tests/conftest.py` builds data. `stop()` drives another process, so it
is the one piece with a failure mode of its own — and the one the suite's known flake came
from.
"""

import subprocess
import sys
import time
from pathlib import Path

from tests.conftest import LOCK_TIMEOUT, stop

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
