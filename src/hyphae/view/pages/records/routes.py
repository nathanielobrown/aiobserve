"""The records page: one page of a thread's raw transcript — where a report's citation lands.

A citation names `(session_id, source, line_no)`; the URL for it is this path with
`?after={line_no - 1}#L{line_no}`, so the cited record is the first row on the page.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view import bounds
from hyphae.view.citation import cited
from hyphae.view.deps import ViewerDep, checked
from hyphae.view.pages.records import markup
from hyphae.view.store import (
    MATCHED_ROWS,
    Page,
    open_store,
    page_rows,
    paged,
)

router = APIRouter()


@router.get("/session/{session_id}/thread/{source}/records")
def records_page(
    session_id: str,
    source: str,
    viewer: ViewerDep,
    after: int = queries.FIRST_PAGE,
    size: int = bounds.RECORDS.default,
) -> Response:
    """One page of a thread's raw transcript — where a report's citation lands.

    A citation names `(session_id, source, line_no)`; the URL for it is this path with
    `?after={line_no - 1}#L{line_no}`, so the cited record is the first row on the page.
    """
    checked(size, bounds.RECORDS.ceiling)
    keyed: dict[str, ParamValue] = {"session_id": session_id, "source": source}
    bound = keyed | {
        "after": after,
        "page_records": size,
        "preview_chars": queries.RECORD_PREVIEW,
    }
    with open_store(viewer.db) as connection:
        page = paged(page_rows(connection, Page.RECORDS, **bound), "line_no")
    # A thread the store never held and a cursor past the end of one it does are the same
    # answer — nothing at this URL. Neither is a page worth rendering empty.
    if not page.rows:
        raise HTTPException(404, "This store holds no records for that thread at that line.")
    # The one record the page fetches unasked: the first row, which is the one a citation
    # named — but only where a record that wide stays inside a page's budget
    # (`bounds.OPENED_RECORD_CHARS`). Past it the row is where every other row is, one
    # click from its own fetch, because a reader who paged here asked for no such thing.
    first = page.rows[0]
    opened = first["line_no"] if first["raw_chars"] <= bounds.OPENED_RECORD_CHARS else None
    return viewer.html(
        markup.records_page(
            session_id=session_id,
            source=source,
            rows=[
                markup.RecordRow(
                    line_no=row["line_no"],
                    type=row["type"],
                    timestamp=row["timestamp"],
                    raw_chars=row["raw_chars"],
                    raw_head=row["raw_head"],
                )
                for row in page.rows
            ],
            matched=first[MATCHED_ROWS],
            opened=opened,
            after=page.after,
            more=page.more,
            size=size,
            citations={Page.RECORDS.value: cited(Page.RECORDS, bound)},
            dev=viewer.dev,
        )
    )
