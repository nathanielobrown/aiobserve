"""The gallery: every scenario the viewer tier pins, as a page you can open and edit against.

`mise run gallery` builds a store from the redacted fixtures, serves it under `--dev`
(`view/dev.py`), and adds an index at `INDEX` listing `tests/view/scenarios.py:SCENARIOS`. Editing
a component or a stylesheet reaches whatever is open — a stylesheet through the reload stream, a
component through a restart — so the loop is: pick a scenario, save, watch. The clock is the
corpus's own and never the wall's (`corpus_now`), so a page holds still between two launches.

Test tooling, not a package feature — it imports `tests/` freely. Privacy is structural: a port
is the only thing that reaches it from outside, so the process can serve nothing but the corpus
it builds itself.
"""

import argparse
import datetime as dt
import os
import shutil
from pathlib import Path
from tempfile import gettempdir

import htpy
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from hyphae.view import format as fmt
from hyphae.view.app import DEV_SHUTDOWN_SECONDS, HOST, build_app, claim
from hyphae.view.components import Html, layout
from hyphae.view.store import open_store
from tests.conftest import build_enriched_store
from tests.view.scenarios import SCENARIOS, Group, Scenario

# Where the index lives. Not `/`: that is the projects page and a scenario in its own right.
INDEX = "/gallery"

# One past the viewer's own port, so a gallery and a viewer over your own store can be open
# side by side. The default rather than the port: a link into the gallery still opens tomorrow,
# and a second gallery beside it — one per branch you are comparing — takes `--port`.
PORT = 8478


def grouped() -> dict[Group, list[tuple[str, Scenario]]]:
    """The scenario list under its headings: groups in `Group` order, rows in registry order.

    The headings come in the order `Group` declares them and the rows in registry order, so
    a reader who learns where a scenario sits finds it in the same place tomorrow.
    """
    return {
        group: [
            (route, scenario) for route, scenario in SCENARIOS.items() if scenario.group is group
        ]
        for group in Group
    }


def corpus_now(store: Path) -> dt.datetime:
    """The present the gallery's pages are read against: the newest session end `store` holds.

    Derived rather than written down, so a corpus recorded next month carries the clock forward
    with it. The corpus's own present rather than a round date, because a page's trailing
    windows are measured back from here — the wall clock leaves every one of them empty.
    """
    with open_store(store) as connection:
        latest = connection.execute("SELECT max(ended_at) FROM sessions").fetchone()
    if latest is None or latest[0] is None:
        raise ValueError(f"no session in {store} records when it ended, so there is no clock")
    return latest[0]


def gallery(store: Path) -> FastAPI:
    """The viewer over `store` in dev mode, with the scenario index mounted at `INDEX`.

    Freezes this process's clock to `corpus_now(store)` first — always, with no way to ask for
    otherwise — so two openings a week apart serve the same page. `fmt.utcnow` is the viewer's
    one clock and the seam written for this (`view/format.py`); the setattr outlives the app it
    is built for, so a test that builds a gallery puts the real one back.
    """
    frozen = corpus_now(store)
    fmt.utcnow = lambda: frozen
    app = build_app(store, dev=True)

    @app.get(INDEX)
    def index() -> HTMLResponse:
        return HTMLResponse(str(index_page()))

    return app


def index_page() -> Html:
    """The scenario list, rendered through the viewer's own layout.

    A page of the thing it indexes: the same masthead and the same stylesheet as every scenario
    it links to, so the index is read in the frame the pages under it are read in. `dev` is
    true by construction — the gallery is the viewer under `--dev`.
    """
    # One link per entry of `tests/view/scenarios.py:SCENARIOS`, under the heading its group
    # names: what the page shows, and the route it stands for beside it. Nothing is listed here
    # that the tests do not cover.
    listing = [
        [
            htpy.h2[group],
            htpy.ul[
                [
                    htpy.li[
                        [
                            htpy.a(data_scenario=route, href=scenario.url)[scenario.title],
                            # The one gap a reader has to see on this page: an `li` is no flex
                            # row, so this space is what holds the name apart from the route.
                            " ",
                            htpy.code[route],
                        ]
                    ]
                    for route, scenario in rows
                ]
            ],
        ]
        for group, rows in grouped().items()
    ]
    return layout.page(
        tab_title="Gallery — hyphae",
        scripts=None,
        main=htpy.fragment[[htpy.h1["Scenarios"], listing]],
        footer=None,
        dev=True,
    )


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


def scratch_dir(owner: int) -> Path:
    """Where a gallery keeps its store: one directory, named after the process that started it.

    A reload worker is a fresh interpreter that can be told nothing — it takes no argument and
    reads nothing from outside itself — so it derives this the way its parent does, from the pid
    the two already share. Named rather than made fresh so a hundred saves reuse one directory
    instead of leaving a hundred behind.
    """
    return Path(gettempdir()) / f"hyphae-gallery-{owner}"


def dev_gallery() -> FastAPI:
    """The gallery as a reload worker builds it, which is the only way it is ever built.

    A page is Python now, so a save restarts the server and uvicorn re-imports this module and
    calls this. Everything the app needs is made here rather than handed in: the store from the
    redacted fixtures, and the clock frozen off it. That is what keeps the privacy claim
    structural — there is no parameter to point somewhere else, and the half second a rebuild
    costs is the price of not having one.
    """
    scratch = scratch_dir(os.getppid())
    scratch.mkdir(exist_ok=True)
    store = scratch / "traces.duckdb"
    # Rebuilt rather than reused: a fixture edited between two saves should reach the page, and
    # the builder writes a new store rather than opening one.
    store.unlink(missing_ok=True)
    build_enriched_store(store, corpus=None)
    return gallery(store)


def main() -> None:
    """Serve the gallery until interrupted, restarting it on every Python save it renders from.

    The parent of the loop: it holds the port and the scratch directory, and the worker under it
    holds the app. `__name__` is `"__main__"` here — the module is run with `-m` — so the worker
    is named the way an importer would name it, off the package this file sits in.
    """
    port = parser().parse_args().port
    claim(port, "Pass --port to use another.")
    print(f"hyphae gallery: http://{HOST}:{port}{INDEX}")  # noqa: T201 — the URL to open
    try:
        uvicorn.run(
            f"{__package__}.{Path(__file__).stem}:{dev_gallery.__name__}",
            factory=True,
            reload=True,
            # The viewer's own package and this tier: a component, a scenario and this file are
            # what a reader edits here, and nothing else should cost them a rebuild.
            reload_dirs=[str(Path(fmt.__file__).parent), str(Path(__file__).parents[1])],
            host=HOST,
            port=port,
            log_level="warning",
            # The same shutdown cap `--dev` takes: the reload stream has no last chunk, so a
            # graceful exit that waited for it would never return (`view/app.py`).
            timeout_graceful_shutdown=DEV_SHUTDOWN_SECONDS,
        )
    finally:
        # The workers named their scratch after this process, so this is the one that clears it.
        shutil.rmtree(scratch_dir(os.getpid()), ignore_errors=True)


if __name__ == "__main__":
    main()
