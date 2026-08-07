"""Starting, serving, and the two things that go wrong under a running viewer.

The store is a file another process writes. An extract can take its lock while a page is
open, and can replace its schema between two page loads, so both are checked per request
rather than once at startup — and both answer with a page that says what to do.
"""

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.export.duckdb import SCHEMA_VERSION
from aiobserve.view.app import CSP, SchemaMoved, StoreLocked, build_app, serve
from tests.view.conftest import fields

# What a writer does to the store: opens it read-write and holds it. The connection has to
# stay referenced — an unnamed one is freed at once, and the lock goes with it.
HOLDER = "import duckdb, sys, time; held = duckdb.connect(sys.argv[1]); time.sleep(30)"

# How long to wait for that subprocess to take the lock before giving up on the test.
LOCK_TIMEOUT = 10.0


@pytest.fixture
def copy(corpus_db: Path, tmp_path: Path) -> Path:
    """A private copy of the corpus, for the tests that write to or lock the store."""
    path = tmp_path / "store.duckdb"
    shutil.copyfile(corpus_db, path)
    return path


def wait_for_lock(path: Path) -> None:
    """Block until the store cannot be opened for reading, or fail saying it never was."""
    deadline = time.monotonic() + LOCK_TIMEOUT
    while time.monotonic() < deadline:
        try:
            duckdb.connect(str(path), read_only=True).close()
        except duckdb.IOException:
            return
        time.sleep(0.05)
    pytest.fail(f"nothing took the lock on {path} within {LOCK_TIMEOUT}s")


def test_a_locked_store_answers_503(copy: Path) -> None:
    """While an extract holds the store, a page says so instead of failing."""
    with TestClient(build_app(copy)) as client:
        holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(copy)])
        try:
            wait_for_lock(copy)
            response = client.get("/")
        finally:
            holder.terminate()
            holder.wait(timeout=LOCK_TIMEOUT)
    # A 503 is the honest answer: the store is there, and it will read again shortly...
    assert response.status_code == 503
    assert "holds the trace store" in fields(response.text, "id", "error")["message"]
    # ...and the error page is a page like any other, policy included.
    assert response.headers["content-security-policy"] == CSP
    # The viewer serves again once the writer lets go.
    with TestClient(build_app(copy)) as client:
        assert client.get("/").status_code == 200


def test_a_store_replaced_under_the_viewer_is_caught_per_request(copy: Path) -> None:
    """A re-extract between two page loads is refused rather than half-read."""
    with TestClient(build_app(copy)) as client:
        assert client.get("/").status_code == 200
        # The store the viewer started against is gone: this is what a schema bump plus a
        # fresh extract looks like from inside a running viewer.
        connection = duckdb.connect(str(copy))
        connection.execute("UPDATE meta SET schema_version = ?", [SCHEMA_VERSION + 1])
        connection.close()
        response = client.get("/")
    assert response.status_code == 503
    assert str(SCHEMA_VERSION) in fields(response.text, "id", "error")["message"]


def test_a_store_this_build_cannot_read_is_refused_at_launch(copy: Path) -> None:
    """The viewer fails to start rather than opening a browser onto an error page."""
    connection = duckdb.connect(str(copy))
    connection.execute("UPDATE meta SET schema_version = ?", [SCHEMA_VERSION - 1])
    connection.close()
    with pytest.raises(SchemaMoved):
        build_app(copy)


def test_a_store_that_is_not_there_is_refused_at_launch(tmp_path: Path) -> None:
    """A typo in `--db` is an error at startup, not an empty session list."""
    with pytest.raises(duckdb.IOException):
        build_app(tmp_path / "nothing.duckdb")


def test_a_locked_store_is_refused_at_launch(copy: Path) -> None:
    """Starting against a store an extract holds says which failure it was."""
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(copy)])
    try:
        wait_for_lock(copy)
        with pytest.raises(StoreLocked):
            build_app(copy)
    finally:
        holder.terminate()
        holder.wait(timeout=LOCK_TIMEOUT)


def test_a_taken_port_names_itself_and_the_way_out(copy: Path) -> None:
    """A second viewer says which port is taken and how to pick another."""
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        with pytest.raises(SystemExit) as refused:
            serve(copy, port, open_browser=False)
    assert str(port) in str(refused.value)
    assert "--port" in str(refused.value)
