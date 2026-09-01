"""The viewer itself: what `build_app` assembles over a trace store, and what `serve` runs.

`build_app(db_path)` returns a FastAPI app over one store. It mounts the statics, answers a
locked or moved store with a page rather than a stack trace, puts the `Viewer` on `app.state`
for the dependencies in `view/deps.py`, and registers each route module's router in turn — the
two lists, the node pages, the pages that are not a node's, the expansions, and the fragments.

Nothing the viewer serves writes: every request opens its own read-only connection
(`view/store.py`), checks the store's schema version, renders, and closes. That is what lets an
extract run while a page is open, and what makes a locked store a 503 rather than a crash.

Route order is a contract: `tools/gen_routes.py` reads `app.routes` in registration order into
the table in `docs/viewer.md`, which is why the routes are extended onto the app rather than
included as routers — an included router arrives as one opaque object that no consumer of
`app.routes` can see through.
"""

import os
import socket
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from hyphae.export.duckdb import StoreLocked
from hyphae.view import (
    expansions,
    fragments,
    node_pages,
)
from hyphae.view.deps import Viewer
from hyphae.view.pages.errors import routes as errors
from hyphae.view.pages.offload import routes as offload
from hyphae.view.pages.projects import routes as projects
from hyphae.view.pages.query import routes as query
from hyphae.view.pages.records import routes as records
from hyphae.view.pages.sessions import routes as sessions
from hyphae.view.store import (
    SchemaMoved,
    open_store,
)

# Loopback only, and a port unlikely to be taken. Fixed rather than picked at startup so a
# link pasted into a note opens the same page tomorrow.
HOST = "127.0.0.1"
PORT = 8477

# How long `--dev` waits for open reload streams to close before it stops waiting. One second
# because there is nothing to wait for: the stream never ends on its own (`view/dev.py`).
DEV_SHUTDOWN_SECONDS = 1

STATIC = Path(__file__).parent / "static"

# Where a reload worker reads the store from. `serve` sets it before uvicorn forks; a worker is
# a fresh interpreter that re-imports this module, so a path closed over in the parent is gone
# by the time the app is built. Read in exactly one place, below.
DEV_STORE = "HYPHAE_VIEW_DB"

# Nothing loads from anywhere but this app: no CDN, no inline script, no remote font. The
# viewer renders text a transcript wrote, so the escaping is the first defence and this is
# the second.
CSP = "default-src 'self'"


