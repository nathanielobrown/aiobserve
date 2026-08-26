"""The route table in `docs/viewer.md`: every page the viewer serves, from the app's own routes.

Run by a cog block in that document — `uv run python -m tools.gen_routes` — so a page added to
`view/app.py` reaches the table without anyone remembering it. Each row's description is the
handler's docstring cut to its first sentence, which makes the docstring the one place a page
is described: a handler that carries none crashes this rather than printing a blank cell.

Fragment routes are left out by rule rather than by a list, because they serve pieces of a page
a reader never types a URL for (`nodes.BODY_URL`, `nodes.KIN_URL`).
"""

import posixpath
import tempfile
from pathlib import Path

from fastapi import FastAPI
from starlette.routing import Route

from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.view import nodes
from aiobserve.view.app import build_app
from tools import text


def _fragment_root() -> str:
    """Where every fragment URL hangs, read off the constants the app mints them from.

    Derived rather than spelled, so a fragment route is excluded by where it lives and a new
    one needs an entry nowhere. Two constants that stopped sharing a parent would leave the
    rule covering neither, so that crashes.
    """
    root = posixpath.commonpath([nodes.BODY_URL, nodes.KIN_URL])
    if root in ("", "/"):
        raise ValueError(f"`{nodes.BODY_URL}` and `{nodes.KIN_URL}` share no fragment root")
    return f"{root}/"


FRAGMENT_ROOT = _fragment_root()
# Routes the app answers that no reader reads. FastAPI serves its own schema even with the
# docs UI off (`build_app` passes `docs_url=None`), and the table is about pages.
EXCLUDED = ("/openapi.json",)

HEADERS = ("Page", "Route")


def built_app() -> FastAPI:
    """The viewer over an empty store built for this call — only its routes are read.

    `build_app` opens the store at startup, so there is no route table to read without one.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "routes.duckdb"
        with DuckDbExporter(path):
            pass
        return build_app(path)


def pages(app: FastAPI) -> list[Route]:
    """Every GET route that serves a reader a page, in the order the app declares them."""
    served = [
        route
        for route in app.routes
        if isinstance(route, Route) and "GET" in (route.methods or set())
    ]
    missing = set(EXCLUDED) - {route.path for route in served}
    if missing:
        raise ValueError(f"excluded routes the app no longer serves: {sorted(missing)}")
    return [
        route
        for route in served
        if not route.path.startswith(FRAGMENT_ROOT) and route.path not in EXCLUDED
    ]


def described(route: Route) -> str:
    """What the page is, in the handler's own words."""
    docstring = route.endpoint.__doc__
    if not docstring:
        raise ValueError(
            f"`{route.endpoint.__name__}` serves `{route.path}` with no docstring to describe it"
        )
    return text.gloss(docstring)


def table(app: FastAPI) -> str:
    """The route table for one app."""
    return text.table(HEADERS, ((described(route), f"`{route.path}`") for route in pages(app)))


def generate() -> str:
    """The route table as the cog block splices it."""
    return table(built_app())


def main() -> None:
    print(generate())


if __name__ == "__main__":
    main()
