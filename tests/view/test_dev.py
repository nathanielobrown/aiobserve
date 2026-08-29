"""The dev loop's server half: what `--dev` adds to a page, and what prod never carries.

Two apps over the one fixture store, compared byte for byte — the seam is served HTML, like
the rest of this tier. Two things HTML cannot show get shapes of their own:

- The reload stream never ends, so `TestClient` cannot read it. It runs a whole request
  through its portal and buffers the body before handing back a response
  (`starlette/testclient.py:353`), so a response with no last chunk hangs the caller rather
  than streaming to it. The leaves below drive the ASGI protocol instead: the same app, the
  same middleware, a different client
- uvicorn's graceful exit is only observable in a real uvicorn, so one slow leaf runs one and
  interrupts it with a stream open

The change sets handed to `event_for` are invented — watchfiles yields `set[tuple[Change, str]]`
and no recording of one exists — which is why one slow leaf drives the real `awatch` and checks
that shape against what the invented ones assume.
"""

import asyncio
import signal
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator, MutableMapping
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from watchfiles import Change, awatch

import hyphae.view
from hyphae.view.app import CSP, HOST, STATIC, build_app, claim
from hyphae.view.dev import RELOAD_URL, Event, Rendered, event_for, reload_router
from tests.view.scenarios import SCENARIOS

# The one line a page adds under `--dev`, whole. A prod page is the dev page with this string
# taken out and nothing else changed.
TAG = b'<script src="/static/dev-reload.js" defer></script>'


@pytest.fixture(scope="module")
def dev_client(enriched_db: Path) -> Iterator[TestClient]:
    """The viewer as `--dev` builds it, over the described corpus the prod sweep also reads.

    The described store rather than the bare one because six routes fetch what an enrichment
    pass wrote, and a store no pass has touched answers those with a 404.
    """
    with TestClient(build_app(enriched_db, dev=True)) as served:
        yield served


# --- What a change set asks the browser to do -------------------------------------------


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        # Invented change sets, labelled: watchfiles yields `set[tuple[Change, str]]` and no
        # recording of one exists. The slow leaf below checks the shape.
        # If every path in the set is a stylesheet the page can keep its state...
        (("static/style.css", "static/pygments.css"), Event.CSS),
        # ...but the client script beside a stylesheet is a page event, because a set the fast
        # path takes is a set whose script edit never reaches the browser...
        (("static/style.css", "static/dev-reload.js"), Event.PAGE),
        # ...and the client script itself only takes effect on a load.
        (("static/dev-reload.js",), Event.PAGE),
    ],
)
def test_a_change_set_is_a_css_event_only_when_every_path_in_it_is_a_stylesheet(
    paths: tuple[str, ...], expected: Event
) -> None:
    """A stylesheet swaps in place; anything else — a template, a script — needs a reload."""
    assert event_for({(Change.modified, path) for path in paths}) == expected


@pytest.mark.parametrize("change", list(Change))
def test_what_happened_to_a_stylesheet_does_not_change_what_the_browser_does(
    change: Change,
) -> None:
    """A stylesheet added, edited or deleted is one thing to a page: fetch the sheets again."""
    assert event_for({(change, "static/style.css")}) == Event.CSS


def test_a_change_set_with_nothing_in_it_is_a_broken_assumption_rather_than_an_event() -> None:
    """The watcher yields only when something changed, so an empty set is a bug to crash on."""
    with pytest.raises(ValueError, match="empty"):
        event_for(set())


@pytest.mark.parametrize(
    ("path", "watched"),
    [
        # What the viewer renders from, which is what a save should reach the browser through...
        ("/w/style.css", True),
        ("/w/dev-reload.js", True),
        # ...a page, which is Python now: uvicorn restarts the server on that save, and a
        # message from here would race the restart it is a symptom of...
        ("/w/node_pages.py", False),
        # ...the directory macOS reports beside a saved file, which has no suffix and would
        # read as a page event if it got through...
        ("/w", False),
        # ...a file under a watched directory the viewer does not render...
        ("/w/README.md", False),
        # ...and what watchfiles' own filter drops, which this one still defers to.
        ("/w/__pycache__/style.css", False),
    ],
)
def test_the_watcher_is_told_to_report_only_what_the_viewer_renders_from(
    path: str, watched: bool
) -> None:
    """The filter the stream watches under, read directly: suffix, and watchfiles' own noise."""
    assert Rendered()(Change.modified, path) is watched


