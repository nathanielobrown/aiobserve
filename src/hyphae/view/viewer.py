"""What a route module is built with: the store to read, and the way a page is rendered.

`Viewer` is one per app. A route module takes it as its factory's argument rather than
reaching into `request.app.state`, so a route body stays a plain typed function of what it
needs — and ends in `viewer.html(component(...))`, which is the only place markup meets a
response.
"""

from dataclasses import dataclass
from pathlib import Path

import htpy
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from hyphae.view.components import pages as components


@dataclass(frozen=True)
class Viewer:
    """The store every route reads, the frame it renders through, and whether `--dev` is on."""

    db: Path
    dev: bool
    # The Jinja environment the templates still left over render through. Goes away with them.
    templates: Jinja2Templates

    def html(self, element: htpy.Renderable, *, status: int = 200) -> HTMLResponse:
        """One rendered page, as a response.

        Rendered whole before the response exists, deliberately: a stream would flush a 200 and
        the markup above a failure before it knew, leaving a reader a page that looks finished
        (`tests/view/test_lifecycle.py`).
        """
        return HTMLResponse(str(element), status_code=status)

    def error(self, status: int, message: str) -> HTMLResponse:
        """The error page, which is what every handler in `build_app` answers with."""
        return self.html(
            components.error_page(status=status, message=message, dev=self.dev), status=status
        )
