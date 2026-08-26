"""The gallery: every scenario the viewer tier pins, as a page you can open and edit against.

`mise run gallery` builds a store from the redacted fixtures, serves it under `--dev`
(`view/dev.py`), and adds an index at `INDEX` listing `tests/view/scenarios.py:ROUTES`. Editing
a template or a stylesheet reloads whatever is open, so the loop is: pick a scenario, save,
watch.

Test tooling, not a package feature — it imports `tests/` freely. Privacy is structural: `main`
takes no arguments, so the process can serve nothing but the corpus it builds itself.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from fastapi import FastAPI, Request, Response
from starlette.templating import Jinja2Templates

from aiobserve.view.app import DEV_SHUTDOWN_SECONDS, HOST, TEMPLATES, build_app, claim
from tests.conftest import build_enriched_store
from tests.view.scenarios import ROUTES

# Where the index lives. Not `/`: that is the projects page and a scenario in its own right.
INDEX = "/gallery"

# One past the viewer's own port, so a gallery and a viewer over your own store can be open
# side by side. Fixed, so a link into the gallery still opens tomorrow.
PORT = 8478

_GALLERY = Path(__file__).parent


def gallery(store: Path) -> FastAPI:
    """The viewer over `store` in dev mode, with the scenario index mounted at `INDEX`."""
    app = build_app(store, dev=True)
    # The package's templates are on the path too, so the index extends `base.html` and reads
    # as a page of the thing it indexes. `DEV` is the one global `base.html` asks for, and it
    # is true by construction here.
    templates = Jinja2Templates(directory=[_GALLERY, TEMPLATES])
    templates.env.globals["DEV"] = True  # pyrefly: ignore

    @app.get(INDEX)
    def index(request: Request) -> Response:
        return templates.TemplateResponse(request, "index.html", {"routes": ROUTES})

    return app


def main() -> None:
    """Build the fixture store, serve it, and hold until interrupted.

    No arguments, and none to add: a store path here would be a way to serve private data out
    of a tool whose whole safety is that it cannot.
    """
    with TemporaryDirectory() as scratch:
        store = Path(scratch) / "traces.duckdb"
        build_enriched_store(store, corpus=None)
        claim(PORT, "Stop the gallery already serving — this one takes no port.")
        print(f"aiobserve gallery: http://{HOST}:{PORT}{INDEX}")
        # The same shutdown cap `--dev` takes: the reload stream has no last chunk, so a
        # graceful exit that waited for it would never return (`view/app.py`).
        uvicorn.run(
            gallery(store),
            host=HOST,
            port=PORT,
            log_level="warning",
            timeout_graceful_shutdown=DEV_SHUTDOWN_SECONDS,
        )


if __name__ == "__main__":
    main()
