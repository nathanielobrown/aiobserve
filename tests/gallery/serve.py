"""The gallery: every scenario the viewer tier pins, as a page you can open and edit against.

`mise run gallery` builds a store from the redacted fixtures, serves it under `--dev`
(`view/dev.py`), and adds an index at `INDEX` listing `tests/view/scenarios.py:SCENARIOS`. Editing
a template or a stylesheet reloads whatever is open, so the loop is: pick a scenario, save,
watch.

Test tooling, not a package feature — it imports `tests/` freely. Privacy is structural: a port
is the only thing that reaches it from outside, so the process can serve nothing but the corpus
it builds itself.
"""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from fastapi import FastAPI, Request, Response
from starlette.templating import Jinja2Templates

from hyphae.view.app import DEV_SHUTDOWN_SECONDS, HOST, build_app, claim
from hyphae.view.templating import TEMPLATES
from tests.conftest import build_enriched_store
from tests.view.scenarios import SCENARIOS, Group, Scenario

# Where the index lives. Not `/`: that is the projects page and a scenario in its own right.
INDEX = "/gallery"

# One past the viewer's own port, so a gallery and a viewer over your own store can be open
# side by side. The default rather than the port: a link into the gallery still opens tomorrow,
# and a second gallery beside it — one per branch you are comparing — takes `--port`.
PORT = 8478

_GALLERY = Path(__file__).parent


def grouped() -> dict[Group, list[tuple[str, Scenario]]]:
    """The scenario list under its headings: groups in `Group` order, rows in registry order.

    Read here rather than in the template because Jinja's `groupby` sorts by the value it
    groups on, and the order the headings come in is the order `Group` declares them.
    """
    return {
        group: [
            (route, scenario) for route, scenario in SCENARIOS.items() if scenario.group is group
        ]
        for group in Group
    }


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
        return templates.TemplateResponse(request, "index.html", {"grouped": grouped()})

    return app


def parser() -> argparse.ArgumentParser:
    """The command line: a port, and deliberately nothing else.

    Where the gallery listens is the one thing a reader can want to change — two branches
    compared side by side, or a port already taken. A store path is what must never be
    addable, so the flags are read off this one place and `tests/gallery/test_serve.py` reads
    it back.
    """
    parse = argparse.ArgumentParser(prog="mise run gallery", description=__doc__)
    parse.add_argument("--port", type=int, default=PORT, help=f"listen here instead of {PORT}")
    return parse


def main() -> None:
    """Build the fixture store, serve it, and hold until interrupted."""
    port = parser().parse_args().port
    with TemporaryDirectory() as scratch:
        store = Path(scratch) / "traces.duckdb"
        build_enriched_store(store, corpus=None)
        claim(port, "Pass --port to use another.")
        print(f"hyphae gallery: http://{HOST}:{port}{INDEX}")  # noqa: T201 — the URL to open
        # The same shutdown cap `--dev` takes: the reload stream has no last chunk, so a
        # graceful exit that waited for it would never return (`view/app.py`).
        uvicorn.run(
            gallery(store),
            host=HOST,
            port=port,
            log_level="warning",
            timeout_graceful_shutdown=DEV_SHUTDOWN_SECONDS,
        )


if __name__ == "__main__":
    main()