# Drives the real watcher over a real directory: a debounce window of wall clock, and the only
# leaf standing between a green classifier and change sets it never sees in this shape.
@pytest.mark.slow
def test_the_change_sets_watchfiles_yields_are_the_shape_the_invented_ones_assume(
    tmp_path: Path,
) -> None:
    """What the real watcher hands `event_for` is a set of `(Change, absolute path)` pairs."""

    async def first_change() -> set[tuple[Change, str]]:
        # ...taking the first debounced set the watcher yields and stopping there...
        async for changes in awatch(tmp_path):
            return changes
        raise AssertionError("the watcher stopped before it saw anything")

    async def watched() -> set[tuple[Change, str]]:
        watcher = asyncio.create_task(first_change())

        # ...while a stylesheet is written under it, again and again, because there is no
        # signal for when the watcher is listening...
        async def poke() -> None:
            while True:
                (tmp_path / "style.css").write_text("body { color: red }")
                await asyncio.sleep(0.15)

        poker = asyncio.create_task(poke())
        try:
            return await asyncio.wait_for(watcher, 10)
        finally:
            poker.cancel()
            await asyncio.gather(poker, return_exceptions=True)

    raw = asyncio.run(watched())
    # ...which is a `Change` beside an absolute path, as a string...
    assert raw
    assert all(isinstance(change, Change) for change, _ in raw)
    assert str(tmp_path / "style.css") in {path for _, path in raw}
    # ...and, on macOS, the directory holding the file as well as the file. A raw set names
    # things that are not files the viewer renders, and a directory has no suffix — so an
    # unfiltered stylesheet save would read as a page event. This is the shape that made
    # `Rendered` necessary, and no invented set would have shown it.
    kept = {(change, path) for change, path in raw if Rendered()(change, path)}
    assert {path for _, path in kept} == {str(tmp_path / "style.css")}
    # ...which leaves the classifier reading a real set the way it reads the invented ones.
    assert event_for(kept) == Event.CSS


# --- What the dev app serves that the shipped one does not ------------------------------


@pytest.mark.parametrize("path", sorted(scenario.url for scenario in SCENARIOS.values()))
def test_a_dev_page_is_a_prod_page_plus_the_one_script_tag(
    path: str, enriched_client: TestClient, dev_client: TestClient
) -> None:
    """`--dev` changes one line of every page and nothing else, and no prod page mentions it.

    One comparison for both halves of the promise: that the dev loop reaches every page, and
    that a viewer built without it serves exactly what it served before.
    """
    dev = dev_client.get(path)
    prod = enriched_client.get(path)
    assert dev.status_code == 200 and prod.status_code == 200, path
    # Taking the tag out of the dev page leaves the prod page, byte for byte...
    assert dev.content.replace(TAG, b"") == prod.content, path
    # ...it lands once on a whole page and not at all on a fragment, which stands inside no
    # frame and comes back identical outright...
    assert dev.content.count(TAG) == (0 if path.startswith("/fragment/") else 1), path
    # ...and the shipped viewer names neither the client script nor the route it listens on.
    assert b"dev-reload" not in prod.content, path
    assert b"/dev/" not in prod.content, path


def test_the_shipped_viewer_declares_no_route_under_dev(
    client: TestClient, dev_client: TestClient
) -> None:
    """`--dev` adds the reload stream and nothing else; without it the route is not there.

    The two halves together are what keeps `SCENARIOS` meaning "everything the shipped viewer
    serves" — the completeness leaf in `test_bounds.py` never has to list a dev route.
    """
    assert declared(client) == set(SCENARIOS)
    assert declared(dev_client) == set(SCENARIOS) | {RELOAD_URL}
    assert client.get(RELOAD_URL).status_code == 404


def test_the_client_script_is_the_file_on_disk_whichever_mode_asked_for_it(
    client: TestClient, dev_client: TestClient
) -> None:
    """The static mount serves the reload client either way; only dev asks for it."""
    on_disk = (STATIC / "dev-reload.js").read_bytes()
    assert client.get("/static/dev-reload.js").content == on_disk
    assert dev_client.get("/static/dev-reload.js").content == on_disk


