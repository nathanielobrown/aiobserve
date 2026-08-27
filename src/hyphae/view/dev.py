"""The dev loop's server half: a file watcher behind a server-sent-event stream.

`build_app(db_path, dev=True)` mounts `reload_router` and includes the client script that
listens on it; nothing else imports this module. That is deliberate — watchfiles is a dev-group
dependency, so an installed viewer never imports it and `--dev` in a checkout without the dev
group fails at startup rather than serving a loop that never fires.

Server-sent events rather than a WebSocket because `CSP` allows a same-origin GET already, and
because `EventSource` retries a dropped connection on its own. The reconnect carries no
message, so the client reloads on the reconnect itself: an open page follows a restarted
server instead of showing what the old one rendered (`.claude/rules/viewer-ui.md`).
"""

from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from watchfiles import Change, DefaultFilter, awatch

from hyphae.view.app import STATIC
from hyphae.view.templating import TEMPLATES

# Where the client listens. Under `/dev/` so that one prefix names everything `--dev` adds.
RELOAD_URL = "/dev/reload"

# What a stylesheet is called, which is the whole of the classification below.
STYLESHEET = ".css"

# What a running viewer can serve differently without being restarted. A `.py` edit is not
# here on purpose: the reloader uvicorn would need takes an import string, and the loop this
# serves needs no restart at all (`plans/ui-dev-loop/design.md`).
RENDERED = frozenset({".html", STYLESHEET, ".js"})


class Rendered(DefaultFilter):
    """watchfiles' own noise filter, narrowed to the files the viewer renders from.

    The narrowing is not tidiness: macOS reports the containing *directory* alongside a saved
    file, and a directory has no suffix, so an unfiltered stylesheet save reads as a page
    event. `tests/view/test_dev.py` records that shape off the real watcher.
    """

    def __call__(self, change: Change, path: str) -> bool:
        return super().__call__(change, path) and Path(path).suffix in RENDERED


class Event(StrEnum):
    """What the browser is being asked to do with what just changed on disk."""

    # Only stylesheets changed: re-fetch them in place, and the page keeps its scroll, its
    # open sections, and whatever else a reload would cost.
    CSS = "css"
    # Something the server renders changed: ask for the page again.
    PAGE = "page"


def event_for(changes: set[tuple[Change, str]]) -> Event:
    """What one debounced watchfiles change set asks the browser to do.

    CSS only when *every* path in the set is a stylesheet: a template saved alongside one is a
    page event, or the edit that needs a render is the one the fast path swallows.
    """
    if not changes:
        raise ValueError("watchfiles yielded an empty change set, which it does not do")
    return Event.CSS if all(path.endswith(STYLESHEET) for _, path in changes) else Event.PAGE


def reload_router(watch_paths: Sequence[Path] = (TEMPLATES, STATIC)) -> APIRouter:
    """The reload stream, watching `watch_paths` for edits.

    The paths are an argument so a test can point a router at a temporary directory rather
    than write into the package while the suite runs; `build_app` passes the package's own.
    """
    router = APIRouter()

    @router.get(RELOAD_URL)
    async def reload_stream() -> StreamingResponse:
        return StreamingResponse(_events(watch_paths), media_type="text/event-stream")

    return router


async def _events(paths: Sequence[Path]) -> AsyncIterator[str]:
    """One message per debounced change set, until the reader hangs up or the server exits.

    The stream has no last message, so `serve` caps uvicorn's graceful shutdown under `--dev`:
    an exit that waits for every in-flight response would otherwise wait on this one forever.
    """
    async for changes in awatch(*paths, watch_filter=Rendered()):
        yield f"data: {event_for(changes)}\n\n"
