"""The offload page: one chunk of a tool result written to a file instead of the transcript.

The name is the transcript's own file name, so it may hold anything a tool named a file. It is
a key into the store and never a path the server opens (`docs/viewer.md`).
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds
from hyphae.view.citation import cited
from hyphae.view.deps import ViewerDep, checked
from hyphae.view.pages.offload import markup
from hyphae.view.store import (
    Page,
    open_store,
    page_rows,
)

router = APIRouter()


@router.get("/session/{session_id}/offload/{offload_name:path}")
def offload_page(
    session_id: str,
    offload_name: str,
    viewer: ViewerDep,
    after: int = 0,
    size: int = bounds.CHUNK.default,
) -> Response:
    """One chunk of a tool result Claude Code wrote to a file beside the transcript.

    The name is the transcript's own file name, so it may hold anything a tool named a
    file — spaces, percent signs, something shaped like a path. It is a key into the store
    and never a path the server opens, which is what makes the shape of it uninteresting.
    """
    checked(size, bounds.CHUNK.ceiling)
    if after < 0:
        raise HTTPException(400, "Ask for an offset of 0 or more.")
    bound: dict[str, ParamValue] = {
        "session_id": session_id,
        "name": offload_name,
        "after_chars": after,
        "chunk_chars": size,
    }
    with open_store(viewer.db) as connection:
        rows = page_rows(connection, Page.OFFLOAD, **bound)
    if not rows:
        raise HTTPException(404, "No offloaded result of that name is in this session.")
    row = rows[0]
    file = markup.OffloadFile(
        name=row["name"],
        size_bytes=row["size_bytes"],
        content_chars=row["content_chars"],
        lossy_decode=row["lossy_decode"],
        chunk=row["chunk"],
    )
    served = after + len(file.chunk)
    return viewer.html(
        markup.offload_page(
            session_id=session_id,
            file=file,
            # Where the next chunk starts, or None when this one reached the end.
            after=served if served < file.content_chars else None,
            size=size,
            citations={Page.OFFLOAD.value: cited(Page.OFFLOAD, bound)},
            dev=viewer.dev,
        )
    )