def build_app(db_path: Path, *, dev: bool = False) -> FastAPI:
    """The viewer over the store at `db_path`, which must exist and hold this schema.

    Under `dev` the app also serves the reload stream and puts its client on every page, so a
    saved stylesheet reaches an open one (`view/dev.py`). Nothing else differs: a prod page is
    a dev page minus that one script tag.
    """
    resolved = db_path.resolve()
    # Fail at startup rather than on the first page: a typo in `--db` should not open a
    # browser onto an error page.
    with open_store(resolved):
        pass

    app = FastAPI(title="hyphae", docs_url=None, redoc_url=None)
    if dev:
        # Imported here and nowhere else, because `view/dev.py` imports watchfiles — a
        # dev-group dependency an installed viewer does not have. A hoist to the top of this
        # file would make the shipped viewer depend on it, and `tests/view/test_dev.py` fails
        # the moment one happens.
        from hyphae.view import dev as dev_loop  # noqa: PLC0415

        app.include_router(dev_loop.reload_router())
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    # Where every route reads the store from: a route is a module-level function now, so it
    # asks for this by `Depends` rather than closing over it (`view/deps.py`).
    viewer = Viewer(db=resolved, dev=dev)
    app.state.viewer = viewer

    @app.middleware("http")
    async def _policy(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["content-security-policy"] = CSP
        return response

    @app.exception_handler(StoreLocked)
    def _locked(request: Request, exception: Exception) -> Response:
        return viewer.error(
            503,
            "Another process holds the trace store — an extract or an enrich is running. "
            "The page will load once it finishes.",
        )

    @app.exception_handler(SchemaMoved)
    def _moved(request: Request, exception: Exception) -> Response:
        # The opener's own sentence: it names both versions and the remedy that fits the
        # store on disk. All the viewer adds is what to do once the store is right.
        return viewer.error(503, f"{exception} Restart the viewer.")

    @app.exception_handler(StarletteHTTPException)
    def _http(request: Request, exception: Exception) -> Response:
        # Narrowing for the type checker: Starlette dispatches this handler by that class.
        assert isinstance(exception, StarletteHTTPException)  # noqa: S101
        return viewer.error(exception.status_code, exception.detail)

    # Extended rather than `include_router`: FastAPI keeps an included router nested under
    # one opaque route object, and `tools/gen_routes.py` and the payload sweep both read
    # `app.routes` expecting the routes themselves (`tests/view/test_dev.py` says so too).
    app.router.routes.extend(projects.router.routes)
    app.router.routes.extend(sessions.router.routes)
    app.router.routes.extend(node_pages.router.routes)
    app.router.routes.extend(errors.router.routes)
    app.router.routes.extend(query.router.routes)
    app.router.routes.extend(records.router.routes)
    app.router.routes.extend(offload.router.routes)
    app.router.routes.extend(expansions.router.routes)
    app.router.routes.extend(fragments.router.routes)
    return app


def claim(port: int, remedy: str) -> None:
    """Refuse `port` before anything binds it, naming the port and `remedy` — how to get one.

    A second server on a fixed port is the one startup failure a reader hits by accident, so
    the refusal names the port it wanted and leaves the way out to the caller that knows it.
    """
    with socket.socket() as probe:
        # The option asyncio sets for uvicorn, so the probe asks the server's question rather
        # than a stricter one: a port a stopped server left in `TIME_WAIT` is still bindable.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, port))
        except OSError as error:
            raise SystemExit(
                f"port {port} is in use — something may already be serving at "
                f"http://{HOST}:{port}/. {remedy}"
            ) from error


def dev_app() -> FastAPI:
    """The dev viewer, built in whichever process imports this — the reload worker's entry.

    uvicorn re-imports the factory on every restart, so the store arrives in the environment
    rather than in a closure. A worker started without one refuses here instead of serving an
    empty viewer that answers every page with a 503.
    """
    named = os.environ.get(DEV_STORE, "")
    if not named:
        raise RuntimeError(f"{DEV_STORE} is unset or empty, so a reload worker has no store")
    return build_app(Path(named), dev=True)


def serve(db_path: Path, port: int, *, open_browser: bool, dev: bool) -> None:
    """Run the viewer until interrupted, refusing a port something else already holds.

    `dev` adds the reload loop (`view/dev.py`) and restarts the server on a Python edit; it has
    no default because the two viewers are different things and the caller knows which it wants.
    """
    claim(port, "Pass --port to use another.")
    url = f"http://{HOST}:{port}/"
    print(f"hp view: {db_path} at {url}")  # noqa: T201 — the URL the person needs
    if open_browser:
        webbrowser.open(url)
    if not dev:
        uvicorn.run(build_app(db_path, dev=False), host=HOST, port=port, log_level="warning")
        return
    # A page is Python now, so the dev loop is a restart: uvicorn watches the package and
    # re-imports `dev_app` on every save. That takes an import string rather than an app, which
    # is what the environment variable above is for. The browser follows on its own — the
    # reload stream drops when the worker does, and the client reloads on the reconnect
    # (`view/static/dev-reload.js`).
    os.environ[DEV_STORE] = str(db_path)
    uvicorn.run(
        f"{__name__}:{dev_app.__name__}",
        factory=True,
        reload=True,
        reload_dirs=[str(Path(__file__).parent)],
        host=HOST,
        port=port,
        log_level="warning",
        # uvicorn's graceful exit waits for every in-flight response, and a reload stream has
        # no last chunk — so Ctrl-C with a browser listening would never return.
        timeout_graceful_shutdown=DEV_SHUTDOWN_SECONDS,
    )
