"""What a route module is built with: the store to read, and the way a page is rendered.

`Viewer` is one per app. A route module takes it as its factory's argument rather than
reaching into `request.app.state`, so a route body stays a plain typed function of what it
needs.
"""

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates


@dataclass(frozen=True)
class Viewer:
    """The store every route reads, and the environment it renders through."""

    db: Path
    templates: Jinja2Templates

    def error(self, request: Request, status: int, message: str) -> Response:
        """The error page, which is what every handler in `build_app` answers with."""
        return self.templates.TemplateResponse(
            request, "error.html", {"status": status, "message": message}, status_code=status
        )
