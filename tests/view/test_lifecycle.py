"""Starting, serving, and the three things that go wrong under a running viewer.

The store is a file another process writes. An extract can take its lock while a page is
open, and can replace its schema between two page loads, so both are checked per request
rather than once at startup — and both answer with a page that says what to do.

The third is the viewer's own: a component that raises halfway down a page. It is why
`Viewer.html` renders whole before the response exists rather than streaming.
"""

import shutil
import socket
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.export.schema import SCHEMA_VERSION
from hyphae.view.app import CSP, build_app, serve
from hyphae.view.components import parts
from hyphae.view.store import SchemaMoved, StoreLocked
from tests.conftest import locked
from tests.view.conftest import fields

# Markup a half-rendered page would carry, distinctive enough to find anywhere in a response.
HALF = "<!--rendered-before-the-component-exploded-->"


@pytest.fixture
def copy(corpus_db: Path, tmp_path: Path) -> Path:
    """A private copy of the corpus, for the tests that write to or lock the store."""
    path = tmp_path / "store.duckdb"
    shutil.copyfile(corpus_db, path)
    return path


def test_a_locked_store_answers_503(copy: Path) -> None:
    """While an extract holds the store, a page says so instead of failing."""
    with TestClient(build_app(copy)) as client, locked(copy):
        response = client.get("/")
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
    with locked(copy), pytest.raises(StoreLocked):
        build_app(copy)


def test_a_taken_port_names_itself_and_the_way_out(copy: Path) -> None:
    """A second viewer says which port is taken and how to pick another."""
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        with pytest.raises(SystemExit) as refused:
            serve(copy, port, open_browser=False, dev=False)
    assert str(port) in str(refused.value)
    assert "--port" in str(refused.value)


def test_a_component_that_raises_mid_page_answers_500_and_sends_nothing(
    corpus_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page is rendered whole before the response exists, so a failure is never half-sent.

    The query page really does render `parts.code`, so replacing it puts the failure inside
    htpy's render rather than in the route body — the same place a real bug would be. The
    replacement yields markup and then raises, which is the case streaming would get wrong: the
    status is already 200 and the bytes already flushed by the time the raise happens, and a
    reader is left with a page that looks finished.
    """

    rendered: list[str] = []

    def explodes(**_: object) -> Iterator[str]:
        rendered.append(HALF)
        yield HALF
        raise RuntimeError("the component exploded halfway down the page")

    monkeypatch.setattr(parts, "code", explodes)
    # `raise_server_exceptions=False`, so this reads what a browser would get rather than the
    # traceback the test client re-raises by default.
    with TestClient(build_app(corpus_db), raise_server_exceptions=False) as client:
        response = client.get("/query/view_sessions")
    # htpy really did render the top of the component before the raise, so there was markup
    # here to leak...
    assert rendered == [HALF]
    # ...and the reader gets a failure instead of it, with not one byte of the half-built page.
    assert response.status_code == 500
    assert HALF not in response.text
