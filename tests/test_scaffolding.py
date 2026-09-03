"""The shared scaffolding's own moving parts: the pins on the suite's environment, and a store's
lock.

Everything else in `tests/conftest.py` builds data. The pins reach into a shipped library, the
temp root, and the venv itself, and `locked()` drives another process, so they are the pieces
with failure modes of their own — and both of the suite's known flakes came from the last.
"""

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest

from hyphae.export.duckdb import DuckDbExporter, open_trace_store
from tests.conftest import BLOCK_SIZE, LOCK_TIMEOUT, NO_WAIT, locked, opens_elsewhere, stop

# What a connection reports its thread pool as.
_THREADS = "SELECT current_setting('threads')"
# What a store reports it was laid out in.
_BLOCK_SIZE = "SELECT block_size FROM pragma_database_size()"
# DuckDB's own default block, which is what a store built outside the suite is laid out in.
STOCK_BLOCK_SIZE = 262144

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


def test_every_store_the_suite_opens_runs_on_one_duckdb_thread(tmp_path: Path) -> None:
    """A store opened anywhere under the suite queries single-threaded, however it was opened.

    The pin is what makes the parallel run pay (`plans/test-runtime/design.md`), and it has to
    hold for connections the harness opens directly *and* for the ones shipped code opens
    under it — the viewer opens one per request through `open_trace_store`.
    """
    # If a store is opened the way a builder opens one, for write...
    path = tmp_path / "traces.duckdb"
    DuckDbExporter(path, wait=NO_WAIT)
    with duckdb.connect(str(path)) as writable:
        # ...then it queries on a single thread...
        assert writable.execute(_THREADS).fetchone() == (1,)
    # ...and so does the read-only connection the viewer takes per request, through the
    # shipped opener the pin never touched.
    with open_trace_store(path, read_only=True, wait=NO_WAIT) as reader:
        assert reader.execute(_THREADS).fetchone() == (1,)


def test_every_store_the_suite_creates_is_laid_out_in_small_blocks(tmp_path: Path) -> None:
    """A store any builder under the suite creates weighs what its rows do, not what DuckDB's
    default block does — and a store that already exists keeps the layout it was born with.

    At the default 256 KB block every table and index of a fresh store takes one, so a
    one-session store weighs 9 MB and a run of the suite writes gigabytes. The pin is what
    holds that down (`tests/conftest.py`), and this leaf is what says it still does.
    """
    # If a store is created the way every builder creates one...
    path = tmp_path / "traces.duckdb"
    DuckDbExporter(path, wait=NO_WAIT)
    # ...then it is laid out in the smallest block DuckDB allows...
    with duckdb.connect(str(path), read_only=True) as reader:
        assert reader.execute(_BLOCK_SIZE).fetchone() == (BLOCK_SIZE,)
    # ...while a store created with an explicit layout — DuckDB's own default here, the layout
    # a store outside the suite has — keeps it when the suite opens it for write.
    stock = tmp_path / "stock.duckdb"
    with duckdb.connect(str(stock), config={"default_block_size": str(STOCK_BLOCK_SIZE)}):
        pass
    with duckdb.connect(str(stock)) as writable:
        assert writable.execute(_BLOCK_SIZE).fetchone() == (STOCK_BLOCK_SIZE,)


def test_every_temp_dir_the_suite_hands_out_sits_where_spotlight_does_not_look(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A `tmp_path` sits under a `.noindex` directory, so the stores a run leaves there are
    never handed to the macOS indexer — unless the run named its own base with `--basetemp`,
    which is a developer's choice the suite does not override.
    """
    if request.config.option.basetemp:
        pytest.skip("--basetemp names its own root")
    assert any(parent.name.endswith(".noindex") for parent in tmp_path.parents), tmp_path


@pytest.mark.skipif(
    sys.platform != "darwin", reason="only macOS routes a title through Launch Services"
)
def test_no_worker_can_check_in_with_launch_services() -> None:
    """The venv on a Mac has no `setproctitle`, so a worker never announces itself to the desktop.

    pytest-xdist retitles a worker for every test it runs, through `setproctitle` when that
    module is importable. On macOS the module checks the process in with Launch Services as an
    application and turns each retitle into a name-change notification that every GUI process
    answers — at 12 workers, thousands a second, enough to pin `launchservicesd` and freeze the
    desktop for the length of the run. `pyproject.toml` keeps the module off the Mac.
    """
    assert importlib.util.find_spec("setproctitle") is None


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
