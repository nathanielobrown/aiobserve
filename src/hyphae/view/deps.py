"""What a route asks FastAPI for instead of closing over it: the viewer, a connection, knobs.

Every route is a module-level function, so what used to arrive by closure arrives by
`Depends`. `ViewerDep` is the app's one `Viewer`, put on `app.state` by `build_app`; `Db` is
one request's read-only connection, opened and closed around the route.

`Db` holds that connection until the response is built, which is longer than an explicit
`with open_store(...)` inside the route. Short windows are what let `hp extract` write while a
page is open, so `Db` is for the fragment routes, whose markup is a line or two. A route that
renders a document opens the store itself and closes it before `viewer.html(...)` runs.

`checked` is here rather than beside the sizes it reads because refusing what is out of bounds
is a route's job: a presenter is callable without a request, and a module that imports
`HTTPException` is not (`tests/view/test_layout.py`). The node page's four knobs are parsed the
same way, one page in (`view/pages/node/routes/knobs.py`).
"""

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from hyphae.view.components import Html
from hyphae.view.components import error as error_markup
from hyphae.view.store import open_store


@dataclass(frozen=True)
class Viewer:
    """The store every route reads, and whether `--dev` is on. One per app."""

    db: Path
    dev: bool

    def html(self, element: Html, *, status: int = 200) -> HTMLResponse:
        """One rendered page, as a response.

        Rendered whole before the response exists, deliberately: a stream would flush a 200 and
        the markup above a failure before it knew, leaving a reader a page that looks finished
        (`tests/view/test_lifecycle.py`).
        """
        return HTMLResponse(str(element), status_code=status)

    def error(self, status: int, message: str) -> HTMLResponse:
        """The error page, which is what every handler in `build_app` answers with."""
        return self.html(
            error_markup.error_page(status=status, message=message, dev=self.dev), status=status
        )


def app_viewer(request: Request) -> Viewer:
    """The viewer `build_app` put on the app, for the routes it registered."""
    viewer = request.app.state.viewer
    # An app assembled without one is a programming error, and every route below would
    # otherwise fail somewhere further in with something less legible.
    assert isinstance(viewer, Viewer)  # noqa: S101
    return viewer


ViewerDep = Annotated[Viewer, Depends(app_viewer)]


def request_store(viewer: ViewerDep) -> Generator[duckdb.DuckDBPyConnection]:
    """One request's read-only connection, closed when the response is done.

    Read the window trade-off above before binding this in a route that renders a page.
    """
    with open_store(viewer.db) as connection:
        yield connection


Db = Annotated[duckdb.DuckDBPyConnection, Depends(request_store)]


def checked(size: int, ceiling: int) -> int:
    """A page size from a query string, or a 400 — every route's sizes go through here."""
    if not 1 <= size <= ceiling:
        raise HTTPException(400, f"Ask for a page size between 1 and {ceiling}.")
    return size