def test_the_reload_stream_answers_as_an_event_stream_under_the_same_policy(
    enriched_db: Path,
) -> None:
    """`/dev/reload` is SSE, and it carries the policy every other response carries.

    The whole shape of this loop — a same-origin GET, a client script served from the app —
    was chosen to leave `CSP` untouched, so the string is read back here rather than trusted.
    """
    streamed = asyncio.run(_stream(build_app(enriched_db, dev=True), RELOAD_URL, chunks=0))
    assert streamed.status == 200
    # Starlette appends a charset to any `text/*` media type, so the type is read off the head.
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert streamed.headers["content-security-policy"] == CSP


@pytest.mark.parametrize(("name", "expected"), [("style.css", b"css"), ("dev-reload.js", b"page")])
def test_a_file_saved_under_a_watched_path_becomes_one_message_on_the_stream(
    tmp_path: Path, name: str, expected: bytes
) -> None:
    """Saving a file the viewer renders from sends the browser the event for its kind.

    The watch paths are an argument for exactly this: a router pointed at `tmp_path` closes
    the loop end to end without writing into the package while the suite runs.
    """
    app = FastAPI()
    app.include_router(reload_router([tmp_path]))
    saved = tmp_path / name
    streamed = asyncio.run(
        _stream(app, RELOAD_URL, chunks=1, poke=lambda: saved.write_text("edited"))
    )
    assert streamed.body == [b"data: " + expected + b"\n\n"]


def test_dev_mode_without_the_watcher_installed_refuses_to_start(
    corpus_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--dev` in a checkout with no dev dependencies fails at startup, not at the first save.

    What pins the design's claim that the shipped viewer gains no dependency: it fails the
    moment someone hoists the `view.dev` import to the top of `view/app.py`.
    """
    # A `None` in `sys.modules` is what the import machinery reads as "this module is not to
    # be had" — the shortest way to hide an installed package from one import...
    monkeypatch.setitem(sys.modules, "watchfiles", None)
    # ...and `view.dev` has to be re-imported for the hiding to reach it, which means clearing
    # it from both places an import looks: the module table, and the package it hangs off.
    monkeypatch.delitem(sys.modules, "hyphae.view.dev", raising=False)
    monkeypatch.delattr(hyphae.view, "dev", raising=False)
    with pytest.raises(ImportError):
        build_app(corpus_db, dev=True)
    # The shipped viewer is untouched by the absence.
    assert build_app(corpus_db) is not None


# Runs a real uvicorn in a child and interrupts it: seconds of wall clock, and the only place
# graceful shutdown is observable — `TestClient` never runs uvicorn's exit path at all.
@pytest.mark.slow
def test_an_open_stream_does_not_hold_the_server_open_when_it_is_interrupted(
    corpus_db: Path,
) -> None:
    """Ctrl-C ends a dev viewer that has a browser listening on the reload stream.

    An SSE response has no last chunk, so uvicorn's graceful wait — which waits for every
    in-flight response — would never return. `serve` caps that wait under `--dev`.
    """
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-c", _SERVER, str(corpus_db), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        _await_server(base)
        # With a reader on the stream, the way a browser with the page open is...
        with httpx.stream("GET", f"{base}{RELOAD_URL}", timeout=10) as stream:
            assert stream.status_code == 200
            # ...an interrupt still reaps the process, rather than waiting on a response
            # that never completes.
            server.send_signal(signal.SIGINT)
            assert server.wait(timeout=20) == 0
    finally:
        if server.poll() is None:
            server.kill()
        server.wait(timeout=10)
        # Nothing reads the server's output, so its pipe would be closed by the collector
        # instead — as a `ResourceWarning` the suite raises.
        if server.stdout is not None:
            server.stdout.close()


def test_a_port_the_server_could_bind_is_not_refused_by_the_probe_that_guards_it() -> None:
    """Stopping a dev viewer and starting it again is the loop's own move, so `claim` may only
    refuse a port the server would have failed on.

    A connection the server side closed holds its address in `TIME_WAIT` for a minute or so. A
    plain bind is refused there, while the socket asyncio hands uvicorn takes it anyway — so a
    probe without `SO_REUSEADDR` refuses the restart the viewer would have served.
    """
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, 0))
        port = int(listener.getsockname()[1])
        listener.listen()
        with socket.socket() as reader:
            reader.connect((HOST, port))
            # The server side closing first is what leaves the address in `TIME_WAIT`.
            listener.accept()[0].close()
    claim(port, "Unreachable: the port is free.")

    # And the case it is there for still refuses, naming the port and the way out.
    with socket.socket() as held:
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind((HOST, port))
        held.listen()
        with pytest.raises(SystemExit, match=f"port {port} is in use.*Stop the other one"):
            claim(port, "Stop the other one.")


# --- Scaffolding -------------------------------------------------------------------------

# A dev viewer in a child process, for the interrupt leaf above.
_SERVER = """
import sys
from pathlib import Path
from hyphae.view.app import serve

serve(Path(sys.argv[1]), int(sys.argv[2]), open_browser=False, dev=True)
"""


class Streamed(NamedTuple):
    """One streamed response as the ASGI protocol handed it over."""

    status: int
    headers: dict[str, str]
    # One entry per `http.response.body` message taken before hanging up.
    body: list[bytes]


async def _stream(
    app: FastAPI,
    path: str,
    *,
    chunks: int,
    poke: Callable[[], object] | None = None,
    deadline: float = 15.0,
) -> Streamed:
    """One GET against `app`, taken to its first `chunks` body messages, then hung up.

    The client `TestClient` cannot be for a response that never ends. `poke` is called over
    and over while the stream is open: the watcher starts only after the headers go out, and
    nothing says when it is listening, so a single save can land before anything is watching.
    """
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    # Typed as ASGI types them, so that `app(...)` below takes these two without a cast.
    sent: asyncio.Queue[MutableMapping[str, Any]] = asyncio.Queue()
    gone = asyncio.Event()

    async def receive() -> dict[str, Any]:
        # The request has no body, and this client stays on the line until it is cancelled.
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        await sent.put(message)

    async def poking() -> None:
        while True:
            assert poke is not None
            poke()
            await asyncio.sleep(0.15)

    served = asyncio.create_task(app(scope, receive, send))
    poker = asyncio.create_task(poking()) if poke is not None else None
    try:
        start = await asyncio.wait_for(sent.get(), deadline)
        assert start["type"] == "http.response.start", start
        body: list[bytes] = []
        for _ in range(chunks):
            message = await asyncio.wait_for(sent.get(), deadline)
            assert message["type"] == "http.response.body", message
            body.append(message["body"])
    finally:
        for task in (poker, served):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (poker, served) if t is not None), return_exceptions=True)
    return Streamed(
        start["status"],
        {key.decode(): value.decode() for key, value in start["headers"]},
        body,
    )


def declared(client: TestClient) -> set[str]:
    """Every path an app declares, its included routers' included.

    `test_bounds.py:test_every_route_the_viewer_exposes_is_in_the_payload_sweep` reads the top
    level alone, which is the whole of the shipped viewer;
    FastAPI keeps an included router nested under one route object rather than flattening its
    routes into `app.routes`, so the dev half needs the walk.
    """
    found: set[str] = set()
    pending: list[Any] = list(client.app.routes)  # pyrefly: ignore
    while pending:
        route = pending.pop()
        if isinstance(route, APIRoute):
            found.add(route.path)
            continue
        # An included router keeps its own routes on the router it was built from.
        included = getattr(route, "original_router", None)
        pending += list(getattr(included, "routes", []))
    return found


def _free_port() -> int:
    """A port nothing holds — never the viewer's default, which a reader may be using."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _await_server(base: str, deadline: float = 20.0) -> None:
    """Block until the child viewer answers, or say how long it was given."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_poll(base, deadline))
    finally:
        loop.close()


async def _poll(base: str, deadline: float) -> None:
    async def until_served() -> None:
        async with httpx.AsyncClient() as reader:
            while True:
                try:
                    if (await reader.get(f"{base}/", timeout=2)).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)

    try:
        await asyncio.wait_for(until_served(), deadline)
    except TimeoutError:
        raise AssertionError(f"the viewer did not answer at {base} within {deadline}s") from None
